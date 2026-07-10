# settings for Playwright agent
"""Central configuration for the lead-generation scraper."""

import math
import os

import psutil

# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
HEADLESS = os.environ.get("SCRAPER_HEADLESS", "1") == "1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1366, "height": 850}
LOCALE = "en-US"

# Default navigation timeout (ms)
NAV_TIMEOUT_MS = 45_000

# ---------------------------------------------------------------------------
# Google Maps scraping
# ---------------------------------------------------------------------------
MAPS_SEARCH_URL = "https://www.google.com/maps/search/{query}?hl=en"

# How many listing detail pages to scrape in parallel (tabs per browser)
DETAIL_CONCURRENCY = 10

# Max scroll attempts on the results feed before giving up
MAX_FEED_SCROLLS = 60

# Ceiling wait after a scroll for new cards to appear (ms) — wait_for_function
# in _collect_listing_links already resolves as soon as the DOM actually
# updates, so this only matters when nothing new loads (stale rounds);
# lowered from 650 so giving up on a dry feed doesn't cost as much.
FEED_SCROLL_PAUSE_MS = 400

# Hard ceiling (seconds) on the detail-scraping phase. Guards against a
# query where most/all detail pages fail (e.g. transient rate-limiting on
# a specific place) grinding for minutes — past this, return whatever
# leads were found so far instead of hanging until every retry exhausts.
DETAIL_PHASE_TIMEOUT_S = 45

# Detail-page-specific timeouts — separate from NAV_TIMEOUT_MS (that's
# fine for the one-off consent/search-page navigation, but far too
# generous per single place-page when a batch of these stacks up across
# retries and workers).
#
# 8s/6s was tried and measured too tight: on this host, Maps often
# genuinely takes 10-20s to render a place page even when it's going to
# succeed (not dead/stuck) — at 8s/6s that showed up as a 90% per-link
# failure rate on some runs (2/20 succeeded), a real quality-gate
# violation, not a speed win. 12s/8s gives real-but-slow loads a fair
# chance while still being tighter than the original 15s/15s.
DETAIL_GOTO_TIMEOUT_MS = 12_000
DETAIL_H1_WAIT_TIMEOUT_MS = 8_000


def get_detail_phase_timeout(limit: int) -> int:
    """Scale the detail-phase ceiling to the request size instead of a flat
    45s for every job — a 10-lead request shouldn't get the same grace
    period as a 1000-lead one.

    Floor of 30s (not 15-20s): measured (Iteration with clinic/Rewa) that a
    tighter floor cuts the phase off before workers get through even two
    sequential per-worker links when Maps responds slowly (a real, frequent
    condition on this host) — each link can legitimately cost up to
    DETAIL_GOTO_TIMEOUT_MS + DETAIL_H1_WAIT_TIMEOUT_MS (~14s) even without
    retrying, and a worker may need 2+ links to reach its share of the
    batch. Cutting the phase early there returned 2/10 leads at 50% phone
    — a quality-gate violation, not just a slow response.
    """
    return min(45, max(30, limit * 3))

# Backpressure (Speed Optimization Playbook / Optimization 16): the browser
# is now a single long-lived shared instance (api/server.py), so nothing
# else limits how many /scrape/sync requests can run their browser phase at
# the same time. Each job sizes its own tab count via get_optimal_concurrency()
# without knowing about other concurrently running jobs.
#
# Measured directly on this 4-core host: 2 concurrent limit=5 jobs (each
# opening its own ~10 detail tabs against the shared browser) caused severe
# contention — one job returned 0/5 leads, the other 3/4 (75%, below the
# quality gate) at ~84s each instead of the normal ~20-30s. Same failure
# mode loop_engineering.md documented for in-job pipelining (Iteration 3-5),
# just at the cross-job level. Default is 1 (serialize scrape jobs) because
# that's what's actually been verified stable on this host — raise it only
# after measuring headroom under real concurrent load on the target host,
# and re-check get_optimal_concurrency's per-job tab count still leaves
# enough CPU/RAM for the other concurrent jobs it doesn't know about.
MAX_CONCURRENT_SCRAPE_JOBS = int(os.environ.get("MAX_CONCURRENT_SCRAPE_JOBS", "1"))

# A/B toggle: pipelined producer-consumer (scroll + detail-scrape running
# concurrently) vs sequential (scroll fully, then scrape). Default off —
# measured (loop_engineering.md, Iterations 3-5) to regress 2-5x on this
# 4-core host, back when detail tabs still opened a fresh page per link
# with no batched evaluate/commit-nav. Re-testing now that those exist.
PIPELINE_DETAIL_SCRAPING = os.environ.get("SCRAPER_PIPELINE", "0") == "1"

# Card-shortcut: build a lead directly from search-card DOM fields (name,
# phone, website, category, rating, address heuristic) instead of opening
# a detail page, for candidates whose card already has a phone number.
# Measured on a 6-run rotated benchmark (3 baseline vs 3 shortcut,
# identical "architecture in Indore" limit=10 workload): median latency
# 16.86s -> 5.95s (~65% reduction), phone_fill 0.80-1.00 -> 0.90-1.00
# (held above the 0.80 gate every run), 10/10 leads and zero crashes in
# both modes. Candidates without a card phone still fall back to the
# existing _extract_place() detail-page path unchanged. Default on — set
# to "0" to force the original detail-navigate-every-candidate behavior.
CARD_SHORTCUT_ENABLED = os.environ.get("CARD_SHORTCUT_ENABLED", "1") == "1"

# ---------------------------------------------------------------------------
# Lead enrichment (visit business website to find emails / socials)
# ---------------------------------------------------------------------------
ENRICH_TIMEOUT_MS = 10_000
ENRICH_CONCURRENCY = 10
# Candidate contact-page paths tried after the homepage
CONTACT_PATHS = ["contact", "contact-us", "contactus", "about", "about-us"]

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")


# ---------------------------------------------------------------------------
# Dynamic concurrency (Speed Optimization Playbook, Step 6)
# ---------------------------------------------------------------------------
# Set by get_optimal_concurrency()'s last call — read via get_last_cpu_load()
# so callers can log *why* a given concurrency was chosen without each of
# them calling psutil.cpu_percent() again themselves (which would consume
# the delta window get_optimal_concurrency() itself needs).
_last_cpu_load: float = 0.0


def get_optimal_concurrency(cpu_multiplier: int = 4, hard_cap: int = 50) -> int:
    """Right-size concurrent tabs/requests to the machine's *current* headroom.

    ``hard_cap`` should be the previously-tuned fixed constant for that pool
    (``DETAIL_CONCURRENCY`` / ``ENRICH_CONCURRENCY``) — measured benchmarks
    (Iteration 3) showed that scaling *up* past those on a small/busy host
    (e.g. 4 CPU cores) causes contention, not speedup, for the CPU-heavy
    Playwright detail tabs. So this only scales concurrency *down* under
    real RAM pressure, it never scales up beyond the known-good value.

    Also backs off under real-time CPU load, not just core *count* — measured
    live: opening DETAIL_CONCURRENCY (10) tabs at once while this host was
    already busy with unrelated work returned 0/10 leads, every worker still
    stuck in its first page-open/navigation when the 30s detail-phase
    ceiling cut them all off (core-count-only sizing had no way to see that
    the host was already saturated before the batch even started).
    ``cpu_percent(interval=None)`` is non-blocking — it reports the delta
    since the last call in this process, so it never stalls the event loop;
    the very first call of the process returns 0.0 (no throttling), which
    is fine since every call after that reflects real load.

    The >=90% floor is 3, not 2 — bounded benchmark (2 vs 3 vs 4 workers,
    identical "architecture in Indore" limit=10 workload, 3 runs each,
    rotated order, host pinned at 97-100% CPU throughout) measured:
      - 2 workers: 0/3 collapses, 0/3 timeouts, median phase_wall_ms 23,751
      - 3 workers: 0/3 collapses, 0/3 timeouts, median phase_wall_ms 21,810
        (~8% faster phase, ~18% faster server_time than 2, same stability)
      - 4 workers: 1/3 collapsed (needed a fallback query), 2/3 hit a
        timeout, median phase_wall_ms 30,337 — *slower* than both 2 and 3,
        because per-candidate cost roughly doubled under the extra
        contention. 4 fails "no collapse" and "wall time improves" at once.
    """
    cpu_count = os.cpu_count() or 4
    available_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
    ram_based_limit = int(available_ram_gb * 1024 / 100)  # ~100MB per tab
    cpu_based_limit = cpu_count * cpu_multiplier
    limit = max(1, min(ram_based_limit, cpu_based_limit, hard_cap))

    cpu_load = psutil.cpu_percent(interval=None)
    global _last_cpu_load
    _last_cpu_load = cpu_load
    if cpu_load >= 90:
        limit = min(limit, 3)
    elif cpu_load >= 75:
        limit = max(1, limit // 2)
    return limit


def get_last_cpu_load() -> float:
    """Diagnostics only: the CPU-load reading from the most recent
    get_optimal_concurrency() call. Call that first — this doesn't sample
    on its own."""
    return _last_cpu_load


# ---------------------------------------------------------------------------
# Social Profile Discovery Agent (Instagram/Facebook public profile lookup)
# ---------------------------------------------------------------------------
# Separate concurrency pool from ENRICH_CONCURRENCY (Playbook Step 15):
# website/profile HTTP fetches here are unrelated traffic to the maps-lead
# email enrichment pool and should be sized/tuned independently.
SOCIAL_HTTP_CONCURRENCY = 10

# Unmeasured starting value (no benchmark yet backs this number) — kept
# small deliberately since Instagram/Facebook are far more aggressive
# about rate-limiting/blocking repeated anonymous requests from one IP
# than an arbitrary small-business website is. Tune down further if a
# benchmark run shows elevated block/redirect rates.
SOCIAL_PROFILE_HTTP_CONCURRENCY = 5

SOCIAL_WEBSITE_TIMEOUT_MS = 8_000
SOCIAL_PROFILE_TIMEOUT_MS = 8_000

# Playwright fallback for follower/following/post-count/bio/image
# enrichment when the plain-HTTP og:meta fetch can't get them.
#
# PHASE 7 ROOT CAUSE (traced on a real profile, not assumed): Instagram
# serves ZERO og:meta/JSON-LD/_sharedData to an anonymous plain HTTP GET
# — confirmed via direct inspection (og:title/og:description/og:image all
# count=0, <title>Instagram</title> generic shell, no JS-rendered data in
# the raw response at all). The profile metadata is injected into the DOM
# entirely client-side after the React app hydrates, so Source A (HTTP)
# can never retrieve it — not a parser bug, not a login wall, not a
# timeout. A real rendered (anonymous, no-login) browser session DOES see
# it: og:description/meta[name=description] populate post-hydration with
# real counts and bio, no login/challenge wall encountered. See
# agents/social_agent.py's fetch_instagram_via_browser().
# PROMOTED (Phase 7 report): tested on 8 real, already-discovered
# Instagram profiles — 7/8 returned real, verified follower/following/
# post counts + bio + profile image with zero fabrication; the 1 failure
# was a genuinely nonexistent/removed account ("Profile isn't
# available"), not a method flaw. No login, no CAPTCHA bypass, identity
# verified against the already-validated URL before accepting data.
SOCIAL_BROWSER_FALLBACK_ENABLED = os.environ.get("SOCIAL_BROWSER_FALLBACK_ENABLED", "1") == "1"
SOCIAL_BROWSER_CONCURRENCY = 3
SOCIAL_BROWSER_NAV_TIMEOUT_MS = 20_000
SOCIAL_BROWSER_HYDRATE_TIMEOUT_MS = 6_000

# Phase 7 latency fix (isolated optimization #1): baseline instrumentation
# (3-run measurement) proved two defects — (1) the hydration wait used
# Playwright's default state="visible" on a <meta> tag, which has no
# bounding box and can never be "visible", so it burned the full 6s
# timeout on every single profile attempt regardless of real hydration
# speed (now fixed: state="attached"); (2) the get_attribute() reads after
# it had no explicit timeout, so a profile whose metadata never appears
# (e.g. a removed/nonexistent account) inherited the context's 45s
# default and occupied one of only SOCIAL_BROWSER_CONCURRENCY slots for
# 53-68s, measured directly. This bounds each individual metadata read
# instead. Value chosen from measured evidence, not guessed: real
# successful extraction (both fields + parsing combined) topped out at
# ~2.4s across 37 real profile attempts in the baseline; 3000ms per
# individual field read leaves ~25x margin over that observed max while
# still capping worst-case waste at a small fraction of the prior 45s.
SOCIAL_BROWSER_METADATA_READ_TIMEOUT_MS = 3_000

# Isolated Fix #3: Instagram HTTP-first is proven, structurally guaranteed
# dead work (Task 2 — 10/10 checks across 5 known valid profiles, 2 reps
# each, plus the original Phase 7 investigation: every anonymous request
# gets the identical ~596KB unhydrated JS shell, zero og:meta, zero
# JSON-LD, zero _sharedData — this is how Instagram serves ALL anonymous
# non-browser requests, not a flaky/session-specific failure rate). When
# disabled, Instagram enrichment skips straight to the proven-working
# browser fallback instead of paying ~400-1700ms per dead HTTP round-trip
# first. Facebook's HTTP path is completely untouched by this flag.
# PROMOTED (Isolated Fix #3 report): rotated A/B benchmark (A B B A A B,
# 6 runs) — median 42.81s -> 33.8s, best 34.56s -> 19.31s, worst
# 57.43s -> 45.54s, all improved. 10/10 exact limit held in all 6 runs,
# 0 duplicates, 0 schema leaks, Facebook HTTP path unaffected in every
# run (still 3 attempts/run regardless of this flag). Set to "1" to roll
# back to HTTP-first if needed.
SOCIAL_INSTAGRAM_HTTP_FIRST_ENABLED = os.environ.get("SOCIAL_INSTAGRAM_HTTP_FIRST_ENABLED", "0") == "1"

# Hard ceiling on requested limit for /social/scrape/sync — discovery here
# is a much lossier funnel than the Maps scraper (not every business has a
# resolvable public IG/FB profile), so an unbounded limit risks a request
# that scans a very large Maps candidate pool for a small qualified yield.
SOCIAL_MAX_LIMIT = 200

# Phase 2 streaming-pipeline experiment (Maps card producer -> bounded
# queue -> website resolver workers, overlapping Maps discovery with
# website checks instead of waiting for the full card list). Default OFF
# — matches this project's own convention (CARD_SHORTCUT_ENABLED,
# PIPELINE_DETAIL_SCRAPING) of shipping a new/unproven path behind a flag
# until repeated benchmark evidence (not one lucky run) justifies
# promoting it to the default. See agents/social_agent.py for the
# threshold-controlled producer/consumer implementation.
SOCIAL_STREAMING_PIPELINE_ENABLED = os.environ.get("SOCIAL_STREAMING_PIPELINE_ENABLED", "0") == "1"

# How many unique candidates the producer must have queued before resolver
# workers start consuming (Phase 2, Task 4) — configurable per-request by
# the benchmark harness, this is just the default when unspecified.
SOCIAL_STREAM_START_THRESHOLD = int(os.environ.get("SOCIAL_STREAM_START_THRESHOLD", "5"))

# Bounded queue size between the Maps card producer and website resolver
# workers. Sized from Phase 1 evidence, not guessed: SOCIAL_HTTP_CONCURRENCY
# (10) resolver workers plus the observed ~17-candidate resolution pool for
# a limit=10 request, with headroom so the producer doesn't stall waiting
# for queue space before resolvers have even started at the threshold.
SOCIAL_STREAM_QUEUE_SIZE = 40

# Circuit breaker for known-zero-yield anonymous profile enrichment (Phase
# 1 measured 0/19 successful IG/FB metadata fetches every run). After this
# many consecutive failed attempts *for a given platform* in one process,
# skip further identical attempts for the rest of that request's
# enrichment phase — output fields still return null (never fabricated),
# this only avoids paying dead network time repeatedly on the critical
# path for a result we already know will be null. Kept per-request (not
# global/process-lifetime) so a future change in platform behavior isn't
# permanently masked by an early failure streak.
SOCIAL_ENRICHMENT_CIRCUIT_BREAKER_THRESHOLD = 3


def get_social_stream_phase_timeout(limit: int) -> int:
    """Ceiling (seconds) on the streaming producer/worker phase for one
    Maps query — same scaling shape as get_detail_phase_timeout, sized a
    bit higher since each candidate here pays a full website fetch
    (homepage + optional contact page), not just a Maps detail page."""
    return min(60, max(30, limit * 4))


# ---------------------------------------------------------------------------
# Phase 3 — Maps candidate-target policy experiment (social discovery only).
# Production default ("3x") is the original, unbenchmarked formula from the
# initial build. Other policies are for the isolated benchmark harness only
# (api/routers/social.py) — do not flip the env var in production until a
# policy is promoted by repeated benchmark evidence (Phase 3 report).
# ---------------------------------------------------------------------------
SOCIAL_CANDIDATE_TARGET_POLICY = os.environ.get("SOCIAL_CANDIDATE_TARGET_POLICY", "3x")


def get_social_maps_target(needed: int, policy: str | None = None) -> int:
    """How many Maps candidates to ask for to reach ``needed`` qualified
    social leads. ``policy`` selects the formula (Phase 3, Task 2):

    - "3x" (production default, unchanged): max(needed*3, needed+10) — the
      original unmeasured starting multiplier from the initial build.
    - "2x": max(ceil(needed*2.0), needed+5)
    - "1.5x": max(ceil(needed*1.5), needed+5)
    - "adaptive": same initial ask as "1.5x"; the caller (discover()) runs
      one bounded backfill round if the smaller initial target undershoots.
    """
    policy = policy or SOCIAL_CANDIDATE_TARGET_POLICY
    if policy == "2x":
        return max(math.ceil(needed * 2.0), needed + 5)
    if policy in ("1.5x", "adaptive"):
        return max(math.ceil(needed * 1.5), needed + 5)
    return max(needed * 3, needed + 10)


def get_social_time_budget(limit: int) -> float:
    """Hard ceiling (seconds) on the whole /social/scrape/sync request —
    same reasoning as _get_scrape_time_budget in api/server.py (bounded
    query-fallback loop), sized a bit higher since each query here pays
    both a Maps discovery phase AND a website/profile HTTP phase."""
    return min(150.0, max(75.0, limit * 10.0))


# ---------------------------------------------------------------------------
# Phase 4 — resumable Maps candidate session (social discovery only).
# Root cause of the ~15-27.5s Phase 3 backfill cost: agents/social_agent.py's
# adaptive policy called MapsAgent.scrape() a second time, and scrape()
# unconditionally opens a fresh BrowserContext/Page/navigation every call
# (agents/maps_agent.py, context = browser_manager.new_context() then
# context.close() in finally) — never touched here. This flag instead lets
# the social endpoint keep ONE page alive across multiple candidate-target
# asks, reusing the same same-page continuation MapsAgent.scrape() already
# does *internally* for its own backfill loop. Default OFF pending the
# Phase 4 micro-benchmark (Task 8 promotion gate) — matches this project's
# convention of shipping a new/unproven path behind a flag.
# ---------------------------------------------------------------------------
SOCIAL_RESUMABLE_MAPS_ENABLED = os.environ.get("SOCIAL_RESUMABLE_MAPS_ENABLED", "0") == "1"

# Chunk size for resumable candidate collection (Task 10 — chunked, not
# card-by-card, continuation: Phase 2 already measured that thin per-card
# streaming starves the website-resolution worker pool). Selected per
# request by the benchmark harness; production default only matters if
# SOCIAL_RESUMABLE_MAPS_ENABLED is ever turned on.
SOCIAL_RESUMABLE_CHUNK_SIZE = int(os.environ.get("SOCIAL_RESUMABLE_CHUNK_SIZE", "8"))


# ---------------------------------------------------------------------------
# Phase 5 — card-only Maps seed collection (social discovery only).
# Measured (Task 2, 3 production runs): the Maps detail-page phase costs
# 7.1-10.4s of every 15.5-19.5s Maps scrape() call, entirely to satisfy
# CARD_SHORTCUT_ENABLED's phone-completeness gate (agents/maps_agent.py
# line ~769: falls back to detail-page navigation whenever a card lacks a
# phone number) — a concern the social product never has (confirmed: no
# code path in agents/social_agent.py reads a candidate's "phone" field).
# This flag swaps MapsAgent.scrape() for a card-only collector
# (collect_social_seed_cards(), reusing Phase 4's SocialMapsSession) that
# never navigates to a Maps detail page. Independent of
# SOCIAL_STREAMING_PIPELINE_ENABLED and SOCIAL_RESUMABLE_MAPS_ENABLED —
# does not touch either.
#
# PROMOTED (Phase 5 report, default now ON): isolated seed-acquisition
# benchmark measured ~3x faster median (16.06s -> 5.43s) with zero
# variance and IDENTICAL website coverage (18/20 every run, both paths).
# Full social benchmark: median 30.48s -> 16.15s (10/10 exact-limit held
# in all 10 runs), cross-validated on 2 other query/location workloads
# with consistent improvement, no IG/FB coverage collapse. Set to "0" to
# roll back to the original MapsAgent.scrape()-based path if needed.
# ---------------------------------------------------------------------------
SOCIAL_CARD_ONLY_SEEDS_ENABLED = os.environ.get("SOCIAL_CARD_ONLY_SEEDS_ENABLED", "1") == "1"
