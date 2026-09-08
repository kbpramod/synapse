import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import is_headless, DEFAULT_TEST_TIMEOUT_S


def run_test_script(
    test_file_path: str,
    timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
    cwd: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
    headed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Executes a Python Playwright test script in an isolated subprocess."""
    test_path = Path(test_file_path).resolve()
    if not test_path.exists():
        return {
            "exit_code": 1,
            "passed": False,
            "stdout": "",
            "stderr": f"Test script file not found: {test_path}",
            "duration_s": 0.0,
            "error_summary": "Test file not found",
            "trace_path": None,
            "screenshot_paths": [],
        }

    working_dir = cwd or str(test_path.parent)
    if headed is None and env_vars and "HEADLESS" in env_vars:
        headed = env_vars["HEADLESS"].lower() not in ("true", "1", "yes")

    run_headless = is_headless(override=None if headed is None else not headed)

    run_env = os.environ.copy()
    run_env["PYTHONUNBUFFERED"] = "1"
    run_env["HEADLESS"] = "true" if run_headless else "false"
    if env_vars:
        run_env.update(env_vars)

    start_time = time.time()
    try:
        process = subprocess.run(
            [sys.executable, str(test_path)],
            cwd=working_dir,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
        )
        duration = time.time() - start_time
        exit_code = process.returncode
        stdout = process.stdout or ""
        stderr = process.stderr or ""
        passed = (exit_code == 0)

        # Parse error summary from output for self-healing and analysis
        error_summary = None
        if not passed:
            lines = [l.strip() for l in (stderr or stdout).splitlines() if l.strip()]
            error_lines = [
                l for l in lines
                if any(k in l.lower() for k in ("error:", "exception:", "assertionerror", "failed", "timeout", "timed out"))
            ]
            error_summary = error_lines[-1] if error_lines else (lines[-1] if lines else "Execution failed")

        screenshots: List[str] = [str(img) for img in Path(working_dir).glob("**/*.png")]
        traces = list(Path(working_dir).glob("**/*.zip"))
        trace_path = str(traces[0]) if traces else None

        return {
            "exit_code": exit_code,
            "passed": passed,
            "stdout": stdout,
            "stderr": stderr,
            "duration_s": round(duration, 2),
            "error_summary": error_summary,
            "trace_path": trace_path,
            "screenshot_paths": screenshots,
        }
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start_time
        return {
            "exit_code": 124,
            "passed": False,
            "stdout": e.stdout or "",
            "stderr": f"Test timed out after {timeout_s} seconds.",
            "duration_s": round(duration, 2),
            "error_summary": f"Execution timed out ({timeout_s}s)",
            "trace_path": None,
            "screenshot_paths": [],
        }
    except Exception as e:
        return {
            "exit_code": 1,
            "passed": False,
            "stdout": "",
            "stderr": str(e),
            "duration_s": round(time.time() - start_time, 2),
            "error_summary": str(e),
            "trace_path": None,
            "screenshot_paths": [],
        }


# Backwards compatibility alias
run_python_test_script = run_test_script
