from src.ai.client import ai_client


class EmbeddingService:

    def __init__(self):
        self.client = ai_client

    async def embed(self, text: str) -> list[float]:

        response = await self.client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )

        return response.data[0].embedding

    async def embed_many(
        self,
        texts: list[str]
    ) -> list[list[float]]:

        response = await self.client.embeddings.create(
            model="text-embedding-3-small",
            input=texts
        )

        return [
            item.embedding
            for item in response.data
        ]


embedding_service = EmbeddingService()