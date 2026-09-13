"""
De-bunching and scheduling window distribution logic for Forge tests.

Prevents execution spikes at exact cron boundaries by spreading target execution
timestamps across the recurrence window.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
import zoneinfo
try:
    from croniter import croniter
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    venv_site = Path(__file__).resolve().parents[2] / ".venv" / "Lib" / "site-packages"
    if venv_site.exists() and str(venv_site) not in sys.path:
        sys.path.insert(0, str(venv_site))
    from croniter import croniter


def get_cron_window_seconds(
    cron_expression: str,
    base_time: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> float:
    """
    Calculates the duration in seconds of one recurrence cycle/window defined by
    the cron expression.
    Examples:
      - '0 * * * *' (hourly) -> 3600.0s
      - '0 0 * * *' (daily) -> 86400.0s
      - '*/15 * * * *' (15 min) -> 900.0s
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    if base_time is None:
        base = datetime.now(tz)
    else:
        if base_time.tzinfo is None:
            base = base_time.replace(tzinfo=timezone.utc).astimezone(tz)
        else:
            base = base_time.astimezone(tz)

    try:
        iter1 = croniter(cron_expression, base)
        t1 = iter1.get_next(datetime)
        iter2 = croniter(cron_expression, t1)
        t2 = iter2.get_next(datetime)
        window = (t2 - t1).total_seconds()
        return max(window, 60.0)
    except Exception:
        # Fallback to 24h window if parsing fails
        return 86400.0


def compute_next_run(
    cron_expression: str,
    base_time: Optional[datetime] = None,
    schedule_offset_seconds: int = 0,
    tz_name: str = "UTC",
) -> datetime:
    """
    Calculates the next target execution timestamp for a test.
    Honors the test's `schedule_offset_seconds` so that tests distributed across
    a window remain staggered on future runs, rather than collapsing back onto
    the exact cron boundary.
    Guarantees the returned timestamp is in the future (> now).
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc

    now = datetime.now(timezone.utc).astimezone(tz)
    window_s = get_cron_window_seconds(cron_expression, now, tz_name)
    clamped_offset = int(schedule_offset_seconds % window_s) if window_s > 0 else 0

    try:
        # 1. Start from the most recent boundary prior to now
        ci_prev = croniter(cron_expression, now)
        prev_boundary = ci_prev.get_prev(datetime)
        candidate = prev_boundary + timedelta(seconds=clamped_offset)

        # If that target slot in the current window has already passed,
        # advance to next boundary + offset
        if candidate <= now:
            ci_next = croniter(cron_expression, now)
            next_boundary = ci_next.get_next(datetime)
            candidate = next_boundary + timedelta(seconds=clamped_offset)
    except Exception:
        # Fallback: simple 24 hour shift
        candidate = now + timedelta(hours=24)

    return candidate.astimezone(timezone.utc)


def distribute_cohort(
    tests: List[Dict[str, Any]],
    base_time: Optional[datetime] = None,
    tz_name: str = "UTC",
) -> List[Dict[str, Any]]:
    """
    Distributes N tests sharing a frequency window across that window.
    Target spacing: S = Window / N.
    Offset for test index i: round(i * S) seconds.
    Example: 100 hourly tests -> target spacing roughly 36 seconds across 3600s.
    Assigns:
      - `schedule_offset_seconds`: the persistent offset in the window
      - `next_run_at`: target UTC datetime for next execution
    """
    if not tests:
        return []

    cron_expr = tests[0].get("cron_expression") or "0 0 * * *"
    window_s = get_cron_window_seconds(cron_expr, base_time, tz_name)
    n = len(tests)
    spacing = window_s / float(n) if n > 0 else window_s

    distributed = []
    for i, test in enumerate(tests):
        offset = int(round(i * spacing))
        # Ensure offset strictly stays within the window
        offset = min(offset, max(0, int(window_s - 1)))
        next_run = compute_next_run(
            cron_expression=test.get("cron_expression") or cron_expr,
            base_time=base_time,
            schedule_offset_seconds=offset,
            tz_name=test.get("timezone") or tz_name,
        )

        item = dict(test)
        item["schedule_offset_seconds"] = offset
        item["next_run_at"] = next_run
        distributed.append(item)

    return distributed
