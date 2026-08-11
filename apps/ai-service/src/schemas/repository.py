from pydantic import BaseModel, ConfigDict, Field


class RepositoryResponse(BaseModel):
    id: str
    name: str
    status: str
    lastSync: str = Field(..., alias="lastSync")
    knowledgeNodes: int = Field(..., alias="knowledgeNodes")
    docPages: int = Field(..., alias="docPages")
    githubUrl: str = Field(..., alias="githubUrl")
    connectedAt: str = Field(..., alias="connectedAt")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class RepositoryConnectRequest(BaseModel):
    repository: str


class RepositoryUpdateRequest(BaseModel):
    name: str


class DocPageResponse(BaseModel):
    id: str
    title: str
    content: str

    model_config = ConfigDict(from_attributes=True)


class SourceReference(BaseModel):
    title: str
    category: str
    path: str


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    id: str
    question: str
    answer: str
    sources: list[SourceReference]
    timestamp: str
