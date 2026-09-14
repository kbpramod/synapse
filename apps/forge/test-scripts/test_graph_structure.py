import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.graph import create_forge_graph, route_correctness, route_action_runner
from agents.onboarding_graph import create_onboarding_graph
from agents.state import ForgeState


def test_graph_compilation():
    print("[TEST] Compiling Forge Decoupled StateGraph...")
    graph = create_forge_graph()
    assert graph is not None, "Graph failed to compile"

    node_names = set(graph.nodes.keys())
    expected_nodes = {
        "discover",
        "understanding",
        "planner",
        "action_builder",
        "action_runner",
        "result_discovery",
        "expectation_analysis",
        "correctness",
        "heal_action",
        "heal_expectation",
        "assemble_testcase",
        "advance_test",
    }
    for expected in expected_nodes:
        assert expected in node_names, f"Node '{expected}' missing from graph nodes: {node_names}"
    print(f"[TEST PASS] All {len(expected_nodes)} decoupled nodes verified in graph: {sorted(list(expected_nodes))}")


def test_onboarding_graph_compilation():
    print("[TEST] Compiling Forge Onboarding Decoupled StateGraph...")
    onboarding_graph = create_onboarding_graph()
    assert onboarding_graph is not None, "Onboarding graph failed to compile"

    user_nodes = {k for k in onboarding_graph.nodes.keys() if not k.startswith("__")}
    expected_nodes = {
        "discover",
        "understanding",
        "expectation",
        "planner",
        "get_next_hypothesis",
        "action_builder",
        "action_runner",
        "result_discovery",
        "expectation_analysis",
        "correctness",
        "heal_action",
        "heal_expectation",
        "assemble_testcase",
    }
    for expected in expected_nodes:
        assert expected in user_nodes, f"Node '{expected}' missing from onboarding nodes: {user_nodes}"
    print(f"[TEST PASS] All {len(expected_nodes)} onboarding nodes verified: {sorted(list(expected_nodes))}")


def test_routing_logic():
    print("[TEST] Testing decoupled conditional routing logic...")

    # Case 1: Action Runner passed -> should route to 'result_discovery'
    pass_action_state: ForgeState = {
        "action_result": {"passed": True, "exit_code": 0},
    }
    assert route_action_runner(pass_action_state) == "result_discovery"
    print("  [PASS] Action runner passed -> routes to 'result_discovery'")

    # Case 2: Action Runner failed (mechanical) under budget -> routes to 'heal_action'
    fail_action_state: ForgeState = {
        "action_result": {"passed": False, "exit_code": 1},
        "action_heal_attempt": 0,
        "max_action_heals": 3,
    }
    assert route_action_runner(fail_action_state) == "heal_action"
    print("  [PASS] Action mechanical failure under budget -> routes to 'heal_action'")

    # Case 3: Correctness verdict CORRECT -> routes to 'assemble_testcase'
    correct_state: ForgeState = {
        "correctness_verdict": "CORRECT",
    }
    assert route_correctness(correct_state) == "assemble_testcase"
    print("  [PASS] Correctness CORRECT -> routes to 'assemble_testcase'")

    # Case 4: Correctness verdict ACTION_DEFECT under budget -> routes to 'heal_action'
    action_defect_state: ForgeState = {
        "correctness_verdict": "ACTION_DEFECT",
        "action_heal_attempt": 1,
        "max_action_heals": 3,
    }
    assert route_correctness(action_defect_state) == "heal_action"
    print("  [PASS] Correctness ACTION_DEFECT under budget -> routes to 'heal_action'")

    # Case 5: Correctness verdict EXPECTATION_DEFECT under budget -> routes to 'heal_expectation'
    exp_defect_state: ForgeState = {
        "correctness_verdict": "EXPECTATION_DEFECT",
        "expectation_heal_attempt": 0,
        "max_expectation_heals": 2,
    }
    assert route_correctness(exp_defect_state) == "heal_expectation"
    print("  [PASS] Correctness EXPECTATION_DEFECT under budget -> routes to 'heal_expectation'")

    # Case 6: Correctness verdict INCONCLUSIVE with remaining tests -> routes to 'advance_test'
    inconclusive_state: ForgeState = {
        "correctness_verdict": "INCONCLUSIVE",
        "current_test_idx": 0,
        "test_plan": [{"id": "t1"}, {"id": "t2"}],
    }
    assert route_correctness(inconclusive_state) == "advance_test"
    print("  [PASS] Correctness INCONCLUSIVE with remaining tests -> routes to 'advance_test'")

    # Case 7: Correctness verdict APP_BUG on final test -> routes to '__end__'
    app_bug_state: ForgeState = {
        "correctness_verdict": "APP_BUG",
        "current_test_idx": 1,
        "test_plan": [{"id": "t1"}, {"id": "t2"}],
    }
    assert route_correctness(app_bug_state) == "__end__"
    print("  [PASS] Correctness APP_BUG on final test -> routes to '__end__'")


def main():
    print("=" * 60)
    print("RUNNING FORGE DECOUPLED GRAPH VERIFICATION")
    print("=" * 60)
    test_graph_compilation()
    test_onboarding_graph_compilation()
    test_routing_logic()
    print("=" * 60)
    print("ALL DECOUPLED GRAPH VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
