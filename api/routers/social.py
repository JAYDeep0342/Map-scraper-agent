import time
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agents.social_agent import SocialAgent
from browser.browser_manager import BrowserManager

logger = logging.getLogger("social-api")

router = APIRouter(prefix="/scrape/social", tags=["Social Media Scraper"])

class SocialRequest(BaseModel):
    url: str = Field(..., description="Social media profile URL (e.g., Instagram, Twitter)")

class SocialResponse(BaseModel):
    success: bool
    time_seconds: float
    data: dict

@router.post("", response_model=SocialResponse)
async def scrape_social(req: SocialRequest):
    """Scrapes a given social media profile URL to extract public info."""
    logger.info(f"[Social] Scraping URL: {req.url}")
    start = time.time()
    
    try:
        async with BrowserManager() as manager:
            agent = SocialAgent(manager)
            data = await agent.scrape(req.url)
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        logger.error(f"[Social] Failed after {elapsed}s: {exc}")
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "Social media scraping failed",
                "detail": str(exc),
            },
        )

    elapsed = round(time.time() - start, 2)
    logger.info(f"[Social] Scrape done in {elapsed}s for {req.url}")
    
    return SocialResponse(
        success=True,
        time_seconds=elapsed,
        data=data
    )
