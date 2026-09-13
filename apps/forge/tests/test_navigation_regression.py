import sys
from pathlib import Path

# Ensure src is in python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json
import pytest
from agents.nodes.analyzer import classify_navigation
from runner.python_runner import _parse_telemetry
from agents.cron_graph import route_editor, route_verifier
from agents.nodes.editor import apply_search_replace_blocks


def test_classify_navigation_normal_journey():
    """
    Regression Test: Normal journey navigation (/ -> /contact_us) must NOT be
    classified as an authentication redirect.
    """
    target = "https://www.automationexercise.com/"
    failure = "https://www.automationexercise.com/contact_us"
    test_scenario = {
        "title": "Contact Us Journey",
        "intent": "User navigates to Contact Us and fills the form",
        "steps": ["Click on 'Contact us' link", "Submit contact form"],
    }
    visible_errors = []

    nav = classify_navigation(target, failure, test_scenario, visible_errors)

    assert nav["auth_redirect"] is False, "Normal journey navigation must not be flagged as auth_redirect"
    assert nav["expected"] is True, "Navigation to /contact_us is expected journey progress"
    assert nav["failure_url"] == failure
    assert nav["target_url"] == target


def test_classify_navigation_genuine_auth_redirect():
    """
    Regression Test: Navigation from protected route to /login must be flagged as auth_redirect.
    """
    target = "https://www.automationexercise.com/account/orders"
    failure = "https://www.automationexercise.com/login"
    test_scenario = {
        "title": "View Orders",
        "intent": "User checks previous orders",
    }

    nav = classify_navigation(target, failure, test_scenario, [])

    assert nav["auth_redirect"] is True, "Redirect to /login from protected route must be detected"
    assert nav["expected"] is False, "Unexpected redirect to login is not expected journey progress"


def test_classify_navigation_with_query_param_redirect():
    """
    Regression Test: Destination containing returnUrl or redirect query parameter.
    """
    target = "https://app.example.com/dashboard"
    failure = "https://app.example.com/auth?returnUrl=/dashboard"

    nav = classify_navigation(target, failure, {}, [])

    assert nav["auth_redirect"] is True
    assert nav["expected"] is False


def test_telemetry_visible_errors_sanitization():
    """
    Regression Test: VISIBLE_ERRORS must not contain normal headings or success messages.
    """
    stdout = """
[FAILURE_URL] https://www.automationexercise.com/contact_us
[VISIBLE_ERRORS] ["CONTACT US", "GET IN TOUCH", "FEEDBACK FOR US", "You have been successfully subscribed!", "SUBSCRIPTION", "Please fill in this required field"]
[ERROR_ELEMENTS] ["[role='alert'], .alert-danger"]
"""
    stderr = ""

    telemetry = _parse_telemetry(stdout, stderr)

    assert telemetry["failure_url"] == "https://www.automationexercise.com/contact_us"
    errors = telemetry["visible_errors"]

    # Headings and success alerts must be filtered out
    assert "CONTACT US" not in errors
    assert "GET IN TOUCH" not in errors
    assert "FEEDBACK FOR US" not in errors
    assert "You have been successfully subscribed!" not in errors
    assert "SUBSCRIPTION" not in errors

    # Legitimate errors must be preserved
    assert "Please fill in this required field" in errors
    assert len(telemetry["error_elements"]) == 1


def test_editor_circuit_breaker_routes(monkeypatch):
    """
    Regression Test: route_editor must halt futile retries when edit_status is no_change or failed.
    """
    from db.repository import ForgeRepository
    monkeypatch.setattr(ForgeRepository, "record_test_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(ForgeRepository, "update_test_run_timestamps", lambda *args, **kwargs: None)

    # 1. When edit is applied -> route to runner
    state_applied = {"edit_status": "applied", "current_test": {"id": "test_1"}}
    assert route_editor(state_applied) == "runner"

    # 2. When edit produced no change -> circuit breaker triggers, halts to get_next_test
    state_no_change = {"edit_status": "no_change", "current_test": {"id": "test_1"}}
    assert route_editor(state_no_change) == "get_next_test"

    # 3. When edit failed -> halts to get_next_test
    state_failed = {"edit_status": "failed", "current_test": {"id": "test_1"}}
    assert route_editor(state_failed) == "get_next_test"


def test_verifier_terminates_cycle():
    """
    Regression Test: route_verifier must always terminate to get_next_test rather than re-looping.
    """
    assert route_verifier({"verifier_verdict": "CONFIRMED_APP_BUG"}) == "get_next_test"
    assert route_verifier({"verifier_verdict": "FAILED_AUTOMATION"}) == "get_next_test"
    assert route_verifier({"verifier_verdict": "INCONCLUSIVE"}) == "get_next_test"


def test_apply_search_replace_resilience():
    """
    Regression Test: apply_search_replace_blocks handles mixed whitespace and line endings gracefully.
    """
    original = "def test_flow():\n    page.goto('https://example.com')\n    expect(page.locator('h1')).to_have_text('Old')\n"
    response_with_trailing_spaces = """
<<<<<<< SEARCH
    expect(page.locator('h1')).to_have_text('Old')   
=======
    expect(page.locator('h2')).to_have_text('New')
>>>>>>>
"""
    result = apply_search_replace_blocks(original, response_with_trailing_spaces)
    assert result is not None
    assert "expect(page.locator('h2')).to_have_text('New')" in result


if __name__ == "__main__":
    print("Running test_classify_navigation_normal_journey...")
    test_classify_navigation_normal_journey()
    print("  PASSED")

    print("Running test_classify_navigation_genuine_auth_redirect...")
    test_classify_navigation_genuine_auth_redirect()
    print("  PASSED")

    print("Running test_classify_navigation_with_query_param_redirect...")
    test_classify_navigation_with_query_param_redirect()
    print("  PASSED")

    print("Running test_telemetry_visible_errors_sanitization...")
    test_telemetry_visible_errors_sanitization()
    print("  PASSED")

    print("Running test_editor_circuit_breaker_routes...")
    class MockMonkeypatch:
        def setattr(self, target, name, value):
            setattr(target, name, value)
    test_editor_circuit_breaker_routes(MockMonkeypatch())
    print("  PASSED")

    print("Running test_verifier_terminates_cycle...")
    test_verifier_terminates_cycle()
    print("  PASSED")

    print("Running test_apply_search_replace_resilience...")
    test_apply_search_replace_resilience()
    print("  PASSED")

    print("\nALL 7 REGRESSION TESTS PASSED SUCCESSFULLY!")
