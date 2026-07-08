# Google Maps Scraper — Technical Flow Report

Ye report Google Maps Lead Scraper ke **poore working flow, technologies aur algorithms** ko cover karta hai.

---

## 1. Overview

Ye project ek **Google Maps Lead Scraper API** hai jo kisi bhi keyword + location (jaise "restaurant in Indore") ke liye business leads nikalta hai — naam, rating, address, phone, website, coordinates — aur chaahe to unki website se **email + social links** bhi nikal deta hai.

Entry point: [main.py](playwright-agent/main.py) → FastAPI server start karta hai (`uvicorn`).

---

## 2. Technology Stack

| Layer | Technology | Kaam |
|---|---|---|
| Browser automation | **Playwright (async, Chromium)** | Real browser control karke Google Maps ko human ki tarah operate karta hai |
| API framework | **FastAPI + Uvicorn** | REST endpoints serve karta hai |
| Concurrency | **Python asyncio** (`Semaphore`, `Lock`, `Event`, `gather`) | Multiple tabs/pages ek saath parallel chalata hai |
| Validation | **Pydantic v2** | Request/response ka schema validate karta hai |
| Parsing | **CSS selectors + Regex** | HTML se name, coordinates, phone, email nikalta hai |

Core files:
- [browser/browser_manager.py](playwright-agent/browser/browser_manager.py) — Chromium launch + stealth setup
- [agents/maps_agent.py](playwright-agent/agents/maps_agent.py) — Maps scraping logic
- [skills/extract_skill.py](playwright-agent/skills/extract_skill.py) — website se email/social nikalna
- [api/server.py](playwright-agent/api/server.py) — API endpoints + orchestration
- [config/settings.py](playwright-agent/config/settings.py) — sab tunable settings (timeouts, concurrency)

---

## 3. End-to-End Flow

```
Client (Spring Boot / Postman)
        │  POST /scrape/sync {keyword, location, limit, find_emails}
        ▼
api/server.py  →  _build_query_list()  →  _run_scrape()
        │
        ├─► Batch 1..N (25 queries/batch, fresh browser per batch)
        │        │
        │        ▼
        │   MapsAgent.scrape(query)
        │        │
        │        ├─► Google Maps pe navigate + consent screen handle
        │        ├─► results feed scroll karke listing links collect
        │        └─► har link pe parallel tabs khol ke detail extract
        │
        ▼
   (optional) ExtractSkill.enrich_leads()  →  website visit → email/social nikalna
        │
        ▼
   JSON Response → leads[]
```

### Step-by-step

1. **Request aata hai** — `keyword`, `location`, `limit` (1 – 100,000), `find_emails` flag. ([server.py:164](playwright-agent/api/server.py#L164))

2. **Query fan-out** (`_build_query_list`) — Google Maps ek single search se ~120 se zyada results nahi deta, isliye `limit` bada ho to system khud extra queries banata hai:
   - Primary query (`keyword in location`)
   - City ke different areas (`keyword in <area> <location>`)
   - Prefix variations (best/top rated/popular/famous/top)
   - 600+ India cities
   - State-level queries (bahut bade limit ke liye)

3. **Batch-wise browser sessions** — queries 25-25 ke batches mein chalti hain, har batch ka apna fresh browser hota hai. Ek batch crash ho jaye to sirf wahi batch fail hota hai, poora job nahi rukta (crash resilience).

4. **Listing collection** (`_collect_listing_links`) — results feed (sidebar) ko scroll karke `/maps/place/` links collect karta hai. Scroll rukta hai jab:
   - target result count mil jaaye, ya
   - "You've reached the end of the list" text dikh jaaye, ya
   - lagataar 3 rounds tak koi naya link na aaye (stale detection)

5. **Parallel detail scraping** (`_scrape_details`) — collected links pe ek saath 10 tabs (`DETAIL_CONCURRENCY`) khulti hain, har tab se: name, category, rating, reviews, address, phone, website, plus code, latitude/longitude nikalte hain. Duplicate leads (same name + address) ek `set` se filter hote hain.

6. **Email/Social enrichment** (optional) — alag fresh browser session mein har business ki website visit hoti hai. Pehle homepage se email regex se dhoondhta hai; na mile to "contact/about/support" wale links khud dhoond ke unko bhi crawl karta hai. Social links (Facebook/Instagram/LinkedIn/YouTube/WhatsApp etc.) bhi isi tarah nikalte hain.

7. **Response** — sab unique leads JSON array mein client ko wapas.

---

## 4. Key Algorithms / Techniques

| Algorithm | Kahan | Kya karta hai |
|---|---|---|
| **Query fan-out strategy** | `_build_query_list()` | Ek query se limited results milne ki restriction ko cities/areas/prefixes mein tod ke bypass karta hai |
| **Infinite-scroll with stale detection** | `_collect_listing_links()` | Feed ko scroll karta hai, 3 baar result na badhe to rukta hai (infinite loop se bachne ke liye) |
| **Producer-consumer via asyncio.Semaphore** | `_scrape_details()`, `enrich_leads()` | Ek time pe max N tabs hi open rahe (resource control), baaki queue mein wait karte hain |
| **Early stop with asyncio.Event** | `_scrape_details()` worker | `limit` reach hote hi baaki pending tasks turant cancel/skip ho jaate hain |
| **Dedup via composite key (set)** | `seen = {(name, address)}` | Same business do baar (different query se) na aaye |
| **Batch isolation for crash resilience** | `_run_scrape()` | Har 25 queries ke baad naya browser — ek crash pura scrape nahi girata |
| **Anti-bot stealth** | `browser_manager.py` | `navigator.webdriver` chhupana, fake plugins/languages, real Chrome headers — Google ke bot-detection se bachne ke liye |
| **Regex-based extraction** | `maps_agent.py`, `extract_skill.py` | Coordinates (`!3d..!4d..`), phone, email pattern match |

---

## 5. API Summary

| Endpoint | Method | Kaam |
|---|---|---|
| `/health` | GET | Service up hai ya nahi check karta hai |
| `/scrape/sync` | POST | Maps scrape karke leads JSON return karta hai (synchronous) |

Configuration tuning: [config/settings.py](playwright-agent/config/settings.py) mein `DETAIL_CONCURRENCY`, `MAX_FEED_SCROLLS`, `FEED_SCROLL_PAUSE_MS`, `ENRICH_CONCURRENCY` jaise values change kar sakte hain.
