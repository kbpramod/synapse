import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status

from db.repository import ForgeRepository
from schemas.website import WebsiteCreate, WebsiteRequest, WebsiteResponse

logger = logging.getLogger("forge.api.website")

route = APIRouter(prefix="/website", tags=["Website"])


@route.post("/", response_model=WebsiteResponse, status_code=status.HTTP_201_CREATED)
def create_or_upsert_website(request: WebsiteCreate):
    """Creates or updates a target website in PostgreSQL."""
    if not request.url or not request.url.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Valid website URL must be provided.",
        )
    url = request.url.strip()
    try:
        website_row = ForgeRepository.create_website(
            url=url,
            is_active=request.is_active,
            app_name=request.app_name,
            environment=request.environment,
        )
        return WebsiteResponse.model_validate(website_row)
    except Exception as e:
        logger.error(f"[WEBSITE API] Failed to create website '{url}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save website: {str(e)}",
        )


@route.get("/", response_model=List[WebsiteResponse])
def list_websites(active_only: bool = False):
    """Lists all registered websites in PostgreSQL."""
    rows = ForgeRepository.list_websites(active_only=active_only)
    return [WebsiteResponse.model_validate(r) for r in rows]


@route.get("/{website_id}", response_model=WebsiteResponse)
def get_website(website_id: int):
    """Retrieves a specific website by its ID."""
    row = ForgeRepository.get_website_by_id(website_id)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Website ID {website_id} not found.",
        )
    return WebsiteResponse.model_validate(row)


@route.get("/{website_id}/runs")
def list_website_runs(website_id: int, limit: int = 50):
    """Lists recent test executions for every test belonging to this website's domain."""
    website = ForgeRepository.get_website_by_id(website_id)
    if not website:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Website ID {website_id} not found.",
        )

    runs = ForgeRepository.get_runs_for_domain(domain=website["domain"], limit=limit)
    return {
        "website_id": website_id,
        "domain": website["domain"],
        "count": len(runs),
        "runs": runs,
    }


@route.get("/{website_id}/pages")
def list_website_pages(website_id: int):
    """Lists all discovered pages for a website with their associated test counts."""
    website = ForgeRepository.get_website_by_id(website_id)
    if not website:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Website ID {website_id} not found.",
        )

    pages = ForgeRepository.list_pages_for_website(website_id=website_id)
    return {
        "website_id": website_id,
        "domain": website["domain"],
        "count": len(pages),
        "pages": pages,
    }


@route.get("/{website_id}/pages/{page_id}/tests")
def list_page_tests(website_id: int, page_id: int):
    """Lists all tests linked to a specific discovered page."""
    website = ForgeRepository.get_website_by_id(website_id)
    if not website:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Website ID {website_id} not found.",
        )

    page = ForgeRepository.get_page_by_id(page_id)
    if not page or page.get("website_id") != website_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Page ID {page_id} not found for website {website_id}.",
        )

    tests = ForgeRepository.get_tests_for_page(page_id=page_id)
    return {
        "website_id": website_id,
        "page_id": page_id,
        "page_url": page.get("url"),
        "page_title": page.get("title"),
        "count": len(tests),
        "tests": tests,
    }


@route.delete("/{website_id}", status_code=status.HTTP_200_OK)
def delete_website(website_id: int):
    """Deletes a website and cascades deletion to associated accounts and tests."""
    deleted = ForgeRepository.delete_website(website_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Website ID {website_id} not found.",
        )
    return {"status": "deleted", "website_id": website_id}