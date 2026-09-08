import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agents.state import FailureContext, ForgeState, VerificationState
from db.repository import ForgeRepository
from storage.local import sanitize_domain

logger = logging.getLogger("forge.agent.verifier")


def verifier_node(state: ForgeState) -> Dict[str, Any]:
    """
    CRON GRAPH -> VERIFICATION GRAPH DELEGATION NODE:
    Constructs an explicit VerificationState from the current test and execution telemetry,
    invokes the standalone Verification Graph, and integrates the verdict back into ForgeState.
    """
    from agents.verification_graph import create_verification_graph

    current_test = state.get("current_test") or {}
    exec_res = state.get("execution_result") or {}
    analysis = state.get("analysis") or {}
    test_id = str(current_test.get("test_id") or current_test.get("id") or "unknown_test")
    target_url = state.get("target_url") or current_test.get("page_url") or "https://example.com"

    target_domain = state.get("target_domain") or sanitize_domain(target_url)

    logger.warning(
        f"[VERIFIER NODE] Delegating suspected application failure in '{test_id}' "
        f"to standalone Verification Graph. Reason: {analysis.get('reason')}"
    )

    # 1. Build FailureContext with original failure evidence
    screenshot = (exec_res.get("screenshot_paths") or [None])[0] if exec_res.get("screenshot_paths") else None
    failure_ctx: FailureContext = {
        "expected": current_test.get("expected_outcome") or "Expected user capability to succeed",
        "actual": analysis.get("reason") or exec_res.get("error_summary") or "Interaction failed",
        "failed_step": analysis.get("failure_type") or "Test step execution",
        "error": exec_res.get("error_summary"),
        "screenshot": screenshot,
        "trace": exec_res.get("trace_path"),
        "console_errors": [],
        "network_errors": [],
    }

    # 2. Prepare isolated VerificationState
    v_state: VerificationState = {
        "application_id": target_domain,
        "target_url": target_url,
        "target_domain": target_domain,
        "failed_test_id": test_id,
        "failure_context": failure_ctx,
        "config": state.get("config", {}),
        "evidence": [],
    }

    # 3. Invoke independent Verification Graph
    v_graph = create_verification_graph()
    v_final = v_graph.invoke(v_state)

    verdict = v_final.get("verdict", "NOT_CONFIRMED")
    confidence = v_final.get("confidence", 0.90)
    reason = v_final.get("reason", "Verification assessment completed")
    evidence = v_final.get("evidence", [])
    report = v_final.get("report")
    smoke_result = v_final.get("smoke_result")

    logger.info(
        f"[VERIFIER NODE] Verification Graph returned: [{verdict}] "
        f"(Confidence: {confidence:.2f}) — {reason}"
    )

    incident_reports = list(state.get("incident_reports", []))
    suite_summary = list(state.get("suite_summary", []))
    heal_attempt = state.get("heal_attempt", 0)
    max_heal_attempts = state.get("max_heal_attempts", 3)

    if verdict == "CONFIRMED_APP_BUG":
        if report:
            incident_reports.append(report)
        suite_summary.append({
            "id": test_id,
            "title": current_test.get("title"),
            "status": "CONFIRMED_BUG",
            "incident_id": report.get("incident_id") if report else None,
            "error": reason,
        })
        # Advance test cron schedule in PostgreSQL
        cron_hours = current_test.get("cron_interval_hours", 24)
        try:
            ForgeRepository.update_test_run_timestamps(test_id, cron_hours)
            logger.info(f"[VERIFIER NODE] Advanced next_run_at for confirmed bug '{test_id}' by {cron_hours}h.")
        except Exception as e:
            logger.warning(f"[VERIFIER NODE] Could not advance test schedule: {e}")

    else:
        # verdict is "NOT_CONFIRMED" or "INCONCLUSIVE" — in both cases no bug is filed and the
        # failure is treated as an automation defect to be healed.
        # Check if heal attempts exceeded budget to avoid infinite healing loop
        if heal_attempt >= max_heal_attempts:
            logger.warning(
                f"[VERIFIER NODE] Test '{test_id}' was NOT confirmed as app bug, but exceeded max heal attempts "
                f"({heal_attempt}/{max_heal_attempts}). Marking test run as failed for this cycle."
            )
            # Record failed run and advance timestamps
            try:
                # Reuse the cycle's run_id so this row ties to the archived script revisions.
                run_id = state.get("run_id") or f"run_{test_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                ForgeRepository.record_test_run(
                    run_id=run_id,
                    test_id=test_id,
                    exit_code=exec_res.get("exit_code", 1),
                    status="failed",
                    duration_s=exec_res.get("duration_s", 0.0),
                    error_summary=f"Unhealable automation divergence: {reason}",
                    stdout=exec_res.get("stdout", ""),
                    stderr=exec_res.get("stderr", ""),
                )
                cron_hours = current_test.get("cron_interval_hours", 24)
                ForgeRepository.update_test_run_timestamps(test_id, cron_hours)
            except Exception as e:
                logger.warning(f"[VERIFIER NODE] Could not record failed run: {e}")

            suite_summary.append({
                "id": test_id,
                "title": current_test.get("title"),
                "status": "FAILED_AUTOMATION",
                "error": reason,
            })
            verdict = "EXHAUSTED_HEALS"

    return {
        "failure_context": failure_ctx,
        "verifier_verdict": verdict,
        "verifier_reason": reason,
        "incident_reports": incident_reports,
        "suite_summary": suite_summary,
        "smoke_result": smoke_result,
    }


# Backwards compatibility re-exports from verification package
from agents.nodes.verification.context_loader import load_failure_context_node
from agents.nodes.verification.smoke_builder import build_smoke_verification_test_node
from agents.nodes.verification.smoke_runner import run_smoke_test_node
from agents.nodes.verification.verifier_evaluator import verifier_llm_node
from agents.nodes.verification.report_generator import report_node
