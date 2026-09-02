import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import text
from db.connection import get_connection

logger = logging.getLogger("forge.db.repository")


class ForgeRepository:
    """
    Data access repository for Forge. All queries are strictly scoped to the `forge` schema.
    """

    @staticmethod
    def upsert_website(domain: str, start_url: str) -> None:
        sql = """
        INSERT INTO forge.websites (domain, start_url, last_discovered_at)
        VALUES (:domain, :start_url, NOW())
        ON CONFLICT (domain) DO UPDATE SET
            start_url = EXCLUDED.start_url,
            last_discovered_at = NOW();
        """
        with get_connection() as conn:
            conn.execute(text(sql), {"domain": domain, "start_url": start_url})

    @staticmethod
    def record_page_discovery(
        domain: str,
        page_info: Dict[str, Any],
        understanding: Optional[Dict[str, Any]] = None,
    ) -> None:
        understanding = understanding or {}
        sql = """
        INSERT INTO forge.pages (domain, url, title, slug, page_type, purpose, primary_actions, state_preconditions, discovered_at)
        VALUES (:domain, :url, :title, :slug, :page_type, :purpose, :primary_actions, :state_preconditions, NOW())
        ON CONFLICT (url) DO UPDATE SET
            title = EXCLUDED.title,
            page_type = COALESCE(EXCLUDED.page_type, forge.pages.page_type),
            purpose = COALESCE(EXCLUDED.purpose, forge.pages.purpose),
            primary_actions = COALESCE(EXCLUDED.primary_actions, forge.pages.primary_actions),
            state_preconditions = COALESCE(EXCLUDED.state_preconditions, forge.pages.state_preconditions),
            discovered_at = NOW();
        """
        with get_connection() as conn:
            conn.execute(
                text(sql),
                {
                    "domain": domain,
                    "url": page_info.get("url"),
                    "title": page_info.get("title"),
                    "slug": page_info.get("slug"),
                    "page_type": understanding.get("page_type"),
                    "purpose": understanding.get("purpose"),
                    "primary_actions": json.dumps(understanding.get("primary_actions", [])),
                    "state_preconditions": str(understanding.get("state_preconditions", "")),
                },
            )

    @staticmethod
    def record_elements(page_url: str, elements: List[Dict[str, Any]]) -> None:
        if not elements:
            return

        sql = """
        INSERT INTO forge.elements (forge_id, page_url, tag, element_type, text, selector, bounding_box, discovered_at)
        VALUES (:forge_id, :page_url, :tag, :element_type, :text, :selector, :bounding_box, NOW())
        ON CONFLICT (forge_id, page_url) DO UPDATE SET
            text = EXCLUDED.text,
            selector = EXCLUDED.selector,
            bounding_box = EXCLUDED.bounding_box,
            discovered_at = NOW();
        """
        params = [
            {
                "forge_id": el.get("forge_id") or f"el_{i}",
                "page_url": page_url,
                "tag": el.get("tag", "element"),
                "element_type": el.get("type") or el.get("role", "generic"),
                "text": (el.get("text") or "")[:500],
                "selector": el.get("selector", ""),
                "bounding_box": json.dumps(el.get("bounding_box", {})),
            }
            for i, el in enumerate(elements)
        ]

        with get_connection() as conn:
            for p in params:
                conn.execute(text(sql), p)

    @staticmethod
    def save_test(
        test_id: str,
        domain: str,
        page_url: str,
        title: str,
        description: str,
        category: str = "regression",
        priority: str = "medium",
        steps: Optional[List[str]] = None,
        expected_outcome: str = "",
        script_path: Optional[str] = None,
        test_code: Optional[str] = None,
        language: str = "typescript",
    ) -> None:
        sql = """
        INSERT INTO forge.tests (
            test_id, domain, page_url, title, description, category, priority,
            steps, expected_outcome, script_path, test_code, language, status, updated_at
        ) VALUES (
            :test_id, :domain, :page_url, :title, :description, :category, :priority,
            :steps, :expected_outcome, :script_path, :test_code, :language, 'active', NOW()
        )
        ON CONFLICT (test_id) DO UPDATE SET
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            steps = EXCLUDED.steps,
            expected_outcome = EXCLUDED.expected_outcome,
            script_path = COALESCE(EXCLUDED.script_path, forge.tests.script_path),
            test_code = COALESCE(EXCLUDED.test_code, forge.tests.test_code),
            language = EXCLUDED.language,
            status = 'active',
            updated_at = NOW();
        """
        with get_connection() as conn:
            conn.execute(
                text(sql),
                {
                    "test_id": test_id,
                    "domain": domain,
                    "page_url": page_url,
                    "title": title,
                    "description": description,
                    "category": category,
                    "priority": priority,
                    "steps": json.dumps(steps or []),
                    "expected_outcome": expected_outcome,
                    "script_path": script_path,
                    "test_code": test_code,
                    "language": language,
                },
            )

    @staticmethod
    def record_test_run(
        run_id: str,
        test_id: str,
        exit_code: int,
        status: str,
        duration_s: float,
        error_summary: Optional[str] = None,
        stdout: str = "",
        stderr: str = "",
        screenshot_paths: Optional[List[str]] = None,
        trace_path: Optional[str] = None,
    ) -> None:
        sql = """
        INSERT INTO forge.test_runs (
            run_id, test_id, exit_code, status, duration_s,
            error_summary, stdout, stderr, screenshot_paths, trace_path, executed_at
        ) VALUES (
            :run_id, :test_id, :exit_code, :status, :duration_s,
            :error_summary, :stdout, :stderr, :screenshot_paths, :trace_path, NOW()
        );
        """
        with get_connection() as conn:
            conn.execute(
                text(sql),
                {
                    "run_id": run_id,
                    "test_id": test_id,
                    "exit_code": exit_code,
                    "status": status,
                    "duration_s": round(duration_s, 2),
                    "error_summary": error_summary,
                    "stdout": stdout[-3000:],
                    "stderr": stderr[-3000:],
                    "screenshot_paths": json.dumps(screenshot_paths or []),
                    "trace_path": trace_path,
                },
            )

    @staticmethod
    def record_heal(
        test_id: str,
        attempt: int,
        diagnosis: str,
        fix_plan: str,
        error_snippet: str = "",
        run_id: Optional[str] = None,
    ) -> None:
        sql = """
        INSERT INTO forge.heals (test_id, run_id, attempt, error_snippet, diagnosis, fix_plan, healed_at)
        VALUES (:test_id, :run_id, :attempt, :error_snippet, :diagnosis, :fix_plan, NOW());
        """
        with get_connection() as conn:
            conn.execute(
                text(sql),
                {
                    "test_id": test_id,
                    "run_id": run_id,
                    "attempt": attempt,
                    "error_snippet": error_snippet[:500],
                    "diagnosis": diagnosis,
                    "fix_plan": fix_plan,
                },
            )

    @staticmethod
    def get_active_tests(domain: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM forge.tests WHERE status = 'active'"
        params = {}
        if domain:
            sql += " AND domain = :domain"
            params["domain"] = domain
        sql += " ORDER BY priority DESC, id ASC"

        with get_connection() as conn:
            result = conn.execute(text(sql), params)
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def get_failing_tests(since_hours: int = 24) -> List[Dict[str, Any]]:
        sql = """
        SELECT DISTINCT ON (r.test_id)
            r.test_id, t.title, t.domain, r.status, r.error_summary, r.duration_s, r.executed_at
        FROM forge.test_runs r
        JOIN forge.tests t ON r.test_id = t.test_id
        WHERE r.status != 'PASSED'
          AND r.executed_at >= NOW() - INTERVAL ':hours HOUR'
        ORDER BY r.test_id, r.executed_at DESC;
        """.replace(":hours", str(int(since_hours)))

        with get_connection() as conn:
            result = conn.execute(text(sql))
            return [dict(r) for r in result.mappings().all()]

    @staticmethod
    def search_tests(query_text: str) -> List[Dict[str, Any]]:
        sql = """
        SELECT test_id, domain, title, description, category, priority, script_path, status
        FROM forge.tests
        WHERE title ILIKE :q OR description ILIKE :q OR test_id ILIKE :q OR domain ILIKE :q
        ORDER BY priority DESC;
        """
        with get_connection() as conn:
            result = conn.execute(text(sql), {"q": f"%{query_text}%"})
            return [dict(r) for r in result.mappings().all()]

    @staticmethod
    def get_regression_summary(hours: int = 24) -> Dict[str, Any]:
        sql = """
        SELECT
            COUNT(*) as total_runs,
            COUNT(*) FILTER (WHERE status = 'PASSED') as passed_runs,
            COUNT(*) FILTER (WHERE status = 'FAILED') as failed_runs,
            COUNT(*) FILTER (WHERE status = 'HEALED') as healed_runs,
            AVG(duration_s) as avg_duration_s
        FROM forge.test_runs
        WHERE executed_at >= NOW() - INTERVAL ':hours HOUR';
        """.replace(":hours", str(int(hours)))

        with get_connection() as conn:
            row = dict(conn.execute(text(sql)).mappings().one())
            return {
                "total_runs": row.get("total_runs") or 0,
                "passed_runs": row.get("passed_runs") or 0,
                "failed_runs": row.get("failed_runs") or 0,
                "healed_runs": row.get("healed_runs") or 0,
                "avg_duration_s": round(row.get("avg_duration_s") or 0.0, 2),
            }
