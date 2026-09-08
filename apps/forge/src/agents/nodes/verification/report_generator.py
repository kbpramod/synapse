import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from agents.state import VerificationState
from db.repository import ForgeRepository
from storage.local import get_website_storage_dir, sanitize_domain, mirror_to_cloud

logger = logging.getLogger("forge.verification.report")


def report_node(state: VerificationState) -> Dict[str, Any]:
    """
    REPORT node:
    Compiles an official Bug Incident Report when verdict is CONFIRMED_APP_BUG.
    Persists to disk in storage/<domain>/reports/ and indexes into PostgreSQL test_runs.
    """
    failed_test_id = state.get("failed_test_id", "unknown_test")
    target_url = state.get("target_url") or ""
    domain = state.get("target_domain") or (sanitize_domain(target_url) if target_url else "global")
    verdict = state.get("verdict", "CONFIRMED_APP_BUG")
    confidence = state.get("confidence", 0.95)
    reason = state.get("reason", "Confirmed Application Bug")
    evidence = state.get("evidence", [])
    failure_ctx = state.get("failure_context") or {}
    smoke_res = state.get("smoke_result") or {}

    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    incident_id = f"incident_{failed_test_id}_{timestamp_str}"

    report_data = {
        "incident_id": incident_id,
        "test_id": failed_test_id,
        "application_id": state.get("application_id", domain),
        "domain": domain,
        "target_url": target_url,
        "verdict": verdict,
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence,
        "failure_context": failure_ctx,
        "smoke_execution": {
            "passed": smoke_res.get("passed", False),
            "exit_code": smoke_res.get("exit_code"),
            "duration_s": smoke_res.get("duration_s", 0.0),
        },
        "reported_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1. Save artifact to disk
    try:
        site_storage = get_website_storage_dir(target_url) if target_url else Path("storage")
        reports_dir = site_storage / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_file = reports_dir / f"{incident_id}.json"
        report_text = json.dumps(report_data, indent=2)
        report_file.write_text(report_text, encoding="utf-8")
        mirror_to_cloud(report_file, report_text, content_type="application/json")
        logger.info(f"[VERIFICATION - REPORT] Bug report saved to: {report_file}")
    except Exception as err:
        logger.warning(f"[VERIFICATION - REPORT] Could not save bug report to disk: {err}")

    # 2. Record in PostgreSQL test_runs
    try:
        ForgeRepository.record_test_run(
            run_id=incident_id,
            test_id=failed_test_id,
            exit_code=smoke_res.get("exit_code", 1),
            status="APP_BUG",
            duration_s=smoke_res.get("duration_s", 0.0),
            error_summary=reason[:500],
            stdout=smoke_res.get("stdout", ""),
            stderr=smoke_res.get("stderr", ""),
        )
        logger.info(f"[VERIFICATION - REPORT] Indexed incident '{incident_id}' into PostgreSQL test_runs.")
    except Exception as db_err:
        logger.warning(f"[VERIFICATION - REPORT] PostgreSQL indexing notice: {db_err}")

    logger.critical(
        f"[VERIFICATION - REPORT] *** CONFIRMED APPLICATION BUG REPORTED *** "
        f"Test: '{failed_test_id}' | Incident ID: '{incident_id}' | Confidence: {confidence:.2f}"
    )

    return {"report": report_data}
