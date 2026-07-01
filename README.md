 Google Maps Lead Scraper API

A production-ready REST API that scrapes Google Maps business listings and enriches them with emails and social media links. Built with **FastAPI** + **Playwright** and designed for integration with Spring Boot backends.

---

## Features

- **Bulk lead extraction** — scrapes business name, category, rating, reviews, address, phone, website, coordinates, and Maps URL
- **Email enrichment** — visits each business website (and common contact pages) to extract real email addresses
- **Social link detection** — finds Facebook, Instagram, LinkedIn, Twitter/X, YouTube, TikTok, and WhatsApp profile links
- **Concurrent scraping** — opens multiple browser tabs in parallel for speed
- **Spring Boot ready** — single sync endpoint with CORS enabled, returns clean JSON
- **Windows compatible** — uses `WindowsProactorEventLoopPolicy` automatically

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI + Uvicorn |
| Browser Automation | Playwright (Chromium) |
| Validation | Pydantic v2 |
| Language | Python 3.11+ |

---

## Project Structure

playwright-agent/
├── main.py                  # CLI entry point (--host, --port, --reload)
├── api/
│   └── server.py            # FastAPI app, endpoints, request/response models
├── agents/
│   └── maps_agent.py        # Playwright agent — scrolls feed, extracts place details
├── skills/
│   └── extract_skill.py     # Email & social link harvester
├── browser/
│   └── browser_manager.py   # Browser lifecycle management
└── config/
└── settings.py          # Concurrency, timeouts, URLs, output dir



---

## Quick Start

### 1. Clone & create virtual environment

```bash
git clone <repo-url>
cd "GOOGLE SRAPER AGENT"
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
2. Install dependencies

pip install fastapi uvicorn playwright pydantic
playwright install chromium
3. Run the server

cd playwright-agent
python main.py
Or with custom options:


python main.py --port 9000 --reload
Server starts at http://localhost:8000

Interactive docs at http://localhost:8000/docs

API Reference
GET /health
Quick health check.


{ "status": "ok", "service": "google-maps-lead-scraper" }
POST /scrape/sync
Scrape Google Maps and return leads immediately.

Request Body


{
  "keyword":     "restaurant",
  "location":    "Indore",
  "limit":       20,
  "find_emails": true
}
Field	Type	Required	Description
keyword	string	Yes	Business type (e.g. "dentist", "b tech college")
location	string	Yes	City or area (e.g. "Mumbai", "Delhi")
limit	int	No (default: 20)	Max leads to return (1–500)
find_emails	bool	No (default: true)	Visit websites to extract emails & socials
Response


{
  "success": true,
  "total_leads": 18,
  "query": "restaurant in Indore",
  "time_seconds": 47.3,
  "leads": [
    {
      "query": "restaurant in Indore",
      "name": "Sayaji Hotel",
      "category": "Hotel",
      "rating": "4.2",
      "reviews": "3841",
      "address": "H-1, Scheme 54 PU4, Vijay Nagar, Indore",
      "phone": "+917312554000",
      "website": "https://sayajihotels.com",
      "emails": "reservations@sayajihotels.com",
      "social_links": "https://instagram.com/sayajihotels",
      "plus_code": "2GGF+J3 Indore",
      "latitude": "22.7533",
      "longitude": "75.8937",
      "maps_url": "https://www.google.com/maps/place/..."
    }
  ]
}
Configuration
Edit playwright-agent/config/settings.py to tune behaviour:

Setting	Default	Description
HEADLESS	1 (true)	Set SCRAPER_HEADLESS=0 env var to see the browser
DETAIL_CONCURRENCY	4	Parallel tabs for scraping place detail pages
MAX_FEED_SCROLLS	60	Max scroll attempts on the results feed
FEED_SCROLL_PAUSE_MS	1200	Pause between scrolls (ms)
ENRICH_CONCURRENCY	4	Parallel tabs for email enrichment
ENRICH_TIMEOUT_MS	20000	Timeout per website visit (ms)
Environment Variables

SCRAPER_HEADLESS=0  
Spring Boot Integration Example

RestTemplate restTemplate = new RestTemplate();

Map<String, Object> body = Map.of(
    "keyword",     "ca firm",
    "location",    "Ahmedabad",
    "limit",       50,
    "find_emails", true
);

ResponseEntity<String> response = restTemplate.postForEntity(
    "http://localhost:8000/scrape/sync",
    new HttpEntity<>(body, headers),
    String.class
);
