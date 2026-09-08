import logging
from typing import Any, Dict, Literal
from langgraph.graph import StateGraph, START, END

from agents.state import VerificationState
from agents.nodes.verification.context_loader import load_failure_context_node
from agents.nodes.discover import discover_node
from agents.nodes.verification.smoke_builder import build_smoke_verification_test_node
from agents.nodes.verification.smoke_runner import run_smoke_test_node
from agents.nodes.verification.verifier_evaluator import verifier_llm_node
from agents.nodes.verification.report_generator import report_node

logger = logging.getLogger("forge.agent.verification_graph")


def route_verification_verdict(state: VerificationState) -> Literal["report", "__end__"]:
    """
    Conditional router out of the Verifier LLM node:
    - 'CONFIRMED_APP_BUG': Verified genuine application bug -> route to 'report' -> END.
    - 'NOT_CONFIRMED': False alarm / automation divergence -> route directly to END.
    - 'INCONCLUSIVE': Verification produced no application-level evidence (e.g. the smoke
      probe itself failed to execute) -> END without filing a report, so the failure is
      healed and retried rather than misreported as an application bug.
    """
    verdict = state.get("verdict", "NOT_CONFIRMED")
    if verdict == "CONFIRMED_APP_BUG":
        logger.critical("[VERIFICATION GRAPH] Verdict: CONFIRMED_APP_BUG -> Routing to Incident Report.")
        return "report"
    else:
        logger.info("[VERIFICATION GRAPH] Verdict: NOT_CONFIRMED -> Concluding Verification without Bug Report.")
        return "__end__"


def create_verification_graph():
    """
    Constructs and compiles the standalone VERIFICATION GRAPH in LangGraph:

                    START
                      │
                      ▼
              ┌──────────────┐
              │ LOAD FAILURE │
              │   CONTEXT    │
              └──────┬───────┘
                     │
                     ▼
              ┌──────────────┐
              │   DISCOVER   │
              │  Playwright  │
              └──────┬───────┘
                     │
                     ▼
             ┌────────────────┐
             │  BUILD SMOKE   │
             │   VERIFICATION │
             │      TEST      │
             └───────┬────────┘
                     │
                     ▼
              ┌──────────────┐
              │ RUN SMOKE    │
              │    TEST      │
              └──────┬───────┘
                     │
                     ▼
             ┌────────────────┐
             │ VERIFIER LLM   │
             └───────┬────────┘
                     │
          ┌──────────┴──────────┐
          ▼                     ▼
   CONFIRMED_APP_BUG      NOT_CONFIRMED
          │                     │
          ▼                     ▼
       REPORT                  END
          │
          ▼
         END
    """
    builder = StateGraph(VerificationState)

    # 1. Add All Verification Nodes
    builder.add_node("load_failure_context", load_failure_context_node)
    builder.add_node("discover", discover_node)
    builder.add_node("build_smoke_verification_test", build_smoke_verification_test_node)
    builder.add_node("run_smoke_test", run_smoke_test_node)
    builder.add_node("verifier_llm", verifier_llm_node)
    builder.add_node("report", report_node)

    # 2. Linear Pipeline Sequence
    builder.add_edge(START, "load_failure_context")
    builder.add_edge("load_failure_context", "discover")
    builder.add_edge("discover", "build_smoke_verification_test")
    builder.add_edge("build_smoke_verification_test", "run_smoke_test")
    builder.add_edge("run_smoke_test", "verifier_llm")

    # 3. Branching out of Verifier LLM
    builder.add_conditional_edges(
        "verifier_llm",
        route_verification_verdict,
        {
            "report": "report",
            "__end__": END,
        }
    )

    # 4. Report termination
    builder.add_edge("report", END)

    graph = builder.compile()
    return graph
