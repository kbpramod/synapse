import logging
from typing import Any, Dict
from agents.state import ForgeState
from config import is_headless
from runner.python_runner import run_test_script

logger = logging.getLogger("forge.agent.runner")


def runner_node(state: ForgeState) -> Dict[str, Any]:
    """
    RUNNER node: Executes the generated Playwright test script in an isolated subprocess.
    """
    test_file_path = state.get("test_file_path")
    if not test_file_path:
        raise ValueError("Cannot run runner_node without test_file_path in state.")

    config = state.get("config", {})
    timeout_s = config.get("test_timeout_s", 45)
    headless_bool = config.get("headless")
    if headless_bool is None:
        headless_bool = is_headless()

    headless_str = "true" if headless_bool else "false"
    logger.info(f"[RUNNER] Executing test script: {test_file_path} (timeout={timeout_s}s, headless={headless_str})")

    env_vars = {"HEADLESS": headless_str}
    exec_result = run_test_script(
        test_file_path,
        timeout_s=timeout_s,
        env_vars=env_vars,
        headed=not headless_bool,
    )

    logger.info(
        f"[RUNNER] Execution finished: exit_code={exec_result.get('exit_code')}, "
        f"passed={exec_result.get('passed')}, duration={exec_result.get('duration_s')}s"
    )

    return {"execution_result": exec_result}
