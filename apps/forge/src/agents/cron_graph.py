import logging
from datetime import datetime, timezone
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


def route_analyzer(state: ForgeState) -> Literal["get_next_test", "discover_for_heal", "verifier"]:
    """
    Conditional router out of the Analyzer node:
    - 'PASS': Test completed successfully -> route to 'get_next_test'.
    - 'NEED_HEAL': Test failed due to automation defect -> route to 'discover_for_heal'.
    - 'SUSPECTED_APP_FAILURE': Severe anomaly/max heals exceeded -> route to 'verifier'.
    """
    analysis = state.get("analysis") or {}
    verdict = analysis.get("verdict", "PASS")

    if verdict == "PASS":
        logger.info("[CRON GRAPH] Analyzer: PASS -> Advancing to next test.")
        return "get_next_test"
    elif verdict == "NEED_HEAL":
        logger.info("[CRON GRAPH] Analyzer: NEED_HEAL -> Routing to Destination Discovery for Healing.")
        return "discover_for_heal"
    else:  # "SUSPECTED_APP_FAILURE" or fallback
        logger.warning("[CRON GRAPH] Analyzer: SUSPECTED_APP_FAILURE -> Routing to Verifier pipeline.")
        return "verifier"


def route_editor(state: ForgeState) -> Literal["runner", "get_next_test"]:
    """
    Circuit-breaker conditional router out of Editor node:
    - If edit_status == "applied": re-run test script via 'runner'.
    - If edit_status in ("no_change", "failed"): halt futile retries, record failure, advance schedule, and advance to 'get_next_test'.
    """
    edit_status = state.get("edit_status", "not_attempted")
    if edit_status == "applied":
        logger.info("[CRON GRAPH] Editor: edit applied successfully -> Re-executing test with Runner.")
        return "runner"

    # Circuit breaker triggered: halt re-runs
    current_test = state.get("current_test") or {}
    test_id = str(current_test.get("test_id") or current_test.get("id") or "unknown_test")
    exec_res = state.get("execution_result") or {}
    run_id = state.get("run_id") or f"run_{test_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    logger.warning(
        f"[CRON GRAPH] Editor circuit breaker triggered for '{test_id}' (status={edit_status}). "
        "Halting retry cycle to prevent burning budget on unchanged code."
    )
    try:
        from db.repository import ForgeRepository
        ForgeRepository.record_test_run(
            run_id=run_id,
            test_id=test_id,
            exit_code=exec_res.get("exit_code", 1),
            status="failed",
            duration_s=exec_res.get("duration_s", 0.0),
            error_summary=f"Unhealable automation defect: Editor produced status '{edit_status}'",
            stdout=exec_res.get("stdout", ""),
            stderr=exec_res.get("stderr", ""),
        )
        cron_hours = current_test.get("cron_interval_hours", 24)
        ForgeRepository.update_test_run_timestamps(test_id, cron_hours)
    except Exception as e:
        logger.warning(f"[CRON GRAPH] Could not record failed run in circuit breaker: {e}")

    suite_summary = list(state.get("suite_summary", []))
    suite_summary.append({
        "id": test_id,
        "title": current_test.get("title"),
        "status": "FAILED_AUTOMATION",
        "error": f"Editor produced status: {edit_status}",
    })
    state["suite_summary"] = suite_summary

    return "get_next_test"


def route_verifier(state: ForgeState) -> Literal["get_next_test"]:
    """
    All verification outcomes in the cron cycle terminate and advance to 'get_next_test'.
    Never re-loops to Healer or secondary LLM smoke probe.
    """
    verdict = state.get("verifier_verdict", "INCONCLUSIVE")
    logger.info(f"[CRON GRAPH] Verifier verdict [{verdict}] -> Terminating cycle and advancing to next test.")
    return "get_next_test"


# Backwards compatibility alias for older unit tests
route_verifier_llm = route_verifier


def create_cron_graph():
    """
    Constructs and compiles CRON GRAPH in LangGraph:

    START
      │
      ▼
    GET NEXT TEST ◄────────────────────────┐
      │       ▲                            │
      │       │                            │
    (test)   PASS                          │
      │       │                            │
      ▼       │                            │
    RUNNER    │                            │
      │       │                            │
      ▼       │                            │
    OBSERVER  │                            │
      │       │                            │
      ▼       │                            │
    ANALYZER ──┘                            │
      │                                    │
      ├──────── NEED_HEAL ──► DISCOVER     │
      │                          │         │
      │                          ▼         │
      │                       HEALER       │
      │                          │         │
      │                          ▼         │
      │                       EDITOR       │
      │                          │         │
      │                 ┌────────┴───────┐ │
      │                 ▼                ▼ │
      │            (applied)       (no_change/failed)
      │                 │                │ │
      │                 ▼                └─┤
      │               RUNNER               │
      │                                    │
      └─ SUSPECTED_APP_FAILURE ─► VERIFIER ┘
    """
    builder = StateGraph(ForgeState)

    # 1. Add All Nodes
    builder.add_node("get_next_test", get_next_test_node)
    builder.add_node("runner", runner_node)
    builder.add_node("observer", observer_node)
    builder.add_node("analyzer", analyzer_node)

    # Self-Healing Nodes
    builder.add_node("discover_for_heal", discover_node)
    builder.add_node("healer", healer_node)
    builder.add_node("editor", editor_node)

    # Verification Adapter Node
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
            "discover_for_heal": "discover_for_heal",
            "verifier": "verifier",
        }
    )

    # 4. Self-Healing Loop: DISCOVER -> HEALER -> EDITOR -> ROUTE_EDITOR
    builder.add_edge("discover_for_heal", "healer")
    builder.add_edge("healer", "editor")
    builder.add_conditional_edges(
        "editor",
        route_editor,
        {
            "runner": "runner",
            "get_next_test": "get_next_test",
        }
    )

    # 5. Verifier Branching: All outcomes terminate cycle and advance to GET NEXT TEST
    builder.add_conditional_edges(
        "verifier",
        route_verifier,
        {
            "get_next_test": "get_next_test",
        }
    )

    graph = builder.compile()
    return graph
