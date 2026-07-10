"""Social Profile Discovery Agent — public Instagram/Facebook lookup.

Discovers businesses matching a (keyword, location) query and resolves
their public Instagram/Facebook profiles. Discovery-first, enrichment-
second (Phase 12 of the design spec): finding enough qualified leads is
never blocked by slow per-profile follower/following enrichment.

Candidate identity source: Google Maps (via MapsAgent), reusing the
existing production-proven card-first discovery instead of scraping a
general web search engine (see api/routers/social.py module docstring for
why). A candidate only becomes a *qualified* lead when its own official
website links to the Instagram/Facebook profile (or the Maps "website"
field *is* the profile itself) — no name-similarity guessing.

PHASE 1 INSTRUMENTATION (2026-07-09): this module now records detailed
per-candidate/per-request timing (see SocialRequestTrace, candidate_metrics
in discover()) to find the dominant component of the measured 21.7s
limit=10 critical path — purely additive, no pipeline/behavior change yet.
"""

import asyncio
import time
import urllib.parse

from playwright.async_api import TimeoutError as PWTimeout

from agents.maps_agent import MapsAgent, _parse_card_middle_segment, _lead_from_card, _percentile
from browser.browser_manager import BrowserManager
from config import settings
from skills.social_link_skill import (
    SocialLinkSkill, classify_platform, normalize_social_url,
    parse_count, extract_instagram_bio, _IG_META_COUNTS_RE,
)


def _normalize_key(text: str) -> str:
    return " ".join(text.strip().lower().split())


class SocialRequestTrace:
    """Mirrors agents/maps_agent.py's _RequestTrace — marks a monotonic
    elapsed-ms timestamp the *first* time each named milestone is reached.
    Spans the whole /social/scrape/sync request (created in
    api/routers/social.py, passed into every discover() call), since the
    Nth-qualified-lead milestones are request-level, not per-query."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.marks: dict[str, float] = {}
        self._resolved_thresholds = [1, 5, 8, 10]
        self.resolved_count = 0

    def mark(self, name: str) -> None:
        if name not in self.marks:
            self.marks[name] = round((time.monotonic() - self._t0) * 1000, 1)

    def mark_link_resolved(self) -> None:
        """Call once per candidate that resolves a usable IG/FB link, in
        real completion order. Measured pre-dedup (dedup is a cheap
        in-memory batch step after gather, not a network-bound one) — an
        honest proxy for "qualified lead", not identical to it."""
        self.resolved_count += 1
        for n in self._resolved_thresholds:
            if self.resolved_count >= n:
                self.mark(f"{n}_link_resolved_ms")

    def elapsed_ms(self) -> float:
        return round((time.monotonic() - self._t0) * 1000, 1)


def _peak_concurrency(intervals: list[tuple[float, float]]) -> int:
    """Reconstruct peak in-flight count from recorded (start,end) pairs via
    a sweep-line, instead of a live polling sampler — exact, zero runtime
    overhead during the actual fetches."""
    if not intervals:
        return 0
    events = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))
    events.sort()
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def _tail_waste(intervals: list[tuple[float, float]], cutoff: float) -> tuple[int, float]:
    """How many recorded fetches were still in flight or started *after*
    ``cutoff`` (the moment enough qualified leads already existed), and how
    much wall-clock time they collectively burned past that point —
    Phase 1 measurement only, nothing is cancelled yet."""
    count, wasted_ms = 0, 0.0
    for start, end in intervals:
        if end > cutoff:
            count += 1
            wasted_ms += (end - max(start, cutoff)) * 1000
    return count, round(wasted_ms, 1)


_YIELD_CHECKPOINTS = (5, 8, 10, 12, 15, 18, 20, 25, 30)
_YIELD_POSITION_TARGETS = (5, 8, 10)


def _compute_yield_curve(candidate_metrics: list[dict]) -> dict:
    """Phase 3, Task 1 — cumulative qualified-lead yield by Maps candidate
    position, in Maps discovery order (``candidate_index``). Answers "how
    many candidates does it actually take to reach N qualified leads" —
    the input the candidate-target policy decision (Task 2) needs instead
    of guessing a multiplier.
    """
    ordered = sorted(
        (cm for cm in candidate_metrics if "candidate_index" in cm),
        key=lambda c: c["candidate_index"],
    )
    cumulative: dict[str, int] = {}
    position_of_nth: dict[int, int] = {}
    running_qualified = 0
    for pos, cm in enumerate(ordered, start=1):
        if cm.get("qualified"):
            running_qualified += 1
            if running_qualified in _YIELD_POSITION_TARGETS and running_qualified not in position_of_nth:
                position_of_nth[running_qualified] = pos
        if pos in _YIELD_CHECKPOINTS:
            cumulative[f"qualified_by_candidate_{pos}"] = running_qualified
    for cp in _YIELD_CHECKPOINTS:
        cumulative.setdefault(f"qualified_by_candidate_{cp}", running_qualified)
    return {
        "cumulative": cumulative,
        "candidate_position_of_5th_qualified": position_of_nth.get(5),
        "candidate_position_of_8th_qualified": position_of_nth.get(8),
        "candidate_position_of_10th_qualified": position_of_nth.get(10),
        "total_candidates_in_curve": len(ordered),
        "total_qualified": running_qualified,
    }


class SocialMapsSession:
    """Phase 4 — social-only resumable Maps candidate session.

    Root cause fixed here (see Phase 4 report "Task 1"): MapsAgent.scrape()
    unconditionally opens a fresh BrowserContext + Page + navigation on
    every call, which is exactly right for one-shot production use
    (/scrape/sync) but meant Phase 3's adaptive backfill paid a full
    re-navigation (~15-27.5s measured) just to ask for a few more
    candidates. MapsAgent.scrape() ALREADY avoids this cost for its own
    internal backfill loop by calling _collect_listing_cards() again on
    the SAME live page (agents/maps_agent.py lines ~292-333) — cheap,
    because Google Maps never unmounts already-rendered cards, so
    re-querying the DOM for a bigger target is one fast round-trip for
    the already-loaded portion plus scrolling only the delta.

    This class exposes that exact same already-proven pattern to the
    social endpoint, without touching MapsAgent.scrape() or any
    production code path. Strictly social-only, gated by
    settings.SOCIAL_RESUMABLE_MAPS_ENABLED (default off).
    """

    def __init__(self, maps_agent: MapsAgent):
        self._maps_agent = maps_agent
        self.context = None
        self.page = None
        self.navigation_count = 0
        self.is_single_business = False

    async def open(self, query: str) -> None:
        self.context = await self._maps_agent.browser_manager.new_context()
        self.page = await self.context.new_page()
        url = settings.MAPS_SEARCH_URL.format(query=urllib.parse.quote(query))
        await self.page.goto(url, wait_until="commit")
        self.navigation_count += 1
        await self._maps_agent._handle_consent(self.page)
        if "/maps/place/" in self.page.url:
            # Single-business direct redirect — no feed to scroll. Rare for
            # a category+location query; the social query-fan-out (multiple
            # location variants) covers this case the same way the
            # non-resumable path does.
            self.is_single_business = True

    async def collect(self, target: int, progress=None) -> list[dict]:
        """Return up to `target` unique cards, continuing scroll from
        wherever the feed currently is — the resumable primitive. Calling
        this again with a bigger `target` on the same open session is the
        whole point: no new navigation, no lost scroll position.
        """
        if self.is_single_business or self.page is None:
            return []
        return await self._maps_agent._collect_listing_cards(self.page, target, progress)

    async def close(self) -> None:
        if self.context is not None:
            await self.context.close()
            self.context = None
            self.page = None


async def collect_social_seed_cards(maps_agent: MapsAgent, query: str, target: int, progress=None) -> list[dict]:
    """Phase 5 — card-only Maps seed collector for social discovery.

    Bypasses MapsAgent._scrape_details()/_extract_place() entirely: no
    detail-page navigation, no phone-completeness gate. Measured (Task 2)
    that phase costs 7-10s of every ~16-20s Maps scrape() call, entirely
    for a field (phone) social never reads. Every field social actually
    needs (name, website, category, address) is already on the card
    (Task 1 field dependency audit) — this reuses _lead_from_card(), the
    exact same shaping production's card-shortcut path already trusts,
    just applied to every candidate instead of only phone-having ones.
    """
    session = SocialMapsSession(maps_agent)
    try:
        await session.open(query)
        raw_cards = await session.collect(target, progress)
    finally:
        await session.close()
    return [_lead_from_card(c, query) for c in raw_cards]


async def fetch_instagram_via_browser(
    browser_manager: BrowserManager, url: str, expected_username: str, semaphore: asyncio.Semaphore,
    concurrency_intervals: list | None = None,
) -> dict:
    """Phase 7, Source B — browser-rendered Instagram profile metadata.

    Root cause established by direct evidence, not assumed: anonymous
    plain-HTTP GETs to an Instagram profile receive zero og:meta/JSON-LD/
    _sharedData — the page is an unhydrated JS shell (<title>Instagram
    </title>, no profile-specific content at all). Instagram only injects
    og:description/meta[name=description] (with real follower/following/
    post counts and, in meta[name=description]'s case, the real bio) into
    the DOM client-side after the React app hydrates — verified live on a
    real profile with no login, no CAPTCHA, no stealth beyond this
    project's existing browser_manager init script.

    Navigates ONLY to the already identity-validated ``url`` — never
    searches by name (Task 7). Rejects the result if the page redirects
    to a different username, a login wall, or a challenge page.

    Phase 7 latency-investigation instrumentation (additive only, no
    behavior change): records per-phase monotonic timing under
    ``result["timing"]`` and marks its own (start, end) interval into
    ``concurrency_intervals`` if given, so callers can reconstruct actual
    concurrency achieved without a live polling sampler.
    """
    result = {
        "bio": "", "profile_image_url": "", "followers": None, "following": None,
        "posts_count": None, "source": "browser", "failure_reason": None,
        "username": expected_username, "timing": {},
    }
    t_enter = time.monotonic()
    async with semaphore:
        t_sem_acquired = time.monotonic()
        result["timing"]["queue_wait_ms"] = round((t_sem_acquired - t_enter) * 1000, 1)
        if concurrency_intervals is not None:
            concurrency_intervals.append([t_sem_acquired, None])
            _interval_slot = concurrency_intervals[-1]
        else:
            _interval_slot = None
        context = None
        try:
            t_ctx_start = time.monotonic()
            context = await browser_manager.new_context()
            page = await context.new_page()
            t_ctx_end = time.monotonic()
            result["timing"]["page_acquire_ms"] = round((t_ctx_end - t_ctx_start) * 1000, 1)

            t_goto_start = time.monotonic()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.SOCIAL_BROWSER_NAV_TIMEOUT_MS)
            except PWTimeout:
                result["failure_reason"] = "navigation_timeout"
                result["timing"]["goto_ms"] = round((time.monotonic() - t_goto_start) * 1000, 1)
                return result
            except Exception as exc:
                result["failure_reason"] = f"navigation_error:{type(exc).__name__}"
                result["timing"]["goto_ms"] = round((time.monotonic() - t_goto_start) * 1000, 1)
                return result
            result["timing"]["goto_ms"] = round((time.monotonic() - t_goto_start) * 1000, 1)

            final_url = page.url
            if "/accounts/login" in final_url:
                result["failure_reason"] = "login_redirect"
                return result
            if "/challenge" in final_url:
                result["failure_reason"] = "challenge_redirect"
                return result
            _platform, _canonical, final_username = normalize_social_url(final_url)
            if final_username and final_username.lower() != expected_username.lower():
                result["failure_reason"] = "identity_mismatch"
                return result

            # Isolated Fix #2 — race valid-profile hydration against proven
            # unavailable-state detection in one Playwright wait_for_function
            # call, instead of waiting out the full hydration ceiling before
            # checking for "unavailable" separately (the prior Fix #1
            # approach). Measured (Task 1/2, 12 checks across 2 reps, 6
            # profiles): meta[name="description"] attaches with real content
            # essentially the instant domcontentloaded fires for every valid
            # profile tested (42-91ms race latency); "isn't available" body
            # text appears reliably ~360-530ms *before* the page title
            # updates for unavailable profiles (1.1-1.4s race latency, vs.
            # the previous ~6.5-6.8s ceiling wait). Zero false positives,
            # zero false negatives across all 12 checks. A single
            # wait_for_function (not two racing asyncio tasks) avoids any
            # task-cancellation/leak risk — the browser evaluates both
            # conditions itself and returns on the first true one.
            t_hydrate_start = time.monotonic()
            terminal_state = None
            try:
                handle = await page.wait_for_function(
                    """() => {
                        const meta = document.querySelector('meta[name="description"]');
                        if (meta && meta.content) return 'valid';
                        const bodyText = document.body ? document.body.innerText : '';
                        if (bodyText && bodyText.toLowerCase().includes("isn't available")) return 'unavailable';
                        return false;
                    }""",
                    timeout=settings.SOCIAL_BROWSER_HYDRATE_TIMEOUT_MS,
                )
                terminal_state = await handle.json_value()
            except PWTimeout:
                terminal_state = None
            result["timing"]["hydration_wait_ms"] = round((time.monotonic() - t_hydrate_start) * 1000, 1)

            if terminal_state == "unavailable":
                result["failure_reason"] = "profile_unavailable"
                return result
            if terminal_state != "valid":
                result["failure_reason"] = "hydration_timeout"
                return result

            # Task 2 — each metadata read gets its own small bounded
            # timeout (SOCIAL_BROWSER_METADATA_READ_TIMEOUT_MS, chosen from
            # measured successful-read timing — see settings.py) and its
            # own try/except, so a slow/missing og:image can never block
            # reading the description (bio/counts), or inherit the 45s
            # context-level navigation timeout as it did before.
            t_extract_start = time.monotonic()
            description = None
            description_timed_out = False
            try:
                t_desc_start = time.monotonic()
                description = await page.get_attribute(
                    'meta[name="description"]', "content",
                    timeout=settings.SOCIAL_BROWSER_METADATA_READ_TIMEOUT_MS,
                )
                result["timing"]["description_read_ms"] = round((time.monotonic() - t_desc_start) * 1000, 1)
            except PWTimeout:
                description_timed_out = True
                result["timing"]["description_read_ms"] = settings.SOCIAL_BROWSER_METADATA_READ_TIMEOUT_MS

            try:
                t_img_start = time.monotonic()
                image = await page.get_attribute(
                    'meta[property="og:image"]', "content",
                    timeout=settings.SOCIAL_BROWSER_METADATA_READ_TIMEOUT_MS,
                )
                result["timing"]["image_read_ms"] = round((time.monotonic() - t_img_start) * 1000, 1)
            except PWTimeout:
                image = None
                result["timing"]["image_read_ms"] = settings.SOCIAL_BROWSER_METADATA_READ_TIMEOUT_MS
            result["profile_image_url"] = image or ""

            t_parse_start = time.monotonic()
            if description:
                try:
                    m = _IG_META_COUNTS_RE.search(description)
                    if m:
                        result["followers"] = parse_count(m.group(1))
                        result["following"] = parse_count(m.group(2))
                        result["posts_count"] = parse_count(m.group(3))
                    result["bio"] = extract_instagram_bio(description)
                except Exception:
                    result["failure_reason"] = "extraction_failed"
            result["timing"]["extraction_parse_ms"] = round((time.monotonic() - t_parse_start) * 1000, 1)
            result["timing"]["extraction_ms"] = round((time.monotonic() - t_extract_start) * 1000, 1)

            if result["followers"] is None and not result["failure_reason"]:
                if description_timed_out:
                    result["failure_reason"] = "metadata_read_timeout"
                elif not description:
                    result["failure_reason"] = "description_missing"
                else:
                    result["failure_reason"] = "extraction_failed"
            return result
        except Exception as exc:
            result["failure_reason"] = f"error:{type(exc).__name__}"
            return result
        finally:
            t_cleanup_start = time.monotonic()
            if context is not None:
                await context.close()
            result["timing"]["page_cleanup_ms"] = round((time.monotonic() - t_cleanup_start) * 1000, 1)
            result["timing"]["total_profile_ms"] = round((time.monotonic() - t_enter) * 1000, 1)
            # Task 5 — single source of truth for terminal_state, derived
            # from failure_reason so every return path (including the
            # generic except-Exception fallback) is covered without
            # repeating this assignment at each individual return.
            result["terminal_state"] = result["failure_reason"] or "valid_profile"
            if _interval_slot is not None:
                _interval_slot[1] = time.monotonic()


class SocialDiscoveryAgent:
    """Resolves a (keyword, location) query into qualified IG/FB leads."""

    def __init__(self, browser_manager: BrowserManager):
        self.browser_manager = browser_manager

    async def discover(
        self, keyword: str, location: str, needed: int, progress=None,
        trace: "SocialRequestTrace | None" = None, target_policy: str | None = None,
    ) -> tuple[list[dict], dict]:
        """Return (leads, metrics) for one Maps query. ``needed`` is how
        many *qualified* social leads this call should try to produce —
        the internal Maps candidate ask is inflated well above that since
        not every business has a resolvable public profile.

        ``target_policy`` (Phase 3, Task 2) selects the candidate-target
        formula — see settings.get_social_maps_target(). Defaults to
        settings.SOCIAL_CANDIDATE_TARGET_POLICY (production default: "3x",
        i.e. the original max(needed*3, needed+10) formula — unchanged
        unless a benchmark promotes a different policy).
        """
        t0 = time.monotonic()
        policy = target_policy or settings.SOCIAL_CANDIDATE_TARGET_POLICY
        metrics = {
            "target_policy": policy,
            "raw_candidates": 0, "unique_candidates": 0,
            "websites_checked": 0, "instagram_profiles_found": 0,
            "facebook_profiles_found": 0, "qualified_leads": 0,
            "duplicates_removed": 0, "profile_enrichment_attempted": 0,
            "profile_enrichment_succeeded": 0,
            "discovery_ms": 0.0, "link_resolution_ms": 0.0, "enrichment_ms": 0.0,
            "homepage_fetch_count": 0, "contact_fallback_fetch_count": 0,
            "websites_ig_or_fb_on_homepage": 0, "websites_requiring_secondary": 0,
            "websites_no_social_found": 0, "no_website_count": 0,
            "peak_website_http_concurrency": 0, "peak_profile_http_concurrency": 0,
            "resolution_wasted_task_count": 0, "resolution_wasted_ms": 0.0,
            "enrichment_wasted_task_count": 0, "enrichment_wasted_ms": 0.0,
            "backfill_rounds": 0, "backfill_ms": 0.0, "backfill_extra_candidates": 0,
        }

        maps_target = settings.get_social_maps_target(needed, policy)
        max_candidate_budget = max(needed * 3, maps_target)  # never exceeds the current-policy ceiling
        metrics["requested_maps_target"] = maps_target
        maps_agent = MapsAgent(self.browser_manager)
        query = f"{keyword} in {location}" if location else keyword
        metrics["card_only_seeds"] = settings.SOCIAL_CARD_ONLY_SEEDS_ENABLED
        if trace:
            trace.mark("maps_discovery_start_ms")
        if settings.SOCIAL_CARD_ONLY_SEEDS_ENABLED:
            # Phase 5 — skips MapsAgent._scrape_details()/detail-page
            # navigation entirely (see settings.py docstring for the
            # measured cost this avoids and why social never needs it).
            candidates = await collect_social_seed_cards(maps_agent, query, maps_target, progress=progress)
        else:
            candidates = await maps_agent.scrape(query, max_results=maps_target, progress=progress)
        if trace:
            # MapsAgent.scrape() returns its full candidate list in one
            # shot (not streamed) — so "first candidate found" and "Maps
            # discovery end" are necessarily the same timestamp today.
            # That's itself a Phase-1 finding, not an instrumentation gap:
            # it confirms discovery and link-resolution are NOT currently
            # pipelined (see api/routers/social.py PHASE-2 note).
            trace.mark("maps_discovery_end_ms")
            trace.mark("first_candidate_ms")
        metrics["raw_candidates"] = len(candidates)
        metrics["discovery_ms"] = round((time.monotonic() - t0) * 1000, 1)

        seen_candidates: set[str] = set()
        unique_candidates = []
        for c in candidates:
            key = f"{_normalize_key(c['name'])}|{_normalize_key(c.get('address', ''))}"
            if key not in seen_candidates:
                seen_candidates.add(key)
                unique_candidates.append(c)
        metrics["unique_candidates"] = len(unique_candidates)

        link_skill = SocialLinkSkill()
        semaphore = asyncio.Semaphore(settings.SOCIAL_HTTP_CONCURRENCY)
        resolved: list[dict] = []
        candidate_metrics: list[dict] = []
        website_intervals: list[tuple[float, float]] = []
        next_index = 0

        async def resolve_one(index: int, candidate: dict) -> None:
            t_created = time.monotonic()
            async with semaphore:
                t_acquired = time.monotonic()
                links, cand_metric = await self._resolve_links(candidate, link_skill, metrics, website_intervals)
                t_done = time.monotonic()
                cand_metric.update({
                    "candidate_index": index,
                    "name": candidate["name"][:60],
                    "website_queue_wait_ms": round((t_acquired - t_created) * 1000, 1),
                    "total_candidate_ms": round((t_done - t_created) * 1000, 1),
                })
                candidate_metrics.append(cand_metric)
                if links["instagram_url"] or links["facebook_url"]:
                    if trace:
                        trace.mark_link_resolved()
                    resolved.append({**candidate, **links, "_idx": index})

        async def resolve_batch(batch: list[dict]) -> None:
            nonlocal next_index
            try:
                await asyncio.gather(*(
                    resolve_one(next_index + i, c) for i, c in enumerate(batch)
                ), return_exceptions=True)
            finally:
                next_index += len(batch)

        leads, seen_ig, seen_fb, seen_identity = [], set(), set(), set()
        qualified_idx: set[int] = set()

        def dedup_and_append(batch: list[dict]) -> None:
            for r in batch:
                ig, fb = r["instagram_url"], r["facebook_url"]
                identity_key = f"{_normalize_key(r['name'])}|{_normalize_key(r.get('address', ''))}"
                if (ig and ig in seen_ig) or (fb and fb in seen_fb) or (
                        not ig and not fb and identity_key in seen_identity):
                    metrics["duplicates_removed"] += 1
                    continue
                if ig:
                    seen_ig.add(ig)
                if fb:
                    seen_fb.add(fb)
                seen_identity.add(identity_key)
                leads.append(r)
                qualified_idx.add(r["_idx"])

        t_link_start = time.monotonic()
        try:
            await resolve_batch(unique_candidates)
            dedup_and_append(resolved)

            # Task 2, Policy D — adaptive two-stage: a single bounded
            # backfill round (not the general 3-round case) if the smaller
            # initial target undershot. Reuses maps_agent.scrape() again (a
            # fresh Maps navigation+scroll, not an incremental continuation
            # — the incremental streaming seam from Phase 2 exists but
            # Task 3 of this phase explicitly excludes mixing streaming
            # into the target-policy experiment) — the cost of that
            # re-navigation is measured (backfill_ms), not hidden.
            if policy == "adaptive" and len(leads) < needed and maps_target < max_candidate_budget:
                deficit = needed - len(leads)
                increment = max(deficit * 2, 5)
                new_target = min(maps_target + increment, max_candidate_budget)
                if new_target > maps_target:
                    t_backfill_start = time.monotonic()
                    maps_target = new_target
                    more_candidates = await maps_agent.scrape(query, max_results=maps_target, progress=progress)
                    incremental = []
                    for c in more_candidates:
                        key = f"{_normalize_key(c['name'])}|{_normalize_key(c.get('address', ''))}"
                        if key not in seen_candidates:
                            seen_candidates.add(key)
                            incremental.append(c)
                    metrics["backfill_extra_candidates"] = len(incremental)
                    if incremental:
                        backfill_start_idx = next_index
                        await resolve_batch(incremental)
                        new_resolved = [r for r in resolved if r["_idx"] >= backfill_start_idx]
                        dedup_and_append(new_resolved)
                    metrics["backfill_rounds"] = 1
                    metrics["backfill_ms"] = round((time.monotonic() - t_backfill_start) * 1000, 1)
                    metrics["raw_candidates"] = len(candidates) + len(more_candidates)
                    metrics["unique_candidates"] = len(seen_candidates)
        finally:
            await link_skill.aclose()
        t_link_end = time.monotonic()
        metrics["link_resolution_ms"] = round((t_link_end - t_link_start) * 1000, 1)
        metrics["peak_website_http_concurrency"] = _peak_concurrency(website_intervals)

        # Phase-1 wasted-tail measurement: how much website-fetch work ran
        # *after* the Nth (=needed) link had already resolved. Nothing is
        # cancelled — this only measures the opportunity.
        need_mark = f"{min(needed, 10)}_link_resolved_ms" if needed <= 10 else None
        if trace and need_mark and need_mark in trace.marks:
            cutoff = trace._t0 + trace.marks[need_mark] / 1000
            wasted_count, wasted_ms = _tail_waste(website_intervals, cutoff)
            metrics["resolution_wasted_task_count"] = wasted_count
            metrics["resolution_wasted_ms"] = wasted_ms

        for cm in candidate_metrics:
            cm["qualified"] = cm.get("candidate_index") in qualified_idx
            if cm.get("rejection_reason") == "no_website":
                metrics["no_website_count"] += 1
                continue
            metrics["homepage_fetch_count"] += 1
            if cm.get("homepage_had_social"):
                metrics["websites_ig_or_fb_on_homepage"] += 1
            if cm.get("used_secondary"):
                metrics["contact_fallback_fetch_count"] += 1
                metrics["websites_requiring_secondary"] += 1
            if cm.get("rejection_reason") == "no_social_links_found":
                metrics["websites_no_social_found"] += 1

        yield_curve = _compute_yield_curve(candidate_metrics)
        metrics["yield_curve"] = yield_curve
        metrics["candidate_overfetch_count"] = (
            metrics["raw_candidates"] - yield_curve["candidate_position_of_10th_qualified"]
            if len(leads) >= 10 and yield_curve["candidate_position_of_10th_qualified"] else None
        )

        metrics["qualified_leads"] = len(leads)
        metrics["instagram_profiles_found"] = sum(1 for l in leads if l["instagram_url"])
        metrics["facebook_profiles_found"] = sum(1 for l in leads if l["facebook_url"])

        t_enrich_start = time.monotonic()
        if trace:
            trace.mark("enrichment_start_ms")
        profile_intervals = await self._enrich(leads, metrics)
        if trace:
            trace.mark("enrichment_end_ms")
        metrics["enrichment_ms"] = round((time.monotonic() - t_enrich_start) * 1000, 1)
        metrics["peak_profile_http_concurrency"] = _peak_concurrency(profile_intervals)

        if progress:
            progress("  Social per-candidate metrics (Phase 1): " + str(candidate_metrics))
            progress("  Social discovery metrics: " + str(metrics))
            if trace:
                progress(f"  Social request trace so far: {trace.marks}")

        return [self._to_output_shape(l) for l in leads], metrics

    # ------------------------------------------------------------------ #
    # Phase 2 EXPERIMENT — streaming pipeline (Maps card producer -> bounded
    # queue -> website resolver workers), gated by
    # settings.SOCIAL_STREAMING_PIPELINE_ENABLED (default off). Mirrors the
    # proven producer/consumer/threshold_event/stop_event pattern already
    # used by MapsAgent.scrape_pipelined_experimental() in this codebase
    # (same shutdown/cancellation shape), applied to website social-link
    # resolution instead of Maps detail-page navigation. discover() above
    # is untouched and remains the sequential baseline this is benchmarked
    # against — do not merge behavior between the two paths.
    # ------------------------------------------------------------------ #
    async def discover_streaming(
        self, keyword: str, location: str, needed: int, start_threshold: int,
        progress=None, trace: "SocialRequestTrace | None" = None,
    ) -> tuple[list[dict], dict]:
        t0 = time.monotonic()
        metrics = {
            "raw_candidates": 0, "unique_candidates": 0,
            "websites_checked": 0, "instagram_profiles_found": 0,
            "facebook_profiles_found": 0, "qualified_leads": 0,
            "duplicates_removed": 0, "profile_enrichment_attempted": 0,
            "profile_enrichment_succeeded": 0,
            "discovery_ms": 0.0, "link_resolution_ms": 0.0, "enrichment_ms": 0.0,
            "homepage_fetch_count": 0, "contact_fallback_fetch_count": 0,
            "websites_ig_or_fb_on_homepage": 0, "websites_requiring_secondary": 0,
            "websites_no_social_found": 0, "no_website_count": 0,
            "peak_website_http_concurrency": 0, "peak_profile_http_concurrency": 0,
            "secondary_pages_avoided": 0, "queue_peak_size": 0, "producer_blocked_ms": 0.0,
            "start_threshold": start_threshold, "producer_done_ms": None,
        }

        maps_target = max(needed * 3, needed + 10)
        maps_agent = MapsAgent(self.browser_manager)
        query = f"{keyword} in {location}" if location else keyword

        queue: "asyncio.Queue[dict | None]" = asyncio.Queue(maxsize=settings.SOCIAL_STREAM_QUEUE_SIZE)
        threshold_event = asyncio.Event()
        stop_event = asyncio.Event()
        producer_done = asyncio.Event()
        concurrency = settings.SOCIAL_HTTP_CONCURRENCY

        seen_candidates: set[str] = set()
        emitted_count = 0
        lock = asyncio.Lock()
        leads: list[dict] = []
        seen_ig: set[str] = set()
        seen_fb: set[str] = set()
        seen_identity: set[str] = set()
        candidate_metrics: list[dict] = []
        website_intervals: list[tuple[float, float]] = []
        link_skill = SocialLinkSkill()

        if trace:
            trace.mark("maps_discovery_start_ms")

        async def on_new_cards(new_cards: list[dict]) -> None:
            nonlocal emitted_count
            for c in new_cards:
                address, _reviews = _parse_card_middle_segment(c.get("middle_segment", ""))
                c["address"] = address
                key = f"{_normalize_key(c['name'])}|{_normalize_key(address)}"
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                t_put_start = time.monotonic()
                await queue.put(c)
                metrics["producer_blocked_ms"] += (time.monotonic() - t_put_start) * 1000
                metrics["queue_peak_size"] = max(metrics["queue_peak_size"], queue.qsize())
                emitted_count += 1
                if trace and emitted_count == 1:
                    trace.mark("first_candidate_ms")
                if emitted_count >= start_threshold:
                    threshold_event.set()

        async def producer_wrapper() -> None:
            try:
                await maps_agent.stream_social_candidates(
                    query, maps_target, on_new_cards, stop_event, progress=progress,
                )
            finally:
                producer_done.set()
                threshold_event.set()  # unblock workers even if threshold never reached
                if trace:
                    trace.mark("maps_discovery_end_ms")
                metrics["producer_done_ms"] = round((time.monotonic() - t0) * 1000, 1)

        async def worker(worker_id: int) -> None:
            await threshold_event.wait()
            if stop_event.is_set():
                return
            while not stop_event.is_set():
                try:
                    card = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if producer_done.is_set() and queue.empty():
                        break
                    continue
                t_created = time.monotonic()
                async with lock:
                    allow_secondary = len(leads) < needed
                links, cand_metric = await self._resolve_links(
                    card, link_skill, metrics, website_intervals, allow_secondary=allow_secondary,
                )
                cand_metric.update({
                    "worker_id": worker_id,
                    "name": card["name"][:60],
                    "total_candidate_ms": round((time.monotonic() - t_created) * 1000, 1),
                })
                candidate_metrics.append(cand_metric)
                if cand_metric.get("secondary_avoided_deficit_met"):
                    metrics["secondary_pages_avoided"] += 1
                if not (links["instagram_url"] or links["facebook_url"]):
                    continue
                if trace:
                    trace.mark_link_resolved()
                candidate_lead = {**card, **links}
                ig, fb = links["instagram_url"], links["facebook_url"]
                identity_key = f"{_normalize_key(card['name'])}|{_normalize_key(card.get('address', ''))}"
                async with lock:
                    if stop_event.is_set():
                        break
                    if (ig and ig in seen_ig) or (fb and fb in seen_fb) or (
                            not ig and not fb and identity_key in seen_identity):
                        metrics["duplicates_removed"] += 1
                        continue
                    if ig:
                        seen_ig.add(ig)
                    if fb:
                        seen_fb.add(fb)
                    seen_identity.add(identity_key)
                    leads.append(candidate_lead)
                    if progress:
                        progress(f"  [social-stream] [{len(leads)}/{needed}] {candidate_lead['name']}")
                    if len(leads) >= needed:
                        # Task 9 — immediate cooperative stop: mirrors the
                        # exact shutdown shape MapsAgent.scrape_pipelined_
                        # experimental() already uses for its own quality
                        # gate (stop_event + hard-cancel producer/siblings)
                        # rather than waiting for the 1s queue.get() poll.
                        stop_event.set()
                        if not producer_task.done():
                            producer_task.cancel()
                        current = asyncio.current_task()
                        for t in worker_tasks:
                            if t is not current and not t.done():
                                t.cancel()
                        break

        producer_task = asyncio.create_task(producer_wrapper())
        worker_tasks = [asyncio.create_task(worker(i)) for i in range(concurrency)]
        phase_timeout = settings.get_social_stream_phase_timeout(needed)
        t_phase_start = time.monotonic()
        try:
            await asyncio.wait_for(
                asyncio.gather(producer_task, *worker_tasks, return_exceptions=True),
                timeout=phase_timeout,
            )
        except asyncio.TimeoutError:
            if progress:
                progress(f"  [social-stream] hit {phase_timeout}s ceiling — {len(leads)} leads found so far.")
            stop_event.set()
            if not producer_task.done():
                producer_task.cancel()
            for t in worker_tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)
        finally:
            await link_skill.aclose()
        metrics["link_resolution_ms"] = round((time.monotonic() - t_phase_start) * 1000, 1)

        metrics["raw_candidates"] = len(seen_candidates)
        metrics["unique_candidates"] = len(seen_candidates)
        metrics["qualified_leads"] = len(leads)
        metrics["instagram_profiles_found"] = sum(1 for l in leads if l["instagram_url"])
        metrics["facebook_profiles_found"] = sum(1 for l in leads if l["facebook_url"])
        metrics["peak_website_http_concurrency"] = _peak_concurrency(website_intervals)
        metrics["discovery_ms"] = metrics["producer_done_ms"] or round((time.monotonic() - t0) * 1000, 1)

        for cm in candidate_metrics:
            if cm.get("rejection_reason") == "no_website":
                metrics["no_website_count"] += 1
                continue
            metrics["homepage_fetch_count"] += 1
            if cm.get("homepage_had_social"):
                metrics["websites_ig_or_fb_on_homepage"] += 1
            if cm.get("used_secondary"):
                metrics["contact_fallback_fetch_count"] += 1
                metrics["websites_requiring_secondary"] += 1
            if cm.get("rejection_reason") == "no_social_links_found":
                metrics["websites_no_social_found"] += 1

        t_enrich_start = time.monotonic()
        if trace:
            trace.mark("enrichment_start_ms")
        profile_intervals = await self._enrich(leads, metrics)
        if trace:
            trace.mark("enrichment_end_ms")
        metrics["enrichment_ms"] = round((time.monotonic() - t_enrich_start) * 1000, 1)
        metrics["peak_profile_http_concurrency"] = _peak_concurrency(profile_intervals)

        if progress:
            progress("  Social STREAM per-candidate metrics: " + str(candidate_metrics))
            progress("  Social STREAM discovery metrics: " + str(metrics))
            if trace:
                progress(f"  Social STREAM request trace so far: {trace.marks}")

        return [self._to_output_shape(l) for l in leads[:needed]], metrics

    # ------------------------------------------------------------------ #
    # Phase 5/8 — website (and Maps "website" field itself) -> IG/FB link,
    # HIGH-confidence identity only.
    # ------------------------------------------------------------------ #
    async def _resolve_links(
        self, candidate: dict, link_skill: SocialLinkSkill, metrics: dict, website_intervals: list,
        allow_secondary: bool = True,
    ) -> tuple[dict, dict]:
        website = candidate.get("website", "")
        result = {"instagram_url": None, "instagram_username": None,
                   "facebook_url": None, "facebook_username": None}
        cand_metric: dict = {"has_instagram": False, "has_facebook": False}
        if not website:
            cand_metric["rejection_reason"] = "no_website"
            return result, cand_metric

        t_validate_start = time.monotonic()
        direct_platform = classify_platform(website)
        if direct_platform:
            platform, canonical, username = normalize_social_url(website)
            cand_metric["identity_validation_ms"] = round((time.monotonic() - t_validate_start) * 1000, 1)
            if platform == "instagram":
                result["instagram_url"], result["instagram_username"] = canonical, username
                cand_metric["has_instagram"] = True
            elif platform == "facebook":
                result["facebook_url"], result["facebook_username"] = canonical, username
                cand_metric["has_facebook"] = True
            else:
                cand_metric["rejection_reason"] = "no_social_links_found"
            return result, cand_metric
        cand_metric["identity_validation_ms"] = round((time.monotonic() - t_validate_start) * 1000, 1)

        metrics["websites_checked"] += 1
        found = await link_skill.discover_from_website(
            website, timing=cand_metric, intervals=website_intervals, allow_secondary=allow_secondary,
        )
        if found.get("instagram"):
            platform, canonical, username = normalize_social_url(found["instagram"])
            result["instagram_url"], result["instagram_username"] = canonical, username
        if found.get("facebook"):
            platform, canonical, username = normalize_social_url(found["facebook"])
            result["facebook_url"], result["facebook_username"] = canonical, username
        if not result["instagram_url"] and not result["facebook_url"]:
            cand_metric["rejection_reason"] = "no_social_links_found"
        cand_metric["has_instagram"] = bool(result["instagram_url"])
        cand_metric["has_facebook"] = bool(result["facebook_url"])
        return result, cand_metric

    # ------------------------------------------------------------------ #
    # Phase 9/10/25/26 — public metadata enrichment, HTTP-first.
    #
    # Phase 2 Task 8 — circuit breaker: Phase 1 measured 0/19 successful
    # anonymous IG/FB metadata fetches across 3 identical runs. Rather
    # than delete enrichment or hardcode "always skip", this trips
    # per-platform *within one request* after SOCIAL_ENRICHMENT_CIRCUIT_
    # BREAKER_THRESHOLD consecutive zero-yield attempts, so a future
    # change in platform behavior self-heals on the next request instead
    # of being permanently masked. Skipped attempts still produce null
    # fields (never fabricated) and are counted, never hidden.
    #
    # Phase 7 — browser fallback (Instagram only): Task 0 traced the exact
    # cause of the HTTP circuit tripping every time — Instagram serves
    # zero og:meta/JSON-LD to anonymous HTTP, full stop, verified on a
    # real profile (see settings.py SOCIAL_BROWSER_FALLBACK_ENABLED
    # docstring). This runs INDEPENDENTLY of the HTTP circuit breaker
    # above (Task 2 requirement) — that breaker exists to stop paying for
    # a known-dead HTTP path, not to block a different, proven-working
    # one. Gated by SOCIAL_BROWSER_FALLBACK_ENABLED + its own bounded
    # concurrency (SOCIAL_BROWSER_CONCURRENCY), independent pools.
    # ------------------------------------------------------------------ #
    async def _enrich(self, leads: list[dict], metrics: dict) -> list[tuple[float, float]]:
        skill = SocialLinkSkill()
        semaphore = asyncio.Semaphore(settings.SOCIAL_PROFILE_HTTP_CONCURRENCY)
        browser_semaphore = asyncio.Semaphore(settings.SOCIAL_BROWSER_CONCURRENCY)
        profile_intervals: list[tuple[float, float]] = []
        lock = asyncio.Lock()
        consecutive_failures = {"instagram": 0, "facebook": 0}
        circuit_open = {"instagram": False, "facebook": False}
        empty_meta = {"bio": "", "profile_image_url": "", "followers": None, "following": None, "posts_count": None}
        metrics.setdefault("enrichment_skipped", 0)
        metrics.setdefault("enrichment_skip_reasons", {})
        metrics.setdefault("platform_capability_state", {"instagram": "unknown", "facebook": "unknown"})
        metrics.setdefault("browser_fallback_attempted", 0)
        metrics.setdefault("browser_fallback_succeeded", 0)
        metrics.setdefault("browser_fallback_failure_reasons", {})

        # Phase 7 latency-investigation instrumentation (additive only).
        t_enrich_wall_start = time.monotonic()
        browser_profile_records: list[dict] = []
        http_records: list[dict] = []
        browser_concurrency_intervals: list = []
        ig_http_dead_ms = 0.0
        ig_http_attempts = 0

        async def try_platform(lead: dict, platform: str, url_key: str, meta_key: str) -> None:
            nonlocal ig_http_dead_ms, ig_http_attempts
            url = lead[url_key]
            if not url:
                return

            # Isolated Fix #3 — Instagram HTTP is proven structurally dead
            # (Task 2: 0/10 usable-yield checks across 5 profiles x 2 reps;
            # every anonymous request gets the identical unhydrated shell).
            # Bypassing it here is Instagram-specific and does not touch
            # Facebook's HTTP path at all — Facebook keeps circuit_open/
            # consecutive_failures accounting exactly as before (Task 5).
            bypass_instagram_http = platform == "instagram" and not settings.SOCIAL_INSTAGRAM_HTTP_FIRST_ENABLED
            async with lock:
                skip_http = circuit_open[platform] or bypass_instagram_http

            meta = empty_meta
            if not skip_http:
                metrics["profile_enrichment_attempted"] += 1
                t_http_start = time.monotonic()
                meta = await skill.fetch_profile_metadata(url, platform, profile_intervals)
                http_ms = round((time.monotonic() - t_http_start) * 1000, 1)
                async with lock:
                    http_records.append({"platform": platform, "http_ms": http_ms, "success": meta.get("followers") is not None})
                    if meta.get("followers") is not None:
                        metrics["profile_enrichment_succeeded"] += 1
                        consecutive_failures[platform] = 0
                        metrics["platform_capability_state"][platform] = "yielding"
                    else:
                        consecutive_failures[platform] += 1
                        if platform == "instagram":
                            ig_http_dead_ms += http_ms
                            ig_http_attempts += 1
                        if (not circuit_open[platform]
                                and consecutive_failures[platform] >= settings.SOCIAL_ENRICHMENT_CIRCUIT_BREAKER_THRESHOLD):
                            circuit_open[platform] = True
                            metrics["platform_capability_state"][platform] = "circuit_open_zero_yield"
            else:
                async with lock:
                    metrics["enrichment_skipped"] += 1
                    skip_reason = "instagram_http_bypassed" if bypass_instagram_http else "circuit_open_zero_yield"
                    metrics["enrichment_skip_reasons"][skip_reason] = (
                        metrics["enrichment_skip_reasons"].get(skip_reason, 0) + 1
                    )

            if platform == "instagram" and meta.get("followers") is None and settings.SOCIAL_BROWSER_FALLBACK_ENABLED:
                username = lead.get("instagram_username")
                if username:
                    async with lock:
                        metrics["browser_fallback_attempted"] += 1
                    # browser_start_ms/browser_queue_wait_ms (Task 1) are
                    # already captured precisely inside fetch_instagram_via_
                    # browser()'s own result["timing"] (queue_wait_ms is
                    # timed from semaphore entry, more accurate than timing
                    # from this call site) — see browser_profile_records below.
                    browser_meta = await fetch_instagram_via_browser(
                        self.browser_manager, url, username, browser_semaphore, browser_concurrency_intervals,
                    )
                    async with lock:
                        browser_profile_records.append({
                            "username": username,
                            "success": browser_meta.get("followers") is not None,
                            "failure_reason": browser_meta.get("failure_reason"),
                            "terminal_state": browser_meta.get("terminal_state"),
                            **browser_meta.get("timing", {}),
                        })
                        if browser_meta.get("followers") is not None:
                            metrics["browser_fallback_succeeded"] += 1
                        elif browser_meta.get("failure_reason"):
                            reason = browser_meta["failure_reason"]
                            metrics["browser_fallback_failure_reasons"][reason] = (
                                metrics["browser_fallback_failure_reasons"].get(reason, 0) + 1
                            )
                    if browser_meta.get("followers") is not None or browser_meta.get("bio"):
                        meta = browser_meta

            lead[meta_key] = meta

        async def enrich_one(lead: dict) -> None:
            async with semaphore:
                lead["_ig_meta"] = None
                lead["_fb_meta"] = None
                await try_platform(lead, "instagram", "instagram_url", "_ig_meta")
                await try_platform(lead, "facebook", "facebook_url", "_fb_meta")

        try:
            await asyncio.gather(*(enrich_one(l) for l in leads), return_exceptions=True)
        finally:
            await skill.aclose()

        # Phase 7 phase-level aggregates.
        profile_ms_list = [r["total_profile_ms"] for r in browser_profile_records if "total_profile_ms" in r]
        queue_wait_list = [r["queue_wait_ms"] for r in browser_profile_records if "queue_wait_ms" in r]
        metrics["browser_enrichment"] = {
            "profiles_requested": metrics["browser_fallback_attempted"],
            "profiles_completed": len(browser_profile_records),
            "profiles_succeeded": sum(1 for r in browser_profile_records if r["success"]),
            "profiles_failed": sum(1 for r in browser_profile_records if not r["success"]),
            "peak_concurrency": _peak_concurrency([tuple(iv) for iv in browser_concurrency_intervals if iv[1] is not None]),
            "p50_profile_ms": _percentile(profile_ms_list, 50),
            "p95_profile_ms": _percentile(profile_ms_list, 95),
            "queue_wait_p50_ms": _percentile(queue_wait_list, 50),
            "queue_wait_p95_ms": _percentile(queue_wait_list, 95),
            "total_wall_ms": round((time.monotonic() - t_enrich_wall_start) * 1000, 1),
            "total_aggregate_ms": round(sum(profile_ms_list), 1),
            "instagram_http_dead_ms": round(ig_http_dead_ms, 1),
            "instagram_http_dead_attempts": ig_http_attempts,
            "instagram_http_first_enabled": settings.SOCIAL_INSTAGRAM_HTTP_FIRST_ENABLED,
            "http_attempts_total": len(http_records),
            "http_records": http_records,
            "per_profile": browser_profile_records,
        }
        return profile_intervals

    def _to_output_shape(self, lead: dict) -> dict:
        ig_meta = lead.get("_ig_meta") or {}
        fb_meta = lead.get("_fb_meta") or {}
        # Deterministic priority: Instagram bio/image first, else Facebook
        # (Phase 25/26) — documented rule, not a silent choice.
        bio = ig_meta.get("bio") or fb_meta.get("bio") or ""
        profile_image_url = ig_meta.get("profile_image_url") or fb_meta.get("profile_image_url") or ""

        return {
            "name": lead["name"],
            "location": lead.get("address", ""),
            "category": lead.get("category", ""),
            "instagram_url": lead["instagram_url"] or "",
            "instagram_username": lead["instagram_username"] or "",
            "instagram_followers": ig_meta.get("followers"),
            "instagram_following": ig_meta.get("following"),
            "instagram_posts_count": ig_meta.get("posts_count"),
            "facebook_url": lead["facebook_url"] or "",
            "facebook_username": lead["facebook_username"] or "",
            "bio": bio,
            "profile_image_url": profile_image_url,
        }
