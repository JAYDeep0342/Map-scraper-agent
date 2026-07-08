# settings for Playwright agent
"""Central configuration for the lead-generation scraper."""

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
