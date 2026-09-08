from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, AliasChoices


class WebsiteRequest(BaseModel):
    url: str
    app_name: Optional[str] = Field(None, validation_alias=AliasChoices("app_name", "appName"))
    environment: Optional[str] = None


class WebsiteCreate(BaseModel):
    url: str
    app_name: Optional[str] = Field(None, validation_alias=AliasChoices("app_name", "appName"))
    environment: Optional[str] = "Production"
    is_active: bool = True


class WebsiteUpdate(BaseModel):
    url: Optional[str] = None
    app_name: Optional[str] = Field(None, validation_alias=AliasChoices("app_name", "appName"))
    environment: Optional[str] = None
    is_active: Optional[bool] = None


class WebsiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    url: str
    domain: str
    app_name: Optional[str] = None
    environment: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_discovered_at: Optional[datetime] = None