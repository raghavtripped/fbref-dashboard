"""
FastAPI backend — match data API + scraper control + CSV export.
Serves the React dashboard and provides all data endpoints.
"""

import json
import csv
import io
import os
import threading
import time
import random
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

from parser import parse_match_report, flatten_match
from scraper import FBrefScraper, cache_path, CACHE_DIR


# --- Data persistence ---
DATA_DIR = Path(os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data")))
DB_FILE = DATA_DIR / "matches.json"

_matches = {}  # {url: match_data_dict}
_status = {
    "running": False,
    "current": "",
    "log": [],
    "progress": 0,
    "total": 0,
    "stop": False,
}
_lock = threading.Lock()


def load_db():
    global _matches
    if DB_FILE.exists():
        try:
            with open(DB_FILE) as f:
                _matches = json.load(f)
        except (json.JSONDecodeError, IOError):
            _matches = {}


def save_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DB_FILE, "w") as f:
        json.dump(_matches, f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_db()
    yield


app = FastAPI(title="FBref Dashboard", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request models ---
class ScrapeRequest(BaseModel):
    urls: list[str] = []
    competition: Optional[str] = None
    season: Optional[str] = None


class ParseRequest(BaseModel):
    url: str
    html: str


# ═══════════════════════════════════════════════════════
# DATA ENDPOINTS
# ═══════════════════════════════════════════════════════

@app.get("/api/matches")
def get_matches():
    """Get all matches (flattened, no player stats)."""
    flat = []
    for url, data in _matches.items():
        row = flatten_match(data)
        row["has_players"] = bool(data.get("_player_stats"))
        flat.append(row)
    return {"matches": flat, "count": len(flat)}


@app.get("/api/match/{match_id}")
def get_match(match_id: str):
    """Get a single match by ID (includes player stats)."""
    for url, data in _matches.items():
        if match_id in url:
            return data
    raise HTTPException(404, "Match not found")


@app.get("/api/stats")
def get_stats():
    """Get aggregate statistics across all matches."""
    if not _matches:
        return {
            "total_matches": 0, "teams": [], "competitions": [],
            "referees": [], "outcomes": {}, "total_goals": 0,
            "total_cards": 0, "avg_goals": 0,
        }

    teams, comps, refs = set(), set(), set()
    outcomes = {"HOME_WIN": 0, "AWAY_WIN": 0, "DRAW": 0}
    total_goals = total_cards = 0

    for data in _matches.values():
        f = flatten_match(data)
        teams.update([f.get("home_team", ""), f.get("away_team", "")])
        comps.add(f.get("competition", ""))
        refs.add(f.get("referee", ""))
        outcome = f.get("outcome", "DRAW")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        total_goals += (f.get("home_goals", 0) or 0) + (f.get("away_goals", 0) or 0)
        total_cards += f.get("total_cards", 0) or 0

    teams.discard("")
    comps.discard("")
    refs.discard("")

    return {
        "total_matches": len(_matches),
        "teams": sorted(teams),
        "competitions": sorted(comps),
        "referees": sorted(refs),
        "outcomes": outcomes,
        "total_goals": total_goals,
        "total_cards": total_cards,
        "avg_goals": round(total_goals / max(len(_matches), 1), 2),
    }


@app.get("/api/players/{match_id}")
def get_players(match_id: str):
    """Get player stats for a specific match."""
    for url, data in _matches.items():
        if match_id in url:
            return data.get("_player_stats", {})
    raise HTTPException(404, "Match not found")


# ═══════════════════════════════════════════════════════
# SCRAPER ENDPOINTS
# ═══════════════════════════════════════════════════════

@app.post("/api/scrape")
def start_scrape(req: ScrapeRequest):
    """Start a background scraping job in a separate thread."""
    if _status["running"]:
        raise HTTPException(409, "Scraper is already running")
    # Run in a real OS thread — Playwright sync API cannot run inside asyncio
    t = threading.Thread(target=_run_scrape, args=(req.urls, req.competition, req.season), daemon=True)
    t.start()
    return {"status": "started"}


@app.get("/api/scraper/status")
def scraper_status():
    """Get current scraper status, progress, and log."""
    return dict(_status)


@app.post("/api/scraper/stop")
def stop_scrape():
    """Signal the scraper to stop after current URL."""
    _status["stop"] = True
    return {"status": "stopping"}


def _run_scrape(urls, comp=None, season=None):
    """Background scraping task."""
    with _lock:
        _status.update(running=True, current="", log=[], progress=0, total=0, stop=False)

    def log(msg):
        _status["log"].append(msg)
        _status["log"] = _status["log"][-200:]

    try:
        with FBrefScraper(headless=True, on_log=log) as scraper:
            if comp and season:
                log(f"Discovering {comp} {season}...")
                urls = scraper.discover(comp, season)

            _status["total"] = len(urls)
            log(f"Starting scrape of {len(urls)} URLs...")

            for i, url in enumerate(urls):
                if _status["stop"]:
                    log("Stopped by user.")
                    break

                slug = url.split("/")[-1] if "/" in url else url
                _status["current"] = slug
                _status["progress"] = i

                try:
                    html = scraper.fetch(url)
                    data = parse_match_report(url, html)
                    _matches[url] = data
                    save_db()
                    f = flatten_match(data)
                    log(f"✓ {f.get('home_team')} {f.get('home_goals')}-{f.get('away_goals')} {f.get('away_team')}")
                except Exception as e:
                    log(f"✗ {slug}: {e}")

                if i < len(urls) - 1 and not _status["stop"]:
                    delay = random.uniform(30, 75)
                    log(f"  ⏳ {delay:.0f}s...")
                    time.sleep(delay)

    except Exception as e:
        log(f"ERROR: {e}")
    finally:
        _status["running"] = False
        _status["progress"] = _status["total"]
        log("Done.")


# ═══════════════════════════════════════════════════════
# MANUAL PARSE ENDPOINTS
# ═══════════════════════════════════════════════════════

@app.post("/api/parse")
def parse_html(req: ParseRequest):
    """Parse manually pasted HTML for a match."""
    try:
        data = parse_match_report(req.url, req.html)
        _matches[req.url] = data
        save_db()
        return {"status": "ok", "match": flatten_match(data)}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/parse-cached")
def parse_cached():
    """Parse all HTML files in the cache directory."""
    parsed_count = 0
    errors = []

    for f in sorted(CACHE_DIR.glob("*.html")):
        url = f"https://fbref.com/en/matches/{f.stem}/"
        if url in _matches:
            continue
        try:
            data = parse_match_report(url, f.read_text(encoding="utf-8"))
            _matches[url] = data
            parsed_count += 1
        except Exception as e:
            errors.append({"file": f.name, "error": str(e)})

    save_db()
    return {"parsed": parsed_count, "errors": errors, "total": len(_matches)}


# ═══════════════════════════════════════════════════════
# EXPORT ENDPOINTS
# ═══════════════════════════════════════════════════════

CORE_COLUMNS = [
    "date", "time", "competition", "season", "round",
    "home_team", "away_team", "home_goals", "away_goals", "outcome",
    "home_xg", "away_xg", "referee", "venue", "attendance",
    "home_manager", "away_manager", "home_captain", "away_captain",
    "home_possession", "away_possession",
    "home_shots_total", "away_shots_total",
    "home_shots_on_target", "away_shots_on_target",
    "home_saves", "away_saves",
    "home_fouls", "away_fouls",
    "home_corners", "away_corners",
    "home_crosses", "away_crosses",
    "home_interceptions", "away_interceptions",
    "home_cards_yellow", "away_cards_yellow",
    "home_cards_red", "away_cards_red",
    "home_cards_total", "away_cards_total",
    "total_cards", "url",
]


@app.get("/api/export/csv")
def export_csv():
    """Export all matches as CSV download."""
    if not _matches:
        raise HTTPException(404, "No matches to export")

    flat = [flatten_match(d) for d in _matches.values()]

    # Build ordered headers
    all_keys = set()
    for row in flat:
        all_keys.update(row.keys())
    existing_core = [c for c in CORE_COLUMNS if c in all_keys]
    extra_cols = sorted([c for c in all_keys if c not in CORE_COLUMNS])
    headers = existing_core + extra_cols

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in flat:
        writer.writerow(row)

    timestamp = datetime.now().strftime("%Y%m%d")
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=fbref_{timestamp}.csv"},
    )


@app.delete("/api/matches")
def clear_all():
    """Clear all match data."""
    _matches.clear()
    save_db()
    return {"status": "cleared"}


# ═══════════════════════════════════════════════════════
# SERVE FRONTEND
# ═══════════════════════════════════════════════════════

frontend_dir = Path(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if (frontend_dir / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
