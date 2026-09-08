from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict
from schemas.website import WebsiteResponse
from schemas.account import AccountResponse


class OnboardingAccountInput(BaseModel):
    username: str
    password: str
    role: str = "user"
    credentials: Dict[str, Any] = {}
    is_active: bool = True


class OnboardingWebsiteInput(BaseModel):
    url: str
    is_active: bool = True


class OnboardingRequest(BaseModel):
    url: Optional[str] = None
    is_active: bool = True
    website: Optional[OnboardingWebsiteInput] = None
    accounts: List[OnboardingAccountInput] = []


class OnboardingResponse(BaseModel):
    status: str = "success"
    message: str
    website: WebsiteResponse
    accounts: List[AccountResponse]
