import json
import logging
from typing import Optional
from langchain_core.tools import tool
from db.repository import ForgeRepository

logger = logging.getLogger("forge.agent.tools")


@tool
def search_tests_tool(query: str) -> str:
    """
    Search the Forge test catalog in Neon PostgreSQL by keyword, feature, or page name.
    Args:
        query: The search term (e.g. 'smoke', 'login', 'checkout', 'pricing')
    """
    try:
        results = ForgeRepository.search_tests(query)
        if not results:
            return f"No tests found matching '{query}'."
        return json.dumps(results, indent=2, default=str)
    except Exception as e:
        return f"Error searching tests: {e}"


@tool
def get_failing_tests_tool(since_hours: int = 24) -> str:
    """
    Retrieves all failing tests from recent regression test runs.
    Args:
        since_hours: Number of hours back to inspect (default: 24)
    """
    try:
        results = ForgeRepository.get_failing_tests(since_hours=since_hours)
        if not results:
            return "Great news! No failing tests detected in the requested time window."
        return json.dumps(results, indent=2, default=str)
    except Exception as e:
        return f"Error retrieving failing tests: {e}"


@tool
def get_regression_summary_tool(hours: int = 24) -> str:
    """
    Returns high-level regression testing metrics (total runs, pass/fail counts, health ratio).
    Args:
        hours: Inspection window in hours (default: 24)
    """
    try:
        summary = ForgeRepository.get_regression_summary(hours=hours)
        total = summary.get("total_runs", 0)
        passed = summary.get("passed_runs", 0)
        ratio = round((passed / total) * 100, 1) if total else 100.0
        summary["health_ratio_percent"] = ratio
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error fetching regression summary: {e}"


@tool
def inspect_test_code_tool(test_id: str) -> str:
    """
    Retrieves the generated test script, steps, and expected outcome for a given test ID.
    Args:
        test_id: Unique test identifier (e.g., 'test_page_smoke')
    """
    try:
        tests = ForgeRepository.search_tests(test_id)
        matching = [t for t in tests if t.get("test_id") == test_id]
        if not matching:
            return f"Test with id '{test_id}' not found."
        test = matching[0]
        return json.dumps(test, indent=2, default=str)
    except Exception as e:
        return f"Error inspecting test: {e}"


FORGE_TEST_TOOLS = [
    search_tests_tool,
    get_failing_tests_tool,
    get_regression_summary_tool,
    inspect_test_code_tool,
]
