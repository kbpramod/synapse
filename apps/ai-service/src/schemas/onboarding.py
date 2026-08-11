from pydantic import BaseModel, ConfigDict, Field


class OnboardingRequest(BaseModel):
    displayName: str = Field(..., alias="displayName")

    model_config = ConfigDict(populate_by_name=True)


class OnboardingResponse(BaseModel):
    success: bool
    message: str


class ProjectResponse(BaseModel):
    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)
