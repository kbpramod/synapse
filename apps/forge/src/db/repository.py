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
    def create_website(
        url: str,
        is_active: bool = True,
        app_name: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates or updates a website by URL, storing app_name and environment."""
        from storage.local import sanitize_domain
        domain = sanitize_domain(url)
        sql = """
        INSERT INTO websites (url, domain, app_name, environment, is_active, created_at, updated_at)
        VALUES (:url, :domain, :app_name, :environment, :is_active, NOW(), NOW())
        ON CONFLICT (url) DO UPDATE SET
            domain = EXCLUDED.domain,
            app_name = COALESCE(EXCLUDED.app_name, websites.app_name),
            environment = COALESCE(EXCLUDED.environment, websites.environment),
            is_active = EXCLUDED.is_active,
            updated_at = NOW()
        RETURNING id, url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at;
        """
        with get_connection() as conn:
            row = conn.execute(
                text(sql),
                {
                    "url": url,
                    "domain": domain,
                    "app_name": app_name,
                    "environment": environment,
                    "is_active": is_active,
                },
            ).mappings().first()
            return dict(row) if row else {}

    @staticmethod
    def upsert_website(
        domain: str,
        start_url: str,
        app_name: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upserts a website by start_url."""
        sql = """
        INSERT INTO websites (url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at)
        VALUES (:url, :domain, :app_name, :environment, TRUE, NOW(), NOW(), NOW())
        ON CONFLICT (url) DO UPDATE SET
            domain = EXCLUDED.domain,
            app_name = COALESCE(EXCLUDED.app_name, websites.app_name),
            environment = COALESCE(EXCLUDED.environment, websites.environment),
            updated_at = NOW(),
            last_discovered_at = NOW()
        RETURNING id, url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at;
        """
        with get_connection() as conn:
            row = conn.execute(
                text(sql),
                {
                    "url": start_url,
                    "domain": domain,
                    "app_name": app_name,
                    "environment": environment,
                },
            ).mappings().first()
            return dict(row) if row else {}

    @staticmethod
    def get_website_by_id(website_id: int) -> Optional[Dict[str, Any]]:
        sql = "SELECT id, url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at FROM websites WHERE id = :id;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"id": website_id}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def get_website_by_url(url: str) -> Optional[Dict[str, Any]]:
        sql = "SELECT id, url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at FROM websites WHERE url = :url;"
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
            sql = "SELECT id, url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at FROM websites WHERE is_active = TRUE ORDER BY id ASC;"
        else:
            sql = "SELECT id, url, domain, app_name, environment, is_active, created_at, updated_at, last_discovered_at FROM websites ORDER BY id ASC;"
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
        website_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        understanding = understanding or {}
        sql = """
        INSERT INTO pages (website_id, domain, url, title, slug, page_type, purpose, primary_actions, state_preconditions, discovered_at)
        VALUES (:website_id, :domain, :url, :title, :slug, :page_type, :purpose, :primary_actions, :state_preconditions, NOW())
        ON CONFLICT (url) DO UPDATE SET
            website_id = COALESCE(EXCLUDED.website_id, pages.website_id),
            title = EXCLUDED.title,
            slug = COALESCE(EXCLUDED.slug, pages.slug),
            page_type = COALESCE(EXCLUDED.page_type, pages.page_type),
            purpose = COALESCE(EXCLUDED.purpose, pages.purpose),
            primary_actions = COALESCE(EXCLUDED.primary_actions, pages.primary_actions),
            state_preconditions = COALESCE(EXCLUDED.state_preconditions, pages.state_preconditions),
            discovered_at = NOW()
        RETURNING id, website_id, domain, url, title, slug, page_type, purpose, primary_actions, state_preconditions, discovered_at;
        """
        with get_connection() as conn:
            row = conn.execute(
                text(sql),
                {
                    "website_id": website_id,
                    "domain": domain,
                    "url": page_info.get("url"),
                    "title": page_info.get("title"),
                    "slug": page_info.get("slug"),
                    "page_type": understanding.get("page_type"),
                    "purpose": understanding.get("purpose"),
                    "primary_actions": json.dumps(understanding.get("primary_actions", [])),
                    "state_preconditions": str(understanding.get("state_preconditions", "")),
                },
            ).mappings().first()
            return dict(row) if row else {}

    @staticmethod
    def get_page_by_url(url: str) -> Optional[Dict[str, Any]]:
        """Retrieves a discovered page by its canonical URL."""
        sql = "SELECT * FROM pages WHERE url = :url;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"url": url}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def get_page_by_id(page_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a discovered page by its primary key ID."""
        sql = "SELECT * FROM pages WHERE id = :id;"
        with get_connection() as conn:
            row = conn.execute(text(sql), {"id": page_id}).mappings().first()
            return dict(row) if row else None

    @staticmethod
    def list_pages_for_website(website_id: int) -> List[Dict[str, Any]]:
        """Lists all discovered pages for a website with their associated test count."""
        sql = """
        SELECT p.*, COUNT(t.id) AS test_count
        FROM pages p
        LEFT JOIN tests t ON t.page_id = p.id
        WHERE p.website_id = :website_id
        GROUP BY p.id
        ORDER BY p.discovered_at ASC;
        """
        with get_connection() as conn:
            rows = conn.execute(text(sql), {"website_id": website_id}).mappings().fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def get_tests_for_page(page_id: int) -> List[Dict[str, Any]]:
        """Returns all planned and generated tests linked to a specific page."""
        sql = "SELECT * FROM tests WHERE page_id = :page_id ORDER BY created_at ASC;"
        with get_connection() as conn:
            rows = conn.execute(text(sql), {"page_id": page_id}).mappings().fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def get_tests_for_page_url(page_url: str) -> List[Dict[str, Any]]:
        """Returns all tests linked to a specific page URL or page record."""
        sql = """
        SELECT t.*
        FROM tests t
        LEFT JOIN pages p ON t.page_id = p.id
        WHERE t.page_url = :page_url OR p.url = :page_url
        ORDER BY t.created_at ASC;
        """
        with get_connection() as conn:
            rows = conn.execute(text(sql), {"page_url": page_url}).mappings().fetchall()
            return [dict(r) for r in rows]

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
        page_id: Optional[int] = None,
        cron_interval_hours: Optional[int] = 24,
        cron_expression: Optional[str] = None,
        enabled: bool = True,
        timezone: str = "UTC",
        schedule_offset_seconds: int = 0,
    ) -> None:
        from scheduler.spacing import compute_next_run

        if not cron_expression and cron_interval_hours:
            if 24 % cron_interval_hours == 0:
                cron_expression = f"0 */{cron_interval_hours} * * *"
            else:
                cron_expression = "0 0 * * *"
        elif not cron_expression:
            cron_expression = "0 0 * * *"

        initial_next_run = compute_next_run(
            cron_expression, schedule_offset_seconds=schedule_offset_seconds, tz_name=timezone
        )

        # Auto-resolve page_id from page_url if not explicitly provided
        if page_id is None and page_url:
            resolved_page = ForgeRepository.get_page_by_url(page_url)
            if resolved_page:
                page_id = resolved_page["id"]

        sql = """
        INSERT INTO tests (
            test_id, domain, page_url, title, description, category, priority,
            steps, expected_outcome, script_path, test_code, language, status,
            website_id, page_id, cron_interval_hours, cron_expression,
            enabled, timezone, schedule_offset_seconds, next_run_at, updated_at
        ) VALUES (
            :test_id, :domain, :page_url, :title, :description, :category, :priority,
            :steps, :expected_outcome, :script_path, :test_code, :language, 'active',
            :website_id, :page_id, :cron_interval_hours, :cron_expression,
            :enabled, :timezone, :schedule_offset_seconds, :next_run_at, NOW()
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
            page_id = COALESCE(EXCLUDED.page_id, tests.page_id),
            cron_interval_hours = COALESCE(EXCLUDED.cron_interval_hours, tests.cron_interval_hours),
            cron_expression = COALESCE(EXCLUDED.cron_expression, tests.cron_expression),
            enabled = EXCLUDED.enabled,
            timezone = EXCLUDED.timezone,
            schedule_offset_seconds = EXCLUDED.schedule_offset_seconds,
            next_run_at = COALESCE(tests.next_run_at, EXCLUDED.next_run_at),
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
                    "page_id": page_id,
                    "cron_interval_hours": cron_interval_hours,
                    "cron_expression": cron_expression,
                    "enabled": enabled,
                    "timezone": timezone,
                    "schedule_offset_seconds": schedule_offset_seconds,
                    "next_run_at": initial_next_run,
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

    # ==========================================
    # Scalable Distributed Scheduler Operations
    # ==========================================
    @staticmethod
    def claim_due_tests(batch_size: int = 50) -> List[Dict[str, Any]]:
        """
        Atomically selects and locks tests currently due for execution using
        PostgreSQL `FOR UPDATE SKIP LOCKED`.
        Inside the same transaction, recalculates and advances their `next_run_at`
        into the future based on their cron frequency and de-bunching offset.
        Guarantees that multiple concurrent scheduler/API nodes will NEVER claim
        or execute the same test twice.
        """
        from scheduler.spacing import compute_next_run

        select_sql = """
        SELECT t.id, t.test_id, t.website_id, t.domain, t.page_url, t.title,
               t.category, t.priority, t.script_path, t.test_code, t.language,
               t.cron_expression, t.timezone, t.schedule_offset_seconds,
               COALESCE(w.concurrency_limit, 2) AS concurrency_limit,
               w.app_name, w.environment
        FROM tests t
        LEFT JOIN websites w ON t.website_id = w.id
        WHERE t.enabled = TRUE
          AND t.status = 'active'
          AND (t.next_run_at <= NOW() OR t.next_run_at IS NULL)
        ORDER BY
          CASE t.priority
            WHEN 'critical' THEN 1
            WHEN 'high' THEN 2
            WHEN 'medium' THEN 3
            ELSE 4
          END ASC,
          COALESCE(t.next_run_at, '1970-01-01'::timestamptz) ASC
        LIMIT :limit
        FOR UPDATE OF t SKIP LOCKED;
        """

        update_sql = """
        UPDATE tests
        SET next_run_at = :next_run_at,
            last_run_at = NOW(),
            updated_at = NOW()
        WHERE id = :id;
        """

        claimed = []
        now_utc = datetime.now(timezone.utc)

        with get_connection() as conn:
            rows = conn.execute(text(select_sql), {"limit": batch_size}).mappings().all()
            if not rows:
                return []

            for r in rows:
                test_dict = dict(r)
                cron_expr = test_dict.get("cron_expression") or "0 0 * * *"
                tz_name = test_dict.get("timezone") or "UTC"
                offset_s = test_dict.get("schedule_offset_seconds") or 0

                next_run = compute_next_run(cron_expr, now_utc, offset_s, tz_name)
                conn.execute(
                    text(update_sql),
                    {
                        "id": test_dict["id"],
                        "next_run_at": next_run,
                    },
                )
                test_dict["claimed_at"] = now_utc.isoformat()
                test_dict["next_run_at"] = next_run.isoformat()
                claimed.append(test_dict)

        return claimed

    @staticmethod
    def get_upcoming_tests(limit: int = 50, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns the upcoming test schedules sorted by next_run_at."""
        sql = """
        SELECT t.id, t.test_id, t.website_id, t.domain, t.page_url, t.title,
               t.category, t.priority, t.cron_expression, t.timezone,
               t.schedule_offset_seconds, t.enabled, t.status,
               t.last_run_at, t.next_run_at,
               w.app_name, w.environment,
               COALESCE(w.concurrency_limit, 2) AS concurrency_limit
        FROM tests t
        LEFT JOIN websites w ON t.website_id = w.id
        WHERE t.enabled = TRUE
          AND t.status = 'active'
        """
        params: Dict[str, Any] = {"limit": limit}
        if domain:
            sql += " AND t.domain = :domain"
            params["domain"] = domain
        sql += " ORDER BY t.next_run_at ASC NULLS FIRST LIMIT :limit;"

        with get_connection() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]

    @staticmethod
    def distribute_tests_schedule(website_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Finds active, enabled tests and groups them by frequency window (cron_expression, timezone).
        Calculates window spacing (W / N) and updates schedule_offset_seconds and next_run_at
        so executions are evenly staggered across time windows instead of spiking at cron boundaries.
        """
        from scheduler.spacing import distribute_cohort

        clauses = ["status = 'active'", "enabled = TRUE"]
        params: Dict[str, Any] = {}
        if website_id:
            clauses.append("website_id = :website_id")
            params["website_id"] = website_id

        where_sql = " AND ".join(clauses)
        sql = f"""
        SELECT id, test_id, website_id, cron_expression, timezone, priority
        FROM tests
        WHERE {where_sql}
        ORDER BY id ASC;
        """

        with get_connection() as conn:
            rows = conn.execute(text(sql), params).mappings().all()
            tests = [dict(r) for r in rows]

            cohorts: Dict[Any, List[Dict[str, Any]]] = {}
            for t in tests:
                cron = t.get("cron_expression") or "0 0 * * *"
                tz = t.get("timezone") or "UTC"
                cohorts.setdefault((cron, tz), []).append(t)

            now_utc = datetime.now(timezone.utc)
            total_updated = 0
            update_sql = """
            UPDATE tests
            SET schedule_offset_seconds = :offset,
                next_run_at = :next_run,
                updated_at = NOW()
            WHERE id = :id;
            """

            for (cron, tz), cohort_tests in cohorts.items():
                distributed = distribute_cohort(cohort_tests, base_time=now_utc, tz_name=tz)
                for item in distributed:
                    conn.execute(
                        text(update_sql),
                        {
                            "id": item["id"],
                            "offset": item["schedule_offset_seconds"],
                            "next_run": item["next_run_at"],
                        },
                    )
                    total_updated += 1

            return {
                "total_tests": len(tests),
                "cohorts_count": len(cohorts),
                "updated_count": total_updated,
            }

    @staticmethod
    def update_test_scheduler_config(
        test_id: str,
        enabled: Optional[bool] = None,
        cron_expression: Optional[str] = None,
        timezone: Optional[str] = None,
        priority: Optional[str] = None,
        schedule_offset_seconds: Optional[int] = None,
    ) -> bool:
        updates = []
        params: Dict[str, Any] = {"test_id": test_id}
        if enabled is not None:
            updates.append("enabled = :enabled")
            params["enabled"] = enabled
        if cron_expression is not None:
            updates.append("cron_expression = :cron_expression")
            params["cron_expression"] = cron_expression
        if timezone is not None:
            updates.append("timezone = :timezone")
            params["timezone"] = timezone
        if priority is not None:
            updates.append("priority = :priority")
            params["priority"] = priority
        if schedule_offset_seconds is not None:
            updates.append("schedule_offset_seconds = :offset")
            params["offset"] = schedule_offset_seconds

        if not updates:
            return False

        updates.append("updated_at = NOW()")
        sql = f"UPDATE tests SET {', '.join(updates)} WHERE test_id = :test_id;"
        with get_connection() as conn:
            res = conn.execute(text(sql), params)
            return res.rowcount > 0

