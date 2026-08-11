from sqlalchemy.orm import Session

from src.ai.embedding_service import embedding_service
from src.ai.llm_service import llm_service
from src.models.knowledge_node import KnowledgeNode
from src.repositories.knowledge_repository import knowledge_repository
from src.schemas.memory import MemoryUpdateRequest


class MemoryService:

    async def flatten(self, request: MemoryUpdateRequest) -> list[dict]:
        facts = []
        for section in request.memory.sections:
            for topic in section.topics:
                for point in topic.points:
                    facts.append({
                        "section": section.title,
                        "topic": topic.title,
                        "text": point
                    })
        return facts

    async def reconcile(
        self,
        db: Session,
        request: MemoryUpdateRequest
    ) -> list[dict]:
        repository_id = request.repositoryId
        facts = await self.flatten(request)

        results = []

        for fact in facts:
            fact_text = fact["text"]

            # 1. Embed the fact text
            embedding = await embedding_service.embed(fact_text)

            # 2. Search for similar existing knowledge nodes in DB
            candidate_nodes = knowledge_repository.search_similar(
                db=db,
                repository_id=repository_id,
                embedding=embedding,
                limit=5
            )

            existing_facts = [
                {
                    "id": node.id,
                    "section": node.section,
                    "topic": node.topic,
                    "text": node.fact
                }
                for node in candidate_nodes
            ]

            # 3. Ask LLM to evaluate action
            decision = await llm_service.evaluate_fact_action(
                incoming_fact=fact,
                existing_facts=existing_facts
            )

            action = decision.get("action", "ADD").upper()
            target_id = decision.get("target_id")
            reasoning = decision.get("reasoning", "")

            # 4. Perform database operation
            executed_action = action
            if action == "ADD" or not target_id:
                new_node = KnowledgeNode(
                    repository_id=repository_id,
                    section=fact["section"],
                    topic=fact["topic"],
                    fact=fact_text,
                    embedding=embedding
                )
                knowledge_repository.create(db, new_node)
                executed_action = "ADD"

            elif action == "UPDATE" and target_id:
                target_node = knowledge_repository.get_by_id(db, target_id)
                if target_node:
                    target_node.section = fact["section"]
                    target_node.topic = fact["topic"]
                    target_node.fact = fact_text
                    target_node.embedding = embedding
                    knowledge_repository.update(db, target_node)
                    executed_action = "UPDATE"
                else:
                    new_node = KnowledgeNode(
                        repository_id=repository_id,
                        section=fact["section"],
                        topic=fact["topic"],
                        fact=fact_text,
                        embedding=embedding
                    )
                    knowledge_repository.create(db, new_node)
                    executed_action = "ADD"

            elif action == "DELETE" and target_id:
                target_node = knowledge_repository.get_by_id(db, target_id)
                if target_node:
                    knowledge_repository.delete(db, target_node)
                    executed_action = "DELETE"
                else:
                    executed_action = "NOOP"

            elif action == "NOOP":
                executed_action = "NOOP"

            results.append({
                "section": fact["section"],
                "topic": fact["topic"],
                "text": fact_text,
                "action": executed_action,
                "reasoning": reasoning
            })

        return results


memory_service = MemoryService()