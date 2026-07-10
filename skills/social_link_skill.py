"""Instagram/Facebook public-profile discovery and metadata extraction.

Scope is deliberately narrow (Instagram + Facebook only, no email, no
website in output — see api/routers/social.py for the request/response
contract). Uses plain HTTP (``httpx``) instead of a Playwright browser tab
for both link discovery and profile metadata — a full Chromium render is
unneeded overhead for the public og:meta tags this reads, and avoids
opening a browser tab per profile (Phase 13/14 of the discovery spec:
async HTTP first, browser fallback only when necessary).
"""

import re
import time
import urllib.parse

import httpx

from config import settings

ANCHOR_RE = re.compile(r'<a\s[^>]*href=["\']([^"\'#][^"\']*)["\']', re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

META_RE_TMPL = r'<meta\s+[^>]*?property=["\']{prop}["\'][^>]*?content=["\']([^"\']*)["\']'
META_RE_TMPL_REV = r'<meta\s+[^>]*?content=["\']([^"\']*)["\'][^>]*?property=["\']{prop}["\']'
META_NAME_RE_TMPL = r'<meta\s+[^>]*?name=["\']{name}["\'][^>]*?content=["\']([^"\']*)["\']'

IG_HOSTS = ("instagram.com",)
FB_HOSTS = ("facebook.com", "fb.com")

# Reserved/non-profile Instagram paths (Phase 6).
IG_RESERVED_PATHS = {
    "explore", "accounts", "reels", "reel", "stories", "direct", "p", "tv",
    "about", "developer", "legal", "privacy", "web", "embed", "graphql",
    "ajax", "api", "challenge", "session", "login", "static",
}

# Reserved/non-identity Facebook paths (Phase 6).
FB_RESERVED_PATHS = {
    "login", "share", "sharer", "search", "groups", "help", "marketplace",
    "watch", "events", "policies", "privacy", "terms", "ads", "business",
    "l.php", "photo.php", "video.php", "pages", "notifications", "settings",
    "messages", "recover", "dialog", "plugins", "tr", "campaign",
}

IG_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_COUNT_RE = re.compile(r"^([\d,]+(?:\.\d+)?)\s*([KkMm]?)$")
_IG_META_COUNTS_RE = re.compile(
    r"([\d.,]+[KkMm]?)\s+Followers,\s*([\d.,]+[KkMm]?)\s+Following,\s*([\d.,]+[KkMm]?)\s+Posts",
    re.IGNORECASE,
)
_FOLLOWERS_RE = re.compile(r"([\d.,]+[KkMm]?)\s+Followers\b", re.IGNORECASE)
_FOLLOWING_RE = re.compile(r"([\d.,]+[KkMm]?)\s+Following\b", re.IGNORECASE)

# Instagram's meta description follows one of two shapes (Phase 7, Task 0
# evidence — traced on a real profile):
#   "X Followers, Y Following, Z Posts - See Instagram photos and videos
#    from NAME (@user)"                                   [no real bio]
#   "X Followers, Y Following, Z Posts - NAME (@user) on Instagram:
#    "REAL BIO TEXT""                                     [real bio, browser-rendered meta[name=description] only]
# Only the second form carries an actual bio — the first is Instagram's
# own generic boilerplate and must be rejected (Task 5), not returned.
_IG_BIO_QUOTED_RE = re.compile(r'on instagram:\s*"(.+)"?\s*$', re.IGNORECASE | re.DOTALL)


def extract_instagram_bio(description: str) -> str:
    """Real bio only — extracted from the quoted text after "on Instagram:
    ". Any other description shape (e.g. the generic "See Instagram
    photos and videos from NAME" boilerplate) has no real bio to give;
    returns "" rather than misreporting boilerplate as a bio (Task 5)."""
    if not description:
        return ""
    m = _IG_BIO_QUOTED_RE.search(description)
    if not m:
        return ""
    bio = m.group(1)
    if bio.endswith('"'):
        bio = bio[:-1]
    return bio.strip()


def parse_count(raw: str | None) -> int | None:
    """Normalize a human-readable count ("1.2K", "12,340", "2.5M") to int.

    Returns None on anything ambiguous rather than guessing (Phase 11) —
    e.g. locale-formatted numbers this doesn't recognize.
    """
    if not raw:
        return None
    m = _COUNT_RE.match(raw.strip().replace(",", ""))
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2).upper()
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000}[suffix]
    return int(round(value * multiplier))


def _host_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.").removeprefix("m.")


def classify_platform(url: str) -> str | None:
    host = _host_of(url)
    if any(host == d or host.endswith("." + d) for d in IG_HOSTS):
        return "instagram"
    if any(host == d or host.endswith("." + d) for d in FB_HOSTS):
        return "facebook"
    return None


def normalize_social_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Validate + canonicalize a candidate Instagram/Facebook URL.

    Returns (platform, canonical_url, username) — all None if the URL is
    not a real profile/page (reserved path, share link, tracking link,
    generic homepage).
    """
    platform = classify_platform(url)
    if platform is None:
        return None, None, None

    parsed = urllib.parse.urlparse(url)
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not path_parts:
        return None, None, None  # bare homepage, not an identity page

    first = path_parts[0].lower()

    if platform == "instagram":
        if first in IG_RESERVED_PATHS:
            return None, None, None
        username = path_parts[0]
        if not IG_USERNAME_RE.match(username):
            return None, None, None
        canonical = f"https://www.instagram.com/{username}/"
        return "instagram", canonical, username

    # facebook
    if first in FB_RESERVED_PATHS:
        return None, None, None
    if first == "profile.php":
        qs = urllib.parse.parse_qs(parsed.query)
        page_id = qs.get("id", [None])[0]
        if not page_id:
            return None, None, None
        canonical = f"https://www.facebook.com/profile.php?id={page_id}"
        return "facebook", canonical, page_id
    username = path_parts[0]
    if not re.match(r"^[A-Za-z0-9.\-]{2,80}$", username):
        return None, None, None
    canonical = f"https://www.facebook.com/{username}/"
    return "facebook", canonical, username


class SocialLinkSkill:
    """Visits business websites and social profile URLs over HTTP."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            headers={
                "User-Agent": settings.USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
            verify=False,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def _fetch(self, url: str, timeout_ms: int, intervals: list | None = None) -> str:
        """``intervals`` (Phase 1 instrumentation, additive-only): if given,
        appends this call's (wall_start, wall_end) monotonic pair — used to
        reconstruct in-flight HTTP concurrency over time after the fact,
        without a live polling sampler. Does not affect fetch behavior."""
        t_start = time.monotonic()
        text = ""
        try:
            resp = await self.client.get(url, timeout=timeout_ms / 1000)
            if resp.status_code < 400:
                text = resp.text
        except Exception:
            pass
        finally:
            if intervals is not None:
                intervals.append((t_start, time.monotonic()))
        return text

    # ------------------------------------------------------------------ #
    # Phase 5 — website -> social link discovery (internal source only,
    # website itself is never returned to the API caller)
    # ------------------------------------------------------------------ #
    async def discover_from_website(
        self, website: str, timing: dict | None = None, intervals: list | None = None,
        allow_secondary: bool = True,
    ) -> dict:
        """Return {"instagram": canonical_url|None, "facebook": canonical_url|None}.

        ``timing``/``intervals`` (Phase 1 instrumentation, additive-only):
        when given, filled in with this call's sub-phase durations — does
        not change what's fetched or how.

        ``allow_secondary`` (Phase 2, Task 7 — deficit-aware secondary-page
        fetch): the streaming resolver passes False once enough qualified
        leads already exist, so a homepage with no social links doesn't
        pay for a contact-page fetch nobody needs. Default True preserves
        exact prior behavior for every existing caller.
        """
        found: dict[str, str] = {}

        t0 = time.monotonic()
        home_html = await self._fetch(website, settings.SOCIAL_WEBSITE_TIMEOUT_MS, intervals)
        t1 = time.monotonic()
        if home_html:
            self._harvest_links(home_html, found)
        t2 = time.monotonic()
        homepage_had_social = bool(found)

        used_secondary = False
        secondary_avoided = False
        if len(found) < 2 and home_html:
            if allow_secondary:
                for contact_url in self._discover_contact_links(home_html, website)[:1]:
                    used_secondary = True
                    contact_html = await self._fetch(contact_url, settings.SOCIAL_WEBSITE_TIMEOUT_MS, intervals)
                    if contact_html:
                        self._harvest_links(contact_html, found)
            else:
                secondary_avoided = True
        t3 = time.monotonic()

        if timing is not None:
            timing["website_fetch_ms"] = round((t1 - t0) * 1000, 1)
            timing["social_link_parse_ms"] = round((t2 - t1) * 1000, 1)
            timing["homepage_had_social"] = homepage_had_social
            timing["used_secondary"] = used_secondary
            timing["secondary_avoided_deficit_met"] = secondary_avoided
            timing["secondary_page_fetch_ms"] = round((t3 - t2) * 1000, 1) if used_secondary else None

        return {"instagram": found.get("instagram"), "facebook": found.get("facebook")}

    def _harvest_links(self, html: str, found: dict[str, str]) -> None:
        html = urllib.parse.unquote(html)
        for href in ANCHOR_RE.findall(html):
            platform, canonical, _username = normalize_social_url(href)
            if platform and platform not in found:
                found[platform] = canonical

    def _discover_contact_links(self, html: str, website: str) -> list[str]:
        host = _host_of(website)
        target_urls, seen = [], set()
        for href in ANCHOR_RE.findall(html):
            href_host = urllib.parse.urlparse(href).netloc.lower().removeprefix("www.")
            if href_host and href_host != host:
                continue
            full_url = urllib.parse.urljoin(website, href)
            normalized = full_url.split("?")[0].rstrip("/")
            if normalized in seen:
                continue
            if any(kw in normalized.lower() for kw in ("contact", "about")):
                seen.add(normalized)
                target_urls.append(full_url)
        return target_urls

    # ------------------------------------------------------------------ #
    # Phase 9/10 — public profile metadata via og:meta tags. Login/consent
    # walls served to anonymous requests are a known, real risk here — on
    # failure this returns Nones rather than fabricating values.
    # ------------------------------------------------------------------ #
    async def fetch_profile_metadata(self, url: str, platform: str, intervals: list | None = None) -> dict:
        html = await self._fetch(url, settings.SOCIAL_PROFILE_TIMEOUT_MS, intervals)
        result = {
            "bio": "", "profile_image_url": "",
            "followers": None, "following": None, "posts_count": None,
        }
        if not html:
            return result

        description = self._meta(html, "og:description") or self._meta_name(html, "description")
        image = self._meta(html, "og:image") or self._meta_name(html, "twitter:image")
        result["profile_image_url"] = image or ""

        if platform == "instagram" and description:
            m = _IG_META_COUNTS_RE.search(description)
            if m:
                result["followers"] = parse_count(m.group(1))
                result["following"] = parse_count(m.group(2))
                result["posts_count"] = parse_count(m.group(3))
            result["bio"] = extract_instagram_bio(description)
        elif description:
            fm = _FOLLOWERS_RE.search(description)
            gm = _FOLLOWING_RE.search(description)
            if fm:
                result["followers"] = parse_count(fm.group(1))
            if gm:
                result["following"] = parse_count(gm.group(1))
            result["bio"] = "" if (fm or gm) else description.strip()

        return result

    def _meta(self, html: str, prop: str) -> str:
        m = re.search(META_RE_TMPL.format(prop=re.escape(prop)), html, re.IGNORECASE)
        if m:
            return _unescape(m.group(1))
        m = re.search(META_RE_TMPL_REV.format(prop=re.escape(prop)), html, re.IGNORECASE)
        return _unescape(m.group(1)) if m else ""

    def _meta_name(self, html: str, name: str) -> str:
        m = re.search(META_NAME_RE_TMPL.format(name=re.escape(name)), html, re.IGNORECASE)
        return _unescape(m.group(1)) if m else ""


def _unescape(text: str) -> str:
    return (
        text.replace("&amp;", "&").replace("&quot;", '"')
        .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
        .strip()
    )
