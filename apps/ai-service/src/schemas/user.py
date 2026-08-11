from pydantic import BaseModel, ConfigDict, Field


class UserMeResponse(BaseModel):
    id: str
    email: str | None = None
    name: str | None = None
    isVerified: bool = Field(default=True, serialization_alias="isVerified")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UserProfileResponse(BaseModel):
    displayName: str | None = Field(default="", serialization_alias="displayName")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UserProfileUpdate(BaseModel):
    displayName: str = Field(..., serialization_alias="displayName")
