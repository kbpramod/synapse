from typing import Any, Dict, List, Optional, TypedDict


class UserJourney(TypedDict, total=False):
    id: str
    type: str  # "SMOKE" | "FLOW"
    intent: str  # Core hypothesis intent, e.g. "A user can log into the application"
    name: str  # Human-readable label
    title: str  # alias/backwards compatibility
    goal: str  # alias/backwards compatibility
    description: str  # alias/backwards compatibility
    priority: str  # "high", "medium", "low"
    category: str  # "capability", "state_transition", "happy_path", "validation", "navigation"
    preconditions: List[str]
    steps: List[str]
    expected: List[str]  # List of expected assertions/states
    expected_outcome: str  # String summary of expected
    evidence: List[str]  # Grounding evidence e.g. ["element:email", "navigation:/dashboard"]
    state_transitions: Optional[List[str]]  # e.g. ["anonymous -> contact modal open"]
    supported_viewports: List[str]  # ["desktop", "tablet", "mobile"]
    viewport: Optional[str]  # Execution-specific target viewport ("desktop", "tablet", "mobile")


# Aliases for backwards compatibility and clarity
TestHypothesis = UserJourney
TestScenario = UserJourney
UserJourney.__test__ = False


class ExecutionResult(TypedDict, total=False):
    exit_code: int
    passed: bool
    stdout: str
    stderr: str
    duration_s: float
    error_summary: Optional[str]
    trace_path: Optional[str]
    screenshot_paths: List[str]


class AnalysisResult(TypedDict, total=False):
    verdict: str  # "PASS" | "NEED_HEAL" | "SUSPECTED_APP_FAILURE" | "APP_BUG" | "FATAL_ERROR"
    reason: str
    failure_type: Optional[str]  # "selector_mismatch", "timeout", "assertion_failure", "server_error", "uncaught_app_exception", etc.
    suggested_fix: Optional[str]


class HealEvent(TypedDict, total=False):
    attempt: int
    test_id: str
    error_snippet: str
    failure_class: str  # "wrong_expectation" | "automation_defect"
    diagnosis: str
    fix_plan: str
    preserve: str


class FailureContext(TypedDict, total=False):
    expected: str
    actual: str
    failed_step: str
    error: Optional[str]
    screenshot: Optional[str]
    trace: Optional[str]
    console_errors: List[str]
    network_errors: List[str]


class VerificationState(TypedDict, total=False):
    application_id: str
    target_url: str
    target_domain: str
    failed_test_id: str
    failure_context: FailureContext
    discovery_data: Optional[Dict[str, Any]]
    page_model: Optional[Dict[str, Any]]
    smoke_test: Optional[Dict[str, Any]]
    smoke_result: Optional[ExecutionResult]
    verdict: Optional[str]  # "CONFIRMED_APP_BUG" | "NOT_CONFIRMED"
    confidence: Optional[float]
    reason: Optional[str]
    evidence: List[str]
    report: Optional[Dict[str, Any]]
    config: Optional[Dict[str, Any]]


class ForgeState(TypedDict, total=False):
    # Target and configuration
    target_url: str
    target_domain: Optional[str]
    config: Dict[str, Any]

    # Discovery & Understanding
    discovery_data: Optional[Dict[str, Any]]
    page_model: Optional[Dict[str, Any]]
    change_detection: Optional[Dict[str, Any]]
    page_understanding: Optional[Dict[str, Any]]

    # Assertions grounded in what discovery actually observed, plus an explicit list of what
    # a single-page snapshot cannot know (see nodes/expectation.py)
    assertable_signals: Optional[Dict[str, Any]]

    # Test Planning & Cron Queue
    test_plan: List[TestScenario]
    test_queue: List[Dict[str, Any]]  # Queue of tests to execute in Cron loop
    current_test_idx: int
    current_test: Optional[TestScenario]

    # Test Scripting & Execution
    run_id: Optional[str]  # stable id for one execution cycle; keys archived script revisions
    test_code: Optional[str]
    test_file_path: Optional[str]
    edit_applied: Optional[bool]  # False when a heal produced byte-identical code
    execution_result: Optional[ExecutionResult]

    # Analysis & Self-Healing Loop
    analysis: Optional[AnalysisResult]
    heal_attempt: int
    max_heal_attempts: int
    healing_history: List[HealEvent]
    healing_plan: Optional[Dict[str, Any]]

    # Standalone Verification Subgraph State & Handoff
    failure_context: Optional[FailureContext]
    verification_state: Optional[VerificationState]
    smoke_result: Optional[ExecutionResult]
    verification_context: Optional[Dict[str, Any]]
    verifier_verdict: Optional[str]  # "CONFIRMED_APP_BUG" | "NOT_CONFIRMED"
    verifier_reason: Optional[str]
    incident_reports: List[Dict[str, Any]]  # Confirmed bug incident logs

    # Aggregate Test Suite Results
    suite_summary: List[Dict[str, Any]]

