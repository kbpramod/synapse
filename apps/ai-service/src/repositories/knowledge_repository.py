from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.knowledge_node import KnowledgeNode


class KnowledgeRepository:

    def create(
        self,
        db: Session,
        node: KnowledgeNode
    ) -> KnowledgeNode:

        db.add(node)
        db.commit()
        db.refresh(node)

        return node

    def update(
        self,
        db: Session,
        node: KnowledgeNode
    ) -> KnowledgeNode:

        db.add(node)
        db.commit()
        db.refresh(node)

        return node

    def delete(
        self,
        db: Session,
        node: KnowledgeNode
    ) -> None:

        db.delete(node)
        db.commit()

    def get_by_id(
        self,
        db: Session,
        node_id: str
    ) -> KnowledgeNode | None:

        statement = (
            select(KnowledgeNode)
            .where(KnowledgeNode.id == node_id)
        )

        return db.scalar(statement)

    def search_similar(
        self,
        db: Session,
        repository_id: str,
        embedding: list[float],
        limit: int = 5
    ) -> list[KnowledgeNode]:

        statement = (
            select(KnowledgeNode)
            .where(
                KnowledgeNode.repository_id == repository_id
            )
            .order_by(
                KnowledgeNode.embedding.cosine_distance(
                    embedding
                )
            )
            .limit(limit)
        )

        return list(db.scalars(statement).all())


knowledge_repository = KnowledgeRepository()