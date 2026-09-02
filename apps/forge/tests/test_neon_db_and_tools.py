import os
import pytest
from db.migrations import init_db
from db.repository import ForgeRepository
from agents.tools.test_tools import (
    search_tests_tool,
    get_failing_tests_tool,
    get_regression_summary_tool,
    inspect_test_code_tool,
)
from config import is_headless


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is required to run Neon PostgreSQL tests"
)
def test_neon_db_initialization_and_schema_safety():
    """Verify that the isolated 'forge' schema and tables initialize cleanly."""
    init_db()


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is required to run Neon PostgreSQL tests"
)
def test_forge_repository_crud():
    """Verify safe CRUD operations against the forge schema."""
    init_db()

    # 1. Upsert website
    ForgeRepository.upsert_website("test-neon.com", "https://test-neon.com")

    # 2. Save test scenario
    test_id = "test_neon_smoke_01"
    ForgeRepository.save_test(
        test_id=test_id,
        domain="test-neon.com",
        page_url="https://test-neon.com",
        title="Smoke Test for Neon Postgres",
        description="Asserts title and button renders properly",
        category="smoke",
        priority="high",
        steps=["Open URL", "Assert Title"],
        expected_outcome="Page loads without error",
        language="typescript",
    )

    # 3. Search test
    results = ForgeRepository.search_tests("Smoke Test for Neon")
    assert len(results) >= 1
    assert any(r["test_id"] == test_id for r in results)

    # 4. Record test run
    ForgeRepository.record_test_run(
        run_id="run_test_01",
        test_id=test_id,
        exit_code=0,
        status="PASSED",
        duration_s=1.45,
        stdout="[TEST PASSED]",
    )

    # 5. Verify regression summary
    summary = ForgeRepository.get_regression_summary(hours=24)
    assert summary["total_runs"] >= 1
    assert summary["passed_runs"] >= 1


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL is required to run Neon PostgreSQL tests"
)
def test_langchain_tools_querying_neon():
    """Verify LangChain tools can invoke queries and return formatted JSON."""
    init_db()

    # Tool 1: search_tests_tool
    search_res = search_tests_tool.invoke({"query": "smoke"})
    assert isinstance(search_res, str)

    # Tool 2: get_regression_summary_tool
    summary_res = get_regression_summary_tool.invoke({"hours": 24})
    assert "health_ratio_percent" in summary_res

    # Tool 3: get_failing_tests_tool
    failing_res = get_failing_tests_tool.invoke({"since_hours": 24})
    assert isinstance(failing_res, str)


def test_headless_config(monkeypatch):
    """Verify centralized headless toggle."""
    monkeypatch.setenv("FORGE_HEADLESS", "false")
    assert is_headless(override=None) is False
    assert is_headless(override=True) is True
    assert is_headless(override=False) is False

    monkeypatch.setenv("FORGE_HEADLESS", "true")
    assert is_headless(override=None) is True


def test_python_test_script_runner(tmp_path):
    """Verify that Python test scripts are executed properly via run_test_script."""
    from runner.playwright_runner import run_test_script

    script_file = tmp_path / "test_sample.py"
    script_file.write_text(
        "import sys\nprint('[TEST PASSED] Sample passed')\nsys.exit(0)\n",
        encoding="utf-8"
    )

    result = run_test_script(str(script_file))
    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert "[TEST PASSED]" in result["stdout"]
