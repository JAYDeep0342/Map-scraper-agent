import time
import logging
import traceback
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from agents.ecommerce_agent import EcommerceAgent, _detect_site
from browser.browser_manager import BrowserManager

logger = logging.getLogger("ecommerce-api")

router = APIRouter(prefix="/scrape/ecommerce", tags=["E-commerce Scraper"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class EcommerceRequest(BaseModel):
    url: str = Field(
        ...,
        description=(
            "E-commerce product page OR search/category URL. "
            "Supported sites: Flipkart, Amazon, Meesho, Myntra, Snapdeal, Nykaa, and generic."
        ),
        examples=[
            "https://www.flipkart.com/search?q=iphone+15",
            "https://www.amazon.in/s?k=headphones",
            "https://www.meesho.com/search?q=kurta",
        ],
    )
    limit: int = Field(
        default=1,
        ge=1,
        le=200,
        description="Max number of products to scrape (default: 1).",
    )
    pages: int = Field(
        default=1,
        ge=1,
        le=10,
        description="How many search-result pages to paginate through (default: 1).",
    )
    enrich_sellers: bool = Field(
        default=False,
        description=(
            "Google-search each seller name to find their phone/email/website. "
            "Slower — adds ~5-10 s per unique seller."
        ),
    )

    @field_validator("url", mode="before")
    @classmethod
    def strip_url(cls, v):
        if isinstance(v, str):
            v = v.strip()
            # Meesho homepage is blocked by Cloudflare for headless browsers.
            # Give the user an immediate, helpful error instead of a 50-second timeout.
            import urllib.parse
            parsed = urllib.parse.urlparse(v)
            if "meesho.com" in parsed.netloc and parsed.path.rstrip("/") == "":
                raise ValueError(
                    "Meesho homepage (meesho.com/) is blocked by Cloudflare for automated browsers. "
                    "Please use a search URL instead, e.g.: "
                    "https://www.meesho.com/search?q=kurta+for-women"
                )
            if "zepto.com" in parsed.netloc and ("/cn/" in parsed.path or "/c/" in parsed.path):
                raise ValueError(
                    "Zepto category URLs (/cn/) are strictly blocked by Cloudflare for automated browsers. "
                    "Please use a Zepto search URL instead, e.g.: "
                    "https://www.zepto.com/search?q=fresh+vegetables"
                )
            if "myntra.com" in parsed.netloc and ("/shop/" in parsed.path or "/checkout/" in parsed.path):
                raise ValueError(
                    "Myntra '/shop/' URLs are promotional banners and do not contain product grids. "
                    "Please use a standard category or search URL instead, e.g.: "
                    "https://www.myntra.com/kids-wear"
                )
        return v



class ProductItem(BaseModel):
    url: str = ""
    product_name: str = ""
    price: str = ""
    image: str = ""
    description: str = ""
    seller_name: str = ""
    seller_phone: str = ""
    seller_email: str = ""
    seller_website: str = ""
    rating: str = ""
    reviews: str = ""
    platform: str = ""


class EcommerceResponse(BaseModel):
    success: bool
    time_seconds: float
    total: int
    platform: str
    data: list[ProductItem]


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=EcommerceResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Scraping failed"},
    },
    summary="Scrape E-Commerce Products",
    description=(
        "Scrape product data from Flipkart, Amazon, Meesho, Myntra, Snapdeal, Nykaa or any generic site.\n\n"
        "**Single product URL** → returns one product with full details.\n\n"
        "**Search/category URL** → collects product links and scrapes each in parallel.\n\n"
        "Set `enrich_sellers=true` to also find seller phone/email via web search (slower)."
    ),
)
async def scrape_ecommerce(req: EcommerceRequest):
    logger.info(
        "[Ecommerce] Request: url=%r  limit=%d  pages=%d  enrich=%s",
        req.url, req.limit, req.pages, req.enrich_sellers,
    )
    start = time.time()

    try:
        async with BrowserManager() as manager:
            agent = EcommerceAgent(manager)
            raw_data = await agent.scrape(
                url=req.url,
                limit=req.limit,
                pages=req.pages,
                enrich_sellers=req.enrich_sellers,
            )
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        tb = traceback.format_exc()
        logger.error("[Ecommerce] Failed after %ss: %s\n%s", elapsed, exc, tb)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "E-commerce scraping failed",
                "detail": str(exc) or tb.strip().splitlines()[-1],
            },
        )

    elapsed = round(time.time() - start, 2)
    logger.info(
        "[Ecommerce] Done: %d products in %ss for %r",
        len(raw_data), elapsed, req.url,
    )

    # Normalise raw dicts into ProductItem (fill missing fields with "")
    items: list[ProductItem] = []
    detected_platform = _detect_site(req.url)
    for d in raw_data:
        item = ProductItem(
            url=d.get("url", ""),
            product_name=d.get("product_name", ""),
            price=d.get("price", ""),
            image=d.get("image", ""),
            description=d.get("description", ""),
            seller_name=d.get("seller_name", ""),
            seller_phone=d.get("seller_phone", ""),
            seller_email=d.get("seller_email", ""),
            seller_website=d.get("seller_website", ""),
            rating=d.get("rating", ""),
            reviews=d.get("reviews", ""),
            platform=d.get("platform", ""),
        )
        items.append(item)




    return EcommerceResponse(
        success=True,
        time_seconds=elapsed,
        total=len(items),
        platform=detected_platform,
        data=items,
    )
