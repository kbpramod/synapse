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

from agents.cron_graph import (
    create_cron_graph,
    route_analyzer,
    route_get_next_test,
    route_verifier,
    route_verifier_llm,
)
from agents.state import ForgeState, UserJourney
from storage.local import get_website_storage_dir


def test_compilation_and_routing():
    print("\n[TEST 1] Verifying Cron Graph Compilation & Routing Rules...")
    graph = create_cron_graph()
    nodes = list(graph.nodes.keys())
    print(f"  Nodes present ({len(nodes)}): {nodes}")

    expected_nodes = [
        "get_next_test",
        "runner",
        "observer",
        "analyzer",
        "healer",
        "discover_for_heal",
        "editor",
        "verifier",
    ]
    for n in expected_nodes:
        assert n in nodes, f"Missing expected node '{n}' in Cron Graph!"
    print(f"  [PASS] All {len(expected_nodes)} Cron Graph core orchestration nodes verified.")

    # 1. Test route_get_next_test
    assert route_get_next_test({"current_test": {"id": "t1"}}) == "runner"
    assert route_get_next_test({"current_test": None}) == "__end__"
    print("  [PASS] route_get_next_test: correctly routes to 'runner' or '__end__'.")

    # 2. Test route_analyzer
    assert route_analyzer({"analysis": {"verdict": "PASS"}}) == "get_next_test"
    assert route_analyzer({"analysis": {"verdict": "NEED_HEAL"}}) == "healer"
    assert route_analyzer({"analysis": {"verdict": "SUSPECTED_APP_FAILURE"}}) == "verifier"
    print("  [PASS] route_analyzer: correctly routes to 'get_next_test', 'healer', or 'verifier'.")

    # 3. Test route_verifier
    assert route_verifier({"verifier_verdict": "CONFIRMED_APP_BUG"}) == "get_next_test"
    assert route_verifier({"verifier_verdict": "EXHAUSTED_HEALS"}) == "get_next_test"
    assert route_verifier({"verifier_verdict": "NOT_CONFIRMED"}) == "healer"
    print("  [PASS] route_verifier: correctly routes to 'get_next_test' (for confirmed bug / exhausted heals) or 'healer'.")


def test_pass_pipeline():
    print("\n[TEST 2] Testing PASS Pipeline Execution...")
    graph = create_cron_graph()

    # Create a simple valid passing test script
    site_storage = get_website_storage_dir("https://example.com")
    tests_dir = site_storage / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_file = tests_dir / "test_pass_sample.py"
    test_file.write_text("""import os
def test_pass():
    print("[TEST PASSED] Quick pass")

if __name__ == "__main__":
    test_pass()
""", encoding="utf-8")

    test_scenario: UserJourney = {
        "id": "test_pass_sample",
        "title": "Pass Sample Test",
        "script_path": str(test_file),
        "page_url": "https://example.com",
        "cron_interval_hours": 6,
    }

    initial_state: ForgeState = {
        "target_url": "https://example.com",
        "target_domain": "example.com",
        "test_queue": [test_scenario],
        "config": {"headless": True},
        "suite_summary": [],
        "incident_reports": [],
    }

    final_state = graph.invoke(initial_state)
    assert final_state.get("current_test") is None, "Current test should be None at queue completion"
    summary = final_state.get("suite_summary", [])
    assert len(summary) > 0, "Suite summary should have recorded the test"
    assert summary[0]["status"] == "PASSED", f"Expected PASSED, got {summary[0]['status']}"
    print(f"  [PASS] PASS path succeeded: Test executed -> Analyzed -> Passed -> Database Updated -> Queue Finished -> END.")


def test_verifier_delegation_flow():
    print("\n[TEST 3] Testing Cron Graph Verifier Delegation...")
    from agents.nodes.verifier import verifier_node

    simulated_state: ForgeState = {
        "target_url": "https://example.com",
        "target_domain": "example.com",
        "current_test": {
            "id": "test_cart_500",
            "title": "Cart Checkout 500 Failure",
            "expected_outcome": "Order processed",
            "cron_interval_hours": 12,
        },
        "execution_result": {
            "exit_code": 1,
            "passed": False,
            "error_summary": "HTTP 500 Internal Server Error: Payment failure",
            "stderr": "POST /api/pay 500",
            "stdout": "",
            "duration_s": 1.5,
        },
        "analysis": {
            "verdict": "SUSPECTED_APP_FAILURE",
            "reason": "Backend returned persistent 500 error",
            "failure_type": "server_error",
        },
        "incident_reports": [],
        "suite_summary": [],
    }

    # Verify delegation through verifier_node
    node_res = verifier_node(simulated_state)
    verdict = node_res.get("verifier_verdict")
    print(f"  Delegated Verification Verdict: {verdict}")
    print(f"  Verifier Reason: {node_res.get('verifier_reason')}")

    assert verdict in ("CONFIRMED_APP_BUG", "NOT_CONFIRMED"), f"Unexpected verdict: {verdict}"
    if verdict == "CONFIRMED_APP_BUG":
        assert len(node_res.get("incident_reports", [])) > 0, "Incident report should be captured in state"
        next_route = route_verifier(node_res)
        assert next_route == "get_next_test", "Confirmed bug should route to get_next_test"
        print("  [PASS] Confirmed bug properly indexed and routed to advance queue.")
    else:
        next_route = route_verifier(node_res)
        assert next_route == "healer", "Not confirmed should divert to healer"
        print("  [PASS] Unconfirmed divergence diverted to healer.")


def main():
    print("=" * 80)
    print("RUNNING CRON GRAPH — V1 DECOUPLED VERIFICATION SUITE")
    print("=" * 80)

    test_compilation_and_routing()
    test_pass_pipeline()
    test_verifier_delegation_flow()

    print("\n" + "=" * 80)
    print("SUCCESS: ALL CRON GRAPH SPECIFICATION TESTS PASSED!")
    print("=" * 80)


if __name__ == "__main__":
    main()
