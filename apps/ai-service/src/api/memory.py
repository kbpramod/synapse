from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.schemas.memory import MemoryUpdateRequest
from src.services.memory_service import memory_service

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.post("/update")
async def update_memory(
    request: MemoryUpdateRequest,
    db: Session = Depends(get_db)
):
    return await memory_service.reconcile(db, request)


@router.post("/search")
async def search_memory(
    request: MemoryUpdateRequest,
    db: Session = Depends(get_db)
):
    return await memory_service.reconcile(db, request)