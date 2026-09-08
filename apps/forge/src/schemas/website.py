from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class WebsiteRequest(BaseModel):
    url: str


class WebsiteCreate(BaseModel):
    url: str
    is_active: bool = True


class WebsiteUpdate(BaseModel):
    url: Optional[str] = None
    is_active: Optional[bool] = None


class WebsiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    url: str
    domain: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_discovered_at: Optional[datetime] = None