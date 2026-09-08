import logging
from typing import Any, Dict
from agents.state import ForgeState

logger = logging.getLogger("forge.agent.onboarding_scheduler")


def get_next_hypothesis_node(state: ForgeState) -> Dict[str, Any]:
    """
    GET NEXT HYPOTHESIS node:
    Dispatches test hypotheses from the planner's test_plan to the builder node one at a time,
    mirroring the Cron Graph's queue-based dispatch (see cron_scheduler.get_next_test_node).

    Without this loop, only the planner's first hypothesis (test_plan[0]) would ever reach the
    builder, silently discarding the rest of the generated test plan.
    """
    if "test_queue" not in state:
        # First entry after planner: seed the queue from the full test plan.
        queue = list(state.get("test_plan", []))
    else:
        queue = list(state.get("test_queue", []))

    if not queue:
        logger.info("[ONBOARDING SCHEDULER] All hypotheses have been built. Onboarding complete.")
        return {"test_queue": [], "current_test": None}

    next_hypothesis = queue.pop(0)
    total = len(state.get("test_plan", []))
    logger.info(
        f"[ONBOARDING SCHEDULER] Dispatching hypothesis '{next_hypothesis.get('id')}' "
        f"[{next_hypothesis.get('type')}] ({len(queue)} remaining of {total})."
    )

    return {
        "test_queue": queue,
        "current_test": next_hypothesis,
        "heal_attempt": 0,
    }
