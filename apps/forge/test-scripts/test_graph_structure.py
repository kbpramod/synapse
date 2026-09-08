import sys
from pathlib import Path

# Add src to sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from agents.graph import create_forge_graph, route_analyzer
from agents.onboarding_graph import create_onboarding_graph
from agents.state import ForgeState, AnalysisResult, TestScenario


def test_graph_compilation():
    print("[TEST] Compiling Forge StateGraph...")
    graph = create_forge_graph()
    assert graph is not None, "Graph failed to compile"
    
    # Check node presence in compiled graph
    node_names = set(graph.nodes.keys())
    expected_nodes = {
        "discover",
        "understanding",
        "planner",
        "builder",
        "runner",
        "observer",
        "analyzer",
        "healer",
        "editor",
        "advance_test"
    }
    for expected in expected_nodes:
        assert expected in node_names, f"Node '{expected}' missing from graph nodes: {node_names}"
    print(f"[TEST PASS] All {len(expected_nodes)} nodes verified in graph: {sorted(list(expected_nodes))}")


def test_onboarding_graph_compilation():
    print("[TEST] Compiling Forge Onboarding StateGraph...")
    onboarding_graph = create_onboarding_graph()
    assert onboarding_graph is not None, "Onboarding graph failed to compile"

    user_nodes = {k for k in onboarding_graph.nodes.keys() if not k.startswith("__")}
    expected_nodes = {
        "discover",
        "understanding",
        "planner",
        "builder",
    }
    assert user_nodes == expected_nodes, f"Onboarding graph should have only {expected_nodes}, but got: {user_nodes}"
    print(f"[TEST PASS] All {len(expected_nodes)} onboarding nodes verified: {sorted(list(expected_nodes))}")


def test_routing_logic():
    print("[TEST] Testing conditional routing logic...")

    # Case 1: Test failed with NEED_HEAL -> should route to 'healer'
    heal_state: ForgeState = {
        "analysis": {"verdict": "NEED_HEAL", "reason": "Selector not found"},
        "current_test_idx": 0,
        "test_plan": [{"id": "test_1"}, {"id": "test_2"}],
    }
    route = route_analyzer(heal_state)
    assert route == "healer", f"Expected 'healer', got '{route}'"
    print("  [PASS] NEED_HEAL correctly routes to 'healer'")

    # Case 2: Test passed and there are more tests -> should route to 'advance_test'
    advance_state: ForgeState = {
        "analysis": {"verdict": "PASS", "reason": "Passed"},
        "current_test_idx": 0,
        "test_plan": [{"id": "test_1"}, {"id": "test_2"}],
    }
    route = route_analyzer(advance_state)
    assert route == "advance_test", f"Expected 'advance_test', got '{route}'"
    print("  [PASS] PASS with remaining tests correctly routes to 'advance_test'")

    # Case 3: Test passed and this was the final test -> should route to '__end__'
    end_state: ForgeState = {
        "analysis": {"verdict": "PASS", "reason": "Passed"},
        "current_test_idx": 1,
        "test_plan": [{"id": "test_1"}, {"id": "test_2"}],
    }
    route = route_analyzer(end_state)
    assert route == "__end__", f"Expected '__end__', got '{route}'"
    print("  [PASS] PASS with last test completed correctly routes to '__end__'")

    # Case 4: APP_BUG found on last test -> routes to '__end__'
    bug_end_state: ForgeState = {
        "analysis": {"verdict": "APP_BUG", "reason": "500 Server error"},
        "current_test_idx": 0,
        "test_plan": [{"id": "test_1"}],
    }
    route = route_analyzer(bug_end_state)
    assert route == "__end__", f"Expected '__end__', got '{route}'"
    print("  [PASS] APP_BUG on final test correctly routes to '__end__'")


def main():
    print("=" * 60)
    print("RUNNING FORGE GRAPH UNIT VERIFICATION")
    print("=" * 60)
    test_graph_compilation()
    test_onboarding_graph_compilation()
    test_routing_logic()
    print("=" * 60)
    print("ALL GRAPH VERIFICATION TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
