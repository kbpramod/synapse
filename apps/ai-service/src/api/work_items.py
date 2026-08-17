from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.work_item import WorkItem
from src.models.event_node import EventNode
from src.models.person import Person

router = APIRouter(prefix="/api/work-items", tags=["Work Items"])


@router.get("", response_model=List[dict])
@router.get("/", response_model=List[dict])
def list_work_items(
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """List all tracked engineering work items with their lifecycle status."""
    query = db.query(WorkItem)
    if status:
        query = query.filter(WorkItem.status == status)

    items = query.order_by(WorkItem.updated_at.desc()).all()
    results = []
    for w in items:
        assignee_name = None
        if w.assignee_person_id:
            person = db.query(Person).filter(Person.id == w.assignee_person_id).first()
            if person:
                assignee_name = person.canonical_name

        events_count = db.query(EventNode).filter(EventNode.work_item_id == w.id).count()

        results.append({
            "id": w.id,
            "title": w.title,
            "description": w.description,
            "status": w.status,
            "area": w.area,
            "assignee": assignee_name,
            "events_count": events_count,
            "created_at": w.created_at,
            "updated_at": w.updated_at
        })
    return results


@router.get("/{work_id}")
def get_work_item_detail(work_id: str, db: Session = Depends(get_db)):
    """Get full lifecycle details, events, and meeting decisions for a work item."""
    work_item = db.query(WorkItem).filter(WorkItem.id == work_id).first()
    if not work_item:
        raise HTTPException(status_code=404, detail="Work item not found")

    events = db.query(EventNode).filter(EventNode.work_item_id == work_id).order_by(EventNode.timestamp.asc()).all()

    assignee_person = None
    if work_item.assignee_person_id:
        assignee_person = db.query(Person).filter(Person.id == work_item.assignee_person_id).first()

    return {
        "work_item": {
            "id": work_item.id,
            "title": work_item.title,
            "description": work_item.description,
            "status": work_item.status,
            "area": work_item.area,
            "assignee": assignee_person.canonical_name if assignee_person else None,
            "created_at": work_item.created_at,
            "updated_at": work_item.updated_at
        },
        "events": [
            {
                "id": e.id,
                "source": e.source,
                "event_type": e.event_type,
                "timestamp": e.timestamp,
                "actor": e.actor,
                "content": e.content,
                "metadata": e.metadata_json
            }
            for e in events
        ]
    }
