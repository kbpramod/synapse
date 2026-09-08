# Forge Scalable Distributed Test Scheduler

Forge includes a horizontally scalable, production-grade distributed test scheduler designed to reliably orchestrate automated test suites across high volumes of websites, environments, and tests.

---

## 1. High-Level Architecture

The scheduler decouples **schedule determination** (PostgreSQL + atomic claimer) from **ephemeral job queueing** (Redis) and **actual execution** (independent Playwright workers).

```
                      ┌──────────────────────────────────────────────┐
                      │         PostgreSQL (Source of Truth)         │
                      │  • forge.tests: cron, next_run_at, enabled   │
                      │  • forge.websites: concurrency_limit         │
                      └──────────────────────┬───────────────────────┘
                                             │
                       Atomic Claim: SELECT ... FOR UPDATE SKIP LOCKED
                       Advances next_run_at inside same transaction
                                             │
            ┌────────────────────────────────┴────────────────────────────────┐
            ▼                                                                 ▼
    ┌──────────────────┐                                             ┌──────────────────┐
    │ Scheduler Node 1 │                                             │ Scheduler Node 2 │
    │ (FastAPI/Daemon) │                                             │ (FastAPI/Daemon) │
    └────────┬─────────┘                                             └────────┬─────────┘
             │                                                                │
             └───────────────────────────────┬────────────────────────────────┘
                                             │ Enqueue Claims
                                             ▼
                          ┌───────────────────────────────────────┐
                          │         Redis (Ephemeral / Queue)     │
                          │  • Priority Queues (critical/high/..) │
                          │  • Active Concurrency Sets per Site   │
                          │  • Delayed Concurrency Backoff ZSET   │
                          │  • Worker & Scheduler Heartbeats      │
                          └──────────────────┬────────────────────┘
                                             │
                              Atomic BLPOP Priority Dequeue
                                             │
            ┌────────────────────────────────┴────────────────────────────────┐
            ▼                                                                 ▼
    ┌──────────────────┐                                             ┌──────────────────┐
    │  Worker Pool 1   │                                             │  Worker Pool 2   │
    │ • Checks site cap│                                             │ • Checks site cap│
    │ • Runs Playwright│                                             │ • Runs Playwright│
    │ • Saves test_runs│                                             │ • Saves test_runs│
    └──────────────────┘                                             └──────────────────┘
```

---

## 2. Core Scheduling Mechanics

### A. Mathematical De-Bunching & Window Spacing

Standard cron schedulers fire all tests sharing a cadence (e.g. `0 * * * *`) at the top of the hour, creating severe resource spikes. Forge treats the cron expression as defining the **frequency window duration** ($W$), not a mandatory synchronous execution boundary.

1. **Window Duration Calculation**:
   For any cron expression (e.g. `0 * * * *` = hourly $\to 3600\text{s}$, `0 0 * * *` = daily $\to 86400\text{s}$, `*/15 * * * *` = 15-minute $\to 900\text{s}$), Forge evaluates the interval $W = T_{next+1} - T_{next}$ using `croniter`.

2. **Cohort Spacing**:
   When $N$ active tests share a frequency window, the target spacing is:
   $$S = \frac{W}{N}$$
   Example: For 100 hourly tests across a 3600-second window, $S = 36\text{s}$.
   Each test $i \in [0, N-1]$ is assigned a persistent offset:
   $$\Delta_i = i \times S$$

3. **Persistent Staggering (`schedule_offset_seconds`)**:
   Each test row in `forge.tests` persists its `schedule_offset_seconds`. When calculating future executions:
   $$\text{next\_run\_at} = \text{croniter}(\text{cron\_expression}, \text{now}).\text{get\_next}() + \Delta_i$$
   This guarantees that recurring tests remain staggered on every cycle and never collapse back onto the 00:00 boundary line.

4. **Target Slot vs Hard Execution Slot**:
   The calculated `next_run_at` represents an **eligibility timestamp** (when the test becomes eligible for execution). Actual execution timing is governed by worker availability and per-website concurrency limits.

---

### B. Atomic Claiming with Zero Duplicate Execution

To allow horizontal scaling of scheduler and API instances:

```sql
SELECT t.id, t.test_id, t.website_id, t.domain, t.page_url, t.title,
       t.category, t.priority, t.script_path, t.test_code, t.language,
       t.cron_expression, t.timezone, t.schedule_offset_seconds,
       COALESCE(w.concurrency_limit, 2) AS concurrency_limit
FROM forge.tests t
LEFT JOIN forge.websites w ON t.website_id = w.id
WHERE t.enabled = TRUE
  AND t.status = 'active'
  AND (t.next_run_at <= NOW() OR t.next_run_at IS NULL)
ORDER BY
  CASE t.priority
    WHEN 'critical' THEN 1
    WHEN 'high' THEN 2
    WHEN 'medium' THEN 3
    ELSE 4
  END ASC,
  COALESCE(t.next_run_at, '1970-01-01'::timestamptz) ASC
LIMIT :batch_size
FOR UPDATE OF t SKIP LOCKED;
```

**Inside the same database transaction**:
1. Claimed rows are locked with `SKIP LOCKED`. Any concurrent scheduler querying at the exact same instant will skip the locked rows and select subsequent due rows.
2. `next_run_at` is immediately advanced into the future.
3. The transaction commits.
4. The claimed jobs are pushed to Redis priority queues.

---

### C. Priority Queues in Redis

Jobs are routed to Redis lists based on their `priority`:
- `forge:queue:critical`
- `forge:queue:high`
- `forge:queue:medium`
- `forge:queue:low`

Workers pop jobs using Redis `BLPOP`:
```python
job = redis.blpop(["forge:queue:critical", "forge:queue:high", "forge:queue:medium", "forge:queue:low"], timeout=2)
```
Because Redis checks keys in order from left to right, `critical` and `high` priority jobs are always processed before `medium` or `low` jobs.

---

### D. Per-Website Concurrency Limiting & Non-Blocking Backoff

To prevent a single application with 50 tests from consuming the entire worker pool:

1. Each website specifies `concurrency_limit` (default: `2`).
2. Active executions are tracked in Redis Set `forge:active:website:{website_id}` containing active `run_id`s with TTL.
3. When a worker dequeues a test:
   - If `SCARD forge:active:website:{website_id} < limit`:
     The worker acquires the slot and proceeds with execution.
   - If `SCARD >= limit`:
     The worker **does not block**. It places the job into `forge:queue:delayed` (a Sorted Set scored by `time.time() + 3.0` seconds) and immediately continues to dequeue jobs for other websites.
4. Ready jobs from `forge:queue:delayed` are automatically requeued back to the active priority queues on each scheduler tick.
5. When a test completes or fails, the worker removes `run_id` from the set in a strict `finally:` block.

---

## 3. Database Schema

### `forge.tests` (Scheduler Fields)
| Column | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `cron_expression` | VARCHAR(100) | `'0 0 * * *'` | Standard 5-field cron cadence. |
| `timezone` | VARCHAR(50) | `'UTC'` | Evaluated time zone (e.g. `'UTC'`, `'America/New_York'`). |
| `schedule_offset_seconds` | INTEGER | `0` | Assigned de-bunching offset in seconds. |
| `enabled` | BOOLEAN | `TRUE` | Whether this test is active for scheduled runs. |
| `priority` | VARCHAR(20) | `'medium'` | Scheduling priority: `'critical'`, `'high'`, `'medium'`, `'low'`. |
| `last_run_at` | TIMESTAMPTZ | `NULL` | Timestamp of last execution. |
| `next_run_at` | TIMESTAMPTZ | Indexed | Target eligibility timestamp for next execution. |

### `forge.websites`
| Column | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `concurrency_limit` | INTEGER | `2` | Maximum concurrent worker runs allowed for this website. |

---

## 4. API Endpoints

The scheduler API is available under `/api/scheduler`:

### Status & Telemetry
- **`GET /api/scheduler/status`**:
  Returns daemon running state, worker pool running state, Redis connection status, queue depths (by priority), active worker count, and list of running tests.
- **`GET /api/scheduler/upcoming?limit=50&domain=example.com`**:
  Returns the chronological list of upcoming tests, including `seconds_until_due` and `is_due_now`.
- **`GET /api/scheduler/metrics?hours=24`**:
  Combines Redis ephemeral queue metrics with PostgreSQL test execution summary (passed, failed, healed counts, average duration).

### Schedule Optimization & Triggers
- **`POST /api/scheduler/distribute`**:
  Body: `{"website_id": 12}` (optional).
  Calculates and persists window spacing ($W / N$) across cohorts of tests sharing the same cadence.
- **`POST /api/scheduler/trigger/{test_id}`**:
  Enqueues an immediate test execution into Redis with `critical` priority, bypassing the scheduled window.
- **`PATCH /api/scheduler/tests/{test_id}`**:
  Updates `enabled`, `cron_expression`, `timezone`, `priority`, or `schedule_offset_seconds`.

### Lifecycle Controls
- **`POST /api/scheduler/daemon/start`**: Starts the continuous background claimer loop.
- **`POST /api/scheduler/daemon/stop`**: Stops the claimer loop.
- **`POST /api/scheduler/worker/start`**: Starts in-process workers (`{"num_workers": 2}`).
- **`POST /api/scheduler/worker/stop`**: Stops in-process workers.

---

## 5. Running Standalone Workers

Workers can also be executed as standalone background CLI processes on independent compute nodes:

```bash
uv run python -m scheduler.worker --workers 4 --id-prefix node_a
```
