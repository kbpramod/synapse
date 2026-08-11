from sqlalchemy.orm import Session

from src.ai.embedding_service import embedding_service
from src.models.knowledge_node import KnowledgeNode
from src.repositories.knowledge_repository import knowledge_repository


class SearchService:

    async def search(
        self,
        db: Session,
        repository_id: str,
        text: str,
        limit: int = 5
    ) -> list[KnowledgeNode]:

        embedding = await embedding_service.embed(text)

        matches = knowledge_repository.search_similar(
            db=db,
            repository_id=repository_id,
            embedding=embedding,
            limit=limit
        )

        return matches


search_service = SearchService()