import json
import os
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from storage.test_artifact_store import (
    save_test_artifacts,
    load_test_artifacts,
    materialize_test_script,
    update_test_manifest,
    classify_test_failure,
    ARTIFACT_NAMES,
)
from storage.supabase_storage import download_json, is_configured


def test_save_and_load_artifact_directory():
    print("\n[TEST 1] Testing save_test_artifacts with all 10 artifacts...")
    domain = "example.com"
    test_id = "test_smoke_artifact_dir"
    target_url = "https://example.com/contact"

    mock_action_spec = {
        "test_id": test_id,
        "target_url": target_url,
        "steps": [
            {"step_id": 1, "action_type": "goto", "value": target_url, "description": "Goto contact"},
            {"step_id": 2, "action_type": "click", "selector": "#submit", "value": None, "description": "Click submit"},
        ],
    }
    mock_action_code = "# Action script\npage.goto('https://example.com/contact')\npage.locator('#submit').click()\n"
    mock_action_result = {
        "passed": True,
        "exit_code": 0,
        "duration_s": 2.1,
        "final_url": "https://example.com/contact/thanks",
    }
    mock_disc_before = {
        "page": {"url": target_url, "title": "Contact Us"},
        "elements": {"buttons": [{"selector": "#submit", "text": "Submit"}]},
    }
    mock_disc_after = {
        "navigation": {"result_url": "https://example.com/contact/thanks"},
        "dom_delta": {"current_headings": ["Thank You!"]},
    }
    mock_exp_spec = {
        "test_id": test_id,
        "expectations": [
            {"code": "expect(page.locator('h1')).to_have_text('Thank You!')", "confidence": 0.95, "evidence": ["DOM heading"]}
        ],
    }
    mock_verification = {
        "verdict": "CORRECT",
        "confidence": 0.95,
        "evidence": ["Action succeeded", "Thank you page appeared"],
        "reason": "Contact submission verified",
    }
    mock_summary = {
        "test_id": test_id,
        "title": "Submit Contact Form",
        "category": "FLOW",
    }
    mock_test_code = '"""PROVENANCE: {}"""\ndef test_contact():\n    pass\n'

    saved_res = save_test_artifacts(
        domain=domain,
        test_id=test_id,
        action_spec=mock_action_spec,
        action_code=mock_action_code,
        action_result=mock_action_result,
        discovery_before=mock_disc_before,
        discovery_after=mock_disc_after,
        expectation_spec=mock_exp_spec,
        verification=mock_verification,
        summary=mock_summary,
        test_code=mock_test_code,
        target_url=target_url,
    )

    assert saved_res is not None, "save_test_artifacts returned None"
    manifest = saved_res["manifest"]
    assert manifest["test_id"] == test_id
    assert manifest["validated"]["action"] is True
    assert manifest["validated"]["expectations"] is True
    assert manifest["last_run"]["status"] == "passed"
    print("  [PASS] Local cache directory populated with 10 files.")

    # Check Supabase Storage if configured
    if is_configured():
        supabase_manifest = download_json(f"{domain}/tests/{test_id}/manifest.json")
        assert supabase_manifest is not None, "Manifest was not uploaded to Supabase Storage"
        assert supabase_manifest["test_id"] == test_id
        print("  [PASS] All 10 artifacts successfully synced to Supabase Storage.")
    else:
        print("  [SKIP] Supabase storage not configured; local cache tested.")

    # Test load_test_artifacts
    print("\n[TEST 2] Testing load_test_artifacts...")
    loaded = load_test_artifacts(domain, test_id)
    assert loaded["manifest"] is not None
    assert loaded["action_spec"]["test_id"] == test_id
    assert loaded["verification"]["verdict"] == "CORRECT"
    assert loaded["test_file_path"] is not None
    assert Path(loaded["test_file_path"]).exists()
    print("  [PASS] Loaded artifacts and verified test.py materialization.")

    # Test update_test_manifest
    print("\n[TEST 3] Testing update_test_manifest...")
    updated_manifest = update_test_manifest(
        domain=domain,
        test_id=test_id,
        updates={
            "version": 2,
            "last_run": {"status": "passed", "duration_s": 1.95},
        },
    )
    assert updated_manifest["version"] == 2
    assert updated_manifest["last_run"]["duration_s"] == 1.95
    print("  [PASS] Manifest updated and synced.")


def test_classify_failure():
    print("\n[TEST 4] Testing classify_test_failure heuristics...")

    # Case A: Expectation Defect (AssertionError)
    res_exp = {
        "exit_code": 1,
        "stderr": "AssertionError: Locator expected to have text 'Thank You' but got 'Error'",
    }
    class_exp = classify_test_failure("test_1", res_exp)
    assert class_exp["failure_stage"] == "expectation"
    assert class_exp["isolate_to"] == "heal_expectation"
    print("  [PASS] Correctly classified as Expectation Defect -> isolate_to: heal_expectation")

    # Case B: Action Defect (TimeoutError on locator)
    res_act = {
        "exit_code": 1,
        "stderr": "playwright._impl._errors.TimeoutError: Timeout 10000ms exceeded waiting for locator('#submit')",
    }
    class_act = classify_test_failure("test_2", res_act)
    assert class_act["failure_stage"] == "action"
    assert class_act["isolate_to"] == "heal_action"
    print("  [PASS] Correctly classified as Action Defect -> isolate_to: heal_action")

    # Case C: Application Bug (HTTP 500)
    res_app = {
        "exit_code": 1,
        "stderr": "Request failed with status: 500 Internal Server Error",
    }
    class_app = classify_test_failure("test_3", res_app)
    assert class_app["failure_stage"] == "application"
    assert class_app["isolate_to"] == "app_bug"
    print("  [PASS] Correctly classified as Application Bug -> isolate_to: app_bug")


if __name__ == "__main__":
    print("=" * 65)
    print("RUNNING TEST ARTIFACT STORE & SUPABASE VERIFICATION")
    print("=" * 65)
    test_save_and_load_artifact_directory()
    test_classify_failure()
    print("\n" + "=" * 65)
    print("ALL TEST ARTIFACT STORE TESTS PASSED SUCCESSFULLY!")
    print("=" * 65)
