from datetime import datetime
from typing import Any, Dict, Optional
from pydantic import BaseModel, ConfigDict


class AccountCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    credentials: Dict[str, Any] = {}
    is_active: bool = True


class AccountUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    website_id: int
    username: str
    role: str
    credentials: Dict[str, Any] = {}
    is_active: bool
    created_at: datetime
    updated_at: datetime
