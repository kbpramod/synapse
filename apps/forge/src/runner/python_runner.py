import os
import sys
import time
import subprocess
import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import is_headless, DEFAULT_TEST_TIMEOUT_S

_FINAL_URL_RE = re.compile(r"\[FINAL_URL\]\s*(\S+)")
_FAILURE_URL_RE = re.compile(r"\[FAILURE_URL\]\s*(\S+)")
_VISIBLE_ERRORS_RE = re.compile(r"\[VISIBLE_ERRORS\]\s*(\[.*?\])")


def _parse_telemetry(stdout: str, stderr: str) -> Dict[str, Any]:
    combined = f"{stdout or ''}\n{stderr or ''}"
    final_url_match = _FINAL_URL_RE.search(combined)
    failure_url_match = _FAILURE_URL_RE.search(combined)
    visible_errors_match = _VISIBLE_ERRORS_RE.search(combined)

    visible_errors: List[str] = []
    if visible_errors_match:
        try:
            parsed = json.loads(visible_errors_match.group(1))
            if isinstance(parsed, list):
                visible_errors = [str(x).strip() for x in parsed if x]
        except Exception:
            pass

    return {
        "final_url": final_url_match.group(1).strip() if final_url_match else None,
        "failure_url": failure_url_match.group(1).strip() if failure_url_match else None,
        "visible_errors": visible_errors,
    }


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
            "final_url": None,
            "failure_url": None,
            "visible_errors": [],
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
        telemetry = _parse_telemetry(stdout, stderr)

        return {
            "exit_code": exit_code,
            "passed": passed,
            "stdout": stdout,
            "stderr": stderr,
            "duration_s": round(duration, 2),
            "error_summary": error_summary,
            "trace_path": trace_path,
            "screenshot_paths": screenshots,
            "final_url": telemetry["final_url"],
            "failure_url": telemetry["failure_url"],
            "visible_errors": telemetry["visible_errors"],
        }
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start_time
        out = e.stdout or ""
        err = e.stderr or ""
        telemetry = _parse_telemetry(out, err)
        return {
            "exit_code": 124,
            "passed": False,
            "stdout": out,
            "stderr": f"Test timed out after {timeout_s} seconds.\n{err}",
            "duration_s": round(duration, 2),
            "error_summary": f"Execution timed out ({timeout_s}s)",
            "trace_path": None,
            "screenshot_paths": [],
            "final_url": telemetry["final_url"],
            "failure_url": telemetry["failure_url"],
            "visible_errors": telemetry["visible_errors"],
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
            "final_url": None,
            "failure_url": None,
            "visible_errors": [],
        }


# Backwards compatibility alias
run_python_test_script = run_test_script
