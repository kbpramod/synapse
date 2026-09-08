import os
import sys
import json
import shutil
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from storage.local import get_planner_storage_dir, save_hypotheses, get_website_storage_dir
from agents.nodes.planner import planner_node
from agents.nodes.builder import builder_node
from agents.state import ForgeState


def test_save_hypotheses_storage():
    print("[TEST 1] Testing save_hypotheses storage structure...")
    test_url = "https://example-test-app.com"
    sample_hypotheses = [
        {
            "id": "smoke_navigation_header",
            "type": "SMOKE",
            "intent": "Verify landing page loads and primary navigation is responsive",
            "preconditions": ["Homepage is loaded in browser"],
            "steps": [
                "Navigate to target URL",
                "Interact with primary navigation menu",
                "Verify navigation destination responds without crash"
            ],
            "expected": [
                "Target view transitions cleanly without runtime console errors"
            ],
            "evidence": [
                "element:nav_bar",
                "element:nav_link",
                "navigation:/about"
            ],
            "supported_viewports": ["desktop", "tablet", "mobile"],
            "priority": "high"
        },
        {
            "id": "flow_login",
            "type": "FLOW",
            "intent": "A user can log into the application",
            "preconditions": [
                "valid credentials are available"
            ],
            "steps": [
                "enter email",
                "enter password",
                "submit login"
            ],
            "expected": [
                "user reaches authenticated application state"
            ],
            "evidence": [
                "element:email",
                "element:password",
                "element:login_button",
                "navigation:/dashboard"
            ],
            "supported_viewports": ["desktop", "tablet", "mobile"],
            "priority": "high"
        }
    ]

    saved_path = save_hypotheses(test_url, sample_hypotheses)
    assert saved_path.exists(), f"Main hypotheses file does not exist: {saved_path}"

    planner_dir = get_planner_storage_dir(test_url)
    smoke_file = planner_dir / "smoke" / "smoke_navigation_header.json"
    flow_file = planner_dir / "flows" / "flow_login.json"
    summary_file = planner_dir / "summary.json"

    assert smoke_file.exists(), f"Smoke file not found at: {smoke_file}"
    assert flow_file.exists(), f"Flow file not found at: {flow_file}"
    assert summary_file.exists(), f"Summary file not found at: {summary_file}"

    summary = json.loads(summary_file.read_text(encoding="utf-8"))
    assert summary["total_hypotheses"] == 2
    assert summary["smoke_count"] == 1
    assert summary["flow_count"] == 1
    assert "smoke_navigation_header" in summary["smoke_ids"]
    assert "flow_login" in summary["flow_ids"]
    print("  [PASS] save_hypotheses verified: smoke/flows folders and summary.json created correctly.")


def test_planner_and_builder_integration():
    print("[TEST 2] Testing planner_node and builder_node with SMOKE and FLOW hypotheses...")
    test_url = "https://example-test-app.com"
    mock_state: ForgeState = {
        "target_url": test_url,
        "config": {"headless": True, "language": "python"},
        "discovery_data": {
            "page": {"url": test_url, "title": "Example Test App"},
            "elements": {
                "buttons": [
                    {"text": "Login", "selector": "#login-btn", "visible": True, "visible_viewports": ["desktop", "mobile"]},
                    {"text": "Contact Us", "selector": "#contact-btn", "visible": True, "visible_viewports": ["desktop"]}
                ],
                "links": [
                    {"text": "About", "href": "/about", "visible": True, "visible_viewports": ["desktop", "mobile"]}
                ],
                "inputs": [
                    {"name": "email", "placeholder": "Email", "type": "email"},
                    {"name": "password", "placeholder": "Password", "type": "password"}
                ],
                "viewports_summary": {"desktop_only_count": 1, "mobile_only_count": 0, "all_viewports_count": 2}
            },
            "text": {"headings": ["Welcome to Example Test App"], "body_text_preview": "Test App Content"}
        },
        "page_understanding": {
            "page_type": "landing_page",
            "purpose": "Showcase platform and accept logins",
            "capabilities": ["authenticate user", "contact company"],
            "state_transitions": ["anonymous -> authenticated", "anonymous -> contact modal open"],
            "primary_actions": ["click login", "click contact us"]
        }
    }

    # Run planner_node
    planner_result = planner_node(mock_state)
    test_plan = planner_result["test_plan"]
    assert len(test_plan) >= 2, f"Expected at least 2 test hypotheses, got {len(test_plan)}"

    # Check schema compliance on all generated hypotheses
    has_smoke = False
    has_flow = False
    for item in test_plan:
        assert "id" in item, "Missing 'id'"
        assert "type" in item, "Missing 'type'"
        assert item["type"] in ("SMOKE", "FLOW"), f"Invalid type: {item['type']}"
        assert "intent" in item, "Missing 'intent'"
        assert "preconditions" in item and isinstance(item["preconditions"], list), "Invalid 'preconditions'"
        assert "steps" in item and isinstance(item["steps"], list), "Invalid 'steps'"
        assert "expected" in item and isinstance(item["expected"], list), "Invalid 'expected'"
        assert "evidence" in item and isinstance(item["evidence"], list), "Invalid 'evidence'"
        if item["type"] == "SMOKE":
            has_smoke = True
        if item["type"] == "FLOW":
            has_flow = True

    assert has_smoke, "Test plan did not include any SMOKE hypothesis"
    assert has_flow, "Test plan did not include any FLOW hypothesis"
    print(f"  [PASS] Planner node produced {len(test_plan)} valid hypotheses (SMOKE and FLOW verified).")

    # Run builder_node for the first test
    mock_state["test_plan"] = test_plan
    mock_state["current_test_idx"] = 0
    mock_state["current_test"] = test_plan[0]

    builder_result = builder_node(mock_state)
    assert builder_result.get("test_code"), "Builder did not return test_code"
    assert builder_result.get("test_file_path"), "Builder did not return test_file_path"
    test_file = Path(builder_result["test_file_path"])
    assert test_file.exists(), f"Generated test file does not exist: {test_file}"
    print(f"  [PASS] Builder node generated valid script: {test_file.name}")


if __name__ == "__main__":
    print("=" * 60)
    print("RUNNING PLANNER HYPOTHESES & STORAGE VERIFICATION")
    print("=" * 60)
    test_save_hypotheses_storage()
    test_planner_and_builder_integration()
    print("=" * 60)
    print("ALL PLANNER HYPOTHESES TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)
