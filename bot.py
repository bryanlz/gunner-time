"""Gunner Time: a Telegram bot that sends Arsenal lineups, goals, red cards and full-time reports.

Data comes from FotMob's public (unofficial) JSON endpoints. Messages go to one Telegram chat.

Usage:
    python bot.py                    one scheduled run (what GitHub Actions runs every 15 minutes)
    python bot.py --get-chat-id      print the chat id of whoever last messaged the bot
    python bot.py --test-message     send a hello message to confirm the token and chat id work
    python bot.py --preview MATCH_ID print every message the bot would send for a match (sends nothing)
"""

import argparse
import html
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

ARSENAL_ID = 9825
SGT = timezone(timedelta(hours=8), "SGT")  # Singapore has no daylight saving, so a fixed offset is exact

FOTMOB = "https://www.fotmob.com/api/data"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

# Lineups come out 60-90 min before kickoff. Starting 3 hours early leaves plenty of slack for
# GitHub's scheduled runs, which can start 10-30+ minutes late.
LINEUP_WINDOW = timedelta(hours=3)       # start following a match this long before kickoff
XG_WAIT_LIMIT = timedelta(minutes=30)    # after full time, wait at most this long for xG before sending anyway
PREMATCH_POLL = 60                       # seconds between checks while waiting for the lineup
LIVE_POLL = 30                           # seconds between checks during the match
MAX_RUNTIME = int(5.5 * 3600)            # one run's limit; GitHub stops jobs at 6 hours

STATE_PATH = os.environ.get("STATE_PATH", "state.json")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

log = logging.getLogger("gunner-time")


# ---------- FotMob ----------

def fetch_json(path, **params):
    # FotMob's CDN caches responses for up to 15 minutes; a unique value per request gets fresh data
    params["_"] = int(time.time())
    for attempt in range(3):
        try:
            r = requests.get(f"{FOTMOB}/{path}", params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            if attempt == 2:
                raise
            time.sleep(10 * (attempt + 1))


def fetch_fixtures():
    data = fetch_json("teams", id=ARSENAL_ID, ccode3="SGP")
    return data["fixtures"]["allFixtures"]["fixtures"]


def fetch_match(match_id):
    return fetch_json("matchDetails", matchId=match_id)


def parse_utc(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


# ---------- Telegram ----------

def send(text, dry_run=False):
    """Send an HTML-formatted message. Returns True on success."""
    if dry_run or not BOT_TOKEN or not CHAT_ID:
        print("\n" + "-" * 40 + "\n" + text + "\n" + "-" * 40)
        return True
    for attempt in range(3):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=20,
            )
            if r.ok:
                return True
            log.warning("Telegram error %s: %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("Telegram request failed: %s", e)
        time.sleep(5 * (attempt + 1))
    return False


# ---------- Reading match data ----------

def esc(s):
    return html.escape(str(s or ""), quote=False)


def minute(e):
    t = e.get("time")
    extra = e.get("overloadTime")
    return f"{t}+{extra}'" if extra else f"{t}'"


def teams(match):
    home, away = match["header"]["teams"]
    return home, away


def score_line(match):
    home, away = teams(match)
    return f"{esc(home['name'])} {home.get('score', 0)}–{away.get('score', 0)} {esc(away['name'])}"


def competition(match):
    g = match.get("general", {})
    name = g.get("leagueName") or ""
    rnd = g.get("leagueRoundName")
    return f"{name} · {rnd}" if rnd and not str(rnd).isdigit() else name


def events(match):
    try:
        return match["content"]["matchFacts"]["events"]["events"] or []
    except (KeyError, TypeError):
        return []


def team_name(match, is_home):
    home, away = teams(match)
    return (home if is_home else away)["name"]


def goal_key(e):
    return f"goal-{(e.get('player') or {}).get('id')}-{e.get('time')}-{e.get('overloadTime')}"


def card_key(e):
    return f"red-{(e.get('player') or {}).get('id')}-{e.get('time')}"


def goals(match):
    return [e for e in events(match) if e.get("type") == "Goal"]


def red_cards(match):
    return [e for e in events(match) if e.get("type") == "Card" and e.get("card") in ("Red", "YellowRed")]


def arsenal_lineup(match):
    """Return Arsenal's confirmed lineup dict, or None if only a predicted lineup exists."""
    lu = match.get("content", {}).get("lineup") or {}
    if lu.get("lineupType") in (None, "predicted"):
        return None
    for side in ("homeTeam", "awayTeam"):
        team = lu.get(side) or {}
        if team.get("id") == ARSENAL_ID and len(team.get("starters") or []) == 11:
            return team
    return None


def xg(match):
    """Return (home_xg, away_xg) strings, or None if FotMob hasn't published xG."""
    try:
        groups = match["content"]["stats"]["Periods"]["All"]["stats"]
    except (KeyError, TypeError):
        return None
    for g in groups:
        for s in g.get("stats") or []:
            if s.get("key") == "expected_goals" and s.get("stats") and None not in s["stats"]:
                return tuple(str(v) for v in s["stats"])
    return None


# ---------- Message formatting ----------

def fmt_lineup(match, team):
    kickoff = parse_utc(match["header"]["status"]["utcTime"]).astimezone(SGT)
    home, away = teams(match)
    lines = [
        "🔴⚪ <b>GUNNER TIME — LINEUP</b>",
        f"{esc(home['name'])} vs {esc(away['name'])}",
        f"{esc(competition(match))}",
        f"Kick-off {kickoff:%H:%M} SGT ({kickoff:%a %-d %b})",
    ]
    if team.get("formation"):
        lines.append(f"Formation: {esc(team['formation'])}")
    lines.append("")
    for i, p in enumerate(team["starters"], 1):
        cap = " (C)" if p.get("isCaptain") else ""
        lines.append(f"{i}. {esc(p['name'])}{cap}")
    subs = [esc(p["name"]) for p in team.get("subs") or []]
    if subs:
        lines += ["", f"<b>Subs:</b> {', '.join(subs)}"]
    return "\n".join(lines)


def goal_detail(e):
    bits = []
    if e.get("ownGoal"):
        bits.append("own goal")
    elif e.get("goalDescription"):
        bits.append(e["goalDescription"].lower())
    return f" ({', '.join(bits)})" if bits else ""


def fmt_goal(match, e):
    scorer_team = team_name(match, e.get("isHome"))
    # For an own goal FotMob credits the team that benefited, so the headline still reads correctly
    arsenal = (teams(match)[0 if e.get("isHome") else 1]["id"] == ARSENAL_ID)
    head = "⚽ <b>GOAL — ARSENAL!</b>" if arsenal else f"⚽ Goal — {esc(scorer_team)}"
    assist = e.get("assistInput")
    new = e.get("newScore") or []
    home, away = teams(match)
    score = (f"{esc(home['name'])} {new[0]}–{new[1]} {esc(away['name'])}"
             if len(new) == 2 else score_line(match))
    return "\n".join([
        head,
        f"{esc(e.get('nameStr') or (e.get('player') or {}).get('name'))} {minute(e)}{goal_detail(e)}",
        f"Assist: {esc(assist) if assist else 'none'}",
        "",
        f"<b>{score}</b>",
    ])


def fmt_red(match, e):
    kind = "second yellow" if e.get("card") == "YellowRed" else "straight red"
    return "\n".join([
        "🟥 <b>RED CARD</b>",
        f"{esc(e.get('nameStr') or (e.get('player') or {}).get('name'))} "
        f"({esc(team_name(match, e.get('isHome')))}) {minute(e)} — {kind}",
        "",
        score_line(match),
    ])


def fmt_ruled_out(match, e):
    return "\n".join([
        "❌ <b>GOAL RULED OUT</b>",
        f"{esc(e.get('nameStr'))} {minute(e)} ({esc(team_name(match, e.get('isHome')))})",
        "",
        f"<b>{score_line(match)}</b>",
    ])


def fmt_summary(match):
    status = match["header"]["status"]
    lines = ["🏁 <b>FULL TIME</b>", f"<b>{score_line(match)}</b>"]
    reason = status.get("reason") or {}
    if reason.get("penalties"):
        h, a = reason["penalties"]
        home, away = teams(match)
        winner = home["name"] if h > a else away["name"]
        lines.append(f"{esc(winner)} win {max(h, a)}–{min(h, a)} on penalties")
    elif reason.get("short") == "AET":
        lines.append("After extra time")
    lines.append(esc(competition(match)))

    lines += ["", "<b>Goals</b>"]
    gs = goals(match)
    if not gs:
        lines.append("None")
    for e in gs:
        assist = e.get("assistInput")
        lines.append(f"⚽ {minute(e)} {esc(e.get('nameStr'))} ({esc(team_name(match, e.get('isHome')))})"
                     f"{goal_detail(e)} — assist: {esc(assist) if assist else 'none'}")

    reds = red_cards(match)
    if reds:
        lines += ["", "<b>Red cards</b>"]
        for e in reds:
            extra = " (2nd yellow)" if e.get("card") == "YellowRed" else ""
            lines.append(f"🟥 {minute(e)} {esc(e.get('nameStr'))} ({esc(team_name(match, e.get('isHome')))}){extra}")

    lines += ["", "<b>xG</b>"]
    x = xg(match)
    home, away = teams(match)
    lines.append(f"{esc(home['name'])} {x[0]} – {x[1]} {esc(away['name'])}" if x else "Not available")
    return "\n".join(lines)


# ---------- State ----------

def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def match_state(state, match_id):
    return state.setdefault(str(match_id), {"lineup": False, "alerted": {}, "summary": False})


# ---------- Core logic ----------

def process_match(match, ms, now, dry_run=False):
    """Send whatever is new for this match. Mutates ms (this match's state). Returns True once fully done."""
    status = match["header"]["status"]
    if status.get("cancelled"):
        log.info("Match cancelled/postponed")
        return True

    if not ms["lineup"]:
        team = arsenal_lineup(match)
        if team and send(fmt_lineup(match, team), dry_run):
            ms["lineup"] = True

    if not status.get("started"):
        return False

    current = {}
    for e in goals(match):
        current[goal_key(e)] = e
    for e in red_cards(match):
        current[card_key(e)] = e

    for key, e in current.items():
        if key in ms["alerted"]:
            continue
        record = {"nameStr": e.get("nameStr"), "time": e.get("time"), "overloadTime": e.get("overloadTime"),
                  "isHome": e.get("isHome"), "player": (e.get("player") or {}).get("id"),
                  "newScore": e.get("newScore")}
        # FotMob sometimes corrects a goal's minute, which changes its key; don't announce it twice
        renamed = [k for k, r in ms["alerted"].items() if k.startswith("goal") and k not in current
                   and r.get("player") == record["player"] and r.get("newScore") == record["newScore"]]
        if key.startswith("goal") and renamed:
            del ms["alerted"][renamed[0]]
            ms["alerted"][key] = record
            continue
        msg = fmt_goal(match, e) if key.startswith("goal") else fmt_red(match, e)
        if send(msg, dry_run):
            ms["alerted"][key] = record

    # A goal we announced that has vanished from the feed, with the score dropping, was overturned (usually VAR)
    home, away = teams(match)
    total = (home.get("score") or 0) + (away.get("score") or 0)
    vanished = [k for k in ms["alerted"] if k.startswith("goal") and k not in current]
    announced = sum(1 for k in ms["alerted"] if k.startswith("goal"))
    if vanished and total < announced:
        for key in vanished[:announced - total]:
            if send(fmt_ruled_out(match, ms["alerted"][key]), dry_run):
                del ms["alerted"][key]

    if status.get("finished") and not ms["summary"]:
        ms.setdefault("finished_seen", now.isoformat())
        waited = now - datetime.fromisoformat(ms["finished_seen"])
        if xg(match) or waited > XG_WAIT_LIMIT:
            if send(fmt_summary(match), dry_run):
                ms["summary"] = True
    return ms["summary"]


def pick_match(fixtures, state, now):
    """The match the bot should focus on: the earliest one not yet finished-and-reported."""
    for f in fixtures:
        st = f.get("status", {})
        kickoff = parse_utc(st["utcTime"])
        if st.get("cancelled") or kickoff < now - timedelta(hours=6):
            continue
        if state.get(str(f["id"]), {}).get("summary"):
            continue
        return f, kickoff
    return None, None


def prune(state, keep=30):
    """Drop old matches so the state file stays small."""
    ids = sorted((k for k in state if k.isdigit()), key=int)
    for k in ids[:-keep]:
        del state[k]


def run(max_runtime):
    """One scheduled run: exit quickly if no match is near, otherwise follow the match.

    GitHub Actions starts this every 15 minutes. A run stops after max_runtime seconds; the
    next scheduled run carries on from the saved state, so long matchdays span several runs.
    """
    if not BOT_TOKEN or not CHAT_ID:
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
    deadline = time.monotonic() + max_runtime
    state = load_state()
    now = datetime.now(timezone.utc)
    # Changes once a month so the bot commits at least monthly; GitHub pauses schedules on repos idle for 60 days
    state["heartbeat"] = now.strftime("%Y-%m")

    fixture, kickoff = pick_match(fetch_fixtures(), state, now)
    if not fixture:
        log.info("No upcoming fixture found")
    elif kickoff - LINEUP_WINDOW > now:
        log.info("Next: %s v %s at %s SGT, nothing to do yet", fixture["home"]["name"],
                 fixture["away"]["name"], kickoff.astimezone(SGT).strftime("%a %d %b %H:%M"))
    else:
        follow(fixture, match_state(state, fixture["id"]), state, deadline)
    prune(state)
    save_state(state)


def follow(fixture, ms, state, deadline):
    """Matchday: check the match repeatedly until its full-time report is sent or the run's time is up."""
    label = f"{fixture['home']['name']} v {fixture['away']['name']}"
    log.info("Following %s", label)
    while time.monotonic() < deadline:
        started = False
        try:
            now = datetime.now(timezone.utc)
            match = fetch_match(fixture["id"])
            done = process_match(match, ms, now)
            save_state(state)
            if done:
                log.info("Finished with %s", label)
                return
            status = match["header"]["status"]
            started = status.get("started")
            if not started and parse_utc(status["utcTime"]) - now > LINEUP_WINDOW:
                log.info("Kickoff moved later, stopping until it's closer")
                return
        except Exception:
            log.exception("Error checking the match, retrying")
        time.sleep(LIVE_POLL if started else PREMATCH_POLL)
    log.info("Reached this run's time limit; the next scheduled run continues from here")


# ---------- Helper commands ----------

def get_chat_id():
    if not BOT_TOKEN:
        sys.exit("Set TELEGRAM_BOT_TOKEN first.")
    r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", timeout=20).json()
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name")
             for u in r.get("result", []) if "message" in u}
    if not chats:
        print("No messages found. Send your bot any message in Telegram, then run this again.")
    for cid, name in chats.items():
        print(f"Chat id for {name}: {cid}")


def preview(match_id):
    match = fetch_match(match_id)
    ms = {"lineup": False, "alerted": {}, "summary": False}
    process_match(match, ms, datetime.now(timezone.utc), dry_run=True)
    if match["header"]["status"].get("finished") and not ms["summary"]:
        print("\n(Full-time report is waiting for xG; it would be sent within 30 min of full time.)")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Gunner Time Arsenal bot")
    p.add_argument("--get-chat-id", action="store_true")
    p.add_argument("--test-message", action="store_true")
    p.add_argument("--preview", metavar="MATCH_ID")
    p.add_argument("--max-runtime", type=int, default=MAX_RUNTIME,
                   help="seconds before a run stops (default: 5.5 hours, under GitHub's 6-hour job limit)")
    args = p.parse_args()
    if args.get_chat_id:
        get_chat_id()
    elif args.test_message:
        ok = send("🔴⚪ <b>Gunner Time</b> is connected. You'll get Arsenal lineups, goals, "
                  "red cards and full-time reports here.")
        print("Sent!" if ok else "Failed, check the token and chat id.")
    elif args.preview:
        preview(args.preview)
    else:
        run(args.max_runtime)


if __name__ == "__main__":
    main()
