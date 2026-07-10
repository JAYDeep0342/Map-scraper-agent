"""Social Profile Discovery API — public Instagram/Facebook lookup.

    POST /social/scrape/sync   → discover businesses for (query, location),
                                  return their public Instagram/Facebook
                                  profiles as JSON.

Separate capability from the Google Maps lead scraper (api/server.py),
added 2026-07-09 — does not touch /scrape/sync or its contract. See
CLAUDE.md section 2 for the (now-extended) scope this lives under.

Candidate discovery deliberately reuses Google Maps (via SocialDiscoveryAgent
-> MapsAgent) instead of scraping a general web search engine for phrases
like "Architects India Instagram": scraping google.com/search result pages
directly is a new, ToS-risky surface with no existing infra behind it in
this project, and Instagram/Facebook's own internal search requires a
logged-in session. Google Maps is already a legitimate, proven business-
discovery source here, so it's reused as the candidate seed and only a
business's own official website linking to its profile is trusted as the
identity signal (see agents/social_agent.py). This is a documented
assumption, not a silent scope-narrowing — flag it if a search API key
(Google Custom Search / Bing) should be wired in instead to unlock
name-only discovery.
"""

import logging
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from agents.social_agent import SocialDiscoveryAgent, SocialRequestTrace
from config import settings
from config.city_areas import CITY_AREAS

logger = logging.getLogger("social-api")

router = APIRouter(prefix="/social", tags=["Social Profile Discovery"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class SocialScrapeRequest(BaseModel):
    """What to discover: a business/professional category in a location."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='Business/professional category to search, e.g. "Architects"',
        examples=["Architects", "Interior Designers", "Architecture Firms"],
    )
    location: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description='Country, state or city, e.g. "India", "Indore", "Mumbai"',
        examples=["India", "Indore", "Mumbai"],
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=settings.SOCIAL_MAX_LIMIT,
        description="Exact number of qualified leads to return when available",
    )

    @field_validator("query", "location", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


class SocialLeadItem(BaseModel):
    """One discovered business with its public Instagram/Facebook profile."""

    name: str = ""
    location: str = ""
    category: str = ""

    instagram_url: str = ""
    instagram_username: str = ""
    instagram_followers: int | None = None
    instagram_following: int | None = None
    instagram_posts_count: int | None = None

    facebook_url: str = ""
    facebook_username: str = ""

    bio: str = ""
    profile_image_url: str = ""


class SocialScrapeResponse(BaseModel):
    """Response returned by /social/scrape/sync."""

    query: str
    location: str
    requested_limit: int
    returned: int
    leads: list[SocialLeadItem]


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Bounded query planner (Phase 2) — small, relevance-aware fan-out, not a
# general web-search crawl. Each entry here is much more expensive than a
# Maps sub-query (full Maps discovery + per-candidate website check), so
# this stays intentionally short.
# ---------------------------------------------------------------------------
_METRO_SAMPLE = ["Delhi", "Mumbai", "Bangalore", "Hyderabad", "Chennai", "Kolkata", "Pune", "Ahmedabad"]


def _is_known_city(location: str) -> bool:
    loc = location.strip().lower()
    return loc in CITY_AREAS or any(city in loc for city in CITY_AREAS)


def _build_social_location_list(location: str) -> list[str]:
    """Location variants to try, ordered by priority. Primary location
    first; area-level variants only for a location this project actually
    recognizes as one specific city (get_city_areas() otherwise falls back
    to generic placeholders like "city center" that make no sense appended
    to a country/state-scale location); a bounded metro sample for
    country-scale locations, for geographic diversity instead of whatever
    single city Maps' own ranking happens to default to.
    """
    variants = [location]
    if _is_known_city(location):
        from config.city_areas import get_city_areas
        for area in get_city_areas(location)[:3]:
            variants.append(f"{area}, {location}")
    elif location.strip().lower() in ("india", "bharat"):
        variants.extend(_METRO_SAMPLE)
    return variants


def _social_dedup_key_ig(lead: dict) -> str | None:
    return lead["instagram_url"] or None


def _social_dedup_key_fb(lead: dict) -> str | None:
    return lead["facebook_url"] or None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.post(
    "/scrape/sync",
    response_model=SocialScrapeResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Discovery failed"},
    },
)
async def social_scrape_sync(req: SocialScrapeRequest, request: Request):
    """Discover businesses for (query, location) and return their public
    Instagram/Facebook profiles (synchronous, exact-limit when available).
    """
    logger.info("Social discovery request: query=%r location=%r limit=%d", req.query, req.location, req.limit)
    start = time.time()

    browser_manager = request.app.state.browser_manager
    scrape_semaphore = request.app.state.scrape_semaphore

    try:
        async with scrape_semaphore:
            leads, job_metrics = await _run_social_scrape(browser_manager, req.query, req.location, req.limit)
    except Exception as exc:
        elapsed = round(time.time() - start, 2)
        logger.error("Social discovery failed after %ss: %s", elapsed, exc)
        raise HTTPException(
            status_code=500,
            detail={"success": False, "error": "Social discovery failed", "detail": str(exc)},
        )

    elapsed = round(time.time() - start, 2)
    job_metrics["total_scrape_s"] = elapsed
    job_metrics["exact_limit_met"] = len(leads) >= req.limit
    attempts = job_metrics.pop("attempts", [])
    logger.info("Social discovery done: %s", job_metrics)
    logger.info("Social discovery attempts (Phase 3, Task 0 fix — per-attempt trace): %s", attempts)

    return SocialScrapeResponse(
        query=req.query,
        location=req.location,
        requested_limit=req.limit,
        returned=len(leads),
        leads=[SocialLeadItem(**lead) for lead in leads],
    )


async def _run_social_scrape(browser_manager, keyword: str, location: str, limit: int) -> tuple[list[dict], dict]:
    """Outer multi-query loop: run location variants against
    SocialDiscoveryAgent until `limit` qualified leads are collected or the
    time budget is exhausted — mirrors api/server.py's _run_scrape loop and
    reuses the same deficit-aware backfill formula as maps_agent.py.
    """
    agent = SocialDiscoveryAgent(browser_manager)
    location_list = _build_social_location_list(location)
    budget = settings.get_social_time_budget(limit)
    t_start = time.time()
    request_t0 = time.monotonic()

    leads: list[dict] = []
    seen_ig: set[str] = set()
    seen_fb: set[str] = set()
    attempts: list[dict] = []

    aggregate = {
        "query": keyword, "location": location, "requested_limit": limit,
        "pipeline": "streaming" if settings.SOCIAL_STREAMING_PIPELINE_ENABLED else "sequential",
        "target_policy": settings.SOCIAL_CANDIDATE_TARGET_POLICY,
        "stream_start_threshold": settings.SOCIAL_STREAM_START_THRESHOLD if settings.SOCIAL_STREAMING_PIPELINE_ENABLED else None,
        "query_plan_count": len(location_list), "queries_executed": 0,
        "raw_candidates": 0, "unique_candidates": 0,
        "instagram_profiles_found": 0, "facebook_profiles_found": 0,
        "websites_checked": 0, "duplicates_removed": 0,
        "profile_enrichment_attempted": 0, "profile_enrichment_succeeded": 0,
        "homepage_fetch_count": 0, "contact_fallback_fetch_count": 0,
        "websites_ig_or_fb_on_homepage": 0, "websites_requiring_secondary": 0,
        "websites_no_social_found": 0, "no_website_count": 0,
        "peak_website_http_concurrency": 0, "peak_profile_http_concurrency": 0,
        "resolution_wasted_task_count": 0, "resolution_wasted_ms": 0.0,
        "secondary_pages_avoided": 0, "enrichment_skipped": 0,
        "backfill_rounds": 0, "backfill_ms": 0.0, "backfill_extra_candidates": 0,
    }

    # Phase 3, Task 0 fix: each attempt (one Maps location-query call) gets
    # its OWN fresh trace instead of one shared object reused across the
    # whole backfill loop — the prior design silently froze
    # enrichment_start/end at attempt #1's timestamps once queries_executed
    # reached 2+, while the resolved-count marks kept incrementing
    # cumulatively, producing a self-inconsistent trace (see Phase 2
    # report). Per-attempt timing is now self-consistent by construction;
    # request-level totals (total_scrape_s, exact_limit_met) were already
    # wall-clock-based and unaffected by that bug.
    for attempt_index, loc_variant in enumerate(location_list, start=1):
        if len(leads) >= limit:
            break
        if time.time() - t_start > budget:
            logger.info("Social scrape time budget (%.0fs) reached with %d/%d leads — stopping.",
                        budget, len(leads), limit)
            break

        deficit = max(0, limit - len(leads))
        aggregate["queries_executed"] += 1
        attempt_trace = SocialRequestTrace()
        t_attempt_start = time.monotonic()
        qualified_before = len(leads)
        try:
            if settings.SOCIAL_STREAMING_PIPELINE_ENABLED:
                batch_leads, m = await agent.discover_streaming(
                    keyword, loc_variant, deficit, settings.SOCIAL_STREAM_START_THRESHOLD,
                    progress=_log_progress, trace=attempt_trace,
                )
            else:
                batch_leads, m = await agent.discover(
                    keyword, loc_variant, deficit, progress=_log_progress, trace=attempt_trace,
                )
        except Exception as exc:
            logger.warning("Social discovery failed for %r: %s", loc_variant, exc)
            continue
        t_attempt_end = time.monotonic()

        for key in ("raw_candidates", "unique_candidates", "instagram_profiles_found",
                    "facebook_profiles_found", "websites_checked", "duplicates_removed",
                    "profile_enrichment_attempted", "profile_enrichment_succeeded",
                    "homepage_fetch_count", "contact_fallback_fetch_count",
                    "websites_ig_or_fb_on_homepage", "websites_requiring_secondary",
                    "websites_no_social_found", "no_website_count",
                    "resolution_wasted_task_count", "secondary_pages_avoided", "enrichment_skipped",
                    "backfill_rounds", "backfill_extra_candidates"):
            aggregate[key] += m.get(key, 0)
        aggregate["resolution_wasted_ms"] += m.get("resolution_wasted_ms", 0.0)
        aggregate["backfill_ms"] += m.get("backfill_ms", 0.0)
        aggregate["peak_website_http_concurrency"] = max(
            aggregate["peak_website_http_concurrency"], m.get("peak_website_http_concurrency", 0))
        aggregate["peak_profile_http_concurrency"] = max(
            aggregate["peak_profile_http_concurrency"], m.get("peak_profile_http_concurrency", 0))

        for lead in batch_leads:
            if len(leads) >= limit:
                break
            ig_key, fb_key = _social_dedup_key_ig(lead), _social_dedup_key_fb(lead)
            if (ig_key and ig_key in seen_ig) or (fb_key and fb_key in seen_fb):
                aggregate["duplicates_removed"] += 1
                continue
            if ig_key:
                seen_ig.add(ig_key)
            if fb_key:
                seen_fb.add(fb_key)
            leads.append(lead)

        marks = attempt_trace.marks
        attempts.append({
            "attempt_index": attempt_index,
            "attempt_query": loc_variant,
            "attempt_start_ms": round((t_attempt_start - request_t0) * 1000, 1),
            "attempt_end_ms": round((t_attempt_end - request_t0) * 1000, 1),
            "maps_discovery_ms": marks.get("maps_discovery_end_ms", 0.0) - marks.get("maps_discovery_start_ms", 0.0),
            "link_resolution_ms": m.get("link_resolution_ms", 0.0),
            "enrichment_ms": m.get("enrichment_ms", 0.0),
            "candidates_found": m.get("raw_candidates", 0),
            "candidates_resolved": m.get("instagram_profiles_found", 0) + m.get("facebook_profiles_found", 0)
                                    + m.get("duplicates_removed", 0),
            "qualified_leads_added": len(leads) - qualified_before,
            "yield_curve": m.get("yield_curve"),
            "candidate_overfetch_count": m.get("candidate_overfetch_count"),
        })

    aggregate["qualified_leads"] = len(leads)
    aggregate["attempts"] = attempts
    return leads[:limit], aggregate


def _log_progress(msg: str) -> None:
    logger.info(msg.strip() if isinstance(msg, str) else msg)
