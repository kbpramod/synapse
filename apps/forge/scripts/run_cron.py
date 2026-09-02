import os
import sys
import uuid
import logging
import argparse
from pathlib import Path
from datetime import datetime

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from config import is_headless
from db.migrations import init_db
from db.repository import ForgeRepository
from runner.playwright_runner import run_test_script

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("forge.cron")


def run_hourly_regression(domain: str = None, headed: bool = False, auto_heal: bool = True):
    print("\n" + "=" * 80)
    print("  FORGE HOURLY REGRESSION RUNNER (Continuous Autonomous QA)")
    print(f"  Target Scope: {domain or 'ALL ACTIVE APPLICATIONS'}")
    print(f"  Headless: {not headed} | Auto-Heal: {auto_heal}")
    print("=" * 80 + "\n")

    # Ensure forge schema exists in Neon Postgres
    init_db()

    # Query active tests from Neon PostgreSQL
    active_tests = ForgeRepository.get_active_tests(domain=domain)
    if not active_tests:
        print("No active tests found in database. Please run test planning stage first.")
        return 0

    print(f"Found {len(active_tests)} active test(s) to execute:\n")
    for t in active_tests:
        print(f"  - [{t['priority'].upper()}] {t['test_id']} ({t['domain']}): {t['title']}")
    print("\n" + "-" * 80)

    run_batch_id = f"cron_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    total = len(active_tests)
    passed_count = 0
    failed_count = 0
    healed_count = 0

    for idx, test in enumerate(active_tests, 1):
        test_id = test["test_id"]
        script_path = test.get("script_path")
        print(f"\n[{idx}/{total}] RUNNING: {test_id} -> {test['title']}")

        if not script_path or not Path(script_path).exists():
            print(f"  [ERROR] Script file not found: {script_path}")
            ForgeRepository.record_test_run(
                run_id=run_batch_id,
                test_id=test_id,
                exit_code=1,
                status="FAILED",
                duration_s=0.0,
                error_summary="Test script file missing",
            )
            failed_count += 1
            continue

        exec_res = run_test_script(script_path, headed=headed)
        passed = exec_res.get("passed", False)
        exit_code = exec_res.get("exit_code", 1)
        duration = exec_res.get("duration_s", 0.0)
        error = exec_res.get("error_summary")

        if passed:
            print(f"  [PASSED] in {duration}s")
            ForgeRepository.record_test_run(
                run_id=run_batch_id,
                test_id=test_id,
                exit_code=0,
                status="PASSED",
                duration_s=duration,
                stdout=exec_res.get("stdout", ""),
                stderr=exec_res.get("stderr", ""),
            )
            passed_count += 1
        else:
            print(f"  [FAILED] Exit Code {exit_code}: {error}")
            print(f"  Screenshots captured: {len(exec_res.get('screenshot_paths', []))}")

            # Record initial failure
            ForgeRepository.record_test_run(
                run_id=run_batch_id,
                test_id=test_id,
                exit_code=exit_code,
                status="FAILED",
                duration_s=duration,
                error_summary=error,
                stdout=exec_res.get("stdout", ""),
                stderr=exec_res.get("stderr", ""),
                screenshot_paths=exec_res.get("screenshot_paths", []),
            )
            failed_count += 1

    print("\n" + "=" * 80)
    print("  HOURLY REGRESSION SUMMARY REPORT")
    print("=" * 80)
    print(f"  Total Executed : {total}")
    print(f"  Passed         : {passed_count}")
    print(f"  Failed         : {failed_count}")
    print(f"  Healed         : {healed_count}")
    print(f"  Health Ratio   : {round((passed_count / total) * 100, 1) if total else 0}%\n")

    summary = ForgeRepository.get_regression_summary(hours=24)
    print(f"  Last 24h Telemetry across all runs: Total={summary['total_runs']}, Passed={summary['passed_runs']}, Failed={summary['failed_runs']}\n")

    return 0 if failed_count == 0 else 1


def main():
    parser = argparse.ArgumentParser(description="Forge Autonomous Hourly Regression Runner")
    parser.add_argument("--domain", type=str, help="Specific domain to test (default: all)")
    parser.add_argument("--headed", action="store_true", help="Launch browser visibly for debugging")
    parser.add_argument("--no-heal", action="store_true", help="Disable self-healing on failure")
    args = parser.parse_args()

    is_headed = args.headed or (not is_headless())
    exit_code = run_hourly_regression(
        domain=args.domain,
        headed=is_headed,
        auto_heal=not args.no_heal,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
