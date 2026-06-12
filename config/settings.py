# settings for Playwright agent
"""Central configuration for the lead-generation scraper."""

import os

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
DETAIL_CONCURRENCY = 4

# Max scroll attempts on the results feed before giving up
MAX_FEED_SCROLLS = 60

# Pause between feed scrolls (ms)
FEED_SCROLL_PAUSE_MS = 1_200

# ---------------------------------------------------------------------------
# Lead enrichment (visit business website to find emails / socials)
# ---------------------------------------------------------------------------
ENRICH_TIMEOUT_MS = 20_000
ENRICH_CONCURRENCY = 4
# Candidate contact-page paths tried after the homepage
CONTACT_PATHS = ["contact", "contact-us", "contactus", "about", "about-us"]

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
