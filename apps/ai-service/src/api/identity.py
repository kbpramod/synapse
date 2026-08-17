from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.person import Person, IdentityLink
from src.services.identity_service import identity_service

router = APIRouter(prefix="/api/identities", tags=["Identity Resolution"])


class IdentityConfirmRequest(BaseModel):
    link_id: str


class IdentityLinkRequest(BaseModel):
    person_id: str
    provider: str
    identity_value: str


@router.get("", response_model=List[dict])
@router.get("/", response_model=List[dict])
def list_identities(db: Session = Depends(get_db)):
    """List all canonical persons and their mapped identities across meetings and GitHub."""
    persons = db.query(Person).all()
    results = []
    for p in persons:
        links = db.query(IdentityLink).filter(IdentityLink.person_id == p.id).all()
        results.append({
            "id": p.id,
            "canonical_name": p.canonical_name,
            "primary_email": p.primary_email,
            "organization": p.organization,
            "identities": [
                {
                    "link_id": l.id,
                    "provider": l.provider,
                    "identity_value": l.identity_value,
                    "state": l.state,
                    "confidence_score": l.confidence_score,
                    "evidence": l.evidence_json
                }
                for l in links
            ]
        })
    return results


@router.post("/confirm")
def confirm_identity(req: IdentityConfirmRequest, db: Session = Depends(get_db)):
    """Manually confirm an identity link (converting PROBABLE -> CONFIRMED)."""
    try:
        link = identity_service.confirm_identity_link(db, req.link_id)
        return {"status": "confirmed", "link_id": link.id, "state": link.state}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/link")
def link_identity_manually(req: IdentityLinkRequest, db: Session = Depends(get_db)):
    """Explicitly link an identity (e.g. GitHub handle to Person) with 1.0 confidence."""
    try:
        link = identity_service.link_identities_manually(
            db=db,
            person_id=req.person_id,
            provider=req.provider,
            identity_value=req.identity_value
        )
        return {"status": "linked", "link_id": link.id, "state": link.state}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
