"""
FBref Scraper — Playwright headless browser with stealth anti-detection.
Handles Cloudflare challenges, rate limiting, retry logic, and HTML caching.
"""

import time
import random
import hashlib
import re
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


MIN_DELAY = 30
MAX_DELAY = 75
CACHE_DIR = Path(os.environ.get("CACHE_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "html_cache")))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

COMP_IDS = {
    "premier-league": 9,
    "la-liga": 12,
    "serie-a": 11,
    "bundesliga": 20,
    "ligue-1": 13,
    "champions-league": 8,
    "mls": 22,
}


def cache_path(url):
    """Get filesystem cache path for a URL based on match ID."""
    parts = url.rstrip("/").split("/")
    for i, p in enumerate(parts):
        if p == "matches" and i + 1 < len(parts):
            return CACHE_DIR / f"{parts[i + 1]}.html"
    return CACHE_DIR / f"{hashlib.md5(url.encode()).hexdigest()[:12]}.html"


def extract_match_urls(html):
    """Extract all match report URLs from a schedule/fixtures page."""
    raw = re.findall(r"/en/matches/[a-f0-9]+/[A-Za-z0-9\-]+", html)
    seen, urls = set(), []
    for p in raw:
        if p not in seen:
            seen.add(p)
            urls.append(f"https://fbref.com{p}")
    return urls


class FBrefScraper:
    """Manages browser lifecycle and page fetching with anti-detection."""

    def __init__(self, headless=True, use_cache=True, on_log=None):
        self.headless = headless
        self.use_cache = use_cache
        self.on_log = on_log
        self.pw = self.browser = self.ctx = None
        self._n = 0

    def log(self, msg):
        if self.on_log:
            self.on_log(msg)
        print(msg)

    def start(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
            ],
        )
        ua = random.choice(USER_AGENTS)
        self.ctx = self.browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        self.ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        self.log(f"[BROWSER] Launched (headless={self.headless})")

    def stop(self):
        if self.ctx:
            self.ctx.close()
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    def fetch(self, url):
        """Fetch a single FBref page. Returns HTML string. Uses cache if available."""
        cp = cache_path(url)
        if self.use_cache and cp.exists():
            html = cp.read_text(encoding="utf-8")
            if len(html) > 1000 and "scorebox" in html:
                self.log(f"[CACHE HIT] {url.split('/')[-2]}")
                return html

        for attempt in range(3):
            page = None
            try:
                page = self.ctx.new_page()

                # Occasionally visit homepage first (human-like behavior)
                if self._n > 0 and random.random() < 0.25:
                    self.log("  [STEALTH] Visiting homepage first...")
                    page.goto("https://fbref.com/en/", wait_until="domcontentloaded", timeout=30000)
                    time.sleep(random.uniform(2, 5))

                self.log(f"[FETCH] Attempt {attempt + 1}/3: {url.split('/')[-2] if '/' in url else url}")
                resp = page.goto(url, wait_until="networkidle", timeout=60000)

                if resp and resp.status == 429:
                    wait_time = 120 + random.uniform(0, 60)
                    self.log(f"  [RATE LIMITED] Waiting {wait_time:.0f}s...")
                    time.sleep(wait_time)
                    continue

                # Check for Cloudflare challenge
                title = page.title()
                if "Just a moment" in title or "Attention Required" in title:
                    self.log("  [CLOUDFLARE] Challenge detected, waiting 15s...")
                    time.sleep(15)
                    try:
                        page.wait_for_load_state("networkidle", timeout=30000)
                    except Exception:
                        pass
                    if "Just a moment" in page.title():
                        self.log("  [CLOUDFLARE] Still blocked")
                        continue

                html = page.content()

                if len(html) < 1000:
                    self.log(f"  [WARN] Response too short ({len(html)} chars)")
                    continue

                if "scorebox" not in html and "team_stats" not in html:
                    if "404" in (page.title() or "") or "Not Found" in (page.title() or ""):
                        raise RuntimeError("PAGE_NOT_FOUND")
                    self.log("  [WARN] Missing expected elements")
                    continue

                # Cache it
                cp.write_text(html, encoding="utf-8")
                self._n += 1
                self.log(f"  [OK] {len(html)} chars cached")
                return html

            except RuntimeError:
                raise
            except Exception as e:
                self.log(f"  [ERROR] Attempt {attempt + 1}: {e}")
                if attempt < 2:
                    time.sleep(random.uniform(10, 30))
            finally:
                if page:
                    page.close()

        raise RuntimeError(f"Failed to fetch after 3 attempts: {url}")

    def discover(self, comp, season):
        """Discover all match URLs for a competition/season from the schedule page."""
        cid = COMP_IDS.get(comp)
        if not cid:
            raise ValueError(f"Unknown competition: {comp}. Known: {list(COMP_IDS.keys())}")
        cn = comp.replace("-", " ").title().replace(" ", "-")
        schedule_url = f"https://fbref.com/en/comps/{cid}/{season}/schedule/{season}-{cn}-Scores-and-Fixtures"
        self.log(f"[DISCOVER] Fetching schedule: {schedule_url}")
        html = self.fetch(schedule_url)
        urls = extract_match_urls(html)
        self.log(f"[DISCOVER] Found {len(urls)} match URLs")
        return urls

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *a):
        self.stop()
