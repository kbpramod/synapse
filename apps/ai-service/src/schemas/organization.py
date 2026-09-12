from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CurrentOrganizationResponse(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    role: Optional[str] = "member"

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class OrganizationMemberResponse(BaseModel):
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    role: str = "org:member"
    image_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class OrganizationMembersListResponse(BaseModel):
    members: List[OrganizationMemberResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class InviteMemberRequest(BaseModel):
    email: str
    role: str = Field(default="org:member", description="Clerk role e.g. org:member or org:admin")


class InviteMemberResponse(BaseModel):
    id: str
    email: str
    role: str
    status: str = "pending"

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
