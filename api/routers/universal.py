import time
import logging
import re
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.universal_agent import UniversalAgent
from agents.ecommerce_agent import EcommerceAgent
from browser.browser_manager import BrowserManager

logger = logging.getLogger("universal-api")

router = APIRouter(prefix="/scrape/universal", tags=["Universal Scraper"])

class UniversalRequest(BaseModel):
    url: str = Field(..., description="Any URL (B2B or E-commerce)")
    limit: int = Field(default=10, ge=1, le=100, description="Max items to scrape (if applicable)")

class UniversalResponse(BaseModel):
    success: bool
    time_seconds: float
    platform: str
    count: int
    data: list | dict

@router.post("/", response_model=UniversalResponse)
async def scrape_universal(req: UniversalRequest):
    start_t = time.time()
    logger.info(f"[UNIVERSAL] Request received for: {req.url}")
    
    url_lower = req.url.lower()
    
    platform = "b2b"
    if re.search(r"amazon\.|flipkart\.|myntra\.|meesho\.", url_lower):
        platform = "ecommerce"
        
    async with BrowserManager() as manager:
        try:
            if platform == "ecommerce":
                agent = EcommerceAgent(manager)
                data = await agent.scrape(req.url, limit=req.limit, enrich_sellers=False)
            else:
                agent = UniversalAgent(manager)
                data = await agent.scrape(req.url, limit=req.limit)
                
            count = len(data) if isinstance(data, list) else (1 if data else 0)
                
            return UniversalResponse(
                success=True,
                time_seconds=round(time.time() - start_t, 2),
                platform=platform,
                count=count,
                data=data
            )
        except Exception as e:
            logger.error(f"[UNIVERSAL] Error scraping {req.url}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))
