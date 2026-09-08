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
    def create_website(url: str, is_active: bool = True) -> Dict[str, Any]:
        """Creates or updates a website by URL."""
        from storage.local import sanitize_domain
        domain = sanitize_domain(url)
        sql = """
        INSERT INTO websites (url, domain, is_active, created_at, updated_at)
        VALUES (:url, :domain, :is_active, NOW(), NOW())
        ON CONFLICT (url) DO UPDATE SET
            domain = EXCLUDED.domain,
            is_active = EXCLUDED.is_active,
            updated_at = NOW()
        RETURNING id, url, domain, is_active, created_at, updated_at, last_discovered_at;
        """
        with get_connection() as conn:
            row = conn.execute(text(sql), {"url": url, "domain": domain, "is_active": is_active}).mappings().first()
            return dict(row) if row else {}

    @staticmethod
    def upsert_website(domain: str, start_url: str) -> Dict[str, Any]:
        """Upserts a website by start_url."""
        sql = """
        INSERT INTO websites (url, domain, is_active, created_at, updated_at, last_discovered_at)
        VALUES (:url, :domain, TRUE, NOW(), NOW(), NOW())
        ON CONFLICT (url) DO UPDATE SET
            domain = EXCLUDED.domain,
            updated_at = NOW(),
            last_discovered_at = NOW()
        RETURNING id, url, domain, is_active, created_at, updated_at, last_discovered_at;
        """
        with get_connection() as conn:
            row = conn.execute(text(sql), {"url": start_url, "domain": domain}).mappings().first()
            return dict(row) if row else {}

    @staticmethod
    def get_website_by_id(website_id: int) -> Optional[Dict[str, Any]]:
        sql = "SELECT id, url, domain, is_active, created_at, updated_at, last_discovered_at FROM websites WHERE id = :id;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"id": website_id}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def get_website_by_url(url: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT id, url, domain, is_active, created_at, updated_at, last_discovered_at FROM websites WHERE url = :url;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"url": url}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def get_credentials_for_website(website_id: int) -> List[Dict[str, Any]]:
        """
        Returns active accounts for exactly one website, INCLUDING passwords, so the test
        builder/editor can write login flows against real credentials instead of inventing
        placeholders.

        Unlike list_accounts_for_website(), this exposes the password column — it is for
        internal agent use only and must never be returned from an API response.
        """
        sql = """
        SELECT username, password, role, credentials
        FROM accounts
        WHERE website_id = :website_id AND is_active = TRUE
        ORDER BY id ASC;
        """
        with get_connection() as conn:
            rows = conn.execute(text(sql), {"website_id": website_id}).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def resolve_website_id(url: str) -> Optional[int]:
        """
        Resolves `url` to exactly ONE website id: an exact URL match if there is one,
        otherwise the oldest website on the same domain.

        Deliberately returns a single id rather than matching a whole domain, because
        several websites can share a domain (e.g. localhost:5173 and localhost:8000) and
        their accounts must never be mixed together.
        """
        from storage.local import sanitize_domain
        domain = sanitize_domain(url)
        sql = """
        SELECT id FROM websites
        WHERE url = :url OR domain = :domain
        ORDER BY (url = :url) DESC, id ASC
        LIMIT 1;
        """
        with get_connection() as conn:
            row = conn.execute(text(sql), {"url": url, "domain": domain}).mappings().first()
            return row["id"] if row else None

    @staticmethod
    def get_credentials_for_url(url: str) -> List[Dict[str, Any]]:
        """
        Convenience wrapper: resolves `url` to a single website, then returns that one
        website's active accounts. Used when a website_id isn't already in hand (e.g. a
        page discovered behind a login, which has no website row of its own and inherits
        its parent site's credentials).
        """
        website_id = ForgeRepository.resolve_website_id(url)
        if website_id is None:
            return []
        return ForgeRepository.get_credentials_for_website(website_id)

    @staticmethod
    def has_test_for_page(page_url: str) -> bool:
        """Whether any test already exists for this exact page URL — used to avoid
        re-onboarding a page (e.g. a post-login dashboard) that's already been discovered."""
        sql = "SELECT EXISTS(SELECT 1 FROM tests WHERE page_url = :page_url) AS found;"
        with get_connection() as conn:
            return bool(conn.execute(text(sql), {"page_url": page_url}).scalar())

    @staticmethod
    def list_websites(active_only: bool = False) -> List[Dict[str, Any]]:
        if active_only:
            sql = "SELECT id, url, domain, is_active, created_at, updated_at, last_discovered_at FROM websites WHERE is_active = TRUE ORDER BY id ASC;"
        else:
            sql = "SELECT id, url, domain, is_active, created_at, updated_at, last_discovered_at FROM websites ORDER BY id ASC;"
        with get_connection() as conn:
            rows = conn.execute(text(sql)).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def delete_website(website_id: int) -> bool:
        sql = "DELETE FROM websites WHERE id = :id;"
        with get_connection() as conn:
            res = conn.execute(text(sql), {"id": website_id})
            return res.rowcount > 0

    # ==========================================
    # Accounts Operations (One-to-Many with Websites)
    # ==========================================
    @staticmethod
    def create_account(
        website_id: int,
        username: str,
        password: str,
        role: str = "user",
        credentials: Optional[Dict[str, Any]] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Creates or updates an account associated with a website."""
        sql = """
        INSERT INTO accounts (website_id, username, password, role, credentials, is_active, created_at, updated_at)
        VALUES (:website_id, :username, :password, :role, :credentials, :is_active, NOW(), NOW())
        ON CONFLICT (website_id, username) DO UPDATE SET
            password = EXCLUDED.password,
            role = EXCLUDED.role,
            credentials = EXCLUDED.credentials,
            is_active = EXCLUDED.is_active,
            updated_at = NOW()
        RETURNING id, website_id, username, role, credentials, is_active, created_at, updated_at;
        """
        with get_connection() as conn:
            row = conn.execute(
                text(sql),
                {
                    "website_id": website_id,
                    "username": username,
                    "password": password,
                    "role": role,
                    "credentials": json.dumps(credentials or {}),
                    "is_active": is_active,
                },
            ).mappings().first()
            return dict(row) if row else {}

    @staticmethod
    def list_accounts_for_website(
        website_id: int,
        role: Optional[str] = None,
        active_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Lists all accounts belonging to a website, optionally filtered by role."""
        clauses = ["website_id = :website_id"]
        params: Dict[str, Any] = {"website_id": website_id}
        if role:
            clauses.append("role = :role")
            params["role"] = role
        if active_only:
            clauses.append("is_active = TRUE")

        where_str = " AND ".join(clauses)
        sql = f"SELECT id, website_id, username, role, credentials, is_active, created_at, updated_at FROM accounts WHERE {where_str} ORDER BY id ASC;"
        with get_connection() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def get_account(account_id: int) -> Optional[Dict[str, Any]]:
        sql = "SELECT id, website_id, username, role, credentials, is_active, created_at, updated_at FROM accounts WHERE id = :id;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"id": account_id}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def delete_account(account_id: int) -> bool:
        sql = "DELETE FROM accounts WHERE id = :id;"
        with get_connection() as conn:
            res = conn.execute(text(sql), {"id": account_id})
            return res.rowcount > 0

    @staticmethod
    def record_page_discovery(
        domain: str,
        page_info: Dict[str, Any],
        understanding: Optional[Dict[str, Any]] = None,
    ) -> None:
        understanding = understanding or {}
        sql = """
        INSERT INTO pages (domain, url, title, slug, page_type, purpose, primary_actions, state_preconditions, discovered_at)
        VALUES (:domain, :url, :title, :slug, :page_type, :purpose, :primary_actions, :state_preconditions, NOW())
        ON CONFLICT (url) DO UPDATE SET
            title = EXCLUDED.title,
            page_type = COALESCE(EXCLUDED.page_type, pages.page_type),
            purpose = COALESCE(EXCLUDED.purpose, pages.purpose),
            primary_actions = COALESCE(EXCLUDED.primary_actions, pages.primary_actions),
            state_preconditions = COALESCE(EXCLUDED.state_preconditions, pages.state_preconditions),
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
        INSERT INTO elements (forge_id, page_url, tag, element_type, text, selector, bounding_box, discovered_at)
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
        website_id: Optional[int] = None,
        cron_interval_hours: Optional[int] = 24,
        cron_expression: Optional[str] = None,
    ) -> None:
        if not cron_expression and cron_interval_hours:
            if 24 % cron_interval_hours == 0:
                cron_expression = f"0 */{cron_interval_hours} * * *"
            else:
                cron_expression = "0 0 * * *"

        sql = """
        INSERT INTO tests (
            test_id, domain, page_url, title, description, category, priority,
            steps, expected_outcome, script_path, test_code, language, status,
            website_id, cron_interval_hours, cron_expression, updated_at
        ) VALUES (
            :test_id, :domain, :page_url, :title, :description, :category, :priority,
            :steps, :expected_outcome, :script_path, :test_code, :language, 'active',
            :website_id, :cron_interval_hours, :cron_expression, NOW()
        )
        ON CONFLICT (test_id) DO UPDATE SET
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            steps = EXCLUDED.steps,
            expected_outcome = EXCLUDED.expected_outcome,
            script_path = COALESCE(EXCLUDED.script_path, tests.script_path),
            test_code = COALESCE(EXCLUDED.test_code, tests.test_code),
            language = EXCLUDED.language,
            website_id = COALESCE(EXCLUDED.website_id, tests.website_id),
            cron_interval_hours = COALESCE(EXCLUDED.cron_interval_hours, tests.cron_interval_hours),
            cron_expression = COALESCE(EXCLUDED.cron_expression, tests.cron_expression),
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
                    "website_id": website_id,
                    "cron_interval_hours": cron_interval_hours,
                    "cron_expression": cron_expression,
                },
            )

    @staticmethod
    def get_test_by_id(test_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a test record including its script_path and cron schedule."""
        sql = "SELECT * FROM tests WHERE test_id = :test_id;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"test_id": test_id}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def update_test_schedule(
        test_id: str,
        cron_interval_hours: int,
        cron_expression: Optional[str] = None,
    ) -> bool:
        """Updates the cron schedule and execution timing for a test."""
        if not cron_expression and cron_interval_hours:
            if 24 % cron_interval_hours == 0:
                cron_expression = f"0 */{cron_interval_hours} * * *"
            else:
                cron_expression = "0 0 * * *"

        sql = """
        UPDATE tests
        SET cron_interval_hours = :cron_interval_hours,
            cron_expression = :cron_expression,
            updated_at = NOW()
        WHERE test_id = :test_id;
        """
        with get_connection() as conn:
            res = conn.execute(
                text(sql),
                {
                    "test_id": test_id,
                    "cron_interval_hours": cron_interval_hours,
                    "cron_expression": cron_expression,
                },
            )
            return res.rowcount > 0

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
        INSERT INTO test_runs (
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
        INSERT INTO heals (test_id, run_id, attempt, error_snippet, diagnosis, fix_plan, healed_at)
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
    def get_due_tests(domain: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Retrieves active tests that are due for execution based on their cron schedule:
        - Never run (last_run_at IS NULL or next_run_at IS NULL)
        - Scheduled run time has passed (next_run_at <= NOW())
        - Elapsed time since last run exceeds cron_interval_hours
        """
        sql = """
        SELECT * FROM tests
        WHERE status = 'active'
          AND (
            last_run_at IS NULL
            OR next_run_at IS NULL
            OR next_run_at <= NOW()
            OR last_run_at <= NOW() - (COALESCE(cron_interval_hours, 24) * INTERVAL '1 hour')
          )
        """
        params: Dict[str, Any] = {"limit": limit}
        if domain:
            sql += " AND domain = :domain"
            params["domain"] = domain
        sql += " ORDER BY priority DESC, COALESCE(last_run_at, '1970-01-01'::timestamptz) ASC LIMIT :limit;"

        with get_connection() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def update_test_run_timestamps(test_id: Any, cron_interval_hours: Optional[int] = None) -> None:
        """
        Updates last_run_at to NOW() and advances next_run_at by cron_interval_hours.
        """
        sql = """
        UPDATE tests
        SET last_run_at = NOW(),
            next_run_at = NOW() + (COALESCE(:hours, cron_interval_hours, 24) * INTERVAL '1 hour'),
            updated_at = NOW()
        WHERE test_id = :test_id OR id::text = :test_id;
        """
        with get_connection() as conn:
            conn.execute(
                text(sql),
                {
                    "test_id": str(test_id),
                    "hours": cron_interval_hours,
                },
            )


    @staticmethod
    def get_active_tests(domain: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM tests WHERE status = 'active'"
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
        FROM test_runs r
        JOIN tests t ON r.test_id = t.test_id
        WHERE r.status != 'PASSED'
          AND r.executed_at >= NOW() - INTERVAL ':hours HOUR'
        ORDER BY r.test_id, r.executed_at DESC;
        """.replace(":hours", str(int(since_hours)))

        with get_connection() as conn:
            result = conn.execute(text(sql))
            return [dict(r) for r in result.mappings().all()]

    @staticmethod
    def get_runs_for_domain(domain: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns the most recent test executions for every test belonging to a domain."""
        sql = """
        SELECT
            r.run_id, r.test_id, t.title, r.status, r.exit_code,
            r.duration_s, r.error_summary, r.executed_at
        FROM test_runs r
        JOIN tests t ON r.test_id = t.test_id
        WHERE t.domain = :domain
        ORDER BY r.executed_at DESC
        LIMIT :limit;
        """
        with get_connection() as conn:
            result = conn.execute(text(sql), {"domain": domain, "limit": limit})
            return [dict(r) for r in result.mappings().all()]

    @staticmethod
    def search_tests(query_text: str) -> List[Dict[str, Any]]:
        sql = """
        SELECT test_id, domain, title, description, category, priority, script_path, status
        FROM tests
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
        FROM test_runs
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

