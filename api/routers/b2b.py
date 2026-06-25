from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import logging

from browser.browser_manager import BrowserManager
from agents.b2b_agent import B2BAgent

logger = logging.getLogger("b2b-api")
router = APIRouter(prefix="/scrape", tags=["B2B Scraping"])

class ScrapeRequest(BaseModel):
    url: str = Field(..., description="The category or search URL of the B2B website (e.g., eleczo.com).")
    limit: int = Field(10, description="Maximum number of products to scrape.")

class B2BItem(BaseModel):
    url: str
    product_name: str | None = None
    sku: str | None = None
    price_with_tax: str | None = None
    base_price: str | None = None
    gst: str | None = None
    image: str | None = None
    pdf_url: str | None = None
    brand: str | None = None
    model: str | None = None
    reference_no: str | None = None
    specifications: dict[str, str] = Field(default_factory=dict)

class ScrapeResponse(BaseModel):
    success: bool
    total: int
    data: list[B2BItem]

@router.post("/b2b", response_model=ScrapeResponse)
async def scrape_b2b(req: ScrapeRequest):
    """Scrape detailed specifications from a B2B component website."""
    logger.info(f"[B2B] Request: url='{req.url}' limit={req.limit}")
    
    try:
        async with BrowserManager() as manager:
            agent = B2BAgent(manager)
            results = await agent.scrape(req.url, limit=req.limit)
            
            return ScrapeResponse(
                success=True,
                total=len(results),
                data=[B2BItem(**r) for r in results]
            )
    except Exception as e:
        logger.error(f"[B2B] Scrape failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
