

"""FastAPI server ΓÇö Google Maps Lead Scraper (Sync API).

Single endpoint designed for Spring Boot integration:

    POST /scrape/sync   ΓåÆ scrape Google Maps, return leads as JSON directly

Run:
    python main.py
    # or: uvicorn api.server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
import logging
import math
import sys
import time
import traceback
from contextlib import asynccontextmanager

import psutil
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from agents.maps_agent import MapsAgent
from browser.browser_manager import BrowserManager
from skills.extract_skill import ExtractSkill
from config import settings
from config.india_cities import INDIA_CITIES, INDIA_STATES
from config.city_areas import get_city_areas

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scraper-api")

# ---------------------------------------------------------------------------
# Shared browser (Speed Optimization Playbook, Step 3 / Optimization 1)
# ---------------------------------------------------------------------------
# One Chromium process + Playwright driver, launched once at server startup
# and reused for every scrape job's whole lifetime ΓÇö measured on this host,
# (re)launching both costs ~2.5-3.5s, which was previously paid on *every*
# request/batch (see _run_scrape). Each job still gets its own isolated
# BrowserContext (browser_manager.new_context()), so concurrent jobs never
# share a mutable page/context. BrowserManager.start() detects a crashed
# browser (is_connected() == False) and relaunches automatically.
browser_manager = BrowserManager()

# Caps how many /scrape/sync jobs run their browser phase at once ΓÇö see
# settings.MAX_CONCURRENT_SCRAPE_JOBS for the measured reasoning (default 1:
# concurrent jobs against the shared browser were measured to contend badly
# on this host). A semaphore, not a single global lock, so raising the
# setting on a beefier host lets that many jobs run fully in parallel
# instead of queueing every request behind one lock.
_scrape_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_SCRAPE_JOBS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Prime psutil's CPU-load baseline (settings.get_optimal_concurrency)
    # here so the delta is meaningful by the time the first real request's
    # detail phase runs, instead of that first call reporting 0.0.
    psutil.cpu_percent(interval=None)
    t0 = time.perf_counter()
    await browser_manager.start()
    logger.info("Shared browser warmed up in %.0fms", (time.perf_counter() - t0) * 1000)
    # Exposed via app.state (not a direct import) so other routers — e.g.
    # api/routers/social.py — can reuse the same shared browser + admission
    # semaphore without a circular import back into this module.
    app.state.browser_manager = browser_manager
    app.state.scrape_semaphore = _scrape_semaphore
    try:
        yield
    finally:
        logger.info("Shutting down shared browser...")
        await browser_manager.close()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Google Maps Lead Scraper API",
    version="2.0",
    description="Sync API for scraping Google Maps business leads. "
                "Designed for Spring Boot backend integration.",
    lifespan=lifespan,
)

# CORS ΓÇö allow all origins so Spring Boot (any port) can call freely.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Decoupled Routers (E-commerce, Universal & Social Profile Discovery)
# ---------------------------------------------------------------------------
# ecommerce and universal routers disabled (files not present in this version)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class ScrapeRequest(BaseModel):
    """What to scrape from Google Maps."""

    keyword: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='Business type to search, e.g. "restaurant"',
        examples=["restaurant", "b tech college", "electronic shop"],
    )
    location: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='City or area, e.g. "Indore"',
        examples=["Indore", "Delhi", "Mumbai"],
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100_000,
        description="Maximum number of leads to return (up to 100,000)",
    )
    find_emails: bool = Field(
        default=True,
        description="Visit each business website to extract emails",
    )

    @field_validator("keyword", "location", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


class LeadItem(BaseModel):
    """One scraped business lead."""

    query: str = ""
    name: str = ""
    category: str = ""
    rating: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    emails: str = ""
    latitude: str = ""
    longitude: str = ""
    maps_url: str = ""


class ScrapeResponse(BaseModel):
    """Response returned by /scrape/sync."""

    success: bool
    total_leads: int
    query: str
    time_seconds: float
    leads: list[LeadItem]


class ErrorResponse(BaseModel):
    """Error envelope."""

    success: bool = False
    error: str
    detail: str = ""
@app.get("/health")
def health():
    """Quick health check ΓÇö Spring Boot can ping this to verify the service is up."""
    return {"status": "ok", "service": "google-maps-lead-scraper"}


@app.post(
    "/scrape/sync",
    response_model=ScrapeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Scraping failed"},
    },
)
async def scrape_sync(req: ScrapeRequest):
    """Scrape Google Maps and return leads directly (synchronous).

    - Builds the query: ``"<keyword> in <location>"``
    - Scrolls the Maps results feed to collect listings
    - Opens each listing to extract details
    - Optionally visits business websites for emails & social links
    - Returns everything as a JSON array
    """
    query = f"{req.keyword} in {req.location}"
    logger.info(
        "Scrape request: query=%r  limit=%d  find_emails=%s",
        query, req.limit, req.find_emails,
    )
    start = time.time()

    try:
        async with _scrape_semaphore:
            leads = await _run_scrape(query, req.limit, req.find_emails)
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        logger.error("Scrape failed after %ss: %s", elapsed, exc)
        raise HTTPException(
            status_code=500,
            detail={
                "success": False,
                "error": "Scraping failed",
                "detail": str(exc),
            },
        )

    elapsed = round(time.time() - start, 2)
    logger.info(
        "Scrape done: %d leads in %ss for %r",
        len(leads), elapsed, query,
    )

    return ScrapeResponse(
        success=True,
        total_leads=len(leads),
        query=query,
        time_seconds=elapsed,
        leads=[LeadItem(**lead) for lead in leads],
    )


# Chunk size for query-plan logging/progress batches. No longer tied to
# browser lifecycle (the browser is now a long-lived singleton, see
# `browser_manager` above) ΓÇö this just groups queries for the per-batch
# time-budget check and log lines below.
BROWSER_BATCH_SIZE = 25

# Generic search prefix variations ΓÇö applied to the ORIGINAL keyword
QUERY_PREFIXES = ["best", "top rated", "popular", "famous", "top"]

# Hard ceiling (seconds) on the whole fallback-query loop in _run_scrape.
# Measured: for a niche category (e.g. "nursing college"), area-based
# fallback queries mostly return the *same* businesses again (colleges
# aren't neighborhood-scoped the way restaurants are) ΓÇö the dedup logic
# correctly drops them as duplicates, but the request still pays full
# scrape time for each attempt. Without a budget this can chain through
# many fallback queries and take 200s+ for a 10-lead request. Past this
# ceiling, return whatever was collected so far instead of grinding
# through the rest of the fallback list.
def _get_scrape_time_budget(limit: int) -> float:
    return min(120.0, max(60.0, limit * 8.0))


def _build_query_list(query: str, limit: int) -> list[str]:
    """Build the full list of location queries to try, ordered by priority.

    Strategy (queries added as needed based on limit):
    1. Primary query
    2. Area-based queries (same city, different zones) ΓÇö ~20 new results each
    3. Prefix variations (best/top/popular + original keyword)
    4. Multi-city across 600+ India cities/districts
    5. State-level queries

    Area/prefix variations (2-3) are queued for *every* limit, not just
    large ones: `_run_scrape`'s batch loop already stops issuing further
    queries the moment `limit` leads are collected, so for a normal query
    (e.g. "restaurant in Indore") the primary query alone satisfies the
    limit and these extra entries are never touched ΓÇö zero added cost.
    But for a niche category with a genuinely small result pool (e.g.
    "nursing college in <city>", ~8 total listings), the primary query
    alone can under-deliver even though the request only asked for 10;
    these fallback variations give it a real chance to reach the limit
    instead of silently returning less than asked.
    """
    queries: list[str] = []

    keyword = query.split(" in ")[0].strip() if " in " in query else query
    location = query.split(" in ", 1)[1].strip() if " in " in query else ""
    location_lower = location.lower()

    # 1. Primary
    queries.append(query)

    # 2. Area-based (same city, different zones) ΓÇö best source of new leads
    if location:
        areas = get_city_areas(location)
        for area in areas:
            queries.append(f"{keyword} in {area} {location}")

    # 3. Prefix variations
    if location:
        for prefix in QUERY_PREFIXES:
            queries.append(f"{prefix} {keyword} in {location}")

    if limit <= 300:
        return queries

    # 4. Multi-city (600+ India cities/districts)
    for city in INDIA_CITIES:
        if city.strip().lower() == location_lower:
            continue
        queries.append(f"{keyword} in {city}")

    if limit <= 15000:
        return queries

    # 5. State-level (for very large limits)
    for state in INDIA_STATES:
        queries.append(f"{keyword} in {state}")

    return queries


async def _run_scrape(
    query: str,
    limit: int,
    find_emails: bool,
) -> list[dict]:
    """Scrape leads by running location queries against the shared browser.

    Queries are grouped into batches of BROWSER_BATCH_SIZE only for
    time-budget checks/logging; all batches share the one long-lived
    browser (module-level `browser_manager`). If a batch raises, that
    batch's remaining queries are skipped after a brief backoff ΓÇö the
    shared browser's own crash-recovery (BrowserManager.start()) handles
    relaunching Chromium if it actually died, so the next batch's first
    new_context() call recovers automatically.
    """
    all_leads: list[dict] = []
    seen: set[tuple] = set()

    def _add_leads(new_leads: list[dict]) -> int:
        added = 0
        for lead in new_leads:
            if len(all_leads) >= limit:
                break
            key = (lead["name"].strip().lower(), lead["address"].strip().lower())
            if key not in seen:
                seen.add(key)
                all_leads.append(lead)
                added += 1
        return added

    # Build the full ordered list of queries to run
    query_list = _build_query_list(query, limit)
    total_queries = len(query_list)
    logger.info(
        "Query plan: %d location searches queued for query %r (limit %d)",
        total_queries, query, limit
    )

    t_scrape_start = time.time()
    scrape_budget = _get_scrape_time_budget(limit)

    # Process queries in batches ΓÇö fresh browser per batch
    queries_done = 0
    for batch_start in range(0, total_queries, BROWSER_BATCH_SIZE):
        if len(all_leads) >= limit:
            logger.info("Limit reached (%d/%d). Stopping search.", len(all_leads), limit)
            break

        if time.time() - t_scrape_start > scrape_budget:
            logger.info(
                "Scrape time budget (%.0fs) reached with %d/%d leads ΓÇö "
                "returning what was found instead of trying more fallback queries.",
                scrape_budget, len(all_leads), limit
            )
            break

        batch = query_list[batch_start: batch_start + BROWSER_BATCH_SIZE]
        batch_num = batch_start // BROWSER_BATCH_SIZE + 1
        total_batches = math.ceil(total_queries / BROWSER_BATCH_SIZE)
        logger.info(
            "--- Batch %d/%d | Leads so far: %d/%d ---",
            batch_num, total_batches, len(all_leads), limit
        )

        try:
            agent = MapsAgent(browser_manager)
            for loc_query in batch:
                if len(all_leads) >= limit:
                    break
                if time.time() - t_scrape_start > scrape_budget:
                    logger.info(
                        "Scrape time budget (%.0fs) reached with %d/%d leads ΓÇö "
                        "stopping fallback queries mid-batch.",
                        scrape_budget, len(all_leads), limit
                    )
                    break
                queries_done += 1
                remaining = limit - len(all_leads)
                logger.info(
                    "[%d/%d] Searching: %r (need %d more)",
                    queries_done, total_queries, loc_query, remaining
                )
                try:
                    leads = await agent.scrape(
                        loc_query,
                        max_results=min(remaining, 30),
                        progress=_log_progress,
                    )
                    added = _add_leads(leads)
                    if added > 0:
                        logger.info(
                            "  +%d new leads (total: %d/%d)",
                            added, len(all_leads), limit
                        )
                except Exception as exc:
                    logger.warning("  Search failed for %r: %s", loc_query, exc)
                    continue

        except Exception as batch_exc:
            logger.warning(
                "Batch %d/%d error: %s ΓÇö pausing briefly before next batch",
                batch_num, total_batches, batch_exc
            )
            await asyncio.sleep(2)  # brief pause, lets a crashed browser be relaunched
            continue

    all_leads = all_leads[:limit]
    logger.info("Scraping done: %d unique leads collected", len(all_leads))

    # Email enrichment via plain HTTP ΓÇö no browser needed (Playbook Step 2)
    if all_leads and find_emails:
        sites = sum(1 for l in all_leads if l.get("website"))
        logger.info("Starting email enrichment for %d websites...", sites)
        try:
            await ExtractSkill().enrich_leads(all_leads, progress=_log_progress)
            with_email = sum(1 for l in all_leads if l.get("emails"))
            logger.info("Enrichment done: %d/%d leads have emails", with_email, len(all_leads))
        except Exception as exc:
            logger.warning("Email enrichment failed (leads still returned without emails): %s", exc)

    phone_count = sum(1 for l in all_leads if l.get("phone"))
    logger.info(
        "Job metrics: %s",
        {
            "query": query,
            "requested_limit": limit,
            "queries_executed": queries_done,
            "total_scrape_s": round(time.time() - t_scrape_start, 2),
            "successful_leads": len(all_leads),
            "phone_count": phone_count,
            "phone_fill_rate": round(phone_count / len(all_leads), 2) if all_leads else 0.0,
            "find_emails": find_emails,
        },
    )

    return all_leads


def _log_progress(msg: str) -> None:
    """Callback for agents to log progress."""
    logger.info(msg.strip())



# ---------------------------------------------------------------------------
# Entry point (when run directly: python -m api.server)
# ------------------------------
# ---------------------------------------------
if __name__ == "__main__":
    import uvicorn


    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    uvicorn.run(app, host="0.0.0.0", port=8000)