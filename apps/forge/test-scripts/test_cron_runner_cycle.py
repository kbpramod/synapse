import sys
from datetime import datetime, timezone
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.cron_runner import run_cron_cycle
from db.connection import get_connection
from db.repository import ForgeRepository
from storage.local import get_website_storage_dir


def test_cron_cycle_database_driven():
    print("=" * 80)
    print("TESTING DATABASE-DRIVEN CONTINUOUS CRON RUNNER & SCHEDULE ADVANCEMENT")
    print("=" * 80)

    test_domain = "cron-e2e-demo.com"
    test_url = f"https://{test_domain}"

    # 1. Create website
    website = ForgeRepository.create_website(test_url, is_active=True)
    website_id = website["id"]
    print(f"\n[STEP 1] Created test website ID={website_id} for {test_url}")

    # 2. Write a minimal passing test script to disk
    site_storage = get_website_storage_dir(test_url)
    tests_dir = site_storage / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_id = "smoke_continuous_check"
    script_file = tests_dir / f"{test_id}.py"
    script_file.write_text("""import sys
def main():
    print("[AUTONOMOUS CRON TEST] Executing scheduled check...")
    print("[AUTONOMOUS CRON TEST] Check passed successfully.")

if __name__ == "__main__":
    main()
""", encoding="utf-8")

    # 3. Save test with cron_interval_hours = 8, last_run_at = NULL (Due immediately!)
    ForgeRepository.save_test(
        test_id=test_id,
        domain=test_domain,
        page_url=test_url,
        title="Continuous Scheduled Smoke Check",
        description="Verifies the autonomous cron runner executes and advances timestamps",
        category="smoke",
        priority="high",
        steps=["Execute check"],
        expected_outcome="Success",
        script_path=str(script_file),
        test_code=script_file.read_text(encoding="utf-8"),
        website_id=website_id,
        cron_interval_hours=8,
    )
    print(f"\n[STEP 2] Saved test '{test_id}' with interval=8 hours and last_run_at=NULL")

    # 4. Check due tests in database
    due_tests = ForgeRepository.get_due_tests(domain=test_domain)
    assert len(due_tests) == 1, f"Expected 1 due test, found {len(due_tests)}"
    assert due_tests[0]["test_id"] == test_id
    print(f"\n[STEP 3] Verified test is DUE: {due_tests[0]['test_id']} (last_run={due_tests[0].get('last_run_at')})")

    # 5. Run a cron cycle
    print("\n[STEP 4] Executing run_cron_cycle()...")
    cycle_result = run_cron_cycle(domain=test_domain, headless=True)
    print(f"  Cycle status   : {cycle_result['status']}")
    print(f"  Executed count : {cycle_result['executed_count']}")
    print(f"  Passed count   : {cycle_result['passed_count']}")
    print(f"  Duration       : {cycle_result['duration_seconds']}s")

    assert cycle_result["status"] == "completed"
    assert cycle_result["executed_count"] == 1
    assert cycle_result["passed_count"] == 1

    # 6. Verify timestamps are advanced in PostgreSQL
    print("\n[STEP 5] Verifying timestamp advancement in PostgreSQL...")
    updated_test = ForgeRepository.get_test_by_id(test_id)
    assert updated_test is not None
    assert updated_test["last_run_at"] is not None, "last_run_at should be set after execution!"
    assert updated_test["next_run_at"] is not None, "next_run_at should be computed after execution!"

    last_run = updated_test["last_run_at"]
    next_run = updated_test["next_run_at"]
    diff_hours = (next_run - last_run).total_seconds() / 3600.0
    print(f"  - Last Run At : {last_run}")
    print(f"  - Next Run At : {next_run}")
    print(f"  - Advance Gap : {diff_hours:.1f} hours (Expected: ~8.0h)")
    assert 7.9 <= diff_hours <= 8.1, f"Expected 8 hours advancement, got {diff_hours}"

    # 7. Check that get_due_tests now returns 0 (test is no longer due!)
    post_due = ForgeRepository.get_due_tests(domain=test_domain)
    assert len(post_due) == 0, f"Expected 0 due tests after execution, found {len(post_due)}"
    print("  [PASS] Test is no longer due. It is scheduled for its next cycle 8 hours from now.")

    # 8. Clean up
    ForgeRepository.delete_website(website_id)
    print(f"\n[STEP 6] Cleaned up test website ID={website_id}")

    print("\n" + "=" * 80)
    print("SUCCESS: CONTINUOUS CRON RUNNER DATABASE DRIVER VERIFIED!")
    print("=" * 80)


if __name__ == "__main__":
    test_cron_cycle_database_driven()
