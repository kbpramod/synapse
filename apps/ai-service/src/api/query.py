from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services.query_service import query_service

router = APIRouter(prefix="/api/query", tags=["Query Engine"])


class QueryRequest(BaseModel):
    query: str
    time_window_days: Optional[int] = 30
    actor: Optional[str] = None


@router.post("", response_model=dict)
@router.post("/", response_model=dict)
async def query_engineering_state(
    req: QueryRequest,
    db: Session = Depends(get_db)
):
    """
    Reconstruct the current lifecycle state of engineering work by querying
    meeting commitments, decisions, and GitHub PR/review execution.
    """
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Query string cannot be empty.")

    result = await query_service.answer_query(
        db=db,
        query=req.query,
        time_window_days=req.time_window_days,
        actor=req.actor
    )
    return result
