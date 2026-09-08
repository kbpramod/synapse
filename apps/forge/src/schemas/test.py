from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TestScheduleUpdate(BaseModel):
    cron_interval_hours: int = Field(..., ge=1, le=168, description="Hours interval between test runs")
    cron_expression: Optional[str] = Field(None, description="Custom 5-part cron expression (e.g. '0 */6 * * *')")


class TestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    test_id: str
    website_id: Optional[int] = None
    domain: str
    page_url: Optional[str] = None
    title: str
    description: Optional[str] = None
    category: str
    priority: str
    steps: Optional[List[str]] = None
    expected_outcome: Optional[str] = None
    script_path: Optional[str] = None
    test_code: Optional[str] = None
    language: str
    status: str
    
    # Cron & Timing
    cron_interval_hours: Optional[int] = 24
    cron_expression: Optional[str] = None
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None

    created_at: datetime
    updated_at: datetime
