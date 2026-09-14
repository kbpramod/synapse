import logging
from pathlib import Path
from typing import Any, Dict
from agents.state import ForgeState

logger = logging.getLogger("forge.agent.observer")


def observer_node(state: ForgeState) -> Dict[str, Any]:
    """
    OBSERVER node: Inspects real runtime outcomes, logs, stderr, stdout,
    exit codes, and failure screenshots from the test execution.
    """
    exec_res = state.get("execution_result") or {}
    test_path = state.get("test_file_path")
    
    passed = exec_res.get("passed", False)
    exit_code = exec_res.get("exit_code", -1)
    duration = exec_res.get("duration_s", 0.0)

    # Inspect test directory for generated screenshots
    screenshots = list(exec_res.get("screenshot_paths", []))
    if test_path:
        test_dir = Path(test_path).parent
        for png in test_dir.glob("*.png"):
            png_str = str(png)
            if png_str not in screenshots:
                screenshots.append(png_str)

    exec_res["screenshot_paths"] = screenshots

    # Ensure failure_url is populated on failure if missing
    if not passed and not exec_res.get("failure_url"):
        current_test = state.get("current_test") or {}
        fallback_url = current_test.get("page_url") or state.get("target_url")
        if fallback_url:
            exec_res["failure_url"] = fallback_url

    # Augment error_summary with assertion/timeout specifics from stderr if generic
    stderr = exec_res.get("stderr", "")
    if not passed and stderr:
        for line in reversed(stderr.splitlines()):
            clean_l = line.strip()
            if any(k in clean_l for k in ("AssertionError:", "TimeoutError:", "Error:", "expect(")):
                exec_res["error_summary"] = clean_l
                break

    logger.info(
        f"[OBSERVER] Observed Test Outcome: Passed={passed}, ExitCode={exit_code}, "
        f"Duration={duration}s, ScreenshotsCaptured={len(screenshots)}, "
        f"FailureURL={exec_res.get('failure_url')}"
    )

    return {"execution_result": exec_res}
