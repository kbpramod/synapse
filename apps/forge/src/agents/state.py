from typing import Any, Dict, List, Optional, TypedDict


class TestScenario(TypedDict, total=False):
    id: str
    title: str
    description: str
    priority: str  # "high", "medium", "low"
    category: str  # "smoke", "happy_path", "validation", "navigation", "edge_case"
    steps: List[str]
    expected_outcome: str


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
    verdict: str  # "PASS", "NEED_HEAL", "APP_BUG", "FATAL_ERROR"
    reason: str
    failure_type: Optional[str]  # "selector_mismatch", "timeout", "assertion_failure", "server_error", etc.
    suggested_fix: Optional[str]


class HealEvent(TypedDict, total=False):
    attempt: int
    test_id: str
    error_snippet: str
    diagnosis: str
    fix_plan: str


class ForgeState(TypedDict, total=False):
    # Target and configuration
    target_url: str
    config: Dict[str, Any]

    # Discovery & Understanding
    discovery_data: Optional[Dict[str, Any]]
    page_understanding: Optional[Dict[str, Any]]

    # Test Planning
    test_plan: List[TestScenario]
    current_test_idx: int
    current_test: Optional[TestScenario]

    # Test Scripting & Execution
    test_code: Optional[str]
    test_file_path: Optional[str]
    execution_result: Optional[ExecutionResult]

    # Analysis & Self-Healing Loop
    analysis: Optional[AnalysisResult]
    heal_attempt: int
    max_heal_attempts: int
    healing_history: List[HealEvent]
    healing_plan: Optional[Dict[str, Any]]

    # Aggregate Test Suite Results
    suite_summary: List[Dict[str, Any]]
