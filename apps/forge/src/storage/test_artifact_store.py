import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.local import _get_storage_root, sanitize_domain
from storage.supabase_storage import (
    download_json,
    download_text,
    file_exists,
    get_storage_path,
    is_configured as is_supabase_configured,
    upload_json,
    upload_text,
)

logger = logging.getLogger("forge.storage.test_artifact_store")

ARTIFACT_NAMES = {
    "manifest": "manifest.json",
    "action": "action.json",
    "action_script": "action.py",
    "action_result": "action_result.json",
    "discovery_before": "discovery_before.json",
    "discovery_after": "discovery_after.json",
    "expectations": "expectations.json",
    "verification": "verification.json",
    "summary": "summary.json",
    "test": "test.py",
}


def sanitize_test_id(test_id: str) -> str:
    safe = re.sub(r"[^\w\.-]", "_", str(test_id)).strip("._")
    return safe or "unknown_test"


def get_test_storage_key(domain: str, test_id: str, filename: str) -> str:
    """Returns the namespaced Supabase Storage key: `<domain>/tests/<test_id>/<filename>`."""
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    return f"{clean_domain}/tests/{clean_test_id}/{filename}"


def get_supabase_test_script_path(domain: str, test_id: str, filename: str = "test.py") -> str:
    """Returns the full Supabase Storage path for a test script: `<prefix>/<domain>/tests/<test_id>/<filename>`."""
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    key = f"{clean_domain}/tests/{clean_test_id}/{filename}"
    return get_storage_path(key)


def get_local_test_dir(domain: str, test_id: str) -> Path:
    """Returns the local cache directory: `<cache_root>/<domain>/tests/<test_id>/`."""
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    folder = _get_storage_root() / clean_domain / "tests" / clean_test_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_test_artifacts(
    domain: str,
    test_id: str,
    action_spec: Dict[str, Any],
    action_code: str,
    action_result: Dict[str, Any],
    discovery_before: Dict[str, Any],
    discovery_after: Dict[str, Any],
    expectation_spec: Dict[str, Any],
    verification: Dict[str, Any],
    summary: Dict[str, Any],
    test_code: str,
    target_url: str,
    version: int = 1,
    status: str = "healthy",
) -> Dict[str, Any]:
    """
    Saves all 10 artifacts of a test scenario:
    1. Writes to local cache `<cache_root>/<domain>/tests/<test_id>/`
    2. Uploads all files to Supabase Storage at `<domain>/tests/<test_id>/`
    
    The structured specifications (`action.json`, `expectations.json`, `verification.json`)
    are the single source of truth; `action.py` and `test.py` are derived executables.
    """
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    local_dir = get_local_test_dir(clean_domain, clean_test_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    action_passed = bool(action_result.get("passed", False))
    verdict = verification.get("verdict", "CORRECT")

    # Construct manifest.json
    manifest = {
        "test_id": clean_test_id,
        "version": version,
        "status": status,
        "domain": clean_domain,
        "target_url": target_url,
        "storage_prefix": f"{clean_domain}/tests/{clean_test_id}",
        "artifacts": dict(ARTIFACT_NAMES),
        "validated": {
            "action": action_passed,
            "expectations": verdict == "CORRECT",
        },
        "last_run": {
            "status": "passed" if (action_passed and verdict == "CORRECT") else "failed",
            "failure_stage": None if action_passed else "action",
            "verdict": None if action_passed else ("heal_action" if not action_passed else "heal_expectation"),
            "timestamp": now_iso,
            "duration_s": action_result.get("duration_s", 0.0),
        },
    }

    # Map filename -> (content, is_json)
    artifacts_map: Dict[str, tuple] = {
        # filename: (content, is_json)
        ARTIFACT_NAMES["manifest"]: (manifest, True),
        ARTIFACT_NAMES["action"]: (action_spec, True),
        ARTIFACT_NAMES["action_script"]: (action_code, False),
        ARTIFACT_NAMES["action_result"]: (action_result, True),
        ARTIFACT_NAMES["discovery_before"]: (discovery_before, True),
        ARTIFACT_NAMES["discovery_after"]: (discovery_after, True),
        ARTIFACT_NAMES["expectations"]: (expectation_spec, True),
        ARTIFACT_NAMES["verification"]: (verification, True),
        ARTIFACT_NAMES["summary"]: (summary, True),
        ARTIFACT_NAMES["test"]: (test_code, False),
    }

    local_paths: Dict[str, str] = {}
    supabase_keys: Dict[str, str] = {}

    for filename, (content, is_json) in artifacts_map.items():
        local_file = local_dir / filename
        s_key = get_test_storage_key(clean_domain, clean_test_id, filename)

        if is_json:
            text_val = json.dumps(content, indent=2, ensure_ascii=False)
            local_file.write_text(text_val, encoding="utf-8")
            if is_supabase_configured():
                upload_json(s_key, content)
        else:
            text_val = str(content or "")
            local_file.write_text(text_val, encoding="utf-8")
            if is_supabase_configured():
                upload_text(s_key, text_val, content_type="text/x-python")

        local_paths[filename] = str(local_file)
        supabase_keys[filename] = s_key

    supabase_script_path = get_supabase_test_script_path(clean_domain, clean_test_id, ARTIFACT_NAMES["test"])

    logger.info(
        f"[TEST_ARTIFACT_STORE] Saved all 10 artifacts for '{clean_test_id}' "
        f"in domain '{clean_domain}' (Supabase Storage + Local Cache)."
    )

    return {
        "manifest": manifest,
        "local_dir": str(local_dir),
        "local_paths": local_paths,
        "supabase_keys": supabase_keys,
        "test_script_path": str(local_dir / ARTIFACT_NAMES["test"]),
        "supabase_script_path": supabase_script_path,
    }


def load_test_artifacts(domain: str, test_id: str) -> Dict[str, Any]:
    """
    Loads all artifacts for a test scenario:
    - Queries Supabase Storage first for durable copies.
    - Falls back to local cache if Supabase is offline or not configured.
    - Ensures `test.py` is materialized to local disk so Playwright can run.
    """
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    local_dir = get_local_test_dir(clean_domain, clean_test_id)

    # 1. Manifest
    manifest_key = get_test_storage_key(clean_domain, clean_test_id, ARTIFACT_NAMES["manifest"])
    manifest = None
    if is_supabase_configured():
        manifest = download_json(manifest_key)

    if not manifest:
        local_manifest = local_dir / ARTIFACT_NAMES["manifest"]
        if local_manifest.exists():
            try:
                manifest = json.loads(local_manifest.read_text(encoding="utf-8"))
            except Exception:
                manifest = None

    if not manifest:
        logger.warning(f"[TEST_ARTIFACT_STORE] No manifest found for '{clean_test_id}' in '{clean_domain}'.")

    # Helper to load file (from Supabase or local cache)
    def _read_artifact(filename: str, is_json: bool) -> Any:
        s_key = get_test_storage_key(clean_domain, clean_test_id, filename)
        if is_supabase_configured():
            val = download_json(s_key) if is_json else download_text(s_key)
            if val is not None:
                return val

        loc_file = local_dir / filename
        if loc_file.exists():
            try:
                txt = loc_file.read_text(encoding="utf-8")
                return json.loads(txt) if is_json else txt
            except Exception:
                return None
        return None

    action_spec = _read_artifact(ARTIFACT_NAMES["action"], True)
    action_code = _read_artifact(ARTIFACT_NAMES["action_script"], False)
    action_result = _read_artifact(ARTIFACT_NAMES["action_result"], True)
    discovery_before = _read_artifact(ARTIFACT_NAMES["discovery_before"], True)
    discovery_after = _read_artifact(ARTIFACT_NAMES["discovery_after"], True)
    expectation_spec = _read_artifact(ARTIFACT_NAMES["expectations"], True)
    verification = _read_artifact(ARTIFACT_NAMES["verification"], True)
    summary = _read_artifact(ARTIFACT_NAMES["summary"], True)
    test_code = _read_artifact(ARTIFACT_NAMES["test"], False)

    # Ensure test.py is materialized on disk for subprocess execution
    test_file_path = local_dir / ARTIFACT_NAMES["test"]
    if test_code and not test_file_path.exists():
        test_file_path.write_text(test_code, encoding="utf-8")

    return {
        "manifest": manifest,
        "action_spec": action_spec,
        "action_code": action_code,
        "action_result": action_result,
        "discovery_before": discovery_before,
        "discovery_after": discovery_after,
        "expectation_spec": expectation_spec,
        "verification": verification,
        "summary": summary,
        "test_code": test_code,
        "test_file_path": str(test_file_path),
        "local_dir": str(local_dir),
    }


def materialize_test_script(domain: str, test_id: str, script_path: Optional[str] = None) -> Path:
    """
    Ensures that `test.py` for this test is materialized at a physical filesystem path
    so that Playwright subprocess runner can execute it.
    """
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    local_dir = get_local_test_dir(clean_domain, clean_test_id)
    test_path = local_dir / ARTIFACT_NAMES["test"]

    if test_path.exists() and test_path.stat().st_size > 0:
        return test_path

    # Try downloading from Supabase Storage
    if is_supabase_configured():
        # 1. Direct download via provided script_path if given
        if script_path:
            code = download_text(script_path)
            if code:
                test_path.write_text(code, encoding="utf-8")
                logger.info(f"[TEST_ARTIFACT_STORE] Materialized '{test_id}' via script_path from Supabase Storage: {test_path}")
                return test_path

        # 2. Standard decoupled artifact layout
        s_key = get_test_storage_key(clean_domain, clean_test_id, ARTIFACT_NAMES["test"])
        code = download_text(s_key)
        if code:
            test_path.write_text(code, encoding="utf-8")
            logger.info(f"[TEST_ARTIFACT_STORE] Materialized '{test_id}' from Supabase Storage: {test_path}")
            return test_path

        # 3. Single-file compatibility key (<domain>/tests/<test_id>.py)
        compat_key = f"{clean_domain}/tests/{clean_test_id}.py"
        code = download_text(compat_key)
        if code:
            test_path.write_text(code, encoding="utf-8")
            logger.info(f"[TEST_ARTIFACT_STORE] Materialized '{test_id}' from compatibility Supabase key: {test_path}")
            return test_path

    return test_path


def update_test_manifest(
    domain: str,
    test_id: str,
    updates: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Updates `manifest.json` with new fields (e.g. `last_run`, `version`, `status`)
    and syncs the updated manifest back to Supabase Storage and local cache.
    """
    clean_domain = sanitize_domain(domain)
    clean_test_id = sanitize_test_id(test_id)
    local_dir = get_local_test_dir(clean_domain, clean_test_id)

    manifest_key = get_test_storage_key(clean_domain, clean_test_id, ARTIFACT_NAMES["manifest"])
    manifest = None

    if is_supabase_configured():
        manifest = download_json(manifest_key)

    if not manifest:
        local_file = local_dir / ARTIFACT_NAMES["manifest"]
        if local_file.exists():
            try:
                manifest = json.loads(local_file.read_text(encoding="utf-8"))
            except Exception:
                manifest = None

    if not manifest:
        manifest = {
            "test_id": clean_test_id,
            "version": 1,
            "status": "healthy",
            "domain": clean_domain,
            "artifacts": dict(ARTIFACT_NAMES),
        }

    # Deep merge top-level keys
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(manifest.get(k), dict):
            manifest[k].update(v)
        else:
            manifest[k] = v

    # Write back locally
    (local_dir / ARTIFACT_NAMES["manifest"]).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Sync to Supabase
    if is_supabase_configured():
        upload_json(manifest_key, manifest)

    logger.info(f"[TEST_ARTIFACT_STORE] Updated manifest for '{clean_test_id}' in '{clean_domain}'.")
    return manifest


def classify_test_failure(
    test_id: str,
    run_result: Dict[str, Any],
    artifacts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Classifies a test execution failure by inspecting stderr, stdout, exit codes, and error traces.
    Decouples the failure into:
    - ACTION DEFECT (mechanical failure: locator timeout, click failed, scroll failed)
    - EXPECTATION DEFECT (assertion failure: expect() mismatch, text mismatch, assertion error)
    - APPLICATION BUG (server error: 500/502/503/crash banner)
    
    Returns structured failure context so Cron can trigger isolated healing without guessing.
    """
    stderr = run_result.get("stderr", "") or ""
    stdout = run_result.get("stdout", "") or ""
    error_summary = run_result.get("error_summary", "") or run_result.get("error", "") or ""
    combined_err = f"{error_summary}\n{stderr}\n{stdout}"

    # 1. Check for Application Bug / 5xx Server Error / Crash
    app_bug_patterns = [
        r"500\s+(Internal\s+Server\s+Error)?",
        r"502\s+Bad\s+Gateway",
        r"503\s+Service\s+Unavailable",
        r"504\s+Gateway\s+Timeout",
        r"fatal\s+error",
        r"application\s+crash",
        r"database\s+connection\s+error",
    ]
    for pattern in app_bug_patterns:
        if re.search(pattern, combined_err, re.I):
            return {
                "status": "failed",
                "failure_stage": "application",
                "isolate_to": "app_bug",
                "action_status": "inconclusive",
                "expectation_status": "not_reached",
                "reason": f"Application crash or server response error detected: {pattern}",
                "evidence": [line.strip() for line in combined_err.splitlines() if re.search(pattern, line, re.I)][:3],
            }

    # 2. Check for Expectation Defect (Playwright assertion or expect error)
    expectation_patterns = [
        r"AssertionError",
        r"expect\(",
        r"to_be_visible",
        r"to_have_text",
        r"to_contain_text",
        r"to_have_title",
        r"to_have_url",
        r"to_have_value",
        r"to_be_checked",
        r"to_be_disabled",
        r"to_be_enabled",
        r"assert\s+page\.",
    ]
    for pattern in expectation_patterns:
        if re.search(pattern, combined_err):
            return {
                "status": "failed",
                "failure_stage": "expectation",
                "isolate_to": "heal_expectation",
                "action_status": "passed",
                "expectation_status": "failed",
                "reason": f"Grounded expectation failed: Assertion mismatch detected ({pattern})",
                "evidence": [line.strip() for line in combined_err.splitlines() if re.search(pattern, line)][:3],
            }

    # 3. Check for Action Defect (Locator, timing, mechanical interaction)
    action_patterns = [
        r"waiting\s+for\s+locator",
        r"Timeout\s+\d+ms\s+exceeded",
        r"TimeoutError",
        r"locator\.click",
        r"locator\.fill",
        r"locator\.select_option",
        r"strict\s+mode\s+violation",
        r"element\s+is\s+not\s+visible",
        r"element\s+is\s+not\s+attached",
        r"page\.goto",
    ]
    for pattern in action_patterns:
        if re.search(pattern, combined_err):
            return {
                "status": "failed",
                "failure_stage": "action",
                "isolate_to": "heal_action",
                "action_status": "failed",
                "expectation_status": "not_reached",
                "reason": f"Mechanical action defect: Playwright interaction failed ({pattern})",
                "evidence": [line.strip() for line in combined_err.splitlines() if re.search(pattern, line)][:3],
            }

    # Fallback: Treat as action defect if exit code != 0
    return {
        "status": "failed",
        "failure_stage": "action",
        "isolate_to": "heal_action",
        "action_status": "failed",
        "expectation_status": "not_reached",
        "reason": "Execution failed with non-zero exit code during interaction",
        "evidence": [line.strip() for line in combined_err.splitlines() if line.strip()][:3],
    }
