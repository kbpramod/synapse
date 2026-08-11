from pydantic import BaseModel


class Topic(BaseModel):
    title: str
    points: list[str]


class Section(BaseModel):
    title: str
    topics: list[Topic]


class MemoryDocument(BaseModel):
    sections: list[Section]


class MemoryUpdateRequest(BaseModel):
    repositoryId: str
    memory: MemoryDocument