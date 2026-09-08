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
from agents.nodes.builder import builder_node

logger = logging.getLogger("forge.agent.onboarding_graph")


def route_get_next_hypothesis(state: ForgeState) -> Literal["builder", "__end__"]:
    """
    Checks if another planned test hypothesis is ready to be built:
    - If current_test is present: route to 'builder'.
    - If the hypothesis queue is exhausted: route to END.
    """
    if state.get("current_test"):
        return "builder"
    return "__end__"


def create_onboarding_graph():
    """
    Constructs and compiles the LangGraph StateGraph for Forge onboarding:
    Discover -> Page Understanding -> Expectation -> Test Planner
             -> [Get Next Hypothesis <-> Builder] -> END

    Expectation turns raw discovery into a catalogue of assertions that are actually grounded
    in the observed page (and states outright what a snapshot cannot know), so the planner and
    builder assert real signals instead of inventing routes or elements.

    The planner emits several SMOKE and FLOW test hypotheses (test_plan). Get Next Hypothesis
    dispatches them to the builder one at a time and loops until every hypothesis has been
    turned into a persisted, runnable test script.
    """
    builder = StateGraph(ForgeState)

    # Add core nodes
    builder.add_node("discover", discover_node)
    builder.add_node("understanding", understanding_node)
    builder.add_node("expectation", expectation_node)
    builder.add_node("planner", planner_node)
    builder.add_node("get_next_hypothesis", get_next_hypothesis_node)
    builder.add_node("builder", builder_node)

    # Add deterministic sequence edges
    builder.add_edge(START, "discover")
    builder.add_edge("discover", "understanding")
    builder.add_edge("understanding", "expectation")
    builder.add_edge("expectation", "planner")
    builder.add_edge("planner", "get_next_hypothesis")

    # Dispatch loop: build every hypothesis in the plan, one at a time
    builder.add_conditional_edges(
        "get_next_hypothesis",
        route_get_next_hypothesis,
        {
            "builder": "builder",
            "__end__": END,
        },
    )
    builder.add_edge("builder", "get_next_hypothesis")

    graph = builder.compile()
    return graph


def run_onboarding_graph(state: Dict[str, Any]) -> None:
    """
    Executes the onboarding graph (discover -> understanding -> planner -> [build loop]) for
    the given initial state (must include target_url). When state includes a website_id, progress
    is published to that website's SSE event stream (used by the onboarding API's live log).
    """
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
    """
    Fires the onboarding graph for `target_url` on a background thread and returns immediately.
    Used when a passing test lands on a page that has never been onboarded (e.g. a login flow
    reaching a dashboard) — coverage should extend to that page without blocking the test run
    that discovered it.

    `storage_state_path` carries the authenticated session the passing test saved just before
    its browser closed. Pages behind a login are only reachable by reusing it.
    """
    config: Dict[str, Any] = {}
    if storage_state_path:
        config["storage_state_path"] = storage_state_path

    state: Dict[str, Any] = {"target_url": target_url, "config": config}
    if website_id is not None:
        state["website_id"] = website_id
    thread = threading.Thread(target=run_onboarding_graph, args=(state,), daemon=True)
    thread.start()
