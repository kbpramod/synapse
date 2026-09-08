import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.cron_runner import run_cron_cycle, start_cron_scheduler_daemon
from db.migrations import init_db
from db.repository import ForgeRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("forge.cron_cli")


def print_banner(mode: str, domain: str = None, interval: int = 60, headed: bool = False):
    print("\n" + "=" * 80)
    print("  FORGE CONTINUOUS CRON TEST SCHEDULER (Autonomous QA Engine)")
    print(f"  Execution Mode : {mode.upper()}")
    print(f"  Target Scope   : {domain or 'ALL REGISTERED WEBSITES'}")
    print(f"  Headless       : {not headed}")
    if mode == "daemon":
        print(f"  Poll Interval  : {interval} seconds")
    print("=" * 80 + "\n")


def print_schedule_table(domain: str = None):
    """Prints the current schedule status of all tests in PostgreSQL."""
    due_tests = ForgeRepository.get_due_tests(domain=domain)
    active_tests = ForgeRepository.get_active_tests(domain=domain)

    print("-" * 80)
    print(f"  SCHEDULE STATUS ({len(active_tests)} Active Tests | {len(due_tests)} Due Now)")
    print("-" * 80)
    for t in active_tests:
        test_id = t["test_id"]
        interval = t.get("cron_interval_hours", 24)
        last_run = t.get("last_run_at") or "Never"
        next_run = t.get("next_run_at") or "Immediately"
        is_due = any(d["test_id"] == test_id for d in due_tests)
        flag = "[DUE NOW]" if is_due else "[SCHEDULED]"
        print(f"  {flag:12} {test_id:<28} Every {interval}h | Last: {str(last_run)[:19]} | Next: {str(next_run)[:19]}")
    print("-" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Forge Continuous Autonomous QA Cron Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute a single cycle for all currently due tests and exit immediately.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously in background polling mode.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Inspect current test schedule status and upcoming run times without executing.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Poll interval in seconds when running in daemon mode (default: 60s).",
    )
    parser.add_argument(
        "--domain",
        type=str,
        default=None,
        help="Filter execution to tests belonging to a specific website domain.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run Playwright browser tests in headed mode (visible browser window).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of due tests to process per cycle (default: 50).",
    )

    args = parser.parse_args()

    # Ensure PostgreSQL migrations are initialized
    init_db()

    if args.status:
        print_banner("STATUS", domain=args.domain)
        print_schedule_table(domain=args.domain)
        return

    if args.daemon:
        print_banner("DAEMON", domain=args.domain, interval=args.interval, headed=args.headed)
        print_schedule_table(domain=args.domain)
        try:
            asyncio.run(
                start_cron_scheduler_daemon(
                    poll_interval_seconds=args.interval,
                    domain=args.domain,
                    headless=not args.headed,
                )
            )
        except KeyboardInterrupt:
            print("\n[CRON CLI] Scheduler stopped by user.")
        return

    # Default to single-cycle execution (--once or default invocation)
    print_banner("SINGLE CYCLE (--once)", domain=args.domain, headed=args.headed)
    print_schedule_table(domain=args.domain)

    result = run_cron_cycle(
        domain=args.domain,
        headless=not args.headed,
        limit=args.limit,
    )

    print("\n" + "=" * 80)
    print("  CRON CYCLE RESULTS")
    print("=" * 80)
    print(f"  Status          : {result.get('status')}")
    print(f"  Due Tests Found : {result.get('due_count', 0)}")
    print(f"  Executed        : {result.get('executed_count', 0)}")
    print(f"  Passed          : {result.get('passed_count', 0)}")
    print(f"  Bugs Confirmed  : {result.get('bug_count', 0)}")
    print(f"  Failed (Drift)  : {result.get('failed_count', 0)}")
    print(f"  Duration        : {result.get('duration_seconds', 0.0)}s")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
