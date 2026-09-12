from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, desc

from models.event_node import EventNode
from models.event_relationship import EventRelationship


class EventRepository:

    def create_event(self, db: Session, event: EventNode) -> EventNode:
        db.add(event)
        db.commit()
        db.refresh(event)
        return event

    def get_by_id(self, db: Session, event_id: str) -> Optional[EventNode]:
        return db.query(EventNode).filter(EventNode.id == event_id).first()

    def get_by_reference_id(
        self,
        db: Session,
        reference_id: str,
        source: Optional[str] = None
    ) -> Optional[EventNode]:
        query = db.query(EventNode).filter(EventNode.reference_id == reference_id)
        if source:
            query = query.filter(EventNode.source == source)
        return query.first()

    def list_events(
        self,
        db: Session,
        source: Optional[str] = None,
        actor: Optional[str] = None,
        entity_id: Optional[str] = None,
        limit: int = 50
    ) -> List[EventNode]:
        query = db.query(EventNode)
        if source:
            query = query.filter(EventNode.source == source)
        if actor:
            query = query.filter(EventNode.actor.ilike(f"%{actor}%"))
        if entity_id:
            query = query.filter(EventNode.entity_id == entity_id)

        return query.order_by(desc(EventNode.timestamp)).limit(limit).all()

    def create_relationship(
        self,
        db: Session,
        relationship: EventRelationship
    ) -> EventRelationship:
        # Avoid duplicate relationships
        existing = db.query(EventRelationship).filter(
            EventRelationship.source_event_id == relationship.source_event_id,
            EventRelationship.target_event_id == relationship.target_event_id,
            EventRelationship.relationship_type == relationship.relationship_type
        ).first()
        if existing:
            return existing

        db.add(relationship)
        db.commit()
        db.refresh(relationship)
        return relationship

    def get_relationships_for_event(
        self,
        db: Session,
        event_id: str
    ) -> List[EventRelationship]:
        return db.query(EventRelationship).filter(
            or_(
                EventRelationship.source_event_id == event_id,
                EventRelationship.target_event_id == event_id
            )
        ).all()


event_repository = EventRepository()
