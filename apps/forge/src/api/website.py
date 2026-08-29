

from fastapi import APIRouter
from schemas.website import WebsiteRequest

route = APIRouter(prefix="/website", tags=["Website"])

@route.post("/")
async def website(request: WebsiteRequest):
    return {"url": request.url}