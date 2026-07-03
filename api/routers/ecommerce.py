import time
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.ecommerce_agent import EcommerceAgent
from browser.browser_manager import BrowserManager

logger = logging.getLogger("ecommerce-api")

router = APIRouter(prefix="/scrape/ecommerce", tags=["E-commerce Scraper"])

class EcommerceRequest(BaseModel):
    url: str = Field(..., description="E-commerce product or search URL (e.g., Amazon, Flipkart)")
    limit: int = Field(default=1, ge=1, le=100, description="Max products to scrape if it's a search URL")
    enrich_sellers: bool = Field(default=True, description="Find seller phone/email/website via web search (slower)")

class EcommerceResponse(BaseModel):
    success: bool
    time_seconds: float
    total: int
    data: list

@router.post("", response_model=EcommerceResponse)
@router.post("/", response_model=EcommerceResponse, include_in_schema=False)
async def scrape_ecommerce(req: EcommerceRequest):
    """Scrapes a given e-commerce product or search URL."""
    logger.info(f"[Ecommerce] Scraping URL: {req.url} (limit={req.limit})")
    start = time.time()
    
    try:
        async with BrowserManager() as manager:
            agent = EcommerceAgent(manager)
            data = await agent.scrape(req.url, limit=req.limit, enrich_sellers=req.enrich_sellers)
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        logger.error(f"[Ecommerce] Failed after {elapsed}s: {exc}")
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "Ecommerce scraping failed",
                "detail": str(exc),
            },
        )

    elapsed = round(time.time() - start, 2)
    logger.info(f"[Ecommerce] Scrape done in {elapsed}s for {req.url} ({len(data)} items)")
    
    return EcommerceResponse(
        success=True,
        time_seconds=elapsed,
        total=len(data),
        data=data
    )


@router.get("", response_model=EcommerceResponse)
@router.get("/", response_model=EcommerceResponse, include_in_schema=False)
async def scrape_ecommerce_get(url: str, limit: int = 1, enrich_sellers: bool = True):
    """Scrapes a given e-commerce product or search URL via GET request (for browser testing)."""
    return await scrape_ecommerce(EcommerceRequest(url=url, limit=limit, enrich_sellers=enrich_sellers))
