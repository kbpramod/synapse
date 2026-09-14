import logging
from typing import Any, Dict, Literal
from langgraph.graph import StateGraph, START, END

from agents.state import ForgeState
from agents.nodes.discover import discover_node
from agents.nodes.understanding import understanding_node
from agents.nodes.planner import planner_node
from agents.nodes.action_builder import action_builder_node
from agents.nodes.action_runner import action_runner_node
from agents.nodes.result_discovery import result_discovery_node
from agents.nodes.expectation_analysis import expectation_analysis_node
from agents.nodes.correctness import correctness_node
from agents.nodes.heal_action import heal_action_node
from agents.nodes.heal_expectation import heal_expectation_node
from agents.nodes.testcase_assembler import testcase_assembler_node

logger = logging.getLogger("forge.agent.graph")


def advance_test_node(state: ForgeState) -> Dict[str, Any]:
    """
    Advances to the next planned test scenario in the test suite,
    resetting per-test action/expectation healing counters and telemetry.
    """
    next_idx = state.get("current_test_idx", 0) + 1
    test_plan = state.get("test_plan", [])
    next_test = test_plan[next_idx] if next_idx < len(test_plan) else None

    logger.info(f"[GRAPH] Advancing to test {next_idx + 1}/{len(test_plan)}: {next_test.get('id') if next_test else 'Suite Completed'}")

    return {
        "current_test_idx": next_idx,
        "current_test": next_test,
        "action_heal_attempt": 0,
        "expectation_heal_attempt": 0,
        "action_spec": None,
        "action_code": None,
        "action_file_path": None,
        "action_result": None,
        "post_action_result": None,
        "expectation_spec": None,
        "correctness_verdict": None,
        "correctness_reason": None,
        "correctness_evaluation": None,
        "test_code": None,
        "test_file_path": None,
    }


def route_post_assembly(state: ForgeState) -> Literal["advance_test", "__end__"]:
    """Routes to 'advance_test' if more scenarios remain in test_plan, else END."""
    current_idx = state.get("current_test_idx", 0)
    test_plan = state.get("test_plan", [])
    if current_idx + 1 < len(test_plan):
        return "advance_test"
    return "__end__"


def route_action_runner(state: ForgeState) -> Literal["result_discovery", "heal_action", "advance_test", "__end__"]:
    """
    Conditional router following Action Runner:
    - If action passed: proceed to 'result_discovery'.
    - If action had mechanical failure:
      - If under heal budget: route to 'heal_action'.
      - If budget exceeded: advance to next test or END.
    """
    action_result = state.get("action_result") or {}
    if action_result.get("passed", False):
        return "result_discovery"

    heal_attempt = state.get("action_heal_attempt", 0)
    max_heals = state.get("max_action_heals", 3)

    if heal_attempt < max_heals:
        logger.info(f"[GRAPH ROUTER] Action mechanical failure -> Routing to heal_action ({heal_attempt + 1}/{max_heals})")
        return "heal_action"

    logger.warning(f"[GRAPH ROUTER] Action heal budget exhausted ({heal_attempt}/{max_heals}) -> Advancing")
    return route_post_assembly(state)


def route_correctness(state: ForgeState) -> Literal["assemble_testcase", "heal_action", "heal_expectation", "advance_test", "__end__"]:
    """
    Conditional router following Correctness Evaluation:
    - 'CORRECT': Action and assertions verified -> route to 'assemble_testcase'.
    - 'ACTION_DEFECT': Semantic action failure -> route to 'heal_action' (if under budget).
    - 'EXPECTATION_DEFECT': Outcome valid but criteria misaligned -> route to 'heal_expectation' (if under budget).
    - 'APP_BUG' / 'INCONCLUSIVE': Log and advance to next test or END.
    """
    verdict = state.get("correctness_verdict", "CORRECT")

    if verdict == "CORRECT":
        return "assemble_testcase"
    elif verdict == "ACTION_DEFECT":
        heal_attempt = state.get("action_heal_attempt", 0)
        max_heals = state.get("max_action_heals", 3)
        if heal_attempt < max_heals:
            return "heal_action"
        return route_post_assembly(state)
    elif verdict == "EXPECTATION_DEFECT":
        exp_attempt = state.get("expectation_heal_attempt", 0)
        max_exp_heals = state.get("max_expectation_heals", 2)
        if exp_attempt < max_exp_heals:
            return "heal_expectation"
        return route_post_assembly(state)
    else:  # "APP_BUG" or "INCONCLUSIVE"
        return route_post_assembly(state)


# Backwards compatibility alias for older test suites
route_analyzer = route_correctness


def create_forge_graph():
    """
    Constructs and compiles the cyclic LangGraph StateGraph implementing Forge's
    Decoupled Action & Expectation Architecture:

    START
      │
      ▼
    DISCOVER ──► UNDERSTANDING ──► PLANNER
                                      │
                                      ▼
                                ACTION_BUILDER ◄─────────────────┐
                                      │                          │
                                      ▼                          │
                                ACTION_RUNNER ◄───┐              │
                                      │           │              │
                    ┌─────────────────┴─────────┐ │              │
                    ▼                           ▼ │ (mechanical  │
             RESULT_DISCOVERY              HEAL_ACTION           │
                    │                           ▲                │
                    ▼                           │                │
           EXPECTATION_ANALYSIS                 │ (semantic      │
                    │                           │  action        │
                    ▼                           │  defect)       │
               CORRECTNESS ─────────────────────┤                │
                    │                           │                │
                    ├────── EXPECTATION_DEFECT ─┴─► HEAL_EXPECTATION
                    │                                    │
                    │ ◄──────────────────────────────────┘
                    │
                    ├────── CORRECT ──────────────► ASSEMBLE_TESTCASE
                    │                                    │
                    ▼                                    ▼
       (APP_BUG / INCONCLUSIVE)                     ADVANCE_TEST ─┘
                    │                                    │
                    └─────────────────► END ◄────────────┘
    """
    builder = StateGraph(ForgeState)

    # 1. Register Core Nodes
    builder.add_node("discover", discover_node)
    builder.add_node("understanding", understanding_node)
    builder.add_node("planner", planner_node)
    builder.add_node("action_builder", action_builder_node)
    builder.add_node("action_runner", action_runner_node)
    builder.add_node("result_discovery", result_discovery_node)
    builder.add_node("expectation_analysis", expectation_analysis_node)
    builder.add_node("correctness", correctness_node)
    builder.add_node("heal_action", heal_action_node)
    builder.add_node("heal_expectation", heal_expectation_node)
    builder.add_node("assemble_testcase", testcase_assembler_node)
    builder.add_node("advance_test", advance_test_node)

    # 2. Linear Entry Sequence
    builder.add_edge(START, "discover")
    builder.add_edge("discover", "understanding")
    builder.add_edge("understanding", "planner")
    builder.add_edge("planner", "action_builder")
    builder.add_edge("action_builder", "action_runner")

    # 3. Action Runner Branching (Mechanical Failure vs Passed)
    builder.add_conditional_edges(
        "action_runner",
        route_action_runner,
        {
            "result_discovery": "result_discovery",
            "heal_action": "heal_action",
            "advance_test": "advance_test",
            "__end__": END,
        }
    )

    # 4. Action Healing loopback
    builder.add_edge("heal_action", "action_runner")

    # 5. Result Analysis Sequence
    builder.add_edge("result_discovery", "expectation_analysis")
    builder.add_edge("expectation_analysis", "correctness")

    # 6. Correctness Evaluation Branching (5-Way Verdict Routing)
    builder.add_conditional_edges(
        "correctness",
        route_correctness,
        {
            "assemble_testcase": "assemble_testcase",
            "heal_action": "heal_action",
            "heal_expectation": "heal_expectation",
            "advance_test": "advance_test",
            "__end__": END,
        }
    )

    # 7. Expectation Healing loopback
    builder.add_edge("heal_expectation", "correctness")

    # 8. Testcase Assembly -> Advance or Complete
    builder.add_conditional_edges(
        "assemble_testcase",
        route_post_assembly,
        {
            "advance_test": "advance_test",
            "__end__": END,
        }
    )

    # 9. Advance Test loops to Action Builder for the next planned hypothesis
    builder.add_edge("advance_test", "action_builder")

    graph = builder.compile()
    return graph
