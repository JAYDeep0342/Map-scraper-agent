"""Maps agent using Playwright for automation.

Scrapes Google Maps search results into structured lead records:
business name, category, rating, address, phone, website and coordinates.
"""

import asyncio
import math
import re
import time
import urllib.parse

from playwright.async_api import BrowserContext, Page, Error as PWError, TimeoutError as PWTimeout

from browser.browser_manager import BrowserManager
from config import settings

END_OF_LIST_MARKERS = ("you've reached the end of the list",)

COORDS_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)")
COORDS_AT_RE = re.compile(r"/@(-?\d+\.\d+),(-?\d+\.\d+)")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    # Maps prefixes icon glyphs (private-use unicode) before some texts.
    return re.sub('[\\ue000-\\uf8ff]', '', text).strip().lstrip(', ')


def _quality_met(leads: list[dict], max_results: int) -> bool:
    """Same gate _scrape_details() already uses to decide when to stop
    early — reused here to decide whether MapsAgent.scrape() needs to go
    back for more candidates instead of returning what it has."""
    if len(leads) < max_results:
        return False
    phone_ratio = sum(1 for l in leads if l.get("phone")) / len(leads) if leads else 0.0
    return phone_ratio >= 0.8 or len(leads) >= max_results * 2


_PURE_NUMBER_RE = re.compile(r"[\d,]+")


def _parse_card_middle_segment(text: str) -> tuple[str, str]:
    """PHASE 5/CARD-SHORTCUT EXPERIMENT — a search card's info row reads
    "{category} · {X}" where X is *either* a review count (pure digits,
    e.g. "220") *or* a street address (contains letters) — Google shows
    one or the other in this position, never both. Returns (address,
    reviews). Unvalidated beyond the 38-card sample from Phase 5 — this
    heuristic is exactly what that phase flagged as needing verification
    before being trusted.
    """
    text = text.strip(" ·").strip()
    if not text:
        return "", ""
    if _PURE_NUMBER_RE.fullmatch(text):
        return "", text.replace(",", "")
    return text, ""


def _lead_from_card(card: dict, query: str) -> dict:
    """CARD-SHORTCUT EXPERIMENT — build a lead directly from search-card
    DOM fields (Phase 5: phone 89%, website 84% present without any detail
    navigation), skipping _extract_place()/detail-page navigation entirely
    for candidates where the card already has a phone number. Returns the
    exact same schema _extract_place() does — no API contract change.
    """
    address, _reviews = _parse_card_middle_segment(card.get("middle_segment", ""))
    lat, lng = "", ""
    m = COORDS_RE.search(card["link"]) or COORDS_AT_RE.search(card["link"])
    if m:
        lat, lng = m.group(1), m.group(2)
    return {
        "query": query,
        "name": card["name"],
        "category": card["category"],
        "rating": card["rating"],
        "address": address,
        "phone": card["phone"],
        "website": card["website"],
        "latitude": lat,
        "longitude": lng,
        "maps_url": card["link"].split("?")[0],
        "emails": "",
    }


class _RequestTrace:
    """Phase-1 critical-path instrumentation — pure observation, records a
    monotonic elapsed-ms timestamp the *first* time each named milestone is
    reached. Never read by any control-flow decision; deleting all uses of
    this class changes zero request behavior. Logged once at the end of
    MapsAgent.scrape() via `progress`."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.marks: dict[str, float] = {}
        self._candidate_thresholds = [1, 3, 5, 10, 15]
        self._lead_thresholds = [1, 5, 8, 10]

    def mark(self, name: str) -> None:
        if name not in self.marks:
            self.marks[name] = round((time.monotonic() - self._t0) * 1000, 1)

    def mark_candidates_discovered(self, count: int) -> None:
        for n in self._candidate_thresholds:
            if count >= n:
                self.mark(f"{n}_candidates_discovered_ms")

    def mark_leads_completed(self, count: int) -> None:
        for n in self._lead_thresholds:
            if count >= n:
                self.mark(f"{n}_leads_completed_ms")


# ------------------------------------------------------------------------- #
# Detail-phase instrumentation (diagnostics only — logged via `progress`,
# never affects what's returned to the API caller or the retry/timeout/
# concurrency behavior). Added to compare a fast run against a slow run
# candidate-by-candidate instead of guessing where the time goes.
# ------------------------------------------------------------------------- #
def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — no numpy dependency in this project."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[k]


def _count_timeout_reasons(metrics: list[dict]) -> dict:
    reasons: dict[str, int] = {}
    for m in metrics:
        r = m["timeout_reason"]
        if r:
            reasons[r] = reasons.get(r, 0) + 1
    return reasons


def _log_detail_phase_metrics(
    metrics: list[dict], worker_stats: dict[int, dict], phase_wall_ms: float, progress,
) -> None:
    if not metrics:
        progress("  Detail metrics: no candidates completed their attempt cycle "
                  "(all still in-flight when the phase ended).")
        return

    goto_ms = [m["goto_commit_ms"] for m in metrics if m["goto_commit_ms"] is not None]
    readiness_ms = [m["readiness_wait_ms"] for m in metrics if m["readiness_wait_ms"] is not None]
    total_ms = [m["total_candidate_ms"] for m in metrics if m["total_candidate_ms"] is not None]
    queue_wait_ms = [m["queue_wait_ms"] for m in metrics if m.get("queue_wait_ms") is not None]

    for wid, stats in worker_stats.items():
        cand_totals = [
            m["total_candidate_ms"] for m in metrics
            if m["worker_id"] == wid and m["total_candidate_ms"] is not None
        ]
        stats["average_candidate_ms"] = (
            round(stats["total_busy_ms"] / stats["candidates_processed"], 1)
            if stats["candidates_processed"] else 0.0
        )
        stats["max_candidate_ms"] = round(max(cand_totals), 1) if cand_totals else 0.0
        stats["utilization_pct"] = (
            round(stats["total_busy_ms"] / phase_wall_ms * 100, 1) if phase_wall_ms else 0.0
        )
        stats["total_busy_ms"] = round(stats["total_busy_ms"], 1)
        stats["total_idle_ms"] = round(stats["total_idle_ms"], 1)

    slowest = sorted(metrics, key=lambda m: m["total_candidate_ms"] or 0, reverse=True)[:5]
    utilizations = [s["utilization_pct"] for s in worker_stats.values()]
    page_creation_ms = [s["page_creation_ms"] for s in worker_stats.values() if "page_creation_ms" in s]
    page_close_ms = [s["page_close_ms"] for s in worker_stats.values() if "page_close_ms" in s]

    summary = {
        "candidates_completed": len(metrics),
        "successes": sum(1 for m in metrics if m["success"]),
        "phase_wall_ms": round(phase_wall_ms, 1),
        # Pool-level cost of the current per-_scrape_details-call lifecycle
        # (new page per worker at start, close per worker at end) — bounded
        # by the *slowest* worker in each stage, since workers start/stop
        # concurrently, not the sum across workers.
        "worker_pool_startup_ms": round(max(page_creation_ms), 1) if page_creation_ms else None,
        "worker_pool_shutdown_ms": round(max(page_close_ms), 1) if page_close_ms else None,
        "p50_queue_wait_ms": round(_percentile(queue_wait_ms, 50), 1),
        "p95_queue_wait_ms": round(_percentile(queue_wait_ms, 95), 1),
        "p50_goto_commit_ms": round(_percentile(goto_ms, 50), 1),
        "p95_goto_commit_ms": round(_percentile(goto_ms, 95), 1),
        "p50_readiness_wait_ms": round(_percentile(readiness_ms, 50), 1),
        "p95_readiness_wait_ms": round(_percentile(readiness_ms, 95), 1),
        "p50_total_candidate_ms": round(_percentile(total_ms, 50), 1),
        "p95_total_candidate_ms": round(_percentile(total_ms, 95), 1),
        "over_3s": sum(1 for t in total_ms if t > 3000),
        "over_5s": sum(1 for t in total_ms if t > 5000),
        "over_10s": sum(1 for t in total_ms if t > 10000),
        "retries_used": sum(1 for m in metrics if m["retry_count"] > 0),
        "avg_worker_utilization_pct": round(sum(utilizations) / len(utilizations), 1) if utilizations else 0.0,
        "timeout_reasons": _count_timeout_reasons(metrics),
    }
    progress(f"  Detail metrics summary: {summary}")
    progress("  Slowest candidates: " + str([
        {
            "worker_id": m["worker_id"], "candidate_index": m["candidate_index"],
            "link": m["link"][:90], "total_candidate_ms": m["total_candidate_ms"],
            "goto_commit_ms": m["goto_commit_ms"], "readiness_wait_ms": m["readiness_wait_ms"],
            "extraction_evaluate_ms": m["extraction_evaluate_ms"], "retry_count": m["retry_count"],
            "timeout_reason": m["timeout_reason"], "consent_or_error_state": m["consent_or_error_state"],
            "redirected": m["redirected"], "success": m["success"],
        }
        for m in slowest
    ]))
    progress(f"  Worker stats: {list(worker_stats.values())}")


class MapsAgent:
    """Agent to interact with Google Maps via Playwright."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    async def scrape(self, query: str, max_results: int = 50, progress=None) -> list[dict]:
        """Scrape up to ``max_results`` leads for a Maps search query."""
        trace = _RequestTrace()
        trace.mark("request_start_ms")
        trace.mark("context_create_start_ms")
        context = await self.browser_manager.new_context()
        trace.mark("context_create_end_ms")
        try:
            t_start = time.monotonic()
            page = await context.new_page()
            url = settings.MAPS_SEARCH_URL.format(query=urllib.parse.quote(query))
            trace.mark("search_navigation_start_ms")
            # "commit" returns as soon as the response starts, instead of
            # waiting for the full domcontentloaded event on this JS-heavy
            # SPA — the explicit wait_for_selector() calls below already
            # gate on the content actually being ready, so this wait was
            # redundant with (and slower than) that.
            await page.goto(url, wait_until="commit")
            trace.mark("search_navigation_end_ms")
            await self._handle_consent(page)

            # Single-business queries redirect straight to the place page.
            if "/maps/place/" in page.url:
                lead = await self._extract_place(page, query)
                return [lead] if lead else []

            if settings.PIPELINE_DETAIL_SCRAPING:
                buffer_results = int(max_results * 1.5) + 15
                leads = await self._pipeline_scrape(context, page, buffer_results, query, max_results, progress)
                await page.close()
                if progress:
                    progress(f"  Pipelined scrape took {time.monotonic() - t_start:.1f}s")
                return leads

            # Adaptive candidate collection (Speed Optimization Playbook,
            # Optimization 4): start with a modest target instead of always
            # collecting int(max_results*1.5)+15 (30 for a 10-lead request,
            # regardless of whether that many are ever needed). Measured
            # across 9 benchmark runs, only 10-12 of those 30 were ever
            # actually processed before the quality gate was satisfied —
            # every time. But a small *fixed* buffer was already tried once
            # (loop_engineering.md Iteration 6: 14 links) and caused a real
            # quality-gate failure (50% phone fill) when many detail pages
            # failed under load and there weren't enough spare candidates
            # to fall back on — so this only shrinks the *initial* ask, and
            # goes back for more (bounded by max_candidate_budget) if the
            # quality gate isn't met, instead of capping the pool small
            # with no way to recover.
            initial_target = max(max_results + 5, math.ceil(max_results * 1.5))
            max_candidate_budget = max(max_results * 2, initial_target)
            backfill_min_batch = max(3, math.ceil(max_results * 0.25))

            total_shortcut = 0
            total_navigated = 0

            current_target = initial_target
            cards = await self._collect_listing_cards(page, current_target, progress, trace=trace)
            t_cards = time.monotonic()
            if progress:
                progress(
                    f"  Found {len(cards)} listings in {t_cards - t_start:.1f}s, "
                    f"scraping details..."
                )
            leads, detail_stats = await self._scrape_details(context, cards, query, max_results, progress, trace=trace)
            total_shortcut += detail_stats["shortcut_count"]
            total_navigated += detail_stats["navigated_count"]
            if progress:
                progress(f"  Detail phase took {time.monotonic() - t_cards:.1f}s")

            while (
                not _quality_met(leads, max_results)
                and current_target < max_candidate_budget
                and len(cards) >= current_target
            ):
                # Deficit-aware batch size: a fixed small increment (e.g.
                # always +3) was measured to need repeated backfill rounds
                # for even a single missing lead — each round pays its own
                # ~2s+ worker-pool startup/shutdown cost regardless of how
                # few candidates it processes. 2x the deficit as a safety
                # margin, since backfill candidates fail at roughly the
                # same rate as the initial batch (not every one succeeds).
                deficit = max(0, max_results - len(leads))
                batch_size = max(deficit * 2, backfill_min_batch)
                new_target = min(current_target + batch_size, max_candidate_budget)
                if progress:
                    progress(
                        f"  Quality gate not met ({len(leads)}/{max_results} leads) — "
                        f"backfilling candidate pool {current_target} -> {new_target}"
                    )
                current_target = new_target
                new_cards = await self._collect_listing_cards(page, current_target, progress, trace=trace)
                seen_links = {c["link"] for c in cards}
                incremental_cards = [c for c in new_cards if c["link"] not in seen_links]
                cards = new_cards
                if not incremental_cards:
                    break  # feed genuinely exhausted — no new candidates to try
                t_backfill = time.monotonic()
                needed_more = max(1, max_results - len(leads))
                more_leads, backfill_stats = await self._scrape_details(
                    context, incremental_cards, query, needed_more, progress, trace=trace
                )
                total_shortcut += backfill_stats["shortcut_count"]
                total_navigated += backfill_stats["navigated_count"]
                if progress:
                    progress(f"  Backfill detail phase took {time.monotonic() - t_backfill:.1f}s")
                seen_keys = {(l["name"], l["address"]) for l in leads}
                for lead in more_leads:
                    key = (lead["name"], lead["address"])
                    if key not in seen_keys:
                        seen_keys.add(key)
                        leads.append(lead)
                leads.sort(key=lambda l: 0 if l.get("phone") else 1)

            trace.mark("cleanup_start_ms")
            await page.close()
            trace.mark("cleanup_end_ms")
            trace.mark("response_ready_ms")

            # Card-shortcut production metrics — observability only, never
            # returned to the API caller, schema/contract unaffected.
            if progress:
                final_count = len(leads)
                final_phone_count = sum(1 for l in leads if l.get("phone"))
                final_address_count = sum(1 for l in leads if l.get("address"))
                progress("  Card-shortcut metrics: " + str({
                    "card_shortcut_enabled": settings.CARD_SHORTCUT_ENABLED,
                    "card_candidates_seen": len(cards),
                    "card_leads_accepted": total_shortcut,
                    "detail_fallback_count": total_navigated,
                    "detail_navigations_avoided": total_shortcut,
                    # Always 1.0 when >0 candidates were shortcut-resolved:
                    # a card is only ever resolved this way when it already
                    # has a phone (the gating condition itself) — this
                    # confirms the invariant holds, not independent variance.
                    "shortcut_phone_fill": 1.0 if total_shortcut else None,
                    "final_phone_fill": round(final_phone_count / final_count, 2) if final_count else 0.0,
                    "final_address_fill": round(final_address_count / final_count, 2) if final_count else 0.0,
                    "total_scrape_s": round(trace.marks.get("response_ready_ms", 0) / 1000, 2),
                }))
                progress(f"  Critical path trace: {trace.marks}")
            return leads
        finally:
            await context.close()

    # ------------------------------------------------------------------ #
    # Results feed
    # ------------------------------------------------------------------ #
    async def _handle_consent(self, page: Page) -> None:
        """Click through the Google consent screen if it appears."""
        try:
            if "consent.google" not in page.url:
                return
            for label in ("Accept all", "I agree", "Alle akzeptieren"):
                btn = page.get_by_role("button", name=label)
                if await btn.count():
                    await btn.first.click()
                    await page.wait_for_load_state("domcontentloaded")
                    return
        except Exception:
            pass

    async def _collect_listing_cards(
        self, page: Page, max_results: int, progress=None, trace: "_RequestTrace | None" = None,
        on_new_cards=None, stop_event: "asyncio.Event | None" = None,
    ) -> list[dict]:
        """Scroll the results feed and collect unique candidates, extracting
        card-visible fields (name, category, rating, phone, website)
        alongside the link during the same DOM query — this costs nothing
        extra over collecting just the link, so it always runs regardless
        of settings.CARD_SHORTCUT_ENABLED; that flag only controls whether
        _scrape_details() *uses* the extra fields to skip navigation.
        Measured (Phase 5, 38-card sample): phone present on 89% of cards,
        website on 84%, with zero detail-page navigation. Card boundary
        confirmed as div.Nv2PK (verified against actual result count, not
        assumed).

        ``on_new_cards``/``stop_event`` (added for the social-discovery
        streaming experiment, agents/social_agent.py) are optional and
        default to None/no-op — every existing caller (MapsAgent.scrape(),
        the production /scrape/sync path) passes neither, so this method's
        behavior for them is unchanged: no new branch executes. When given,
        ``on_new_cards`` is awaited once per scroll round with just that
        round's newly-deduped cards (never touches ``page`` itself), and
        the scroll loop also exits early once ``stop_event`` is set.
        """
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=20_000)
        except PWTimeout:
            if progress:
                progress("  Error: Results feed element not found on the page.")
            return []

        cards: dict[str, dict] = {}  # keyed by canonical link, ordered
        stale_rounds = 0

        for scroll_round in range(settings.MAX_FEED_SCROLLS):
            raw_cards = await page.evaluate(r"""() => {
                const clean = (t) => (t || '').replace(/[-]/g, '').trim();
                return Array.from(document.querySelectorAll('div.Nv2PK')).map(card => {
                    const linkEl = card.querySelector('a.hfpxzc');
                    const nameEl = card.querySelector('span.xxVWCe');
                    const ratingEl = card.querySelector('span.MW4etd');
                    const phoneEl = card.querySelector('span.UsdlK');
                    const websiteEl = card.querySelector('a.lcr4fd[href]');
                    // .W4Efsd matches several nested rows on this card (the
                    // rating alone, a combined multi-line block, the clean
                    // "category · address" line, and the "hours · phone"
                    // line) — pick the one that actually has the "·"
                    // separator, isn't the multi-line combined block, and
                    // isn't the hours/phone line.
                    const hoursPattern = /^(Open|Closed|Opens|Closes)/i;
                    const infoRows = Array.from(card.querySelectorAll('.W4Efsd')).map(r => clean(r.innerText));
                    const categoryRow = infoRows.find(
                        r => r.includes('·') && !r.includes('\n') && !hoursPattern.test(r)
                    ) || '';
                    const parts = categoryRow.split('·').map(p => clean(p));
                    return {
                        link: linkEl ? linkEl.href : '',
                        name: clean(nameEl ? nameEl.textContent : ''),
                        rating: clean(ratingEl ? ratingEl.textContent : ''),
                        phone: clean(phoneEl ? phoneEl.textContent : ''),
                        website: websiteEl ? websiteEl.href : '',
                        category: parts[0] || '',
                        middle_segment: parts.slice(1).join(' ').trim(),
                    };
                });
            }""")
            before = len(cards)
            new_this_round = []
            for c in raw_cards:
                if not c["link"]:
                    continue
                clean_link = c["link"].split("?")[0]
                if clean_link not in cards:
                    cards[clean_link] = c
                    new_this_round.append(c)

            if trace is not None:
                trace.mark_candidates_discovered(len(cards))
            if progress and len(cards) != before:
                progress(f"  Collected {len(cards)} cards so far...")
            if on_new_cards is not None and new_this_round:
                await on_new_cards(new_this_round)

            if stop_event is not None and stop_event.is_set():
                if progress:
                    progress(f"  Stop requested externally: {len(cards)} cards collected.")
                break

            if len(cards) >= max_results:
                if progress:
                    progress(f"  Reached target limit: {len(cards)} >= {max_results}")
                break

            feed_text = await page.locator('div[role="feed"]').inner_text()
            if any(m in feed_text.lower() for m in END_OF_LIST_MARKERS):
                if progress:
                    progress("  Reached end of search results list on Google Maps.")
                break

            stale_rounds = stale_rounds + 1 if len(cards) == before else 0
            if stale_rounds >= 3:
                if progress:
                    progress(f"  Scrolling halted: no new results loaded after 3 attempts. Total cards: {len(cards)}")
                break

            try:
                await page.locator('div[role="feed"] a[href*="/maps/place/"]').last.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                await page.eval_on_selector('div[role="feed"]', "el => el.scrollBy(0, el.clientHeight * 2)")
            try:
                await page.wait_for_function(
                    """(count) => document.querySelectorAll('div.Nv2PK').length > count""",
                    arg=len(cards),
                    timeout=settings.FEED_SCROLL_PAUSE_MS,
                )
            except PWTimeout:
                pass

        return list(cards.values())[:max_results]

    # ------------------------------------------------------------------ #
    # Social Profile Discovery Agent seam (agents/social_agent.py) — new,
    # isolated entry point added for the streaming experiment. Does not
    # call _scrape_details()/_extract_place() (no detail-page navigation:
    # social discovery only needs card-visible fields, never phone, so the
    # production phone-quality-gate concern that drives detail navigation
    # in scrape() doesn't apply here). scrape() itself is untouched.
    # ------------------------------------------------------------------ #
    async def stream_social_candidates(
        self, query: str, max_candidate_budget: int, on_new_cards, stop_event: asyncio.Event, progress=None,
    ) -> int:
        """Scroll-collect card candidates for ``query``, invoking
        ``on_new_cards(list[dict])`` after every round with that round's
        newly-deduped cards, stopping early if ``stop_event`` is set.
        Returns the total unique card count seen. Single-business direct
        redirects (rare for a category+location query) yield zero cards —
        the social pipeline's own Maps-query fan-out (multiple location
        variants) covers that case, same as the production path.
        """
        context = await self.browser_manager.new_context()
        try:
            page = await context.new_page()
            url = settings.MAPS_SEARCH_URL.format(query=urllib.parse.quote(query))
            await page.goto(url, wait_until="commit")
            await self._handle_consent(page)
            if "/maps/place/" in page.url:
                return 0
            cards = await self._collect_listing_cards(
                page, max_candidate_budget, progress, on_new_cards=on_new_cards, stop_event=stop_event,
            )
            return len(cards)
        finally:
            await context.close()

    async def _produce_links_streaming(
        self, page: Page, max_links: int, queue: "asyncio.Queue[str | None]",
        stop_event: asyncio.Event, num_consumers: int, progress=None,
    ) -> None:
        """Scroll the results feed and push new links onto ``queue`` as
        they're found, instead of returning a finished list — lets
        consumers start scraping detail pages while the feed is still
        being scrolled (pipelining A/B test, PIPELINE_DETAIL_SCRAPING)."""
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=20_000)
        except PWTimeout:
            if progress:
                progress("  Error: Results feed element not found on the page.")
            for _ in range(num_consumers):
                await queue.put(None)
            return

        seen: set[str] = set()
        stale_rounds = 0

        for scroll_round in range(settings.MAX_FEED_SCROLLS):
            if stop_event.is_set():
                break

            hrefs = await page.eval_on_selector_all(
                'div[role="feed"] a[href*="/maps/place/"]',
                "els => els.map(e => e.href)",
            )
            before = len(seen)
            for href in hrefs:
                clean = href.split("?")[0]
                if clean not in seen:
                    seen.add(clean)
                    await queue.put(clean)

            if progress and len(seen) != before:
                progress(f"  Collected {len(seen)} links so far...")

            if len(seen) >= max_links:
                if progress:
                    progress(f"  Reached target limit: {len(seen)} >= {max_links}")
                break

            feed_text = await page.locator('div[role="feed"]').inner_text()
            if any(m in feed_text.lower() for m in END_OF_LIST_MARKERS):
                if progress:
                    progress("  Reached end of search results list on Google Maps.")
                break

            stale_rounds = stale_rounds + 1 if len(seen) == before else 0
            if stale_rounds >= 3:
                if progress:
                    progress(f"  Scrolling halted: no new results loaded after 3 attempts. Total links: {len(seen)}")
                break

            try:
                await page.locator('div[role="feed"] a[href*="/maps/place/"]').last.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                await page.eval_on_selector(
                    'div[role="feed"]', "el => el.scrollBy(0, el.clientHeight * 2)"
                )
            try:
                await page.wait_for_function(
                    """(count) => document.querySelectorAll(
                        'div[role="feed"] a[href*="/maps/place/"]'
                    ).length > count""",
                    arg=len(seen),
                    timeout=settings.FEED_SCROLL_PAUSE_MS,
                )
            except PWTimeout:
                pass

        # Natural end (not cancelled) — tell each consumer no more work is
        # coming, so they exit instead of blocking on queue.get() forever.
        for _ in range(num_consumers):
            await queue.put(None)

    async def _pipeline_scrape(
        self, context: BrowserContext, page: Page, max_links: int,
        query: str, max_results: int, progress=None,
    ) -> list[dict]:
        """Run link collection and detail scraping concurrently: a producer
        scrolls the feed while a fixed pool of page-reusing consumers drain
        the queue, instead of two sequential phases."""
        concurrency = settings.get_optimal_concurrency(hard_cap=settings.DETAIL_CONCURRENCY)
        queue: "asyncio.Queue[str | None]" = asyncio.Queue()
        stop_event = asyncio.Event()
        unique_leads: list[dict] = []
        seen: set[tuple] = set()
        lock = asyncio.Lock()

        producer_task = asyncio.create_task(
            self._produce_links_streaming(page, max_links, queue, stop_event, concurrency, progress)
        )

        async def consumer() -> None:
            detail_page = None
            try:
                detail_page = await context.new_page()
                while not stop_event.is_set():
                    link = await queue.get()
                    if link is None:
                        break
                    # Metric discarded here — this pipelined path is off by
                    # default (measured to regress, settings.PIPELINE_DETAIL_SCRAPING)
                    # and isn't wired into the diagnostics added for _scrape_details.
                    lead, _metric = await self._scrape_detail_with_retry_on_page(
                        detail_page, link, query, progress=progress
                    )
                    if lead:
                        async with lock:
                            if stop_event.is_set():
                                break
                            key = (lead["name"], lead["address"])
                            if key not in seen:
                                seen.add(key)
                                unique_leads.append(lead)
                                if progress:
                                    progress(f"  [{len(unique_leads)}/{max_results}] {lead['name']}")
                                phone_ratio = sum(1 for l in unique_leads if l.get("phone")) / len(unique_leads)
                                if len(unique_leads) >= max_results and (
                                    phone_ratio >= 0.8 or len(unique_leads) >= max_results * 2
                                ):
                                    stop_event.set()
                                    if not producer_task.done():
                                        producer_task.cancel()
                                    current = asyncio.current_task()
                                    for t in consumer_tasks:
                                        if t is not current and not t.done():
                                            t.cancel()
                                    break
            except Exception as exc:
                if progress:
                    progress(f"  Error in consumer: {exc}")
            finally:
                if detail_page is not None:
                    await detail_page.close()

        consumer_tasks = [asyncio.create_task(consumer()) for _ in range(concurrency)]
        phase_timeout = settings.get_detail_phase_timeout(max_results)
        try:
            await asyncio.wait_for(
                asyncio.gather(producer_task, *consumer_tasks, return_exceptions=True),
                timeout=phase_timeout,
            )
        except asyncio.TimeoutError:
            if progress:
                progress(
                    f"  Pipeline hit {phase_timeout}s ceiling — "
                    f"returning {len(unique_leads)} leads found so far."
                )
            if not producer_task.done():
                producer_task.cancel()
            for t in consumer_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(producer_task, *consumer_tasks, return_exceptions=True)

        unique_leads.sort(key=lambda l: 0 if l.get("phone") else 1)
        return unique_leads[:max_results]

    # ------------------------------------------------------------------ #
    # Detail pages
    # ------------------------------------------------------------------ #
    async def _scrape_details(
        self, context: BrowserContext, cards: list[dict], query: str, max_results: int, progress=None,
        trace: "_RequestTrace | None" = None,
    ) -> tuple[list[dict], dict]:
        """Resolve each candidate card to a lead. When
        settings.CARD_SHORTCUT_ENABLED is on (default) and a card already
        has a phone number, the lead is built directly from card fields —
        no detail-page navigation at all (measured 6-run benchmark: ~65%
        latency reduction, phone_fill held >=0.80). Any candidate without
        a card phone — or every candidate, when the flag is off — falls
        back to the original _scrape_detail_with_retry_on_page()/
        _extract_place() detail-page path unchanged. Detail pages are
        created lazily (only once a worker actually needs one), which is
        behaviourally identical to the old unconditional-upfront creation
        when the flag is off (every candidate still needs one, just
        created on first use instead of before the loop).
        """
        concurrency = settings.get_optimal_concurrency(hard_cap=settings.DETAIL_CONCURRENCY)
        if progress:
            progress(
                f"  Using {concurrency} concurrent detail workers "
                f"(cpu_load={settings.get_last_cpu_load():.0f}%, "
                f"card_shortcut={'on' if settings.CARD_SHORTCUT_ENABLED else 'off'})"
            )
        queue: "asyncio.Queue[tuple[int, dict] | None]" = asyncio.Queue()
        for idx, c in enumerate(cards):
            await queue.put((idx, c))
        for _ in range(concurrency):
            await queue.put(None)

        unique_leads: list[dict] = []
        seen: set[tuple] = set()
        lock = asyncio.Lock()
        stop_event = asyncio.Event()

        # Instrumentation only (not returned to the API caller) — candidate-
        # level timing to diagnose where detail-phase time goes, and
        # per-worker busy/idle time to see if load is spread evenly. Also
        # tracks shortcut vs navigated counts for the card-shortcut feature.
        candidate_metrics: list[dict] = []
        worker_stats: dict[int, dict] = {
            wid: {
                "worker_id": wid, "candidates_processed": 0, "total_busy_ms": 0.0,
                "total_idle_ms": 0.0, "shortcut_count": 0, "navigated_count": 0,
            }
            for wid in range(concurrency)
        }

        async def consumer(worker_id: int) -> None:
            detail_page = None
            if trace is not None:
                trace.mark("first_detail_worker_started_ms")
            try:
                while not stop_event.is_set():
                    t_idle_start = time.monotonic()
                    item = await queue.get()
                    worker_stats[worker_id]["total_idle_ms"] += (time.monotonic() - t_idle_start) * 1000
                    if item is None:
                        break
                    candidate_index, card = item
                    queue_wait_ms = round((time.monotonic() - t_phase_start) * 1000, 1)
                    t_busy_start = time.monotonic()

                    # Shortcut-eligible only when identity is valid (name +
                    # link) and the required field (phone) is present — a
                    # card missing name or link is never trustworthy enough
                    # to build a lead from without navigating.
                    if settings.CARD_SHORTCUT_ENABLED and card.get("phone") and card.get("name") and card.get("link"):
                        lead = _lead_from_card(card, query)
                        metric = {
                            "worker_id": worker_id, "candidate_index": candidate_index,
                            "link": card["link"], "shortcut": True, "success": True,
                            "total_candidate_ms": round((time.monotonic() - t_busy_start) * 1000, 1),
                            "queue_wait_ms": queue_wait_ms,
                            "goto_commit_ms": None, "readiness_wait_ms": None,
                            "extraction_evaluate_ms": None, "retry_count": 0,
                            "timeout_reason": None, "redirected": False,
                            "consent_or_error_state": None,
                        }
                        worker_stats[worker_id]["shortcut_count"] += 1
                    else:
                        if detail_page is None:
                            t_page_start = time.monotonic()
                            detail_page = await context.new_page()
                            worker_stats[worker_id]["page_creation_ms"] = round(
                                (time.monotonic() - t_page_start) * 1000, 1
                            )
                        lead, metric = await self._scrape_detail_with_retry_on_page(
                            detail_page, card["link"], query, worker_id=worker_id,
                            candidate_index=candidate_index, progress=progress, trace=trace,
                        )
                        metric["queue_wait_ms"] = queue_wait_ms
                        metric["shortcut"] = False
                        worker_stats[worker_id]["navigated_count"] += 1

                    worker_stats[worker_id]["total_busy_ms"] += (time.monotonic() - t_busy_start) * 1000
                    worker_stats[worker_id]["candidates_processed"] += 1
                    candidate_metrics.append(metric)
                    if lead:
                        async with lock:
                            if stop_event.is_set():
                                break
                            key = (lead["name"], lead["address"])
                            if key not in seen:
                                seen.add(key)
                                unique_leads.append(lead)
                                if trace is not None:
                                    trace.mark_leads_completed(len(unique_leads))
                                if progress:
                                    progress(f"  [{len(unique_leads)}/{max_results}] {lead['name']}")
                                # Data Quality Gate (CLAUDE.md #3): don't stop
                                # the moment we hit max_results if the batch's
                                # phone-fill rate is still under 80% — keep
                                # draining the buffer (up to 2x max_results,
                                # a hard cap so a genuinely phone-sparse area
                                # doesn't churn through the whole link pool)
                                # so there's a real chance to back-fill with
                                # phone-having leads instead of just returning
                                # a low-quality batch.
                                phone_ratio = sum(1 for l in unique_leads if l.get("phone")) / len(unique_leads)
                                if len(unique_leads) >= max_results and (
                                    phone_ratio >= 0.8 or len(unique_leads) >= max_results * 2
                                ):
                                    stop_event.set()
                                    for t in consumer_tasks:
                                        if t is not asyncio.current_task() and not t.done():
                                            t.cancel()
                                    break
            except Exception as exc:
                if progress:
                    progress(f"  Error in consumer: {exc}")
            finally:
                if detail_page is not None:
                    t_close_start = time.monotonic()
                    await detail_page.close()
                    worker_stats[worker_id]["page_close_ms"] = round((time.monotonic() - t_close_start) * 1000, 1)

        t_phase_start = time.monotonic()
        consumer_tasks = [asyncio.create_task(consumer(wid)) for wid in range(concurrency)]
        phase_timeout = settings.get_detail_phase_timeout(max_results)
        try:
            await asyncio.wait_for(
                asyncio.gather(*consumer_tasks, return_exceptions=True),
                timeout=phase_timeout,
            )
        except asyncio.TimeoutError:
            if progress:
                progress(
                    f"  Detail phase hit {phase_timeout}s ceiling — "
                    f"returning {len(unique_leads)} leads found so far instead of hanging."
                )
            for t in consumer_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*consumer_tasks, return_exceptions=True)
        phase_wall_ms = (time.monotonic() - t_phase_start) * 1000
        shortcut_total = sum(s["shortcut_count"] for s in worker_stats.values())
        navigated_total = sum(s["navigated_count"] for s in worker_stats.values())
        if progress:
            progress(
                f"  Detail phase resolved {shortcut_total} via card-shortcut, "
                f"{navigated_total} via detail navigation"
            )
            _log_detail_phase_metrics(candidate_metrics, worker_stats, phase_wall_ms, progress)
        # Prioritize phone-having leads when trimming a surplus batch down to
        # max_results, so the returned slice maximizes the quality gate.
        unique_leads.sort(key=lambda l: 0 if l.get("phone") else 1)
        return unique_leads[:max_results], {"shortcut_count": shortcut_total, "navigated_count": navigated_total}

    async def _scrape_detail_with_retry_on_page(
        self, page: Page, link: str, query: str, worker_id: int = -1,
        candidate_index: int = -1, max_retries: int = 1, progress=None,
        trace: "_RequestTrace | None" = None,
    ) -> tuple[dict | None, dict]:
        """Open one place page and extract it, retrying independently on
        navigation errors/timeouts (Speed Optimization Playbook, Step 7).

        max_retries=1 (2 total attempts): tighter than before — a single
        dead/slow link at 2 retries x (goto + h1-wait) could block a worker
        for tens of seconds, dragging the whole batch toward the phase
        ceiling. One retry still absorbs a one-off blip without letting a
        single bad link dominate the batch's wall-clock time.

        Returns ``(lead_or_None, metric)``. ``metric`` is instrumentation
        only — a candidate-level timing breakdown (goto/readiness/extraction/
        retries) used to diagnose where detail-phase time actually goes; it
        never changes what's returned as a lead or the retry/timeout behavior.
        """
        metric: dict = {
            "worker_id": worker_id,
            "candidate_index": candidate_index,
            "link": link,
            "navigation_start": time.time(),
            "queue_wait_ms": None,
            "goto_commit_ms": None,
            "readiness_wait_ms": None,
            "extraction_evaluate_ms": None,
            "total_candidate_ms": None,
            "retry_count": 0,
            "timeout_reason": None,
            "success": False,
            "final_url": "",
            "redirected": False,
            "consent_or_error_state": None,
        }
        t_total_start = time.monotonic()
        for attempt in range(max_retries + 1):
            metric["retry_count"] = attempt
            try:
                # "commit" instead of "domcontentloaded": _extract_place()
                # already waits for the h1 selector explicitly, so waiting
                # for the full DOM event here first was pure added latency.
                if trace is not None:
                    trace.mark("first_detail_navigation_started_ms")
                t_goto_start = time.monotonic()
                await page.goto(link, wait_until="commit", timeout=settings.DETAIL_GOTO_TIMEOUT_MS)
                metric["goto_commit_ms"] = round((time.monotonic() - t_goto_start) * 1000, 1)
                if "consent.google" in page.url:
                    metric["consent_or_error_state"] = "consent"
                lead = await self._extract_place(page, query, metric)
                metric["final_url"] = page.url.split("?")[0]
                metric["redirected"] = metric["final_url"] != link.split("?")[0]
                metric["success"] = lead is not None
                metric["total_candidate_ms"] = round((time.monotonic() - t_total_start) * 1000, 1)
                return lead, metric
            except (PWTimeout, PWError) as e:
                metric["timeout_reason"] = (
                    "goto_timeout" if metric["goto_commit_ms"] is None else f"post_goto:{type(e).__name__}"
                )
                if progress:
                    progress(f"  [detail-timeout attempt {attempt + 1}] {link}: {type(e).__name__}")
                if attempt == max_retries:
                    metric["total_candidate_ms"] = round((time.monotonic() - t_total_start) * 1000, 1)
                    return None, metric
                await asyncio.sleep(0.5 * (attempt + 1))
        metric["total_candidate_ms"] = round((time.monotonic() - t_total_start) * 1000, 1)
        return None, metric

    async def _extract_place(self, page: Page, query: str, metric: dict | None = None) -> dict | None:
        """Extract one business' details from an open place page.

        ``metric`` (instrumentation only, optional) gets readiness/extraction
        timing filled in if provided — never changes what's returned.
        """
        t_readiness_start = time.monotonic()
        try:
            # Tested state="attached" (element present in DOM, doesn't wait
            # for layout/visibility) instead of Playwright's default
            # state="visible" here — measured live: ~15% faster p50
            # readiness (3257ms vs baseline 3844ms), but it introduced a
            # failure mode never seen under "visible" in 15+ prior runs —
            # h1 attached with still-empty innerText (no_name_extracted),
            # extraction running ~23ms after attachment. Only survived that
            # run because the candidate buffer had one spare to cover for
            # it. Reverted: a 15% per-candidate speed gain isn't worth a
            # new, directly-observed hole in the phone/name quality gate.
            await page.wait_for_selector("h1", timeout=settings.DETAIL_H1_WAIT_TIMEOUT_MS)
        except PWTimeout:
            if metric is not None:
                metric["readiness_wait_ms"] = round((time.monotonic() - t_readiness_start) * 1000, 1)
                metric["timeout_reason"] = "h1_wait_timeout"
                if "consent.google" in page.url:
                    metric["consent_or_error_state"] = "consent"
                elif "/maps/place/" not in page.url:
                    metric["consent_or_error_state"] = "redirected_away"
                else:
                    metric["consent_or_error_state"] = "slow_render"
            return None
        if metric is not None:
            metric["readiness_wait_ms"] = round((time.monotonic() - t_readiness_start) * 1000, 1)

        # Single evaluation to avoid multiple IPC roundtrips (very slow under CPU load)
        t_eval_start = time.monotonic()
        data = await page.evaluate(r"""() => {
            const clean = (text) => {
                if (!text) return '';
                return text.replace(/[\uE000-\uF8FF]/g, '').trim().replace(/^,\s*/, '');
            };
            
            const text = (sel) => {
                const el = document.querySelector(sel);
                return el ? clean(el.innerText) : '';
            };
            
            const attr = (sel, name) => {
                const el = document.querySelector(sel);
                return el ? (el.getAttribute(name) || '') : '';
            };

            const name = text('h1.DUwDvf') || text('h1');
            const rating = text('div.F7nice span[aria-hidden="true"]');
            const reviews_raw = attr('div.F7nice span[aria-label*="review"]', 'aria-label');
            const reviews = reviews_raw.replace(/[^\d]/g, '');
            
            let phone = '';
            const phone_id = attr('button[data-item-id^="phone:tel:"]', 'data-item-id');
            if (phone_id) {
                phone = phone_id.replace('phone:tel:', '');
            } else {
                phone = text('button[data-item-id^="phone"] div.Io6YTe');
            }

            return {
                name,
                category: text('button.DkEaL'),
                rating,
                reviews,
                address: text('button[data-item-id="address"] div.Io6YTe'),
                phone,
                website: attr('a[data-item-id="authority"]', 'href'),
                plus_code: text('button[data-item-id="oloc"] div.Io6YTe')
            };
        }""")
        if metric is not None:
            metric["extraction_evaluate_ms"] = round((time.monotonic() - t_eval_start) * 1000, 1)

        if not data or not data.get("name"):
            if metric is not None:
                metric["timeout_reason"] = "no_name_extracted"
            return None

        lat, lng = "", ""
        m = COORDS_RE.search(page.url) or COORDS_AT_RE.search(page.url)
        if m:
            lat, lng = m.group(1), m.group(2)

        return {
            "query": query,
            "name": data["name"],
            "category": data["category"],
            "rating": data["rating"],
            "address": data["address"],
            "phone": data["phone"],
            "website": data["website"],
            "latitude": lat,
            "longitude": lng,
            "maps_url": page.url.split("?")[0],
            "emails": "",
        }

    # ------------------------------------------------------------------ #
    # PHASE 2 EXPERIMENT ONLY — isolated streaming discovery+detail pipeline.
    # Not called from scrape() or anywhere in the production request path;
    # only invoked by the benchmark harness. Safe to delete entirely.
    # ------------------------------------------------------------------ #
    async def scrape_pipelined_experimental(
        self, query: str, max_results: int, startup_threshold: int, progress=None,
    ) -> tuple[list[dict], dict]:
        """Unlike the existing disabled-by-default PIPELINE_DETAIL_SCRAPING
        path (which starts consumers immediately at candidate 0), this holds
        detail workers back until ``startup_threshold`` unique candidates
        are discovered, then runs discovery and detail concurrently for the
        rest of the scroll. Returns ``(leads, trace_marks)`` for the
        benchmark harness — not part of the public API.
        """
        trace = _RequestTrace()
        trace.mark("request_start_ms")
        context = await self.browser_manager.new_context()
        trace.mark("context_create_end_ms")
        try:
            page = await context.new_page()
            url = settings.MAPS_SEARCH_URL.format(query=urllib.parse.quote(query))
            await page.goto(url, wait_until="commit")
            trace.mark("search_navigation_end_ms")
            await self._handle_consent(page)

            if "/maps/place/" in page.url:
                lead = await self._extract_place(page, query)
                return ([lead] if lead else []), trace.marks

            initial_target = max(max_results + 5, math.ceil(max_results * 1.5))
            max_candidate_budget = max(max_results * 2, initial_target)
            concurrency = settings.get_optimal_concurrency(hard_cap=settings.DETAIL_CONCURRENCY)
            if progress:
                progress(
                    f"  [pipeline] Using {concurrency} concurrent detail workers "
                    f"(cpu_load={settings.get_last_cpu_load():.0f}%, startup_threshold={startup_threshold})"
                )

            queue: "asyncio.Queue[tuple[int, str]]" = asyncio.Queue(maxsize=max_candidate_budget + concurrency)
            threshold_event = asyncio.Event()
            stop_event = asyncio.Event()
            producer_done = asyncio.Event()

            seen_links: set[str] = set()
            unique_leads: list[dict] = []
            seen_leads: set[tuple] = set()
            lock = asyncio.Lock()
            candidate_metrics: list[dict] = []
            worker_stats: dict[int, dict] = {
                wid: {"worker_id": wid, "candidates_processed": 0, "total_busy_ms": 0.0, "total_idle_ms": 0.0}
                for wid in range(concurrency)
            }

            async def producer() -> None:
                trace.mark("discovery_start_ms")
                try:
                    await page.wait_for_selector('div[role="feed"]', timeout=20_000)
                except PWTimeout:
                    return
                stale_rounds = 0
                for _scroll_round in range(settings.MAX_FEED_SCROLLS):
                    if stop_event.is_set():
                        break
                    hrefs = await page.eval_on_selector_all(
                        'div[role="feed"] a[href*="/maps/place/"]',
                        "els => els.map(e => e.href)",
                    )
                    before = len(seen_links)
                    for href in hrefs:
                        # Canonical identity: strip query params before
                        # dedup/enqueue (same normalization used everywhere
                        # else in this file) — never enqueue the same place
                        # twice.
                        clean = href.split("?")[0]
                        if clean not in seen_links and len(seen_links) < max_candidate_budget:
                            seen_links.add(clean)
                            await queue.put((len(seen_links) - 1, clean))
                            if len(seen_links) >= startup_threshold:
                                threshold_event.set()
                    trace.mark_candidates_discovered(len(seen_links))
                    if progress and len(seen_links) != before:
                        progress(f"  [pipeline] Collected {len(seen_links)} links so far...")
                    if len(seen_links) >= max_candidate_budget:
                        break
                    feed_text = await page.locator('div[role="feed"]').inner_text()
                    if any(m in feed_text.lower() for m in END_OF_LIST_MARKERS):
                        break
                    stale_rounds = stale_rounds + 1 if len(seen_links) == before else 0
                    if stale_rounds >= 3:
                        break
                    try:
                        await page.locator('div[role="feed"] a[href*="/maps/place/"]').last.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        await page.eval_on_selector('div[role="feed"]', "el => el.scrollBy(0, el.clientHeight * 2)")
                    try:
                        await page.wait_for_function(
                            """(count) => document.querySelectorAll(
                                'div[role="feed"] a[href*="/maps/place/"]'
                            ).length > count""",
                            arg=len(seen_links),
                            timeout=settings.FEED_SCROLL_PAUSE_MS,
                        )
                    except PWTimeout:
                        pass

            async def consumer(worker_id: int) -> None:
                await threshold_event.wait()
                if stop_event.is_set():
                    return
                detail_page = await context.new_page()
                trace.mark("first_detail_worker_started_ms")
                try:
                    while not stop_event.is_set():
                        try:
                            candidate_index, link = await asyncio.wait_for(queue.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if producer_done.is_set() and queue.empty():
                                break
                            continue
                        t_busy_start = time.monotonic()
                        lead, metric = await self._scrape_detail_with_retry_on_page(
                            detail_page, link, query, worker_id=worker_id,
                            candidate_index=candidate_index, progress=progress, trace=trace,
                        )
                        worker_stats[worker_id]["total_busy_ms"] += (time.monotonic() - t_busy_start) * 1000
                        worker_stats[worker_id]["candidates_processed"] += 1
                        candidate_metrics.append(metric)
                        if lead:
                            async with lock:
                                if stop_event.is_set():
                                    break
                                key = (lead["name"], lead["address"])
                                if key not in seen_leads:
                                    seen_leads.add(key)
                                    unique_leads.append(lead)
                                    trace.mark_leads_completed(len(unique_leads))
                                    if progress:
                                        progress(f"  [pipeline w{worker_id}] [{len(unique_leads)}/{max_results}] {lead['name']}")
                                    if _quality_met(unique_leads, max_results):
                                        stop_event.set()
                except Exception as exc:
                    if progress:
                        progress(f"  [pipeline] Error in consumer: {exc}")
                finally:
                    await detail_page.close()

            async def producer_wrapper() -> None:
                try:
                    await producer()
                finally:
                    producer_done.set()
                    threshold_event.set()  # unblock consumers even if threshold never reached
                    trace.mark("discovery_end_ms")

            producer_task = asyncio.create_task(producer_wrapper())
            consumer_tasks = [asyncio.create_task(consumer(wid)) for wid in range(concurrency)]
            phase_timeout = settings.get_detail_phase_timeout(max_results)
            t_phase_start = time.monotonic()
            try:
                await asyncio.wait_for(
                    asyncio.gather(producer_task, *consumer_tasks, return_exceptions=True),
                    timeout=phase_timeout,
                )
            except asyncio.TimeoutError:
                if progress:
                    progress(f"  [pipeline] hit {phase_timeout}s ceiling — returning {len(unique_leads)} leads found so far.")
                stop_event.set()
                if not producer_task.done():
                    producer_task.cancel()
                for t in consumer_tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(producer_task, *consumer_tasks, return_exceptions=True)
            trace.mark("detail_end_ms")
            phase_wall_ms = (time.monotonic() - t_phase_start) * 1000

            if progress:
                _log_detail_phase_metrics(candidate_metrics, worker_stats, phase_wall_ms, progress)

            unique_leads.sort(key=lambda l: 0 if l.get("phone") else 1)
            await page.close()
            trace.mark("response_ready_ms")
            return unique_leads[:max_results], trace.marks
        finally:
            await context.close()
