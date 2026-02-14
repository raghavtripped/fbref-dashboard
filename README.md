# FBref Dashboard

A full-stack web application for scraping, storing, and visualizing football match data from [FBref](https://fbref.com). Built with **FastAPI** (backend), **Playwright** (scraping), and **React** (dashboard).

## Features

- **Automated scraping** — Playwright-based headless browser with stealth anti-detection, Cloudflare handling, rate limit management, and HTML caching
- **Auto-discovery** — Automatically finds all match URLs for a given competition and season
- **Rich data extraction** — 49+ stats per match including xG, possession, shots, fouls, cards, crosses, interceptions, and full player-level stats
- **Interactive dashboard** — Dark-themed React SPA with overview stats, filterable match table, match detail modals with stat bars, and player stats viewer
- **Scraper control panel** — Start/stop scraping from the browser, monitor live progress with logs
- **Manual HTML input** — Fallback for pasting FBref page source when automation fails
- **CSV export** — Download all scraped data as CSV
- **Resume support** — HTML caching means interrupted scrapes pick up where they left off

## Supported Leagues

Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League, MLS

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Start the server
chmod +x run.sh
./run.sh

# Or manually:
cd backend && python -m uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Open **http://localhost:8000** in your browser.

## Project Structure

```
fbref-dashboard/
  backend/
    server.py       # FastAPI server + API endpoints
    scraper.py      # Playwright scraping engine
    parser.py       # HTML parser (BeautifulSoup)
  frontend/
    index.html      # React SPA (single-file, no build step)
  data/
    html_cache/     # Cached match HTML files
    matches.json    # Parsed match data (auto-generated)
  requirements.txt
  run.sh
  README.md
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/matches` | All matches (flattened) |
| GET | `/api/match/{id}` | Single match with player stats |
| GET | `/api/stats` | Aggregate statistics |
| GET | `/api/players/{id}` | Player stats for a match |
| POST | `/api/scrape` | Start background scrape |
| GET | `/api/scraper/status` | Scraper progress + logs |
| POST | `/api/scraper/stop` | Stop scraper |
| POST | `/api/parse` | Parse manually pasted HTML |
| POST | `/api/parse-cached` | Parse all cached HTML files |
| GET | `/api/export/csv` | Download CSV |
| DELETE | `/api/matches` | Clear all data |

## Usage

### Scrape by URL list
1. Go to the **Scraper** tab
2. Paste FBref match report URLs (one per line)
3. Click **Start Scraping**

### Auto-discover a season
1. Go to the **Scraper** tab
2. Switch to **Auto-Discover** mode
3. Select competition and season
4. Click **Start Scraping**

### Manual fallback
1. Go to the **Manual Input** tab
2. Open an FBref match page → View Page Source → Copy all
3. Paste the URL and HTML → **Parse & Save**

## Data Fields

Each match extracts: date, time, competition, season, round, teams, goals, xG, outcome, referee, venue, attendance, managers, captains, possession, shots (total + on target), saves, fouls, corners, crosses, interceptions, yellow/red cards, and 14 player stat tables (summary, passing, defense, possession, misc, shooting, goalkeeper for both teams).
