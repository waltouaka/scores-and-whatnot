"""Fetch last night's NHL goals and NFL touchdowns and write data.js for the website.

Run it:   python fetch_goals.py            (uses yesterday's date)
          python fetch_goals.py 2026-10-05 (uses a specific date)

Only uses Python's built-in libraries, so there is nothing to install.
"""
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

NHL_API = "https://api-web.nhle.com/v1"
ESPN_API = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"

# Team colors for NHL (approximate). NFL colors come from ESPN.
NHL_COLORS = {
    "ANA": "#F47A38", "BOS": "#FFB81C", "BUF": "#002654", "CGY": "#D2001C",
    "CAR": "#CC0000", "CHI": "#CF0A2C", "COL": "#6F263D", "CBJ": "#002654",
    "DAL": "#006847", "DET": "#CE1126", "EDM": "#FF4C00", "FLA": "#C8102E",
    "LAK": "#A2AAAD", "MIN": "#154734", "MTL": "#AF1E2D", "NSH": "#FFB81C",
    "NJD": "#CE1126", "NYI": "#00539B", "NYR": "#0038A8", "OTT": "#C52032",
    "PHI": "#F74902", "PIT": "#FFB81C", "SEA": "#99D9D9", "SJS": "#006D75",
    "STL": "#002F87", "TBL": "#002868", "TOR": "#00205B", "UTA": "#6CACE4",
    "VAN": "#00205B", "VGK": "#B4975A", "WSH": "#C8102E", "WPG": "#041E42",
}


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "goal-site/0.2"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def target_date():
    if len(sys.argv) > 1:
        return sys.argv[1]
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/Toronto"))
    except Exception:  # no timezone data (some Windows setups): use computer time
        now = datetime.now()
    return (now - timedelta(days=1)).strftime("%Y-%m-%d")


def search_link(text):
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(text)


def city_name(place, common):
    """'New York' + 'Giants' -> 'NY Giants' so the two NY/LA teams are distinct."""
    short = {"New York": "NY", "Los Angeles": "LA"}
    if place in short:
        return f"{short[place]} {common}"
    return place or common


# ------------------------------------------------------------------ NHL

def nhl_team(t):
    abbrev = t.get("abbrev", "")
    place = t.get("placeName", {}).get("default", "")
    common = t.get("commonName", {}).get("default", "")
    return {
        "id": t.get("id"), "n": city_name(place, common) or abbrev,
        "c": NHL_COLORS.get(abbrev, "#7A8793"), "s": t.get("score", 0),
        "logo": t.get("logo") or f"https://assets.nhle.com/logos/nhl/svg/{abbrev}_light.svg",
        "logoDark": t.get("darkLogo") or f"https://assets.nhle.com/logos/nhl/svg/{abbrev}_dark.svg",
    }


def nhl_roster(pbp):
    names = {}
    for r in pbp.get("rosterSpots", []):
        first = r.get("firstName", {}).get("default", "")
        last = r.get("lastName", {}).get("default", "")
        names[r.get("playerId")] = f"{first[:1]}. {last}".strip()
    return names


def nhl_goal_type(d, home_id):
    mod = d.get("goalModifier", "")
    if mod == "empty-net":
        return "Empty-net goal"
    if mod == "penalty-shot":
        return "Penalty-shot goal"
    try:
        code = d.get("situationCode", "")
        away_sk, home_sk = int(code[1]), int(code[2])
        own, opp = (home_sk, away_sk) if d.get("eventOwnerTeamId") == home_id else (away_sk, home_sk)
        if own > opp:
            return "Power-play goal"
        if own < opp:
            return "Short-handed goal"
    except Exception:
        pass
    return "Goal"


def parse_nhl_game(game_id, date):
    pbp = get(f"{NHL_API}/gamecenter/{game_id}/play-by-play")
    away, home = nhl_team(pbp["awayTeam"]), nhl_team(pbp["homeTeam"])
    names = nhl_roster(pbp)

    last = pbp.get("gameOutcome", {}).get("lastPeriodType", "REG")
    status = {"OT": "Final / OT", "SO": "Final / SO"}.get(last, "Final")

    plays = []
    for ev in pbp.get("plays", []):
        if ev.get("typeDescKey") != "goal":
            continue
        pd = ev.get("periodDescriptor", {})
        if pd.get("periodType") == "SO":
            continue  # shootout attempts are not regular goals
        d = dict(ev.get("details", {}))
        d["situationCode"] = ev.get("situationCode", "")

        n = pd.get("number", 1)
        mm, ss = (ev.get("timeInPeriod", "0:00").split(":") + ["0"])[:2]
        minute_in_game = (n - 1) * 20 + int(mm) + int(ss) / 60
        label = "OT" if pd.get("periodType") == "OT" else {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")

        side = "home" if d.get("eventOwnerTeamId") == home["id"] else "away"
        who = names.get(d.get("scoringPlayerId"), "Own goal")
        assists = [names.get(d.get(k)) for k in ("assist1PlayerId", "assist2PlayerId") if d.get(k)]

        url = d.get("highlightClipSharingUrl")  # the NHL's own link to the clip
        exact = bool(url)
        if not url:
            url = search_link(f"{who} goal {away['n']} vs {home['n']} {date}")

        plays.append({
            "t": round(minute_in_game, 1), "clock": f"{label} {int(mm)}:{ss}", "team": side,
            "who": who, "assist": ", ".join(a for a in assists if a) or "unassisted",
            "type": nhl_goal_type(d, home["id"]), "v": None, "url": url, "exact": exact,
        })

    for t in (away, home):
        t.pop("id", None)
    return {"sport": "NHL", "status": status, "away": away, "home": home, "plays": plays}


def nhl_games(date):
    schedule = get(f"{NHL_API}/score/{date}")
    games = []
    for g in schedule.get("games", []):
        if g.get("gameState") not in ("FINAL", "OFF"):
            continue  # skip games that are not finished
        try:
            game = parse_nhl_game(g["id"], date)
            games.append(game)
            print(f"  NHL {game['away']['n']} at {game['home']['n']}: {len(game['plays'])} goals")
        except Exception as err:
            print(f"  NHL game {g.get('id')} skipped: {err}")
    return games


# ------------------------------------------------------------------ NFL

def nfl_team(c):
    t = c["team"]
    logo = t.get("logo")
    return {
        "id": t.get("id"), "n": city_name(t.get("location", ""), t.get("name", "")) or t.get("displayName", ""),
        "c": "#" + (t.get("color") or "7A8793"), "s": int(c.get("score") or 0),
        "logo": logo, "logoDark": logo.replace("/500/", "/500-dark/") if logo else None,
    }


def split_td_text(text, fallback):
    """'Josh Allen 10 Yd Run (Tyler Bass Kick)' -> ('Josh Allen', '10-yd run')."""
    m = re.match(r"^(.*?)\s+(\d+)\s+Yd\s+(.*)$", text.strip(), re.I)
    if not m:
        return text.strip() or fallback, fallback
    rest = re.sub(r"\s*\(.*?\)\s*$", "", m.group(3)).strip()
    rest = rest[:1].lower() + rest[1:]
    return m.group(1), f"{m.group(2)}-yd {rest}"


def find_video(videos, who, used):
    """Best-effort: look for an ESPN highlight video that mentions this player scoring."""
    last = who.split()[-1].lower() if who else ""
    if len(last) < 3:
        return None
    for i, v in enumerate(videos):
        if i in used:
            continue
        blob = (v.get("headline", "") + " " + v.get("description", "")).lower()
        if last in blob and any(k in blob for k in ("touchdown", " td", "scores", "end zone")):
            href = v.get("links", {}).get("web", {}).get("href")
            if href:
                used.add(i)
                return href
    return None


def parse_nfl_game(ev, date):
    comp = ev["competitions"][0]
    teams = {c["homeAway"]: nfl_team(c) for c in comp["competitors"]}
    away, home = teams["away"], teams["home"]
    status = ev.get("status", {}).get("type", {}).get("detail", "Final").replace("/", " / ")

    summary = get(f"{ESPN_API}/summary?event={ev['id']}")
    videos = summary.get("videos", [])
    used = set()

    plays = []
    for sp in summary.get("scoringPlays", []):
        ttext = sp.get("type", {}).get("text", "")
        if "touchdown" not in ttext.lower() and sp.get("scoringType", {}).get("name") != "touchdown":
            continue
        p = sp.get("period", {}).get("number", 1)
        mm, ss = (sp.get("clock", {}).get("displayValue", "0:00").split(":") + ["0"])[:2]
        remaining = int(mm) + int(ss) / 60
        minute_in_game = (p - 1) * 15 + (15 - remaining) if p <= 4 else 60 + (10 - remaining)
        label = f"Q{p}" if p <= 4 else "OT"

        who, assist = split_td_text(sp.get("text", ""), ttext)
        side = "home" if sp.get("team", {}).get("id") == home["id"] else "away"

        url = find_video(videos, who, used)
        exact = bool(url)
        if not url:
            url = search_link(f"{who} touchdown {away['n']} vs {home['n']} {date} NFL highlights")

        plays.append({
            "t": round(minute_in_game, 1), "clock": f"{label} {int(mm)}:{ss}", "team": side,
            "who": who, "assist": assist, "type": ttext.replace(" Touchdown", " TD") or "TD",
            "v": None, "url": url, "exact": exact,
        })

    for t in (away, home):
        t.pop("id", None)
    return {"sport": "NFL", "status": status, "away": away, "home": home, "plays": plays}


def nfl_games(date):
    board = get(f"{ESPN_API}/scoreboard?dates={date.replace('-', '')}")
    games = []
    for ev in board.get("events", []):
        if not ev.get("status", {}).get("type", {}).get("completed"):
            continue  # skip games that are not finished
        try:
            game = parse_nfl_game(ev, date)
            games.append(game)
            print(f"  NFL {game['away']['n']} at {game['home']['n']}: {len(game['plays'])} touchdowns")
        except Exception as err:
            print(f"  NFL game {ev.get('id')} skipped: {err}")
    return games


# ------------------------------------------------------------------ main

def main():
    date = target_date()
    print(f"Looking up games for {date} ...")
    games, failed = [], False
    for label, fn in (("NHL", nhl_games), ("NFL", nfl_games)):
        try:
            games += fn(date)
        except Exception as err:
            failed = True
            print(f"{label} lookup failed: {err}")

    with open("data.js", "w", encoding="utf-8") as f:
        f.write(f"window.LIVE_DATE = {json.dumps(date)};\n")
        f.write(f"window.LIVE_GAMES = {json.dumps(games, indent=1)};\n")
    total = sum(len(g["plays"]) for g in games)
    print(f"Done: {len(games)} games, {total} scoring plays written to data.js")
    if failed:
        sys.exit(1)  # makes the nightly job report a problem instead of publishing partial data


if __name__ == "__main__":
    main()
