# Project Instructions — Google Maps Lead Scraper

> Yeh file project ki fixed constraints define karti hai. Har code change,
> suggestion, ya optimization inn rules ke andar hi honi chahiye. Yeh rules
> non-negotiable hain jab tak user khud inhe update na kare.

---

## 0. Mindset

Har change ko ek **senior ML / backend engineer** ki tarah treat karo:
- Koi bhi optimization suggest/implement karne se pehle uska trade-off bolo
  (speed vs accuracy vs cost).
- Guess mat karo — agar ambiguous instruction ho to ek reasonable assumption
  likh ke aage badho, chup-chap mat badlo.
- Benchmark/measure karo (time, success rate) jahan bhi possible ho, sirf
  "should be faster" mat bolo.

---

## 1. Git — Hands Off ❌

- **Git ko kabhi bhi touch nahi karna** — no `git add`, `git commit`,
  `git push`, `git checkout`, `git reset`, `.gitignore` edit, branch operations,
  kuch bhi nahi.
- Agar koi task git operation maangta hua lage, to sirf **suggest** karo
  (text mein) — khud kabhi execute mat karo.
- Isse user manually handle karega.

## 2. Scope — SIRF `playwright-agent/` Folder, Google Scraper API Tak Hi ❌➡️✅

- Kaam **sirf `playwright-agent/` folder ke andar** hona chahiye, aur usme
  bhi **sirf Google Maps Scraper API** (`api/server.py`, `agents/maps_agent.py`,
  `skills/extract_skill.py`, `browser/browser_manager.py`,
  `config/settings.py`) — inn files/logic tak hi kaam karna hai.
- **Confusion avoid karne ke liye specifically note karo:** repo mein ek
  alag folder bhi hai jiska naam **"google scraper agent"** (ya milta-julta
  naam) hai — **yeh alag folder hai, isko bilkul touch nahi karna hai.**
  Sirf `playwright-agent/` wala folder target hai, uske bahar koi bhi
  folder — chahe naam kitna bhi milta-julta ho ("google scraper agent"
  jaisa) — usko kabhi edit/create/delete nahi karna.
- Baaki poore repo/project (frontend, unrelated services, configs bahar ke)
  ko bhi touch mat karo — chahe woh related bhi lage.
- Naya file banana ho to bhi isi `playwright-agent/` folder ke andar
  (`agents/`, `skills/`, `api/`, `browser/`, `config/`) — bahar bilkul nahi,
  chahe folder ka naam kitna bhi related lage.

## 3. Data Quality Gate — Phone Number ☎️

- **Minimum 80% leads** ke paas valid phone number hona chahiye (jo bhi
  batch/response return ho, uska ≥80% phone field filled + valid format ho).
- Agar ek batch/query 80% se neeche gir raha ho, to:
  - Extraction selector/regex ko fix karne ki koshish karo (root cause),
  - ya us specific query/area ko retry/flag karo, blindly drop mat karo.
- Yeh ek **hard quality gate** hai — speed optimization ke chakkar mein
  isse compromise nahi karna.

  > ⚠️ Assumption note: "phone above 80" ko maine **80% fill/valid rate**
  > interpret kiya hai. Agar matlab kuch aur tha (jaise phone number length
  > ya format check), please correct karo — main update kar dunga.

## 4. Email Enrichment — Time Budget ⏱️

- Jab `find_emails=true` ho, to us case mein **response time 30 seconds tak
  ja sakta hai** — yeh acceptable hai, isko forcefully "fast" karne ki
  zaroorat nahi.
- `find_emails=false` case mein speed target alag rahega (jaise 10 leads
  <15 sec discussion already hui hai) — email step is completely optional
  aur separate time budget ke andar aata hai.
- Matlab: **without emails = fast path**, **with emails = up to 30s allowed
  path**. In dono ko alag treat karo, ek doosre ko slow mat karne do.

---

## 5. Speed Optimization Playbook 🚀

Jab bhi speed improve karni ho, is order mein karo — top wale sabse zyada
impact dete hain, sabse kam risk ke saath:

### Step 1 — Resource Blocking (sabse pehla, sabse easy win)
Har Playwright page pe images, fonts, CSS, analytics/ads block karo:
```python
async def block_resources(route, request):
    if request.resource_type in ["image", "font", "media", "stylesheet"]:
        await route.abort()
    elif any(x in request.url for x in
             ["google-analytics", "doubleclick", "facebook.com/tr", "gtag"]):
        await route.abort()
    else:
        await route.continue_()

await page.route("**/*", block_resources)
```
Impact: page load ~40-60% fast, bina data quality kharab kiye.

### Step 2 — Selective Browser Use (Hybrid HTTP + Browser)
- Email/social enrichment (`extract_skill.py`) ke liye **browser mat lagao**
  — `httpx`/`aiohttp` se direct HTTP GET + regex/BeautifulSoup se email
  nikalo. Website scraping mein JS render zaroori nahi hota (zyadatar).
- Sirf Google Maps detail page ke liye hi Playwright browser use karo
  (kyunki woh JS-heavy SPA hai).

### Step 3 — Browser Reuse (Contexts, Not New Browsers)
- Ek hi browser instance ke andar **multiple `BrowserContext` + pages**
  use karo, har lead ke liye naya browser launch mat karo.
```python
browser = await playwright.chromium.launch()
contexts = [await browser.new_context() for _ in range(concurrency)]
pages = [await ctx.new_page() for ctx in contexts]
```

### Step 4 — Request Queue (Pipeline, Not Sequential Phases)
- Listing-link collection aur detail-scraping ko **overlap** karo —
  `asyncio.Queue` use karo taaki jaise-jaise link scroll se milta hai, free
  worker turant usko consume kare, poora scroll khatam hone ka wait na ho.

### Step 5 — Parallel Workers / Concurrency
- `DETAIL_CONCURRENCY` ko requested lead count ke barabar (ya thoda zyada)
  rakho chhote requests ke liye — 10 leads chahiye to 10-15 tabs parallel.

### Step 6 — Dynamic/Autoscaling Concurrency
- Fixed concurrency ke bajaye CPU/RAM ke hisaab se calculate karo:
```python
import psutil, os

def get_optimal_concurrency():
    cpu_count = os.cpu_count()
    available_ram_gb = psutil.virtual_memory().available / (1024**3)
    ram_based_limit = int(available_ram_gb * 1024 / 100)  # ~100MB/tab
    cpu_based_limit = cpu_count * 4  # I/O-bound, CPU se zyada allowed
    return min(ram_based_limit, cpu_based_limit, 50)  # hard safety cap
```

### Step 7 — Retries + Session Management (batch ko slow na karein)
- Har lead ka retry **independent** rakho — ek fail hone se poora batch
  slow/block na ho:
```python
async def scrape_with_retry(page, url, max_retries=2):
    for attempt in range(max_retries + 1):
        try:
            return await scrape_detail(page, url)
        except (TimeoutError, PlaywrightError):
            if attempt == max_retries:
                return None  # is lead ko skip karo, baaki ko mat roko
            await asyncio.sleep(0.5 * (attempt + 1))
```

### Step 8 — Distributed/Cloud Execution (sirf bade jobs ke liye)
- 10-15 leads ke liye zaroorat nahi.
- 10,000+ leads wale bade jobs ke liye: Docker containerize karo, cloud pe
  horizontal scale karo (multiple containers = multiple independent batches).

### Speed Targets (reference)
| Scenario | Target Time |
|---|---|
| 10 leads, `find_emails=false` | ~10-15 sec |
| 10 leads, `find_emails=true` | up to 30 sec (allowed, Rule #4) |
| Bade limit (1000+) | async job pattern use karo, sync endpoint na |

**Priority order agar step-by-step implement karna ho:**
1. Resource blocking → 2. Selective HTTP for enrichment → 3. Browser reuse
→ 4. Queue pipeline → 5-6. Concurrency (fixed then dynamic) → 7. Retries
→ 8. Distributed (only if scale demands).

---

## Quick Reference Table

| Constraint | Rule |
|---|---|
| Git | Never touch — no commands, no config edits |
| Folder scope | ONLY `playwright-agent/` → Google Maps Scraper API files. Do NOT touch any other folder, especially the separately-named "google scraper agent" folder |
| Phone quality | ≥80% leads must have valid phone number |
| Email enrichment time | Up to 30s allowed when `find_emails=true` |
| Non-email response time | Fast path (target ~10-15s for small lead counts) |
| Speed optimization order | Resource blocking → HTTP for enrichment → browser reuse → queue pipeline → concurrency → dynamic scaling → retries → distributed (only if scale needs it) |

---

## Change Log
*(update this section jab bhi koi naya constraint add ho)*

- v1 — initial constraints set (git-lock, folder-scope, phone quality gate,
  email time budget)
