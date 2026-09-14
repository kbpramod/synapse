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
_VISIBLE_ERRORS_RE = re.compile(r"\[VISIBLE_ERRORS\]\s*(\[.*\])")
_ERROR_ELEMENTS_RE = re.compile(r"\[ERROR_ELEMENTS\]\s*(\[.*\])")

_FALSE_POSITIVE_ERROR_RE = re.compile(
    r"\b(success|successfully|subscribed|contact us|get in touch|feedback for us|subscription|automationexercise)\b",
    re.I,
)


def _parse_telemetry(stdout: str, stderr: str) -> Dict[str, Any]:
    combined = f"{stdout or ''}\n{stderr or ''}"
    final_url_match = _FINAL_URL_RE.search(combined)
    failure_url_match = _FAILURE_URL_RE.search(combined)
    visible_errors_match = _VISIBLE_ERRORS_RE.search(combined)
    error_elements_match = _ERROR_ELEMENTS_RE.search(combined)

    visible_errors: List[str] = []
    if visible_errors_match:
        try:
            parsed = json.loads(visible_errors_match.group(1))
            if isinstance(parsed, list):
                visible_errors = [
                    str(x).strip() for x in parsed
                    if x and not _FALSE_POSITIVE_ERROR_RE.search(str(x).strip())
                ]
        except Exception:
            pass

    error_elements: List[str] = []
    if error_elements_match:
        try:
            parsed_el = json.loads(error_elements_match.group(1))
            if isinstance(parsed_el, list):
                error_elements = [str(x).strip() for x in parsed_el if x]
        except Exception:
            pass

    failure_url = failure_url_match.group(1).strip() if failure_url_match else None
    if not failure_url and stderr:
        # Fallback: extract URL from Playwright error logs or traceback
        url_match = re.search(r"https?://[^\s'\"\)<>]+", stderr)
        if url_match:
            failure_url = url_match.group(0).rstrip(".,;")

    return {
        "final_url": final_url_match.group(1).strip() if final_url_match else None,
        "failure_url": failure_url,
        "visible_errors": visible_errors,
        "error_elements": error_elements,
    }


def run_test_script(
    test_file_path: str,
    timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
    cwd: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
    headed: Optional[bool] = None,
) -> Dict[str, Any]:
    """Executes a Python Playwright test script in an isolated subprocess via runner.harness."""
    test_path = Path(test_file_path)
    if not test_path.exists():
        raise FileNotFoundError(f"Test script not found: {test_file_path}")

    src_dir = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pythonpath}".rstrip(os.pathsep)

    if env_vars:
        env.update(env_vars)

    if headed is not None:
        env["HEADLESS"] = "false" if headed else "true"

    cmd = [sys.executable, "-m", "runner.harness", str(test_path.resolve())]

    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            cwd=cwd or str(test_path.parent),
            env=env,
        )
        duration = time.time() - start_time
        stdout = proc.stdout
        stderr = proc.stderr
        passed = proc.returncode == 0

        # Artifacts produced next to test file
        test_stem = test_path.stem
        parent_dir = test_path.parent
        screenshots = [str(p.resolve()) for p in parent_dir.glob(f"{test_stem}*.png")]
        trace_path = None
        traces = list(parent_dir.glob(f"{test_stem}*.zip"))
        if traces:
            trace_path = str(traces[0].resolve())

        error_summary = None
        if not passed:
            lines = [line.strip() for line in stderr.splitlines() if line.strip()]
            error_summary = lines[-1] if lines else f"Process exited with code {proc.returncode}"

        telemetry = _parse_telemetry(stdout, stderr)

        return {
            "exit_code": proc.returncode,
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
            "error_elements": telemetry["error_elements"],
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
            "error_elements": telemetry["error_elements"],
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
            "error_elements": [],
        }


# Backwards compatibility alias
run_python_test_script = run_test_script
