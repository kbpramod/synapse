import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Isolated SQLite in-memory engine for unit testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


from src.db.database import Base
from src.main import app
from db.session import get_db as db_get_db
from src.db.session import get_db as src_get_db
app.dependency_overrides[db_get_db] = override_get_db
app.dependency_overrides[src_get_db] = override_get_db

from models.user import User
from models.organization import Organization
from models.organization_member import OrganizationMember
import api.deps
import src.api.deps
import api.organizations

# Clear remote startup DB hook during isolated unit testing
app.router.on_startup.clear()


class TestOrganizationsEndpoints(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=test_engine)
        app.dependency_overrides[db_get_db] = override_get_db
        app.dependency_overrides[src_get_db] = override_get_db
        self.db = TestingSessionLocal()
        # Seed test organization and user
        self.test_org = Organization(id="org_test_123", name="Acme Technologies Inc")
        self.db.add(self.test_org)
        self.db.commit()

        self.client = TestClient(app)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=test_engine)
        # Reset dependency overrides
        app.dependency_overrides.pop(api.deps.require_auth, None)
        app.dependency_overrides.pop(src.api.deps.require_auth, None)
        app.dependency_overrides.pop(api.deps.get_current_user, None)
        app.dependency_overrides.pop(src.api.deps.get_current_user, None)

    def _mock_auth(self, role: str = "admin", org_id: str = "org_test_123"):
        user = User(
            id="user-internal-uuid",
            clerk_user_id="user_clerk_123",
            email="admin@acme.com",
            name="Admin User",
            is_verified=True
        )
        user.is_verified = True
        user.organization_id = org_id
        user.organization_role = role
        user.organization_slug = "acme-technologies"
        user.current_organization = self.test_org

        for dep_module in (api.deps, src.api.deps):
            if hasattr(dep_module, "require_auth"):
                app.dependency_overrides[dep_module.require_auth] = lambda: user
            if hasattr(dep_module, "get_current_user"):
                app.dependency_overrides[dep_module.get_current_user] = lambda: user
        return user

    def test_get_current_organization_success(self):
        """Test GET /api/v1/organizations/current returns active org metadata."""
        self._mock_auth(role="admin")

        with patch.object(api.organizations.clerk_service, "get_organization") as mock_get_org:
            mock_get_org.return_value = {
                "id": "org_test_123",
                "name": "Acme Technologies (From Clerk)",
                "slug": "acme-tech"
            }

            resp = self.client.get("/api/v1/organizations/current")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["id"], "org_test_123")
            self.assertEqual(data["name"], "Acme Technologies (From Clerk)")
            self.assertEqual(data["slug"], "acme-tech")
            self.assertEqual(data["role"], "admin")

            # Also verify alias /organizations/current
            alias_resp = self.client.get("/organizations/current")
            self.assertEqual(alias_resp.status_code, 200)
            self.assertEqual(alias_resp.json()["id"], "org_test_123")

    def test_get_current_organization_fallback_to_local_db(self):
        """Test GET /api/v1/organizations/current falls back to DB if Clerk get fails."""
        self._mock_auth(role="member")

        with patch.object(api.organizations.clerk_service, "get_organization", return_value=None):
            resp = self.client.get("/api/v1/organizations/current")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["id"], "org_test_123")
            self.assertEqual(data["name"], "Acme Technologies Inc")
            self.assertEqual(data["role"], "member")

    def test_get_current_organization_none_when_no_org(self):
        """Test GET /api/v1/organizations/current returns null gracefully if no active org."""
        user = self._mock_auth(role="member", org_id=None)
        user.organization_id = None
        user.current_organization = None
        resp = self.client.get("/api/v1/organizations/current")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json())

    def test_basic_apis_allowed_without_organization(self):
        """Test basic endpoints like /api/user/me work when user has no active organization."""
        user = self._mock_auth(role="member", org_id=None)
        user.organization_id = None
        user.current_organization = None
        resp = self.client.get("/api/user/me")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["email"], "admin@acme.com")

    def test_get_current_organization_members_success(self):
        """Test GET /api/v1/organizations/current/members returns member list from Clerk."""
        self._mock_auth(role="member")

        mocked_members = [
            {
                "user_id": "user_clerk_1",
                "name": "Alice Smith",
                "email": "alice@acme.com",
                "role": "org:admin",
                "image_url": "https://img.clerk.com/alice.png"
            },
            {
                "user_id": "user_clerk_2",
                "name": "Bob Jones",
                "email": "bob@acme.com",
                "role": "org:member",
                "image_url": None
            }
        ]

        with patch.object(api.organizations.clerk_service, "list_organization_members", return_value=mocked_members):
            resp = self.client.get("/api/v1/organizations/current/members")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("members", data)
            self.assertEqual(len(data["members"]), 2)
            self.assertEqual(data["members"][0]["name"], "Alice Smith")
            self.assertEqual(data["members"][0]["role"], "org:admin")
            self.assertEqual(data["members"][1]["email"], "bob@acme.com")

    def test_invite_member_as_admin_success(self):
        """Test POST /api/v1/organizations/current/members/invite succeeds for admin."""
        self._mock_auth(role="admin")

        mock_invitation = {
            "id": "inv_999",
            "email": "charlie@acme.com",
            "role": "org:member",
            "status": "pending"
        }

        with patch.object(api.organizations.clerk_service, "invite_member", return_value=mock_invitation) as mock_invite:
            payload = {
                "email": "charlie@acme.com",
                "role": "org:member"
            }
            resp = self.client.post("/api/v1/organizations/current/members/invite", json=payload)
            self.assertEqual(resp.status_code, 201)
            data = resp.json()
            self.assertEqual(data["id"], "inv_999")
            self.assertEqual(data["email"], "charlie@acme.com")
            self.assertEqual(data["role"], "org:member")
            self.assertEqual(data["status"], "pending")

            mock_invite.assert_called_once_with(
                organization_id="org_test_123",
                email_address="charlie@acme.com",
                role="org:member",
                inviter_user_id="user_clerk_123"
            )

    def test_invite_member_as_non_admin_forbidden(self):
        """Test POST /api/v1/organizations/current/members/invite returns 403 for non-admin."""
        self._mock_auth(role="member")

        payload = {
            "email": "unauthorized@acme.com",
            "role": "org:member"
        }
        resp = self.client.post("/api/v1/organizations/current/members/invite", json=payload)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Admin privileges required", resp.json()["error"]["message"])

    def test_endpoints_require_authentication(self):
        """Test all organization endpoints return 401 when unauthenticated."""
        # Ensure no auth override
        for dep_module in (api.deps, src.api.deps):
            app.dependency_overrides.pop(dep_module.require_auth, None)
            app.dependency_overrides.pop(dep_module.get_current_user, None)

        resp1 = self.client.get("/api/v1/organizations/current")
        self.assertEqual(resp1.status_code, 401)

        resp2 = self.client.get("/api/v1/organizations/current/members")
        self.assertEqual(resp2.status_code, 401)

        resp3 = self.client.post("/api/v1/organizations/current/members/invite", json={"email": "a@b.com"})
        self.assertEqual(resp3.status_code, 401)

    def test_sync_current_organization_endpoint(self):
        """Test POST /api/v1/organizations/current/sync triggers DB sync."""
        self._mock_auth(role="admin")

        with patch.object(api.organizations.clerk_service, "sync_organization_and_members") as mock_sync:
            mock_sync.return_value = {
                "organization_id": "org_test_123",
                "organization_name": "Acme Technologies",
                "slug": "acme-tech",
                "members_synced_count": 3
            }

            resp = self.client.post("/api/v1/organizations/current/sync")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["data"]["members_synced_count"], 3)
            mock_sync.assert_called_once()

    def test_sync_all_organizations_endpoint(self):
        """Test POST /api/v1/organizations/sync-all triggers global Clerk sync for admin."""
        self._mock_auth(role="admin")

        with patch.object(api.organizations.clerk_service, "sync_all_organizations_and_members") as mock_sync_all:
            mock_sync_all.return_value = {
                "organizations_count": 2,
                "total_memberships_synced": 5,
                "organizations": []
            }

            resp = self.client.post("/api/v1/organizations/sync-all")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["data"]["organizations_count"], 2)
            mock_sync_all.assert_called_once()

    def test_sync_all_organizations_forbidden_for_member(self):
        """Test POST /api/v1/organizations/sync-all rejects non-admin."""
        self._mock_auth(role="member")
        resp = self.client.post("/api/v1/organizations/sync-all")
        self.assertEqual(resp.status_code, 403)

    def test_sync_organization_and_members_db_persistence(self):
        """Test clerk_service.sync_organization_and_members persists directly into database."""
        from src.services.clerk_service import clerk_service

        with patch.object(clerk_service, "get_organization") as mock_get_org, \
             patch.object(clerk_service, "list_organization_members") as mock_list_members:

            mock_get_org.return_value = {
                "id": "org_sync_demo",
                "name": "Demo Sync Org",
                "slug": "demo-sync"
            }
            mock_list_members.return_value = [
                {
                    "user_id": "user_sync_1",
                    "name": "Sync User One",
                    "email": "one@demo.com",
                    "role": "org:admin"
                },
                {
                    "user_id": "user_sync_2",
                    "name": "Sync User Two",
                    "email": "two@demo.com",
                    "role": "org:member"
                }
            ]

            result = clerk_service.sync_organization_and_members(self.db, "org_sync_demo")
            self.assertEqual(result["organization_name"], "Demo Sync Org")
            self.assertEqual(result["members_synced_count"], 2)

            # Verify persisted in database
            db_org = self.db.query(Organization).filter(Organization.id == "org_sync_demo").first()
            self.assertIsNotNone(db_org)
            self.assertEqual(db_org.name, "Demo Sync Org")

            db_members = self.db.query(OrganizationMember).filter(
                OrganizationMember.organization_id == "org_sync_demo"
            ).all()
            self.assertEqual(len(db_members), 2)

            user_ids = [m.user_id for m in db_members]
            db_users = self.db.query(User).filter(User.id.in_(user_ids)).all()
            self.assertEqual(len(db_users), 2)
            emails = {u.email for u in db_users}
            self.assertIn("one@demo.com", emails)
            self.assertIn("two@demo.com", emails)


if __name__ == "__main__":
    unittest.main()
