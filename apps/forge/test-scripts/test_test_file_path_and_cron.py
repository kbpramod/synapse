import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from sqlalchemy.orm import Session
from db.connection import get_engine, get_connection
from db.repository import ForgeRepository
from models import Test, Website
from schemas import TestResponse, TestScheduleUpdate


def test_test_file_path_and_cron():
    print("=" * 70)
    print("VERIFYING TEST FILE PATH STORAGE & CRON TIMINGS IN POSTGRESQL")
    print("=" * 70)

    # 1. Create a parent website to test foreign key linkage
    test_url = "https://cron-test-sample.com"
    website = ForgeRepository.create_website(test_url, is_active=True)
    website_id = website["id"]
    print(f"\n[STEP 1] Created parent website ID={website_id} for {test_url}")

    # 2. Save a test with script_path and cron_interval_hours = 6 (every 6 hours)
    test_id = "smoke_auth_checkpoint"
    script_path = "D:\\Pramod\\Tzylo\\forge\\cron-test-sample.com\\tests\\smoke\\smoke_auth_checkpoint.py"
    test_code = "print('Smoke auth check')"

    ForgeRepository.save_test(
        test_id=test_id,
        domain="cron-test-sample.com",
        page_url=test_url,
        title="Smoke Test: Authentication Checkpoint",
        description="Verifies authentication checkpoint loads cleanly",
        category="smoke",
        priority="high",
        steps=["Open target URL", "Check login prompt is rendered"],
        expected_outcome="Login prompt visible without errors",
        script_path=script_path,
        test_code=test_code,
        language="python",
        website_id=website_id,
        cron_interval_hours=6,  # Run every 6 hours
    )
    print(f"\n[STEP 2] Saved test '{test_id}' with cron_interval_hours=6")

    # 3. Fetch test via repository helper
    fetched = ForgeRepository.get_test_by_id(test_id)
    assert fetched is not None, f"Test '{test_id}' was not found in database!"
    print(f"\n[STEP 3] Retrieved test record:")
    print(f"  - Test ID              : {fetched['test_id']}")
    print(f"  - Website ID Reference : {fetched['website_id']}")
    print(f"  - Script File Path     : {fetched['script_path']}")
    print(f"  - Category             : {fetched['category']}")
    print(f"  - Cron Interval Hours  : {fetched['cron_interval_hours']} hours")
    print(f"  - Cron Expression      : {fetched['cron_expression']}")

    assert fetched["script_path"] == script_path, "Script path mismatch!"
    assert fetched["cron_interval_hours"] == 6, "Cron interval hours mismatch!"
    assert fetched["cron_expression"] == "0 */6 * * *", "Cron expression mismatch!"
    assert fetched["website_id"] == website_id, "Website ID mismatch!"
    print("  [PASS] Test file path and 6-hour cron timings verified in database.")

    # 4. Update cron schedule to run every 12 hours
    print("\n[STEP 4] Updating cron timing to every 12 hours...")
    updated = ForgeRepository.update_test_schedule(test_id, cron_interval_hours=12)
    assert updated is True, "Failed to update test schedule!"

    refetched = ForgeRepository.get_test_by_id(test_id)
    assert refetched["cron_interval_hours"] == 12, "Updated cron interval hours mismatch!"
    assert refetched["cron_expression"] == "0 */12 * * *", "Updated cron expression mismatch!"
    print(f"  [PASS] Schedule updated successfully:")
    print(f"    New Interval: {refetched['cron_interval_hours']} hours | Expression: {refetched['cron_expression']}")

    # 5. Query via SQLAlchemy ORM Session
    print("\n[STEP 5] Querying via SQLAlchemy ORM model...")
    engine = get_engine()
    with Session(engine) as session:
        orm_test = session.query(Test).filter_by(test_id=test_id).first()
        assert orm_test is not None, "ORM query returned None!"
        assert orm_test.script_path == script_path
        assert orm_test.cron_interval_hours == 12
        test_dict = orm_test.to_dict()
        print(f"  [PASS] ORM to_dict() verified:")
        print(f"    {test_dict}")

    # 6. Validate with Pydantic Schema
    print("\n[STEP 6] Validating with Pydantic Schema...")
    pydantic_test = TestResponse.model_validate(refetched)
    assert pydantic_test.cron_interval_hours == 12
    assert pydantic_test.script_path == script_path
    print(f"  [PASS] Pydantic validation successful: {pydantic_test.title} [every {pydantic_test.cron_interval_hours}h]")

    # 7. Clean up
    ForgeRepository.delete_website(website_id)
    print(f"\n[STEP 7] Cascade clean up: Deleted website ID={website_id}")
    cleaned_test = ForgeRepository.get_test_by_id(test_id)
    assert cleaned_test is None, "Test was not cascade deleted with parent website!"
    print("  [PASS] Cascade deletion verified: Deleting website cleanly deleted the associated test.")

    print("\n" + "=" * 70)
    print("SUCCESS: ALL TEST FILE PATH & CRON TIMING CHECKS PASSED!")
    print("=" * 70)


if __name__ == "__main__":
    test_test_file_path_and_cron()
