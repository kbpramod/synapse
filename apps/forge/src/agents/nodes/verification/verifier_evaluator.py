import json
import logging
import re
from typing import Any, Dict, List, Optional
from agents.llm import get_chat_model
from agents.state import VerificationState
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger("forge.verification.evaluator")

_HTTP_STATUS_RE = re.compile(r"\[VERIFY_SMOKE\]\s*HTTP Status:\s*(\d{3})")
_NETWORK_ERROR_RE = re.compile(r"\[NETWORK_ERROR\]\s*(\d{3})\s+(\S+)")
_APP_LOADED_MARKER = "[VERIFY_SMOKE] Application loaded successfully."

# Failures that mean the SMOKE SCRIPT broke, not the application under test.
_SCRIPT_DEFECT_MARKERS = (
    "SyntaxError",
    "NameError",
    "AttributeError",
    "ImportError",
    "IndentationError",
    "TypeError:",
    "playwright._impl._errors.TimeoutError",
    "TimeoutError:",
    "Timeout 30000ms exceeded",
    "waiting for locator",
    "strict mode violation",
    "locator resolved to",
)


def extract_smoke_signals(smoke_res: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pulls hard, structured facts out of the smoke probe's output instead of relying on its
    pass/fail exit code. A smoke script can fail for reasons that say nothing about the
    application (its own bad locator, a timeout, a code error), so the document's HTTP status
    and the explicit success marker are what actually indicate application health.
    """
    stdout = smoke_res.get("stdout") or ""
    stderr = smoke_res.get("stderr") or ""
    combined = f"{stdout}\n{stderr}"

    status_match = _HTTP_STATUS_RE.search(stdout)
    document_status: Optional[int] = int(status_match.group(1)) if status_match else None

    failed_resources = [
        {"status": int(s), "url": u} for s, u in _NETWORK_ERROR_RE.findall(stdout)
    ]
    # A 5xx on the document itself is app breakage; a 404 favicon/analytics beacon is not.
    server_error_resources = [r for r in failed_resources if r["status"] >= 500]

    script_defect = any(marker in combined for marker in _SCRIPT_DEFECT_MARKERS)

    return {
        "document_http_status": document_status,
        "app_loaded_marker_present": _APP_LOADED_MARKER in stdout,
        "failed_sub_resources": failed_resources,
        "server_error_sub_resources": server_error_resources,
        "smoke_script_defect_detected": script_defect,
        "smoke_process_passed": bool(smoke_res.get("passed", False)),
        "smoke_exit_code": smoke_res.get("exit_code"),
    }


def prejudge_from_signals(signals: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Deterministic guardrail applied around the LLM. Returns a forced verdict when the evidence
    is unambiguous, otherwise None (let the LLM weigh it).

    This exists because the evaluator kept reporting healthy applications as bugs: a smoke
    script that failed to execute was being read as proof the app was broken.
    """
    status = signals.get("document_http_status")

    # The application served its document and rendered — it is demonstrably up, so the
    # original failure cannot be an application outage.
    if status is not None and status < 400 and signals.get("app_loaded_marker_present"):
        return {
            "verdict": "NOT_CONFIRMED",
            "confidence": 0.95,
            "reason": (
                f"Application responded HTTP {status} and rendered successfully during fresh "
                f"verification. The original failure was an automation defect, not an app bug."
            ),
            "evidence": [f"Document HTTP status: {status}", "Smoke probe reported successful load"],
        }

    # The application itself returned a server error — genuine breakage.
    if status is not None and status >= 500:
        return {
            "verdict": "CONFIRMED_APP_BUG",
            "confidence": 0.95,
            "reason": f"Application returned HTTP {status} for its own document during fresh verification.",
            "evidence": [f"Document HTTP status: {status}"],
        }

    # The smoke script broke before it could observe anything about the application.
    if signals.get("smoke_script_defect_detected") and status is None:
        return {
            "verdict": "INCONCLUSIVE",
            "confidence": 0.4,
            "reason": (
                "The smoke verification script failed to execute (locator timeout or script error) "
                "and never observed the application's response, so it provides no evidence either "
                "way. Treating as an automation defect rather than an application bug."
            ),
            "evidence": ["Smoke script defect detected", "No document HTTP status was observed"],
        }

    return None

VERIFIER_EVALUATOR_SYSTEM_PROMPT = """You are a Principal Site Reliability Engineer (SRE) and Quality Arbiter.
A user journey test triggered a SUSPECTED_APP_FAILURE.
Your SOLE RESPONSIBILITY is to answer:
"Is this genuinely an application bug, based on both original failure evidence AND fresh browser execution evidence?"

YOU DO NOT DECIDE HEALING. You only judge ground truth reality.

THE SMOKE SCRIPT IS NOT THE APPLICATION (most important rule):
The smoke probe is itself a generated Playwright script. It can fail for reasons that say
NOTHING about the application: its own wrong selector, a locator timeout, a code error, or an
assertion it invented. "The smoke test failed, therefore the app is broken" is INVALID
reasoning and has repeatedly caused healthy applications to be reported as buggy.
Confirming a bug requires POSITIVE evidence from the application itself.

Judge using `derived_smoke_signals`, which is extracted deterministically from the probe's
output — trust it over your own reading of the raw logs:
  - `document_http_status`        the status the app returned for its own page
  - `app_loaded_marker_present`   the probe confirmed the page rendered
  - `smoke_script_defect_detected` the probe itself broke (timeout/selector/code error)
  - `server_error_sub_resources`  sub-requests that returned 5xx

Verdicts:
- "CONFIRMED_APP_BUG": requires POSITIVE application-level evidence, such as:
  - `document_http_status` >= 500, or the page failing to load at all.
  - A sub-request the feature depends on returning 5xx.
  - An uncaught exception originating in APPLICATION code that blocks the capability.
- "NOT_CONFIRMED": the application is demonstrably alive and behaving.
  - `document_http_status` < 400 and `app_loaded_marker_present` is true.
  - The original failure is explained by locator drift, timing, or automation divergence.
- "INCONCLUSIVE": the verification produced no usable evidence about the application.
  - `smoke_script_defect_detected` is true and no `document_http_status` was observed.
  - The probe timed out on its own locator, or never reached the application.
  - When torn between INCONCLUSIVE and CONFIRMED_APP_BUG, choose INCONCLUSIVE. A false bug
    report is far more damaging than a missed one — the test simply gets healed and retried.

EVIDENCE THAT IS NOT SUFFICIENT ON ITS OWN:
- Console warnings/errors, or a failed resource load (favicon, analytics, fonts, tracking
  pixels, third-party scripts). These are present on most healthy sites. Only count them if
  the failing resource is plainly required for the capability under test AND returned 5xx.
- A locator timeout in the original test or in the smoke probe.
- The smoke process exiting non-zero.

Strict Output Format (JSON ONLY):
{
  "verdict": "CONFIRMED_APP_BUG" | "NOT_CONFIRMED" | "INCONCLUSIVE",
  "confidence": 0.0 to 1.0,
  "reason": "Explanation citing the specific application-level evidence (or its absence)",
  "evidence": [
    "Specific observation 1",
    "Specific observation 2"
  ]
}
Output ONLY valid JSON.
"""


def verifier_llm_node(state: VerificationState) -> Dict[str, Any]:
    """
    VERIFIER LLM node:
    Arbitrates between CONFIRMED_APP_BUG and NOT_CONFIRMED by weighing:
    1. Original failure evidence (FailureContext)
    2. Fresh live DOM discovery (DiscoveryData)
    3. Fresh browser execution outcome (SmokeResult)
    """
    failed_test_id = state.get("failed_test_id", "unknown_test")
    failure_ctx = state.get("failure_context") or {}
    smoke_res = state.get("smoke_result") or {}
    disc = state.get("discovery_data") or {}

    logger.info(f"[VERIFICATION - EVALUATOR] Evaluating evidence for '{failed_test_id}'...")

    signals = extract_smoke_signals(smoke_res)
    logger.info(
        f"[VERIFICATION - EVALUATOR] Smoke signals: http_status={signals['document_http_status']}, "
        f"app_loaded={signals['app_loaded_marker_present']}, "
        f"script_defect={signals['smoke_script_defect_detected']}, "
        f"5xx_subresources={len(signals['server_error_sub_resources'])}"
    )

    # Deterministic guardrail: when the evidence is unambiguous, do not let the LLM talk
    # itself into a false bug report (or out of a real one).
    forced = prejudge_from_signals(signals)
    if forced:
        logger.info(
            f"[VERIFICATION - EVALUATOR] Verdict decided deterministically: [{forced['verdict']}] — {forced['reason']}"
        )
        return forced

    eval_payload = {
        "failed_test_id": failed_test_id,
        "target_url": state.get("target_url"),
        "derived_smoke_signals": signals,
        "original_failure_context": {
            "failed_step": failure_ctx.get("failed_step"),
            "expected": failure_ctx.get("expected"),
            "actual": failure_ctx.get("actual"),
            "error": failure_ctx.get("error"),
            "console_errors": failure_ctx.get("console_errors", []),
            "network_errors": failure_ctx.get("network_errors", []),
        },
        "fresh_browser_execution_evidence": {
            "smoke_test_passed": smoke_res.get("passed", False),
            "smoke_test_exit_code": smoke_res.get("exit_code"),
            "smoke_test_stdout": (smoke_res.get("stdout") or "")[-1000:],
            "smoke_test_stderr": (smoke_res.get("stderr") or "")[-1500:],
        },
        "fresh_dom_discovery": {
            "page_title": disc.get("page", {}).get("title"),
            "current_url": disc.get("page", {}).get("url"),
        },
    }

    verdict = "NOT_CONFIRMED"
    confidence = 0.85
    reason = "Verification assessment completed."
    evidence: List[str] = []

    try:
        llm = get_chat_model()
        messages = [
            SystemMessage(content=VERIFIER_EVALUATOR_SYSTEM_PROMPT),
            HumanMessage(content=f"Telemetry & Evidence:\n{json.dumps(eval_payload, indent=2, default=str)}"),
        ]
        response = llm.invoke(messages)
        content = response.content.strip()

        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            content = "\n".join(lines).strip()

        parsed = json.loads(content)
        raw_verdict = str(parsed.get("verdict", "")).strip().upper()
        if "INCONCLUSIVE" in raw_verdict:
            verdict = "INCONCLUSIVE"
        elif "CONFIRMED" in raw_verdict and "NOT" not in raw_verdict:
            verdict = "CONFIRMED_APP_BUG"
        else:
            verdict = "NOT_CONFIRMED"

        confidence = float(parsed.get("confidence", 0.90))
        reason = parsed.get("reason", "Evaluation concluded.")
        evidence = list(parsed.get("evidence", []))

        # Final safety net: never let a bug be confirmed without application-level evidence,
        # even if the LLM asserts one. A smoke script failing to run is not app breakage.
        if verdict == "CONFIRMED_APP_BUG":
            status = signals.get("document_http_status")
            has_app_evidence = (
                (status is not None and status >= 500)
                or bool(signals.get("server_error_sub_resources"))
                or (status is None and not signals.get("app_loaded_marker_present")
                    and not signals.get("smoke_script_defect_detected"))
            )
            if not has_app_evidence:
                logger.warning(
                    "[VERIFICATION - EVALUATOR] Downgrading CONFIRMED_APP_BUG -> INCONCLUSIVE: "
                    "no application-level evidence (no 5xx, no failed document load). "
                    f"Model reasoning was: {reason}"
                )
                verdict = "INCONCLUSIVE"
                confidence = min(confidence, 0.45)
                evidence = list(evidence) + [
                    "Downgraded: smoke failure lacked application-level evidence (no 5xx / document load failure).",
                    f"Original (rejected) reasoning: {reason}",
                ]
                reason = (
                    "Verification was inconclusive: the smoke probe failed without producing any "
                    "application-level evidence (no 5xx response and no document load failure), so "
                    "the failure is treated as an automation defect rather than an application bug."
                )
    except Exception as err:
        logger.warning(f"[VERIFICATION - EVALUATOR] LLM evaluation failed ({err}). Using deterministic evidence heuristic.")
        status = signals.get("document_http_status")
        if status is not None and status >= 500:
            verdict = "CONFIRMED_APP_BUG"
            confidence = 0.92
            reason = f"Application returned HTTP {status} for its own document during fresh verification."
            evidence = [f"Document HTTP status: {status}"]
        elif status is not None and status < 400:
            verdict = "NOT_CONFIRMED"
            confidence = 0.88
            reason = f"Application responded HTTP {status} during fresh verification; original failure was an automation defect."
            evidence = [f"Document HTTP status: {status}"]
        else:
            # No usable application evidence — stay conservative rather than inventing a bug.
            verdict = "INCONCLUSIVE"
            confidence = 0.40
            reason = (
                "Fresh verification produced no application-level evidence (no document HTTP status "
                "observed). Treating as an automation defect rather than an application bug."
            )
            evidence = [
                f"Smoke exit code: {smoke_res.get('exit_code')}",
                f"Smoke script defect detected: {signals.get('smoke_script_defect_detected')}",
            ]

    logger.info(
        f"[VERIFICATION - EVALUATOR] Verdict: [{verdict}] (Confidence: {confidence:.2f}) — {reason}"
    )

    return {
        "verdict": verdict,
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence,
    }
