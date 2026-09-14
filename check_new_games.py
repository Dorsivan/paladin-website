"""
AoE4 new game checker for TyrusLunarspear.
Reads state from aoe4_tracker_state.json, checks aoe4world API for new games,
and outputs JSON with new game details if any are found.
"""
import json
import sys
from pathlib import Path
from datetime import datetime
import requests

API_GAMES_URL = "https://aoe4world.com/api/v0/players/{player_id}/games?leaderboard=rm_solo"
API_GAME_DETAIL_URL = "https://aoe4world.com/api/v0/players/{player_id}/games/{game_id}"
STATE_FILE = Path(__file__).parent / "aoe4_tracker_state.json"
REQUEST_TIMEOUT = 20


def load_state():
    fh = STATE_FILE.open("r", encoding="utf-8")
    data = json.load(fh)
    fh.close()
    return data


def format_duration(seconds):
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return "?"
    minutes = total // 60
    secs = total % 60
    return f"{minutes}:{secs:02d}"


def extract_date(started_at):
    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        return ""


def normalize_civ(civ):
    if not civ:
        return "unknown"
    return civ.lower().replace(" ", "_")


def fetch_and_parse_game(player_id, gid):
    detail_resp = requests.get(
        API_GAME_DETAIL_URL.format(player_id=player_id, game_id=gid),
        timeout=REQUEST_TIMEOUT
    )
    detail_resp.raise_for_status()
    detail = detail_resp.json()

    teams = detail.get("teams", [])
    player_team = None
    enemy_team = None
    for team in teams:
        for member in team:
            pid = str(member.get("profile_id", ""))
            if pid == player_id:
                player_team = member
            else:
                enemy_team = member

    if not player_team or not enemy_team:
        return None

    player_name = player_team.get("name", "")
    player_slug = player_name.strip().replace(" ", "-")
    aoe4_url = "https://aoe4world.com/players/" + player_id + "-" + player_slug + "/games/" + gid

    return {
        "game_id": gid,
        "date": extract_date(detail.get("started_at", "")),
        "map": str(detail.get("map", "")),
        "my_civ": normalize_civ(player_team.get("civilization", "")),
        "opponent_civ": normalize_civ(enemy_team.get("civilization", "")),
        "opponent_name": str(enemy_team.get("name", "")),
        "my_name": str(player_team.get("name", "")),
        "result": str(player_team.get("result", "")),
        "duration": format_duration(detail.get("duration")),
        "aoe4_url": aoe4_url,
    }


def main():
    state = load_state()
    player_id = state["player_id"]
    last_seen = str(state.get("last_seen_game_id", ""))

    resp = requests.get(API_GAMES_URL.format(player_id=player_id), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        games = data.get("games", data)
    else:
        games = data

    new_games = []
    for g in games:
        gid = str(g.get("game_id", "")).strip()
        if not gid:
            continue
        if gid == last_seen:
            break
        new_games.append(g)

    if not new_games:
        print(json.dumps({"new_games": []}))
        return

    results = []
    for g in reversed(new_games):
        gid = str(g.get("game_id", ""))
        try:
            result = fetch_and_parse_game(player_id, gid)
            if result:
                results.append(result)
        except Exception:
            continue

    newest_id = str(games[0].get("game_id", "")).strip()
    print(json.dumps({"new_games": results, "newest_id": newest_id}, ensure_ascii=False))


if __name__ == "__main__":
    main()
