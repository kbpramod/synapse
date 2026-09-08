import json
import os
import sys
from pathlib import Path

# Add project root and src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from agents.state import FailureContext, VerificationState
from agents.verification_graph import create_verification_graph, route_verification_verdict
from agents.nodes.verification.context_loader import load_failure_context_node
from agents.nodes.verification.verifier_evaluator import verifier_llm_node
from agents.nodes.verification.report_generator import report_node


def test_verification_graph_compilation():
    print("\n[TEST 1] Verifying Standalone Verification Graph Structure & Compilation...")
    graph = create_verification_graph()
    nodes = list(graph.nodes.keys())
    print(f"  Nodes present ({len(nodes)}): {nodes}")

    expected_nodes = [
        "load_failure_context",
        "discover",
        "build_smoke_verification_test",
        "run_smoke_test",
        "verifier_llm",
        "report",
    ]
    for n in expected_nodes:
        assert n in nodes, f"Missing expected node '{n}' in Verification Graph!"

    # Test conditional routing
    assert route_verification_verdict({"verdict": "CONFIRMED_APP_BUG"}) == "report"
    assert route_verification_verdict({"verdict": "NOT_CONFIRMED"}) == "__end__"
    print("  [PASS] All 6 Verification Graph nodes and routers verified successfully.")


def test_failure_context_loader():
    print("\n[TEST 2] Testing LOAD FAILURE CONTEXT Node...")
    simulated_state: VerificationState = {
        "failed_test_id": "test_checkout_flow",
        "failure_context": {
            "expected": "Order confirmation modal visible",
            "actual": "HTTP 500 Internal Server Error",
            "failed_step": "click 'Confirm Order'",
            "error": "Server error 500: Database lock timeout",
        }
    }
    loaded = load_failure_context_node(simulated_state)
    ctx = loaded.get("failure_context", {})
    assert ctx.get("failed_step") == "click 'Confirm Order'"
    assert len(ctx.get("network_errors", [])) > 0, "Should detect 500 in network errors"
    print("  [PASS] Failure context normalized and network error telemetry extracted.")


def test_verifier_evaluator_confirmed_bug():
    print("\n[TEST 3] Testing VERIFIER LLM -> CONFIRMED_APP_BUG...")
    simulated_state: VerificationState = {
        "target_url": "https://example.com",
        "failed_test_id": "test_auth_endpoint",
        "failure_context": {
            "expected": "User reaches /dashboard",
            "actual": "HTTP 500 Internal Server Error",
            "failed_step": "Submit login credentials",
            "error": "HTTP 500 Internal Server Error",
            "network_errors": ["POST /api/login returned 500 Internal Server Error"],
        },
        "smoke_result": {
            "exit_code": 1,
            "passed": False,
            "stderr": "HTTP 500 Internal Server Error: Failed to open user session",
            "stdout": "[VERIFY_SMOKE] Status: 500",
            "duration_s": 2.1,
        },
        "discovery_data": {
            "page": {"url": "https://example.com/login", "title": "Login Error"},
        }
    }

    eval_result = verifier_llm_node(simulated_state)
    print(f"  LLM Verdict: {eval_result['verdict']} (Confidence: {eval_result['confidence']})")
    print(f"  Reason: {eval_result['reason']}")
    print(f"  Evidence: {eval_result['evidence']}")

    assert eval_result["verdict"] == "CONFIRMED_APP_BUG", f"Expected CONFIRMED_APP_BUG, got {eval_result['verdict']}"
    assert eval_result["confidence"] > 0.75, "Confidence should be high"
    assert len(eval_result["evidence"]) > 0, "Evidence list should be non-empty"
    print("  [PASS] Verifier correctly confirmed genuine application defect.")


def test_verifier_evaluator_not_confirmed():
    print("\n[TEST 4] Testing VERIFIER LLM -> NOT_CONFIRMED (Locator Divergence)...")
    simulated_state: VerificationState = {
        "target_url": "https://example.com",
        "failed_test_id": "test_contact_button",
        "failure_context": {
            "expected": "Contact form appears",
            "actual": "Timeout 30000ms waiting for button 'Submit Now'",
            "failed_step": "click button 'Submit Now'",
            "error": "TimeoutError: locator resolved to hidden element",
            "console_errors": [],
            "network_errors": [],
        },
        "smoke_result": {
            "exit_code": 0,
            "passed": True,
            "stdout": "[VERIFY_SMOKE] Application loaded successfully. HTTP Status: 200",
            "stderr": "",
            "duration_s": 1.2,
        },
        "discovery_data": {
            "page": {"url": "https://example.com", "title": "Contact Us"},
            "elements": {"buttons": [{"text": "Send Message"}], "inputs": [], "links": []},
        }
    }

    eval_result = verifier_llm_node(simulated_state)
    print(f"  LLM Verdict: {eval_result['verdict']} (Confidence: {eval_result['confidence']})")
    print(f"  Reason: {eval_result['reason']}")
    print(f"  Evidence: {eval_result['evidence']}")

    assert eval_result["verdict"] == "NOT_CONFIRMED", f"Expected NOT_CONFIRMED, got {eval_result['verdict']}"
    assert route_verification_verdict(eval_result) == "__end__", "Router should terminate without false report"
    print("  [PASS] False alarm prevented: NOT_CONFIRMED verdict returned and routed to END.")


def test_report_generation():
    print("\n[TEST 5] Testing REPORT Node for Confirmed Bug...")
    simulated_state: VerificationState = {
        "target_url": "https://example.com",
        "failed_test_id": "test_payment_gateway",
        "verdict": "CONFIRMED_APP_BUG",
        "confidence": 0.96,
        "reason": "Payment API returns 500 on all card transactions",
        "evidence": [
            "POST /api/pay returned HTTP 500",
            "Gateway timeout exception recorded",
        ],
        "failure_context": {
            "expected": "Payment processed successfully",
            "actual": "HTTP 500 error",
        },
        "smoke_result": {
            "exit_code": 1,
            "passed": False,
            "duration_s": 3.0,
        },
    }

    res = report_node(simulated_state)
    report = res.get("report")
    assert report is not None, "Report was not generated!"
    assert report["verdict"] == "CONFIRMED_APP_BUG"
    assert report["test_id"] == "test_payment_gateway"
    assert len(report["evidence"]) == 2

    # Check disk artifact using configured storage directory
    from storage.local import get_website_storage_dir
    report_file = get_website_storage_dir("https://example.com") / "reports" / f"{report['incident_id']}.json"
    assert report_file.exists(), f"Report file not found on disk: {report_file}"
    print(f"  [PASS] Incident Report verified on disk: {report_file.name}")



def main():
    print("=" * 80)
    print("RUNNING STANDALONE VERIFICATION LANGGRAPH TEST SUITE")
    print("=" * 80)

    test_verification_graph_compilation()
    test_failure_context_loader()
    test_verifier_evaluator_confirmed_bug()
    test_verifier_evaluator_not_confirmed()
    test_report_generation()

    print("\n" + "=" * 80)
    print("SUCCESS: ALL STANDALONE VERIFICATION GRAPH TESTS PASSED!")
    print("=" * 80)


if __name__ == "__main__":
    main()
