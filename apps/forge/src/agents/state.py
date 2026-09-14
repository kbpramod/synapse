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
    failure_url: Optional[str]
    visible_errors: Optional[List[str]]
    error_elements: Optional[List[str]]


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


class NavigationInfo(TypedDict, total=False):
    expected: bool
    auth_redirect: bool
    target_url: str
    failure_url: str


class FailureContext(TypedDict, total=False):
    target_url: str
    failure_url: str
    discovery_url: str
    navigation: NavigationInfo
    page_loaded: bool
    expected: str
    actual: str
    failed_step: str
    error: Optional[str]
    error_summary: Optional[str]
    screenshot: Optional[str]
    trace: Optional[str]
    console_errors: List[str]
    network_errors: List[str]
    visible_errors: List[str]
    error_elements: List[str]


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


class ActionStep(TypedDict, total=False):
    step_id: int
    action_type: str  # "goto" | "click" | "fill" | "select" | "press" | "scroll" | "wait"
    selector: Optional[str]
    value: Optional[str]
    description: str


class ActionSpec(TypedDict, total=False):
    test_id: str
    target_url: str
    viewport: Dict[str, int]
    steps: List[ActionStep]
    storage_state_path: Optional[str]
    timeout_ms: int


class DOMDelta(TypedDict, total=False):
    added_elements: List[Dict[str, Any]]
    removed_elements: List[Dict[str, Any]]
    changed_text: List[Dict[str, Any]]
    current_headings: List[str]
    current_forms: List[Dict[str, Any]]
    visible_alerts: List[str]


class PostActionResult(TypedDict, total=False):
    navigation: Dict[str, Any]  # previous_url, result_url, url_changed (bool)
    dom_delta: DOMDelta
    visual: Dict[str, Any]  # screenshot_path
    network: Dict[str, Any]  # requests, responses
    console: Dict[str, Any]  # errors, warnings
    page_metadata: Dict[str, Any]  # title, path, slug
    duration_s: float


class ExpectationItem(TypedDict, total=False):
    type: str  # "url_change" | "element_absence" | "visible_element" | "network_status" | "text_match"
    target: str
    expected_value: Any
    confidence: float
    evidence: List[str]
    code: str  # Valid Playwright python assertion string


class ExpectationSpec(TypedDict, total=False):
    test_id: str
    journey_intent: str
    expectations: List[ExpectationItem]
    summary: str


class TestProvenance(TypedDict, total=False):
    test_id: str
    intent: str
    action_provenance: Dict[str, Any]
    expectation_provenance: List[Dict[str, Any]]


class ForgeState(TypedDict, total=False):
    # Target and configuration
    target_url: str
    target_domain: Optional[str]
    website_id: Optional[int]
    page_id: Optional[int]
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

    # Decoupled Action Lifecycle
    action_spec: Optional[ActionSpec]
    action_code: Optional[str]
    action_file_path: Optional[str]
    action_result: Optional[ExecutionResult]
    action_heal_attempt: int
    max_action_heals: int

    # Post-Action Discovery & Delta
    post_action_result: Optional[PostActionResult]

    # Decoupled Expectation Lifecycle
    expectation_spec: Optional[ExpectationSpec]
    expectation_heal_attempt: int
    max_expectation_heals: int

    # Correctness Evaluation (5-Way Verdict)
    # "CORRECT" | "ACTION_DEFECT" | "EXPECTATION_DEFECT" | "APP_BUG" | "INCONCLUSIVE"
    correctness_verdict: Optional[str]
    correctness_reason: Optional[str]
    correctness_evaluation: Optional[Dict[str, Any]]
    verification_result: Optional[Dict[str, Any]]

    # Final Assembled Test Scripting & Artifact Directory
    run_id: Optional[str]  # stable id for one execution cycle; keys archived script revisions
    test_code: Optional[str]
    test_file_path: Optional[str]
    test_provenance: Optional[TestProvenance]
    test_artifacts: Optional[Dict[str, Any]]
    edit_status: Optional[str]  # "not_attempted" | "applied" | "no_change" | "failed"
    edit_applied: Optional[bool]  # False when a heal produced byte-identical code
    execution_result: Optional[ExecutionResult]

    # Legacy / Cron Analysis & Self-Healing Loop
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


