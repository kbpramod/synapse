import logging
from typing import Any, Dict, Literal
from langgraph.graph import StateGraph, START, END

from agents.state import ForgeState
from agents.nodes.cron_scheduler import get_next_test_node
from agents.nodes.runner import runner_node
from agents.nodes.observer import observer_node
from agents.nodes.analyzer import analyzer_node
from agents.nodes.healer import healer_node
from agents.nodes.discover import discover_node
from agents.nodes.editor import editor_node
from agents.nodes.verifier import verifier_node

logger = logging.getLogger("forge.agent.cron_graph")


def route_get_next_test(state: ForgeState) -> Literal["runner", "__end__"]:
    """
    Checks if a test scenario is ready to execute:
    - If current_test is present: route to 'runner'.
    - If test queue is exhausted: route to END.
    """
    current_test = state.get("current_test")
    if current_test:
        return "runner"
    return "__end__"


def route_analyzer(state: ForgeState) -> Literal["get_next_test", "healer", "verifier"]:
    """
    Conditional router out of the Analyzer node:
    - 'PASS': Test succeeded -> advance to 'get_next_test'.
    - 'NEED_HEAL': Test automation defect -> route to 'healer'.
    - 'SUSPECTED_APP_FAILURE': Severe anomaly/app error -> route to 'verifier'.
    """
    analysis = state.get("analysis") or {}
    verdict = analysis.get("verdict", "PASS")

    if verdict == "PASS":
        logger.info("[CRON GRAPH] Analyzer: PASS -> Advancing to next test.")
        return "get_next_test"
    elif verdict == "NEED_HEAL":
        logger.info("[CRON GRAPH] Analyzer: NEED_HEAL -> Routing to Healer pipeline.")
        return "healer"
    else:  # "SUSPECTED_APP_FAILURE" or fallback
        logger.warning("[CRON GRAPH] Analyzer: SUSPECTED_APP_FAILURE -> Routing to Verifier pipeline.")
        return "verifier"


def route_verifier(state: ForgeState) -> Literal["get_next_test", "healer"]:
    """
    Conditional router out of the Verifier node:
    - 'CONFIRMED_APP_BUG': Real application bug confirmed and reported -> advance to 'get_next_test'.
    - 'EXHAUSTED_HEALS': Unhealable locator mismatch after max retries -> advance to 'get_next_test'.
    - 'NOT_CONFIRMED': False alarm / automation divergence -> divert to 'healer'.
    """
    verdict = state.get("verifier_verdict", "NOT_CONFIRMED")
    if verdict in ("CONFIRMED_APP_BUG", "CONFIRMED", "EXHAUSTED_HEALS"):
        logger.info(f"[CRON GRAPH] Verifier verdict [{verdict}] -> Advancing to next test.")
        return "get_next_test"
    else:
        logger.info("[CRON GRAPH] Verifier verdict [NOT_CONFIRMED] -> Routing to Healer.")
        return "healer"


# Backwards compatibility alias for older unit tests
route_verifier_llm = route_verifier


def create_cron_graph():
    """
    Constructs and compiles CRON GRAPH — V1 in LangGraph:

    START
      │
      ▼
    GET NEXT TEST ◄────────┐
      │       ▲            │
      │       │            │
    (test)   PASS          │
      │       │            │
      ▼       │            │
    RUNNER    │            │
      │       │            │
      ▼       │            │
    OBSERVER  │            │
      │       │            │
      ▼       │            │
    ANALYZER ──┘            │
      │                    │
      ├──────── NEED_HEAL ─┼────────► HEALER
      │                    │             │
      │                    │             ▼
      │                    │          DISCOVER
      │                    │             │
      │                    │             ▼
      │                    │          EDIT TEST
      │                    │             │
      │                    │             ▼
      │                    │           RUNNER
      │                    │
      └─ SUSPECTED_APP_FAILURE ──────► VERIFIER (Verification Graph)
                           │             │
                           │       ┌─────┴─────┐
                           │       ▼           ▼
                           │  CONFIRMED   NOT_CONFIRMED
                           │   APP_BUG         │
                           │       │           │
                           └───────┼───────────┘
                                   │           │
                                   ▼           ▼
                             GET NEXT TEST   HEALER
    """
    builder = StateGraph(ForgeState)

    # 1. Add All Nodes
    builder.add_node("get_next_test", get_next_test_node)
    builder.add_node("runner", runner_node)
    builder.add_node("observer", observer_node)
    builder.add_node("analyzer", analyzer_node)

    # Self-Healing Nodes
    builder.add_node("healer", healer_node)
    builder.add_node("discover_for_heal", discover_node)
    builder.add_node("editor", editor_node)

    # Standalone Verification Subgraph Adapter Node
    builder.add_node("verifier", verifier_node)

    # 2. Sequence Edges from START
    builder.add_edge(START, "get_next_test")

    # Queue Dispatch: if test available -> runner, else -> END
    builder.add_conditional_edges(
        "get_next_test",
        route_get_next_test,
        {
            "runner": "runner",
            "__end__": END,
        }
    )

    # Runner Execution Pipeline
    builder.add_edge("runner", "observer")
    builder.add_edge("observer", "analyzer")

    # 3. Analyzer Branching
    builder.add_conditional_edges(
        "analyzer",
        route_analyzer,
        {
            "get_next_test": "get_next_test",
            "healer": "healer",
            "verifier": "verifier",
        }
    )

    # 4. Self-Healing Loop: HEALER -> DISCOVER -> EDIT TEST -> RUNNER
    builder.add_edge("healer", "discover_for_heal")
    builder.add_edge("discover_for_heal", "editor")
    builder.add_edge("editor", "runner")

    # 5. Verifier Branching: CONFIRMED_APP_BUG -> GET NEXT TEST | NOT_CONFIRMED -> HEALER
    builder.add_conditional_edges(
        "verifier",
        route_verifier,
        {
            "get_next_test": "get_next_test",
            "healer": "healer",
        }
    )

    graph = builder.compile()
    return graph
