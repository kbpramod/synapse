import logging
from typing import Any, Dict, Literal
from agents.state import ForgeState
from config import is_headless
from runner.python_runner import run_test_script

logger = logging.getLogger("forge.agent.action_runner")


def action_runner_node(state: ForgeState) -> Dict[str, Any]:
    """
    ACTION_RUNNER node: Executes the ephemeral action script in an isolated subprocess.
    Because there are zero assertions in the script, any execution failure is 100%
    a mechanical or execution failure in the action sequence.
    """
    action_file_path = state.get("action_file_path")
    if not action_file_path:
        raise ValueError("Cannot run action_runner_node without action_file_path in state.")

    config = state.get("config", {})
    timeout_s = config.get("test_timeout_s", 45)
    headless_bool = config.get("headless")
    if headless_bool is None:
        headless_bool = is_headless()

    headless_str = "true" if headless_bool else "false"
    logger.info(f"[ACTION_RUNNER] Executing action script: {action_file_path} (timeout={timeout_s}s, headless={headless_str})")

    env_vars = {"HEADLESS": headless_str}
    exec_result = run_test_script(
        action_file_path,
        timeout_s=timeout_s,
        env_vars=env_vars,
        headed=not headless_bool,
    )

    status_str = "PASSED" if exec_result.get("passed") else f"FAILED (exit {exec_result.get('exit_code')})"
    logger.info(
        f"[ACTION_RUNNER] Action finished: {status_str}, duration={exec_result.get('duration_s')}s"
    )

    return {"action_result": exec_result}


def route_action_runner(state: ForgeState) -> Literal["result_discovery", "heal_action", "advance_test"]:
    """
    Conditional router out of action_runner:
    - If action passed: proceed to 'result_discovery'.
    - If action failed:
      - If under heal budget: route to 'heal_action'.
      - If heal budget exceeded: route to 'advance_test'.
    """
    action_result = state.get("action_result") or {}
    if action_result.get("passed", False):
        return "result_discovery"

    heal_attempt = state.get("action_heal_attempt", 0)
    max_heals = state.get("max_action_heals", 3)

    if heal_attempt < max_heals:
        logger.info(f"[ACTION_RUNNER ROUTER] Mechanical failure detected -> Routing to heal_action ({heal_attempt + 1}/{max_heals})")
        return "heal_action"
    else:
        logger.warning(f"[ACTION_RUNNER ROUTER] Action heal budget exceeded ({heal_attempt}/{max_heals}) -> Advancing test")
        return "advance_test"
