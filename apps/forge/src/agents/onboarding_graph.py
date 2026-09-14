import logging
import threading
from typing import Any, Dict, Literal, Optional
from langgraph.graph import StateGraph, START, END

from agents.state import ForgeState
from agents.nodes.discover import discover_node
from agents.nodes.understanding import understanding_node
from agents.nodes.expectation import expectation_node
from agents.nodes.planner import planner_node
from agents.nodes.onboarding_scheduler import get_next_hypothesis_node
from agents.nodes.action_builder import action_builder_node
from agents.nodes.action_runner import action_runner_node
from agents.nodes.result_discovery import result_discovery_node
from agents.nodes.expectation_analysis import expectation_analysis_node
from agents.nodes.correctness import correctness_node
from agents.nodes.heal_action import heal_action_node
from agents.nodes.heal_expectation import heal_expectation_node
from agents.nodes.testcase_assembler import testcase_assembler_node

logger = logging.getLogger("forge.agent.onboarding_graph")


def route_get_next_hypothesis(state: ForgeState) -> Literal["action_builder", "__end__"]:
    """
    Checks if another planned test hypothesis is ready to be built:
    - If current_test is present: route to 'action_builder'.
    - If the hypothesis queue is exhausted: route to END.
    """
    if state.get("current_test"):
        return "action_builder"
    return "__end__"


def route_onboarding_action_runner(state: ForgeState) -> Literal["result_discovery", "heal_action", "get_next_hypothesis"]:
    """Routes after action_runner during onboarding."""
    action_result = state.get("action_result") or {}
    if action_result.get("passed", False):
        return "result_discovery"

    heal_attempt = state.get("action_heal_attempt", 0)
    max_heals = state.get("max_action_heals", 3)

    if heal_attempt < max_heals:
        return "heal_action"
    return "get_next_hypothesis"


def route_onboarding_correctness(state: ForgeState) -> Literal["assemble_testcase", "heal_action", "heal_expectation", "get_next_hypothesis"]:
    """Routes after correctness evaluation during onboarding."""
    verdict = state.get("correctness_verdict", "CORRECT")

    if verdict == "CORRECT":
        return "assemble_testcase"
    elif verdict == "ACTION_DEFECT":
        heal_attempt = state.get("action_heal_attempt", 0)
        max_heals = state.get("max_action_heals", 3)
        if heal_attempt < max_heals:
            return "heal_action"
        return "get_next_hypothesis"
    elif verdict == "EXPECTATION_DEFECT":
        exp_attempt = state.get("expectation_heal_attempt", 0)
        max_exp_heals = state.get("max_expectation_heals", 2)
        if exp_attempt < max_exp_heals:
            return "heal_expectation"
        return "get_next_hypothesis"
    else:  # "APP_BUG" or "INCONCLUSIVE"
        return "get_next_hypothesis"


def create_onboarding_graph():
    """
    Constructs and compiles the LangGraph StateGraph for Forge onboarding using the
    Decoupled Action-Expectation Architecture:
    Discover -> Page Understanding -> Expectation Grounding -> Planner
             -> [Get Next Hypothesis -> Action Builder -> Action Runner
                 -> Result Discovery -> Expectation Analysis -> Correctness
                 -> Assemble Testcase -> Get Next Hypothesis] -> END
    """
    builder = StateGraph(ForgeState)

    # Add core nodes
    builder.add_node("discover", discover_node)
    builder.add_node("understanding", understanding_node)
    builder.add_node("expectation", expectation_node)
    builder.add_node("planner", planner_node)
    builder.add_node("get_next_hypothesis", get_next_hypothesis_node)
    builder.add_node("action_builder", action_builder_node)
    builder.add_node("action_runner", action_runner_node)
    builder.add_node("result_discovery", result_discovery_node)
    builder.add_node("expectation_analysis", expectation_analysis_node)
    builder.add_node("correctness", correctness_node)
    builder.add_node("heal_action", heal_action_node)
    builder.add_node("heal_expectation", heal_expectation_node)
    builder.add_node("assemble_testcase", testcase_assembler_node)

    # Deterministic sequence edges
    builder.add_edge(START, "discover")
    builder.add_edge("discover", "understanding")
    builder.add_edge("understanding", "expectation")
    builder.add_edge("expectation", "planner")
    builder.add_edge("planner", "get_next_hypothesis")

    # Hypothesis dispatch loop
    builder.add_conditional_edges(
        "get_next_hypothesis",
        route_get_next_hypothesis,
        {
            "action_builder": "action_builder",
            "__end__": END,
        },
    )
    builder.add_edge("action_builder", "action_runner")

    # Action execution branching
    builder.add_conditional_edges(
        "action_runner",
        route_onboarding_action_runner,
        {
            "result_discovery": "result_discovery",
            "heal_action": "heal_action",
            "get_next_hypothesis": "get_next_hypothesis",
        },
    )
    builder.add_edge("heal_action", "action_runner")

    # Result & expectation analysis
    builder.add_edge("result_discovery", "expectation_analysis")
    builder.add_edge("expectation_analysis", "correctness")

    # Correctness branching
    builder.add_conditional_edges(
        "correctness",
        route_onboarding_correctness,
        {
            "assemble_testcase": "assemble_testcase",
            "heal_action": "heal_action",
            "heal_expectation": "heal_expectation",
            "get_next_hypothesis": "get_next_hypothesis",
        },
    )
    builder.add_edge("heal_expectation", "correctness")

    # When test is assembled, cycle back to get next hypothesis
    builder.add_edge("assemble_testcase", "get_next_hypothesis")

    graph = builder.compile()
    return graph


def run_onboarding_graph(state: Dict[str, Any]) -> None:
    """Executes the onboarding graph for the given initial state."""
    from events import publish_event

    website_id = state.get("website_id")
    graph = create_onboarding_graph()
    if website_id is not None:
        publish_event(website_id, "Onboarding graph started")
    try:
        graph.invoke(state)
        if website_id is not None:
            publish_event(website_id, "Onboarding graph completed")
    except Exception as e:
        if website_id is not None:
            publish_event(website_id, f"Onboarding graph failed: {e}")
        logger.error(f"[ONBOARDING GRAPH] Run failed for target_url={state.get('target_url')}: {e}")


def run_onboarding_graph_background(
    target_url: str,
    website_id: Optional[int] = None,
    storage_state_path: Optional[str] = None,
) -> None:
    """Fires onboarding graph in a background thread."""
    config: Dict[str, Any] = {}
    if storage_state_path:
        config["storage_state_path"] = storage_state_path

    state: Dict[str, Any] = {"target_url": target_url, "config": config}
    if website_id is not None:
        state["website_id"] = website_id
    thread = threading.Thread(target=run_onboarding_graph, args=(state,), daemon=True)
    thread.start()
