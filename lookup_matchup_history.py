"""
Look up past games for a specific matchup (my_civ vs opponent_civ) on a specific map.
Uses the detail endpoint to get accurate civ data.

Usage:
    python3 lookup_matchup_history.py <my_civ> <opponent_civ> <map_name> [--exclude-game-id <id>]

Example:
    python3 lookup_matchup_history.py chinese chinese "Dry Arabia" --exclude-game-id 253051649

Output: JSON with past_games list sorted by date.
"""
import json
import sys
import time
import argparse
import requests

PLAYER_ID = "6827902"
API_GAMES_URL = "https://aoe4world.com/api/v0/players/{}/games?leaderboard=rm_solo".format(PLAYER_ID)
API_DETAIL_URL = "https://aoe4world.com/api/v0/players/{}/games/".format(PLAYER_ID)
REQUEST_TIMEOUT = 20


def normalize(s):
    if not s:
        return ""
    return s.lower().replace(" ", "_")


def fetch_all_game_ids_for_map(map_name):
    """Fetch all game IDs that match the given map from the list endpoint."""
    map_lower = map_name.lower()
    matching_ids = []
    url = API_GAMES_URL
    page = 1

    while url:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        games = data.get("games", [])
        if not games:
            break

        for g in games:
            game_map = str(g.get("map", "")).lower()
            if map_lower in game_map or game_map in map_lower:
                matching_ids.append(str(g.get("game_id", "")))

        url = None
        nc = data.get("next_cursor")
        if nc:
            url = API_GAMES_URL + "&cursor=" + str(nc)
        elif len(games) >= 50:
            url = API_GAMES_URL + "&page=" + str(page + 1)
        page += 1
        if page > 100:
            break

    return matching_ids


def check_game_detail(gid, my_civ_norm, opp_civ_norm):
    """Fetch game detail and check if it matches the civ matchup."""
    resp = requests.get(API_DETAIL_URL + gid, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    detail = resp.json()

    teams = detail.get("teams", [])
    my_civ = None
    opp_civ = None
    my_result = None
    opp_name = None

    for team in teams:
        for member in team:
            pid = str(member.get("profile_id", ""))
            if pid == PLAYER_ID:
                my_civ = normalize(member.get("civilization", ""))
                my_result = member.get("result", "")
            else:
                opp_civ = normalize(member.get("civilization", ""))
                opp_name = member.get("name", "")

    if my_civ == my_civ_norm and opp_civ == opp_civ_norm:
        started = detail.get("started_at", "")[:10]
        duration = detail.get("duration")
        mins = ""
        if duration:
            try:
                total = int(duration)
                mins = "{}:{:02d}".format(total // 60, total % 60)
            except (TypeError, ValueError):
                pass
        return {
            "game_id": gid,
            "date": started,
            "opponent_name": opp_name or "",
            "result": my_result or "",
            "duration": mins,
            "map": str(detail.get("map", "")),
        }
    return None


def main():
    parser = argparse.ArgumentParser(description="Look up matchup history")
    parser.add_argument("my_civ", help="Your civilization")
    parser.add_argument("opponent_civ", help="Opponent civilization")
    parser.add_argument("map_name", help="Map name")
    parser.add_argument("--exclude-game-id", help="Game ID to exclude (e.g. the current game)", default=None)
    args = parser.parse_args()

    my_civ_norm = normalize(args.my_civ)
    opp_civ_norm = normalize(args.opponent_civ)
    exclude_id = args.exclude_game_id

    # Step 1: get all game IDs on this map
    map_game_ids = fetch_all_game_ids_for_map(args.map_name)

    if exclude_id:
        map_game_ids = [gid for gid in map_game_ids if gid != exclude_id]

    # Step 2: check each via detail endpoint
    past_games = []
    for gid in map_game_ids:
        try:
            result = check_game_detail(gid, my_civ_norm, opp_civ_norm)
            if result:
                past_games.append(result)
            time.sleep(0.12)
        except Exception:
            continue

    past_games.sort(key=lambda x: x["date"])

    wins = sum(1 for g in past_games if g["result"] == "win")
    losses = sum(1 for g in past_games if g["result"] == "loss")

    output = {
        "matchup": "{} vs {}".format(my_civ_norm, opp_civ_norm),
        "map": args.map_name,
        "total_games": len(past_games),
        "wins": wins,
        "losses": losses,
        "past_games": past_games,
    }

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
