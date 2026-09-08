"""
Redis-backed distributed queue, concurrency limiter, and coordination for Forge.

Handles:
- Priority queueing (critical > high > medium > low) via Redis BLPOP.
- Per-website concurrency tracking and backoff queue (ZSET).
- Ephemeral worker and scheduler heartbeats.
- Real-time queue metrics and running task inspection.
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
try:
    import redis
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    venv_site = Path(__file__).resolve().parents[2] / ".venv" / "Lib" / "site-packages"
    if venv_site.exists() and str(venv_site) not in sys.path:
        sys.path.insert(0, str(venv_site))
    import redis

from config import REDIS_URL

logger = logging.getLogger("forge.scheduler.queue")

# Redis Key Namespaces
KEY_QUEUE_CRITICAL = "forge:queue:critical"
KEY_QUEUE_HIGH = "forge:queue:high"
KEY_QUEUE_MEDIUM = "forge:queue:medium"
KEY_QUEUE_LOW = "forge:queue:low"
KEY_QUEUE_DELAYED = "forge:queue:delayed"

PRIORITY_KEY_MAP = {
    "critical": KEY_QUEUE_CRITICAL,
    "high": KEY_QUEUE_HIGH,
    "medium": KEY_QUEUE_MEDIUM,
    "low": KEY_QUEUE_LOW,
}

PRIORITY_QUEUES_ORDERED = [
    KEY_QUEUE_CRITICAL,
    KEY_QUEUE_HIGH,
    KEY_QUEUE_MEDIUM,
    KEY_QUEUE_LOW,
]

KEY_RUNNING_JOBS = "forge:running_jobs"
PREFIX_WEBSITE_ACTIVE = "forge:active:website:"
PREFIX_WORKER_HEARTBEAT = "forge:worker:heartbeat:"
PREFIX_SCHEDULER_HEARTBEAT = "forge:scheduler:heartbeat:"


class SchedulerQueue:
    def __init__(self, redis_url: Optional[str] = None):
        url = redis_url or REDIS_URL
        if not url:
            logger.warning("[REDIS] REDIS_URL is not set. Scheduler queue may fail to connect.")
            url = "redis://localhost:6379/0"

        # Upstash rediss:// compatibility
        connection_kwargs: Dict[str, Any] = {
            "decode_responses": True,
            "socket_timeout": 10.0,
            "socket_connect_timeout": 10.0,
            "retry_on_timeout": True,
        }
        if url.startswith("rediss://"):
            connection_kwargs["ssl_cert_reqs"] = None

        self._client = redis.Redis.from_url(url, **connection_kwargs)

    @property
    def client(self) -> redis.Redis:
        return self._client

    def ping(self) -> bool:
        """Verifies Redis connection health."""
        try:
            return bool(self._client.ping())
        except Exception as e:
            logger.error(f"[REDIS] Ping failed: {e}")
            return False

    # ==========================================
    # Enqueue & Dequeue
    # ==========================================
    def enqueue(self, job: Dict[str, Any], priority: Optional[str] = None) -> bool:
        """
        Pushes a test execution job into the appropriate priority queue.
        Default priority is read from job['priority'] or 'medium'.
        """
        try:
            prio = (priority or job.get("priority") or "medium").lower()
            target_key = PRIORITY_KEY_MAP.get(prio, KEY_QUEUE_MEDIUM)
            payload = json.dumps(job)
            self._client.rpush(target_key, payload)
            logger.info(
                f"[REDIS QUEUE] Enqueued test '{job.get('test_id')}' (run_id={job.get('run_id')}) "
                f"to {target_key}"
            )
            return True
        except Exception as e:
            logger.error(f"[REDIS QUEUE] Failed to enqueue job: {e}", exc_info=True)
            return False

    def dequeue(self, timeout: int = 2) -> Optional[Dict[str, Any]]:
        """
        Pops the highest priority available job using Redis BLPOP.
        Checks critical -> high -> medium -> low in strict order.
        """
        try:
            res = self._client.blpop(PRIORITY_QUEUES_ORDERED, timeout=timeout)
            if not res:
                return None
            queue_name, payload = res
            job = json.loads(payload)
            job["_origin_queue"] = queue_name
            return job
        except Exception as e:
            logger.debug(f"[REDIS QUEUE] Dequeue idle/error: {e}")
            return None

    def delay_job(self, job: Dict[str, Any], delay_seconds: float = 3.0, reason: str = "concurrency") -> bool:
        """
        Places a job into the delayed Sorted Set when concurrency limit is reached
        or execution must be deferred. Score is target epoch timestamp.
        """
        try:
            target_epoch = time.time() + max(0.5, delay_seconds)
            payload = json.dumps(job)
            self._client.zadd(KEY_QUEUE_DELAYED, {payload: target_epoch})
            logger.info(
                f"[REDIS QUEUE] Job '{job.get('test_id')}' deferred for {delay_seconds:.1f}s "
                f"(reason: {reason})"
            )
            return True
        except Exception as e:
            logger.error(f"[REDIS QUEUE] Failed to delay job: {e}", exc_info=True)
            return False

    def requeue_delayed_jobs(self, limit: int = 50) -> int:
        """
        Pulls ready jobs from the delayed Sorted Set (score <= now) and pushes
        them back into their priority queues.
        """
        try:
            now_epoch = time.time()
            ready_payloads = self._client.zrangebyscore(
                KEY_QUEUE_DELAYED, 0, now_epoch, start=0, num=limit
            )
            if not ready_payloads:
                return 0

            requeued_count = 0
            for payload in ready_payloads:
                # Remove from sorted set atomically
                removed = self._client.zrem(KEY_QUEUE_DELAYED, payload)
                if removed:
                    try:
                        job = json.loads(payload)
                        prio = (job.get("priority") or "medium").lower()
                        target_key = PRIORITY_KEY_MAP.get(prio, KEY_QUEUE_MEDIUM)
                        self._client.rpush(target_key, payload)
                        requeued_count += 1
                    except Exception:
                        pass

            if requeued_count > 0:
                logger.debug(f"[REDIS QUEUE] Requeued {requeued_count} deferred jobs back to active queues.")
            return requeued_count
        except Exception as e:
            logger.error(f"[REDIS QUEUE] Error requeuing delayed jobs: {e}")
            return 0

    # ==========================================
    # Concurrency Control (Per Website / Environment)
    # ==========================================
    def try_acquire_concurrency(
        self, website_id: Any, run_id: str, limit: int = 2, ttl_seconds: int = 600
    ) -> bool:
        """
        Checks if the website's currently running tests have reached its concurrency limit.
        If under limit: records run_id in the website's active set and returns True.
        If at/above limit: returns False without blocking the worker.
        """
        if not website_id:
            # Tests without a specific website are unconstrained
            return True

        key = f"{PREFIX_WEBSITE_ACTIVE}{website_id}"
        try:
            # Lua script or pipeline to atomically check count and add
            pipe = self._client.pipeline()
            pipe.scard(key)
            pipe.sismember(key, run_id)
            cardinality, is_already_member = pipe.execute()

            if is_already_member:
                return True

            if cardinality >= limit:
                return False

            # Add to set with expiration protection
            pipe2 = self._client.pipeline()
            pipe2.sadd(key, run_id)
            pipe2.expire(key, ttl_seconds)
            pipe2.execute()
            return True
        except Exception as e:
            logger.error(f"[CONCURRENCY] Failed acquiring slot for website {website_id}: {e}")
            return True  # Fail open rather than deadlocking in case of transient Redis issue

    def release_concurrency(self, website_id: Any, run_id: str) -> None:
        """Releases the concurrency slot for a completed/failed test run."""
        if not website_id:
            return
        key = f"{PREFIX_WEBSITE_ACTIVE}{website_id}"
        try:
            self._client.srem(key, run_id)
        except Exception as e:
            logger.error(f"[CONCURRENCY] Error releasing slot for website {website_id}: {e}")

    def get_website_active_count(self, website_id: Any) -> int:
        """Returns number of active test executions for a specific website."""
        key = f"{PREFIX_WEBSITE_ACTIVE}{website_id}"
        try:
            return int(self._client.scard(key))
        except Exception:
            return 0

    # ==========================================
    # Running Jobs Registry
    # ==========================================
    def register_running_job(self, run_id: str, job_info: Dict[str, Any]) -> None:
        """Registers a job as currently executing in workers."""
        try:
            payload = json.dumps({
                **job_info,
                "started_at": datetime.now(timezone.utc).isoformat(),
            })
            self._client.hset(KEY_RUNNING_JOBS, run_id, payload)
        except Exception as e:
            logger.error(f"[RUNNING JOBS] Failed to register run {run_id}: {e}")

    def unregister_running_job(self, run_id: str) -> None:
        """Removes a job from the running registry upon completion."""
        try:
            self._client.hdel(KEY_RUNNING_JOBS, run_id)
        except Exception as e:
            logger.error(f"[RUNNING JOBS] Failed to unregister run {run_id}: {e}")

    def list_running_jobs(self) -> List[Dict[str, Any]]:
        """Returns all currently executing jobs across all workers."""
        try:
            entries = self._client.hgetall(KEY_RUNNING_JOBS)
            running = []
            for run_id, val in entries.items():
                try:
                    data = json.loads(val)
                    data["run_id"] = run_id
                    running.append(data)
                except Exception:
                    pass
            return running
        except Exception as e:
            logger.error(f"[RUNNING JOBS] Failed listing running jobs: {e}")
            return []

    # ==========================================
    # Ephemeral Heartbeats
    # ==========================================
    def beat_scheduler(self, scheduler_id: str, ttl_seconds: int = 30) -> None:
        """Updates heartbeat for a scheduler daemon instance."""
        try:
            key = f"{PREFIX_SCHEDULER_HEARTBEAT}{scheduler_id}"
            self._client.set(key, datetime.now(timezone.utc).isoformat(), ex=ttl_seconds)
        except Exception:
            pass

    def beat_worker(self, worker_id: str, ttl_seconds: int = 30) -> None:
        """Updates heartbeat for a worker instance."""
        try:
            key = f"{PREFIX_WORKER_HEARTBEAT}{worker_id}"
            self._client.set(key, datetime.now(timezone.utc).isoformat(), ex=ttl_seconds)
        except Exception:
            pass

    def count_active_workers(self) -> int:
        """Counts how many workers have reported a heartbeat recently."""
        try:
            keys = self._client.keys(f"{PREFIX_WORKER_HEARTBEAT}*")
            return len(keys)
        except Exception:
            return 0

    def count_active_schedulers(self) -> int:
        """Counts how many scheduler instances are alive."""
        try:
            keys = self._client.keys(f"{PREFIX_SCHEDULER_HEARTBEAT}*")
            return len(keys)
        except Exception:
            return 0

    # ==========================================
    # Metrics
    # ==========================================
    def get_metrics(self) -> Dict[str, Any]:
        """Provides a real-time snapshot of queue depths and worker utilization."""
        try:
            pipe = self._client.pipeline()
            pipe.llen(KEY_QUEUE_CRITICAL)
            pipe.llen(KEY_QUEUE_HIGH)
            pipe.llen(KEY_QUEUE_MEDIUM)
            pipe.llen(KEY_QUEUE_LOW)
            pipe.zcard(KEY_QUEUE_DELAYED)
            pipe.hlen(KEY_RUNNING_JOBS)
            results = pipe.execute()

            critical_len, high_len, medium_len, low_len, delayed_len, running_len = results
            total_queued = critical_len + high_len + medium_len + low_len

            return {
                "redis_connected": True,
                "total_queued": total_queued,
                "queues": {
                    "critical": critical_len,
                    "high": high_len,
                    "medium": medium_len,
                    "low": low_len,
                    "delayed_backoff": delayed_len,
                },
                "running_tests_count": running_len,
                "active_workers_count": self.count_active_workers(),
                "active_schedulers_count": self.count_active_schedulers(),
            }
        except Exception as e:
            return {
                "redis_connected": False,
                "error": str(e),
                "total_queued": 0,
                "queues": {"critical": 0, "high": 0, "medium": 0, "low": 0, "delayed_backoff": 0},
                "running_tests_count": 0,
                "active_workers_count": 0,
                "active_schedulers_count": 0,
            }


# Global singleton instance
queue_manager = SchedulerQueue()
