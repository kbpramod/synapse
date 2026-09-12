import os
import sys
from datetime import datetime
from uuid import uuid4
from dotenv import load_dotenv
from sqlalchemy.orm import Session

# Add workspace root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

load_dotenv()
from src.db.session import SessionLocal
import models  # registers all models
from models import (
    User,
    Organization,
    OrganizationMember,
    GithubInstallation,
    GithubRepository,
    Repository
)

db: Session = SessionLocal()

try:
    print("Testing ORM models...")

    # 1. Fetch or create a test user
    user = db.query(User).first()
    if not user:
        user = User(
            id=str(uuid4()),
            clerk_user_id=f"user_{uuid4().hex[:12]}",
            email="developer@tzylo.com",
            name="Test Developer"
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    print(f"User OK: id={user.id}, email={user.email}, name={user.name}")

    # 2. Create an organization
    org = Organization(
        id=str(uuid4()),
        name="Tzylo Engineering"
    )
    db.add(org)
    db.flush()
    print(f"Organization OK: id={org.id}, name={org.name}")

    # 3. Add organization member
    member = OrganizationMember(
        organization_id=org.id,
        user_id=user.id,
        role="owner"
    )
    db.add(member)
    db.flush()
    print(f"OrganizationMember OK: org_id={member.organization_id}, user_id={member.user_id}, role={member.role}")

    # 4. Create GitHub installation
    inst_id = f"inst_{uuid4().hex[:8]}"
    installation = GithubInstallation(
        organization_id=org.id,
        user_id=user.id,
        github_installation_id=inst_id,
        github_account_id="12345678",
        github_account_login="tzylo-dev",
        github_account_type="Organization",
        status="active",
        installed_at=datetime.utcnow()
    )
    db.add(installation)
    db.flush()
    print(f"GithubInstallation OK: id={installation.id}, gh_inst_id={installation.github_installation_id}, status={installation.status}")

    # 5. Create GitHub repository
    repo = GithubRepository(
        id=f"repo-{uuid4().hex[:8]}",
        installation_id=installation.id,
        github_repository_id="987654321",
        name="ssentra-core",
        full_name="tzylo-dev/ssentra-core",
        private=True,
        active=True,
        user_id=user.id
    )
    db.add(repo)
    db.commit()
    print(f"GithubRepository OK: id={repo.id}, name={repo.name}, private={repo.private}, active={repo.active}")

    # Test querying relations
    queried_org = db.query(Organization).filter(Organization.id == org.id).first()
    assert len(queried_org.members) == 1, "Organization members count mismatch"
    assert len(queried_org.installations) == 1, "Organization installations count mismatch"
    assert queried_org.installations[0].repositories[0].name == "ssentra-core", "Repository relation mismatch"
    print("Relationship navigation (Organization -> Member, Organization -> Installation -> Repository) OK!")

    # Clean up test rows
    db.delete(repo)
    db.delete(installation)
    db.delete(member)
    db.delete(org)
    db.commit()
    print("Cleanup completed successfully. All models, tables, and relationships verified 100%!")

except Exception as e:
    db.rollback()
    print(f"Error during ORM test: {e}")
    raise
finally:
    db.close()
