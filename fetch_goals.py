"""Fetch NHL goals and NFL touchdowns/field goals and save them in data.js for the website.

Run it:   python fetch_goals.py            (fills in every missing night since SEASON_START,
                                            and refreshes the last few nights)
          python fetch_goals.py 2026-10-05 (just that one night)

Also saves the NHL standings and, for every game, team stats and game videos.
Only uses Python's built-in libraries, so there is nothing to install.
"""
import json
import os
import re
import sys
import time
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
    time.sleep(0.15)  # be polite to the free APIs
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


# ------------------------------------------------------------------ YouTube lookups (NFL clips)
# ESPN does not list NFL clips, so we look for them on YouTube. With a free YOUTUBE_API_KEY (optional, set it as a
# GitHub secret) the official search is used; without one we read the normal YouTube search page.
YT_BUDGET = [400]  # most lookups per run, so a run can never drag on


def _yt_scrape(query):
    url = "https://www.youtube.com/results?sp=EgIQAQ%253D%253D&search_query=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                                               "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", "replace")
    m = re.search(r"ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o and isinstance(o["videoRenderer"], dict):
                v = o["videoRenderer"]
                try:
                    out.append({"id": v["videoId"], "title": "".join(r.get("text", "") for r in v["title"]["runs"]),
                                "channel": "".join(r.get("text", "") for r in v.get("ownerText", {}).get("runs", []))})
                except Exception:
                    pass
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(json.loads(m.group(1)))
    return out


def _yt_api(query, key):
    url = ("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&videoEmbeddable=true&maxResults=6&q="
           + urllib.parse.quote_plus(query) + "&key=" + key)
    return [{"id": i["id"]["videoId"], "title": i["snippet"]["title"], "channel": i["snippet"]["channelTitle"]}
            for i in get(url).get("items", [])]


def yt_results(query):
    if YT_BUDGET[0] <= 0:
        return []
    YT_BUDGET[0] -= 1
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if key:
        try:
            return _yt_api(query, key)
        except Exception as err:
            print(f"    YouTube API failed ({err}); reading the search page instead")
    try:
        return _yt_scrape(query)
    except Exception as err:
        print(f"    YouTube search failed: {err}")
        return []


def yt_embeddable(video_id):
    """True only if YouTube itself says the video can play on other websites (the NFL's own uploads usually say no)."""
    try:
        req = urllib.request.Request("https://www.youtube.com/watch?v=" + video_id,
                                     headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                                              "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
        m = re.search(r'"playableInEmbed"\s*:\s*(true|false)', html)
        if m:
            return m.group(1) == "true"
        # could not read the flag: fall back to YouTube's oEmbed check
        get("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote_plus("https://www.youtube.com/watch?v=" + video_id))
        return True
    except Exception:
        return False


def yt_pick(query, must, avoid=()):
    """First result whose title mentions every word in `must` (and is allowed to be embedded). Official NFL uploads first."""
    res = [r for r in yt_results(query) if r["id"] not in avoid and all(w.lower() in r["title"].lower() for w in must if w)]
    res.sort(key=lambda r: ("highlight" not in r["title"].lower(), "nfl" not in r["channel"].lower()))
    for r in res[:6]:  # keep going down the list until one that can really be played inside the page
        if yt_embeddable(r["id"]):
            return {"id": r["id"], "title": r["title"]}
    return None


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
        "id": t.get("id"), "n": city_name(place, common) or abbrev, "ab": abbrev,
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


def nhl_heads(pbp):
    return {r.get("playerId"): r.get("headshot") for r in pbp.get("rosterSpots", []) if r.get("headshot")}


def nfl_heads(summary):
    """name (lower case) -> headshot link, for everyone in the game's box score and rosters."""
    out = {}

    def walk(o):
        if isinstance(o, dict):
            name = o.get("displayName") or o.get("fullName")
            if name and o.get("id"):
                hs = (o.get("headshot") or {}).get("href") if isinstance(o.get("headshot"), dict) else o.get("headshot")
                out.setdefault(name.lower(), hs or f"https://a.espncdn.com/i/headshots/nfl/players/full/{o['id']}.png")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk((summary.get("boxscore") or {}).get("players") or [])
    walk(summary.get("rosters") or [])
    return out


ROSTER_CACHE = {}


def nfl_roster_heads(team_id):
    """name (lower case) -> headshot for a team's whole roster (ESPN's team roster list). Cached for the run."""
    if team_id in ROSTER_CACHE:
        return ROSTER_CACHE[team_id]
    out = {}
    try:
        data = get(f"{ESPN_API}/teams/{team_id}/roster")

        def walk(o):
            if isinstance(o, dict):
                name = o.get("displayName") or o.get("fullName")
                if name and o.get("id"):
                    hs = o.get("headshot")
                    hs = hs.get("href") if isinstance(hs, dict) else hs
                    out[name.lower()] = hs or f"https://a.espncdn.com/i/headshots/nfl/players/full/{o['id']}.png"
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(data.get("athletes") or [])
    except Exception as err:
        print(f"    roster lookup failed for team {team_id}: {err}")
    ROSTER_CACHE[team_id] = out
    return out


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


NHL_STAT_NAMES = {"sog": "Shots on goal", "faceoffWinningPctg": "Faceoff win %", "powerPlay": "Power play",
                  "pim": "Penalty minutes", "hits": "Hits", "blockedShots": "Blocked shots",
                  "giveaways": "Giveaways", "takeaways": "Takeaways"}


def nhl_extras(game_id):
    """Team stats + line score from the right-rail feed. Never fails the whole game."""
    stats, line = [], None
    try:
        rr = get(f"{NHL_API}/gamecenter/{game_id}/right-rail")
        for item in rr.get("teamGameStats", []):
            label = NHL_STAT_NAMES.get(item.get("category"))
            if not label:
                continue
            a, h = item.get("awayValue"), item.get("homeValue")
            if item["category"] == "faceoffWinningPctg":
                a, h = f"{round(float(a) * 100)}%", f"{round(float(h) * 100)}%"
            stats.append([label, str(a), str(h)])
        by = (rr.get("linescore") or {}).get("byPeriod") or []
        if by:
            cols = []
            for p in by:
                d = p.get("periodDescriptor", {})
                cols.append("SO" if d.get("periodType") == "SO" else "OT" if d.get("periodType") == "OT" else str(d.get("number")))
            line = {"cols": cols, "away": [p.get("away", 0) for p in by], "home": [p.get("home", 0) for p in by]}
    except Exception as err:
        print(f"    (no team stats for NHL game {game_id}: {err})")
    return stats, line


def video_id(path):
    m = re.search(r"(\d{8,})$", str(path or ""))
    return m.group(1) if m else None


def parse_nhl_game(game_id, date, sched=None):
    sched = sched or {}
    pbp = get(f"{NHL_API}/gamecenter/{game_id}/play-by-play")
    away, home = nhl_team(pbp["awayTeam"]), nhl_team(pbp["homeTeam"])
    names = nhl_roster(pbp)
    heads = nhl_heads(pbp)

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
        clip_id = d.get("highlightClip") or video_id(d.get("highlightClipSharingUrl"))
        embed = ({"kind": "iframe", "src": f"https://players.brightcove.net/6415718365001/EXtG1xJ7H_default/index.html?videoId={clip_id}"}
                 if clip_id else None)

        plays.append({
            "t": round(minute_in_game, 1), "clock": f"{label} {int(mm)}:{ss}", "team": side,
            "who": who, "assist": ", ".join(a for a in assists if a) or "unassisted",
            "type": nhl_goal_type(d, home["id"]), "kind": "goal", "embed": embed, "url": url, "exact": exact,
            **({"img": heads[d.get("scoringPlayerId")]} if heads.get(d.get("scoringPlayerId")) else {}),
        })

    stats, line = nhl_extras(game_id)
    vids = []
    for label, key in (("Game recap", "threeMinRecap"), ("Condensed game", "condensedGame")):
        vid = video_id(sched.get(key))
        if vid:
            vids.append({"label": label, "id": vid, "url": "https://www.nhl.com" + sched[key]})

    for t in (away, home):
        t.pop("id", None)
    game = {"sport": "NHL", "id": str(game_id), "date": date, "pre": pbp.get("gameType") == 1,
            "status": status, "away": away, "home": home, "plays": plays, "stats": stats, "vids": vids}
    if line:
        game["ls"] = line
    return game


def nhl_games(date):
    schedule = get(f"{NHL_API}/score/{date}")
    games = []
    for g in schedule.get("games", []):
        if g.get("gameState") not in ("FINAL", "OFF"):
            continue  # skip games that are not finished
        if g.get("gameDate") not in (None, date):
            continue  # the feed can include the next night's games too
        try:
            game = parse_nhl_game(g["id"], date, g)
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
        "ab": t.get("abbreviation", ""), "nick": t.get("name", ""),
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
        return None, None
    for i, v in enumerate(videos):
        if i in used:
            continue
        blob = (v.get("headline", "") + " " + v.get("description", "")).lower()
        if last in blob and any(k in blob for k in ("touchdown", " td", "scores", "end zone", "field goal", " fg", "kicks")):
            links = v.get("links", {})
            href = links.get("web", {}).get("href")
            src = links.get("source", {})
            mp4 = (src.get("HD") or src.get("full") or links.get("mobile", {}).get("source") or {}).get("href")
            if mp4 and ".mp4" not in mp4.lower():
                mp4 = None  # only plain video files can play inside the page
            if href or mp4:
                used.add(i)
                return href, mp4
    return None, None


NFL_WEEK1 = "2026-09-10"  # only used if ESPN leaves the week number out


def nfl_week(ev, date):
    """('Week 4', 204): a label for the page plus a number to sort weeks by."""
    n = (ev.get("week") or {}).get("number")
    t = (ev.get("season") or {}).get("type") or 2
    if not n:
        n = max(1, (datetime.strptime(date, "%Y-%m-%d") - datetime.strptime(NFL_WEEK1, "%Y-%m-%d")).days // 7 + 1)
        t = 2
    if t == 1:
        label = f"Preseason {n}"
    elif t == 3:
        label = {1: "Wild Card", 2: "Divisional", 3: "Conference Championship", 5: "Super Bowl"}.get(n, "Playoffs")
    else:
        label = f"Week {n}"
    return label, t * 100 + n


NFL_STAT_NAMES = [("firstDowns", "1st downs"), ("totalYards", "Total yards"), ("netPassingYards", "Passing yards"),
                  ("rushingYards", "Rushing yards"), ("thirdDownEff", "3rd down"), ("fourthDownEff", "4th down"),
                  ("turnovers", "Turnovers"), ("totalPenaltiesYards", "Penalties-yards"), ("possessionTime", "Possession"),
                  ("sacksYardsLost", "Sacked-yards"), ("yardsPerPlay", "Yards per play"), ("redZoneAttempts", "Red zone")]


def nfl_stats(summary, away_id, home_id):
    by = {}
    for tm in (summary.get("boxscore") or {}).get("teams", []):
        by[str((tm.get("team") or {}).get("id"))] = {x.get("name"): x.get("displayValue") for x in tm.get("statistics", [])}
    a, h = by.get(str(away_id), {}), by.get(str(home_id), {})
    return [[label, str(a.get(k, "-")), str(h.get(k, "-"))] for k, label in NFL_STAT_NAMES if k in a or k in h]


def video_links(v):
    links = v.get("links", {})
    href = links.get("web", {}).get("href")
    src = links.get("source", {})
    mp4 = (src.get("HD") or src.get("full") or links.get("mobile", {}).get("source") or {}).get("href")
    if mp4 and ".mp4" not in mp4.lower():
        mp4 = None
    return href, mp4


def parse_nfl_game(ev, date, prev=None):
    comp = ev["competitions"][0]
    teams = {c["homeAway"]: nfl_team(c) for c in comp["competitors"]}
    away, home = teams["away"], teams["home"]
    status = ev.get("status", {}).get("type", {}).get("detail", "Final").replace("/", " / ")

    summary = get(f"{ESPN_API}/summary?event={ev['id']}")
    # every video ESPN lists for this game, from any of the places it puts them
    videos = list(summary.get("videos") or []) + list(summary.get("highlights") or []) + list(comp.get("highlights") or [])
    used = set()

    # the game's own highlight video, and whether this game is recent enough to look for every play separately
    heads = nfl_heads(summary)
    for t in (away, home):  # the full rosters fill in anyone the box score missed (kickers, receivers...)
        for k, v in nfl_roster_heads(t["id"]).items():
            heads.setdefault(k, v)
    prev = prev or {}
    old_yt = next((h.get("yt") for h in prev.get("hl", []) if h.get("yt")), None)
    old_plays = {(p.get("who"), p.get("clock")): p for p in prev.get("plays", [])}
    game_clip = ({"id": old_yt, "title": ""} if old_yt else
                 yt_pick(f"{away['n']} {away['nick']} vs {home['n']} {home['nick']} highlights NFL {date[:4]}", [away["nick"], home["nick"]]))
    used_yt = {game_clip["id"]} if game_clip else set()
    try:
        recent = (datetime.now() - datetime.strptime(date, "%Y-%m-%d")).days <= 10
    except Exception:
        recent = False

    plays = []
    for sp in summary.get("scoringPlays", []):
        ttext = sp.get("type", {}).get("text", "")
        is_td = "touchdown" in ttext.lower() or sp.get("scoringType", {}).get("name") == "touchdown"
        is_fg = "field goal" in ttext.lower()
        if not (is_td or is_fg):
            continue
        p = sp.get("period", {}).get("number", 1)
        mm, ss = (sp.get("clock", {}).get("displayValue", "0:00").split(":") + ["0"])[:2]
        remaining = int(mm) + int(ss) / 60
        minute_in_game = (p - 1) * 15 + (15 - remaining) if p <= 4 else 60 + (10 - remaining)
        label = f"Q{p}" if p <= 4 else "OT"

        who, assist = split_td_text(sp.get("text", ""), ttext)
        passer = (re.search(r"\bfrom ([A-Z][\w.'\- ]+?)(?: \(|$)", sp.get("text", "")) or [None, ""])[1].strip()
        if is_fg:
            assist = assist.replace("field Goal", "field goal")
        side = "home" if sp.get("team", {}).get("id") == home["id"] else "away"

        url, mp4 = find_video(videos, who, used)
        exact = bool(url)
        embed = {"kind": "video", "src": mp4} if mp4 else None
        of_game = False
        before = old_plays.get((who, f"{label} {int(mm)}:{ss}"))
        if not embed and before and before.get("embed") and not before.get("ofGame"):
            embed, url, exact = before["embed"], before["url"], True  # found on an earlier run: keep it
        if not embed:
            last = who.split()[-1] if who.split() else ""
            clip = None
            if recent and len(last) > 2:  # a clip of this exact play, found by player name
                clip = yt_pick(f"{who} {'field goal' if is_fg else 'touchdown'} {away['nick']} {home['nick']} NFL", [last], avoid=used_yt)
            if clip:
                used_yt.add(clip["id"])
            elif game_clip:
                clip, of_game = game_clip, True  # otherwise the game's highlight video
            if clip:
                embed = {"kind": "iframe", "src": "https://www.youtube.com/embed/" + clip["id"] + "?autoplay=1&rel=0"}
                url, exact = "https://www.youtube.com/watch?v=" + clip["id"], True
        if not url:
            url = search_link(f"{who} {'field goal' if is_fg else 'touchdown'} {away['n']} vs {home['n']} {date} NFL highlights")

        plays.append({
            "t": round(minute_in_game, 1), "clock": f"{label} {int(mm)}:{ss}", "team": side,
            "who": who, "assist": assist, "type": "Field goal" if is_fg else (ttext.replace(" Touchdown", " TD") or "TD"),
            "kind": "fg" if is_fg else "td", "embed": embed,
            "url": url, "exact": exact, **({"ofGame": True} if of_game else {}),
            **({"img": heads[who.lower()]} if heads.get(who.lower()) else {}),
            **({"img2": heads[passer.lower()]} if passer and heads.get(passer.lower()) else {}),
        })

    wk, wkn = nfl_week(ev, date)
    stats = nfl_stats(summary, away["id"], home["id"])
    hl = []
    for v in videos:
        href, mp4 = video_links(v)
        if (href or mp4) and len(hl) < 3 and (href, mp4) not in [(x["url"], x["src"]) for x in hl]:
            hl.append({"label": v.get("headline") or "Highlights", "src": mp4, "url": href})
    if game_clip:
        hl.insert(0, {"label": "Game highlights", "yt": game_clip["id"], "url": "https://www.youtube.com/watch?v=" + game_clip["id"], "src": None})
    ls = {}
    for side in ("away", "home"):
        c = next((x for x in comp["competitors"] if x["homeAway"] == side), {})
        ls[side] = [int(float(x.get("value") or 0)) for x in c.get("linescores", [])]
    line = ({"cols": [f"Q{i + 1}" if i < 4 else "OT" for i in range(len(ls["away"]))], "away": ls["away"], "home": ls["home"]}
            if ls["away"] and len(ls["away"]) == len(ls["home"]) else None)

    for t in (away, home):
        t.pop("id", None)
        t.pop("nick", None)
    game = {"sport": "NFL", "id": str(ev["id"]), "date": date, "wk": wk, "wkn": wkn,
            "status": status, "away": away, "home": home, "plays": plays, "stats": stats, "hl": hl}
    if line:
        game["ls"] = line
    return game


def nfl_games(date):
    board = get(f"{ESPN_API}/scoreboard?dates={date.replace('-', '')}")
    games = []
    for ev in board.get("events", []):
        if not ev.get("status", {}).get("type", {}).get("completed"):
            continue  # skip games that are not finished
        try:
            game = parse_nfl_game(ev, date)
            games.append(game)
            print(f"  NFL {game['away']['n']} at {game['home']['n']}: {len(game['plays'])} scoring plays ({game['wk']})")
        except Exception as err:
            print(f"  NFL game {ev.get('id')} skipped: {err}")
    return games


def et_date(iso):
    """ESPN gives a UTC time like 2026-10-05T00:15Z; the site files games under the US Eastern calendar day."""
    dt = datetime.strptime(iso.replace("Z", "")[:16], "%Y-%m-%dT%H:%M")
    try:
        from zoneinfo import ZoneInfo
        from datetime import timezone
        return dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("America/Toronto")).strftime("%Y-%m-%d")
    except Exception:
        return (dt - timedelta(hours=4)).strftime("%Y-%m-%d")


def nfl_season(days, end):
    """Every finished NFL game, fetched a whole WEEK at a time (Thursday to Monday), so no game is missed."""
    year = SEASON_START[:4]
    last = datetime.strptime(end, "%Y-%m-%d")
    have = {g["id"]: (d, g) for d, gl in days.items() for g in gl if g.get("sport") == "NFL"}
    added = failed = 0
    for stype, weeks in ((2, range(1, 19)), (3, range(1, 6))):
        for wk in weeks:
            try:
                board = get(f"{ESPN_API}/scoreboard?dates={year}&seasontype={stype}&week={wk}")
            except Exception as err:
                print(f"  NFL week {wk} (type {stype}) lookup failed: {err}")
                failed += 1
                continue
            for ev in board.get("events", []):
                if not ev.get("status", {}).get("type", {}).get("completed"):
                    continue
                date = et_date(ev["date"])
                if date > end:
                    continue
                old = have.get(str(ev["id"]))
                # already saved and old enough that nothing will change: skip the slow lookups
                if old and "hl" in old[1] and (not old[1]["plays"] or any(p.get("img") for p in old[1]["plays"])) and (last - datetime.strptime(date, "%Y-%m-%d")).days >= REFRESH_NIGHTS:
                    continue
                try:
                    game = parse_nfl_game(ev, date, old[1] if old else None)
                except Exception as err:
                    print(f"  NFL game {ev.get('id')} skipped: {err}")
                    continue
                if old:
                    days[old[0]] = [g for g in days[old[0]] if not (g.get("sport") == "NFL" and g.get("id") == game["id"])]
                days.setdefault(date, []).append(game)
                have[game["id"]] = (date, game)
                added += 1
                print(f"  NFL {game['away']['n']} at {game['home']['n']}: {len(game['plays'])} scoring plays ({game['wk']})")
    return added, failed


# ------------------------------------------------------------------ main

def fetch_standings():
    data = get(f"{NHL_API}/standings/now")
    teams = []
    for t in data.get("standings", []):
        streak = f"{t.get('streakCode', '')}{t.get('streakCount', '')}"
        teams.append({
            "ab": (t.get("teamAbbrev") or {}).get("default", ""), "name": (t.get("teamName") or {}).get("default", ""),
            "logo": t.get("teamLogo"), "logoDark": t.get("teamLogoDark"),
            "conf": t.get("conferenceName"), "div": t.get("divisionName"), "seq": t.get("divisionSequence"),
            "gp": t.get("gamesPlayed"), "w": t.get("wins"), "l": t.get("losses"), "otl": t.get("otLosses"),
            "pts": t.get("points"), "pp": t.get("pointPctg"), "row": t.get("regulationPlusOtWins"),
            "gf": t.get("goalFor"), "ga": t.get("goalAgainst"), "diff": t.get("goalDifferential"),
            "strk": streak, "l10": f"{t.get('l10Wins', 0)}-{t.get('l10Losses', 0)}-{t.get('l10OtLosses', 0)}",
        })
    date = next((t.get("date") for t in data.get("standings", []) if t.get("date")), None)
    return {"date": date, "teams": teams}


SEASON_START = "2026-09-01"  # the first run fills in every night from here to yesterday
REFRESH_NIGHTS = 3           # recent nights are re-checked each run, because clips post late


def load_var(name):
    """Read one saved value back out of data.js (None if it isn't there)."""
    prefix = f"window.{name} = "
    try:
        with open("data.js", encoding="utf-8") as f:
            for line in f:
                if line.startswith(prefix):
                    return json.loads(line[len(prefix):].strip().rstrip(";"))
    except Exception:
        pass
    return None


def load_days():
    """Read the nights saved by earlier runs, so history keeps growing."""
    return load_var("DAYS") or {}  # no file yet, or the old format: start the whole season over


def fetch_day(date):
    games, failed = [], False
    for label, fn in (("NHL", nhl_games),):  # football is fetched week by week (nfl_season)
        try:
            games += fn(date)
        except Exception as err:
            failed = True
            print(f"{label} lookup failed for {date}: {err}")
    return games, failed


def main():
    end = target_date()
    days = load_days()
    last = datetime.strptime(end, "%Y-%m-%d")

    if len(sys.argv) > 1:
        todo = [end]
    else:
        todo, d = [], datetime.strptime(SEASON_START, "%Y-%m-%d")
        while d <= last:
            date = d.strftime("%Y-%m-%d")
            # nights with no games are saved as empty, so only truly missing nights get looked up
            if date not in days or (last - d).days < REFRESH_NIGHTS:
                todo.append(date)
            d += timedelta(days=1)

    ok = bad = 0
    for date in todo:
        print(f"Looking up {date} ...")
        games, failed = fetch_day(date)
        if failed:
            bad += 1  # keep any older copy; this night is tried again next run
            continue
        days[date] = games
        ok += 1

    try:
        added, bad_weeks = nfl_season(days, end)
        print(f"NFL: {added} games saved or refreshed" + (f" ({bad_weeks} weeks failed)" if bad_weeks else ""))
    except Exception as err:
        print(f"NFL season lookup failed: {err}")

    try:
        standings = fetch_standings()
        print(f"Standings saved: {len(standings['teams'])} teams")
    except Exception as err:
        print(f"Standings lookup failed: {err}")
        standings = load_var("STANDINGS")  # keep the last good copy

    latest = max((d for d, g in days.items() if g), default=end)
    with open("data.js", "w", encoding="utf-8") as f:
        f.write(f"window.LIVE_DATE = {json.dumps(latest)};\n")
        f.write("window.STANDINGS = " + json.dumps(standings, separators=(",", ":")) + ";\n")
        f.write("window.DAYS = " + json.dumps(days, separators=(",", ":")) + ";\n")
    nights = sum(1 for g in days.values() if g)
    total = sum(len(g["plays"]) for day in days.values() for g in day)
    print(f"Done: {nights} nights with games, {total} scoring plays saved in data.js"
          + (f" ({bad} nights failed and will be retried)" if bad else ""))
    if bad and not ok:
        sys.exit(1)  # nothing worked at all (feeds down?): make the nightly job show a red X


if __name__ == "__main__":
    main()
