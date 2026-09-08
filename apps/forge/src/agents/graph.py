import logging
from typing import Any, Dict, Literal
from langgraph.graph import StateGraph, START, END

from agents.state import ForgeState
from agents.nodes.discover import discover_node
from agents.nodes.understanding import understanding_node
from agents.nodes.planner import planner_node
from agents.nodes.builder import builder_node
from agents.nodes.runner import runner_node
from agents.nodes.observer import observer_node
from agents.nodes.analyzer import analyzer_node
from agents.nodes.healer import healer_node
from agents.nodes.editor import editor_node

logger = logging.getLogger("forge.agent.graph")


def advance_test_node(state: ForgeState) -> Dict[str, Any]:
    """
    Advances to the next planned test scenario in the test suite,
    resetting per-test healing counters and telemetry.
    """
    next_idx = state.get("current_test_idx", 0) + 1
    test_plan = state.get("test_plan", [])
    next_test = test_plan[next_idx] if next_idx < len(test_plan) else None

    logger.info(f"[GRAPH] Advancing to test {next_idx + 1}/{len(test_plan)}: {next_test.get('id') if next_test else 'Done'}")

    return {
        "current_test_idx": next_idx,
        "current_test": next_test,
        "heal_attempt": 0,
        "healing_history": [],
        "execution_result": None,
        "analysis": None,
    }


def route_analyzer(state: ForgeState) -> Literal["healer", "advance_test", "__end__"]:
    """
    Conditional router following the Analyzer node:
    - If analysis verdict is 'NEED_HEAL', route to 'healer' (Loop back to builder).
    - If 'PASS' or 'APP_BUG' / max retries reached:
      - If there are remaining tests in the test plan, route to 'advance_test'.
      - Otherwise, route to END.
    """
    analysis = state.get("analysis") or {}
    verdict = analysis.get("verdict", "PASS")

    if verdict == "NEED_HEAL":
        return "healer"

    current_idx = state.get("current_test_idx", 0)
    test_plan = state.get("test_plan", [])

    if current_idx + 1 < len(test_plan):
        return "advance_test"

    return "__end__"


def create_forge_graph():
    """
    Constructs and compiles the cyclic LangGraph StateGraph for Forge:
    Discover -> Page Understanding -> Test Planner -> Test Builder ->
    Runner -> Observer -> Analyzer -> [Healer -> Builder] / [Advance -> Builder] / [END]
    """
    builder = StateGraph(ForgeState)

    # Add core nodes
    builder.add_node("discover", discover_node)
    builder.add_node("understanding", understanding_node)
    builder.add_node("planner", planner_node)
    builder.add_node("builder", builder_node)
    builder.add_node("runner", runner_node)
    builder.add_node("observer", observer_node)
    builder.add_node("analyzer", analyzer_node)
    builder.add_node("healer", healer_node)
    builder.add_node("editor", editor_node)
    builder.add_node("advance_test", advance_test_node)

    # Add deterministic sequence edges
    builder.add_edge(START, "discover")
    builder.add_edge("discover", "understanding")
    builder.add_edge("understanding", "planner")
    builder.add_edge("planner", "builder")
    builder.add_edge("builder", "runner")
    builder.add_edge("runner", "observer")
    builder.add_edge("observer", "analyzer")

    # Add cyclic and branching edges
    builder.add_conditional_edges(
        "analyzer",
        route_analyzer,
        {
            "healer": "healer", 
            "advance_test": "advance_test",
            "__end__": END
        }
    )

    # Healer routes to Editor to surgically edit the existing test script
    builder.add_edge("healer", "editor")
    # Editor routes back to Runner to verify the patched test script
    builder.add_edge("editor", "runner")

    # Advance Test loops to Test Builder for the next scenario
    builder.add_edge("advance_test", "builder")

    graph = builder.compile()
    return graph
