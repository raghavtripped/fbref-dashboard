"""
FBref Match Report Parser
Extracts all available data from an FBref match report HTML page.
Handles HTML comments (FBref hides stat tables inside <!-- --> blocks).
"""

from bs4 import BeautifulSoup
import re


def preprocess_html(html):
    """Remove HTML comment markers to reveal hidden data tables."""
    return BeautifulSoup(html.replace("<!--", "").replace("-->", ""), "html.parser")


def normalize_key(text):
    """Convert label to snake_case key."""
    return re.sub(r"^_+|_+$", "", re.sub(r"[^a-z0-9]+", "_", text.lower().strip()))


def safe_int(val, default=None):
    try:
        return int(re.sub(r"[^\d-]", "", str(val)))
    except (ValueError, TypeError):
        return default


def safe_float(val, default=None):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(val)))
    except (ValueError, TypeError):
        return default


def parse_team_stats_table(soup):
    """Parse the #team_stats table with all stat categories."""
    table = soup.find(id="team_stats")
    stats = {}
    if not table:
        return stats

    current_category = ""
    for row in table.find_all("tr"):
        th = row.find("th")
        tds = row.find_all("td")

        if th and len(tds) == 0:
            current_category = th.get_text(strip=True) or ""
            continue

        if len(tds) >= 2 and current_category:
            home_text = tds[0].get_text(strip=True) or ""
            away_text = tds[1].get_text(strip=True) or ""
            base_key = normalize_key(current_category)

            # "X of Y" pattern (Shots on Target, Saves)
            if " of " in home_text:
                def parse_of(text):
                    m = re.search(r"(\d+)\s+of\s+(\d+)", text)
                    return (int(m.group(1)), int(m.group(2))) if m else (None, None)

                h_count, h_total = parse_of(home_text)
                a_count, a_total = parse_of(away_text)

                if base_key == "shots_on_target":
                    stats["home_shots_on_target"] = h_count
                    stats["away_shots_on_target"] = a_count
                    stats["home_shots_total"] = h_total
                    stats["away_shots_total"] = a_total
                else:
                    stats[f"home_{base_key}"] = h_count
                    stats[f"away_{base_key}"] = a_count
                    stats[f"home_{base_key}_total"] = h_total
                    stats[f"away_{base_key}_total"] = a_total

                if "%" in home_text:
                    def parse_pct(t):
                        m = re.search(r"(\d+(?:\.\d+)?)%", t)
                        return float(m.group(1)) if m else None
                    stats[f"home_{base_key}_pct"] = parse_pct(home_text)
                    stats[f"away_{base_key}_pct"] = parse_pct(away_text)

            # Cards
            elif current_category == "Cards":
                def count_cards(td):
                    html_str = str(td)
                    yellows = html_str.count("yellow_card") + html_str.count("yellow_red_card")
                    reds = html_str.count("red_card")
                    return {"yellow": yellows, "red": reds}
                hc = count_cards(tds[0])
                ac = count_cards(tds[1])
                stats["home_cards_yellow"] = hc["yellow"]
                stats["home_cards_red"] = hc["red"]
                stats["away_cards_yellow"] = ac["yellow"]
                stats["away_cards_red"] = ac["red"]

            # Standard number/percentage
            else:
                def parse_val(s):
                    if not s:
                        return None
                    cleaned = s.replace("%", "").strip()
                    try:
                        return float(cleaned)
                    except ValueError:
                        return None
                stats[f"home_{base_key}"] = parse_val(home_text)
                stats[f"away_{base_key}"] = parse_val(away_text)

            current_category = ""

    return stats


def parse_extra_stats(soup):
    """Parse #team_stats_extra div triplets (Home, Label, Away)."""
    container = soup.find(id="team_stats_extra")
    stats = {}
    if not container:
        return stats

    leaves = [
        d.get_text(strip=True)
        for d in container.find_all("div")
        if not d.find_all("div", recursive=False) and d.get_text(strip=True)
    ]

    i = 0
    while i + 2 < len(leaves):
        try:
            h_val = float(leaves[i])
            label_text = leaves[i + 1]
            a_val = float(leaves[i + 2])

            # Label should NOT be numeric
            try:
                float(label_text)
                i += 1
                continue
            except ValueError:
                pass

            key = normalize_key(label_text)
            if f"home_{key}" not in stats:
                stats[f"home_{key}"] = h_val
                stats[f"away_{key}"] = a_val
            i += 3
        except ValueError:
            i += 1

    return stats


def parse_player_table(soup, table_id):
    """Parse a player stats table and return list of player dicts."""
    table = soup.find(id=table_id)
    if not table:
        return []

    thead = table.find("thead")
    if not thead:
        return []

    headers = [
        th.get_text(strip=True) for th in thead.find_all("tr")[-1].find_all(["th", "td"])
    ]

    tbody = table.find("tbody")
    if not tbody:
        return []

    players = []
    for row in tbody.find_all("tr"):
        if row.get("class") and any(
            c in str(row.get("class", [])) for c in ["thead", "spacer"]
        ):
            continue

        cells = row.find_all(["th", "td"])
        if len(cells) < 3:
            continue

        player = {}
        for j, cell in enumerate(cells):
            if j < len(headers):
                key = normalize_key(headers[j]) if headers[j] else f"col_{j}"
                val = cell.get_text(strip=True)
                if key not in ("player", "nation", "pos", "age"):
                    try:
                        player[key] = int(val) if "." not in val else float(val)
                    except (ValueError, TypeError):
                        player[key] = val
                else:
                    player[key] = val

        if player.get("player") and player["player"] not in ("", "Player"):
            players.append(player)

    return players


def parse_all_tables_generic(soup):
    """Generic fallback: extract all <table> elements with data-stat attributes.
    This captures any tables the specific parsers might have missed."""
    extra_data = {}
    parsed_table_ids = {"team_stats", "player_stats"}  # Already parsed by specific functions

    for table in soup.find_all("table"):
        table_id = table.get("id", "")
        if not table_id or table_id in parsed_table_ids:
            continue

        # Skip player stat tables (already parsed)
        if "stats_" in table_id or "keeper_stats" in table_id:
            continue

        rows = []
        for tr in table.find_all("tr"):
            row = {}
            for td in tr.find_all(["td", "th"]):
                stat = td.get("data-stat", "")
                if stat:
                    row[stat] = td.get_text(strip=True)
            if row:
                rows.append(row)

        if rows:
            extra_data[table_id] = rows

    return extra_data


def parse_match_report(url, html):
    """Main parser: extracts everything from an FBref match report page."""
    soup = preprocess_html(html)

    title = soup.title.string if soup.title else ""
    if "Just a moment" in title:
        raise RuntimeError("BLOCKED_CLOUDFLARE")
    if "Page Not Found" in title or "404" in title:
        raise RuntimeError("PAGE_NOT_FOUND")
    if "429" in title or "Too Many Requests" in title:
        raise RuntimeError("RATE_LIMIT")

    result = {"url": url}

    # === SCOREBOX ===
    scorebox = soup.find("div", class_="scorebox")
    if not scorebox:
        raise RuntimeError("INVALID_PAGE_STRUCTURE")

    home_div = soup.find(id="sb_team_0")
    away_div = soup.find(id="sb_team_1")

    if not home_div or not away_div:
        team_divs = [
            d for d in scorebox.find_all("div", recursive=False)
            if "scorebox_meta" not in (d.get("class") or [])
        ]
        if len(team_divs) >= 2:
            home_div, away_div = team_divs[0], team_divs[-1]
        else:
            raise RuntimeError("INVALID_PAGE_STRUCTURE")

    # Team names, scores, xG, manager, captain
    for div, prefix in [(home_div, "home"), (away_div, "away")]:
        strong = div.find("strong")
        link = strong.find("a") if strong else None
        result[f"{prefix}_team"] = link.get_text(strip=True) if link else "Unknown"

        score_el = div.find("div", class_="score")
        result[f"{prefix}_goals"] = safe_int(score_el.get_text(strip=True), 0) if score_el else 0

        xg_el = div.find("div", class_="score_xg")
        result[f"{prefix}_xg"] = safe_float(xg_el.get_text(strip=True)) if xg_el else None

        for dp in div.find_all("div", class_="datapoint"):
            text = dp.get_text(strip=True)
            if "Manager" in text:
                result[f"{prefix}_manager"] = text.replace("Manager:", "").replace("Manager", "").strip()
            elif "Captain" in text:
                result[f"{prefix}_captain"] = text.replace("Captain:", "").replace("Captain", "").strip()

    # Outcome
    hg, ag = result["home_goals"], result["away_goals"]
    result["outcome"] = "HOME_WIN" if hg > ag else ("AWAY_WIN" if ag > hg else "DRAW")

    # === METADATA ===
    meta = soup.find("div", class_="scorebox_meta")
    if meta:
        meta_text = meta.get_text(" ", strip=True)

        date_link = meta.find("a", href=re.compile(r"/matches/20"))
        if date_link:
            result["date"] = date_link.get_text(strip=True)

        time_el = meta.find("span", class_="venuetime")
        if time_el:
            result["time"] = time_el.get_text(strip=True)
        else:
            tm = re.search(r"(\d{1,2}:\d{2})", meta_text)
            if tm:
                result["time"] = tm.group(1)

        comp_link = meta.find("a", href=re.compile(r"/comps/|/seasons/"))
        if comp_link:
            full = comp_link.get_text(strip=True)
            m = re.match(r"^(\d{4}-\d{4})\s+(.+)", full)
            if m:
                result["season"] = m.group(1)
                result["competition"] = m.group(2)
            else:
                result["competition"] = full

        round_m = re.search(
            r"\((Matchweek \d+|Round of \d+|Quarter-finals|Semi-finals|Final)\)",
            meta_text, re.I
        )
        if round_m:
            result["round"] = round_m.group(1)

        venue_m = re.search(r"Venue:\s*(.+?)(?:Attendance|Officials|$)", meta_text)
        if venue_m:
            result["venue"] = venue_m.group(1).strip().rstrip(",")

        att_m = re.search(r"Attendance:\s*([\d,]+)", meta_text)
        if att_m:
            result["attendance"] = int(att_m.group(1).replace(",", ""))

        ref_m = re.search(r"Officials?:\s*([^(·]+)\s*\(Referee\)", meta_text)
        if ref_m:
            result["referee"] = ref_m.group(1).strip()

    # === TEAM STATS ===
    result.update(parse_team_stats_table(soup))
    result.update(parse_extra_stats(soup))

    # Cards totals
    if "home_cards_yellow" in result and "home_cards_red" in result:
        result["home_cards_total"] = result.get("home_cards_yellow", 0) + result.get("home_cards_red", 0)
        result["away_cards_total"] = result.get("away_cards_yellow", 0) + result.get("away_cards_red", 0)
        result["total_cards"] = result["home_cards_total"] + result["away_cards_total"]

    # === PLAYER STATS ===
    player_stats = {}
    for side in ["home", "away"]:
        for stat_type in ["summary", "passing", "defense", "possession", "misc", "shooting"]:
            data = parse_player_table(soup, f"stats_{side}_{stat_type}")
            if data:
                player_stats[f"{side}_{stat_type}"] = data
        gk = parse_player_table(soup, f"keeper_stats_{side}")
        if gk:
            player_stats[f"{side}_gk"] = gk

    result["_player_stats"] = player_stats

    # Generic fallback: capture any other tables we might have missed
    extra_tables = parse_all_tables_generic(soup)
    if extra_tables:
        result["_extra_tables"] = extra_tables

    return result


def flatten_match(data):
    """Return only match-level stats (no nested player data)."""
    return {k: v for k, v in data.items() if not k.startswith("_")}
