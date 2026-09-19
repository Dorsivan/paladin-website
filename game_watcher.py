"""
AoE4 Game Watcher - Detects when TyrusLunarspear starts a game and sends
matchup tips from past games to Discord.

Runs as a lightweight background daemon. No AI tokens used for detection -
only a minimal AI call when a game is actually detected, to format tips.

Usage: python3 game_watcher.py
"""
import json
import re
import sys
import time
import logging
import subprocess
from pathlib import Path

import requests

# --- Configuration ---
PLAYER_ID = "6827902"
API_GAMES_URL = "https://aoe4world.com/api/v0/players/{}/games?leaderboard=rm_solo&limit=3".format(PLAYER_ID)
POLL_INTERVAL = 30  # seconds between API checks
GAME_HISTORY_DIR = Path(__file__).parent / "docs" / "Video Games" / "AOE4" / "Game History"
STATE_FILE = Path(__file__).parent / "game_watcher_state.json"
SITE_BASE_URL = "https://aura-aura.apps.ocp.paladinaura.com"
DOCS_DIR = Path(__file__).parent / "docs"
DISCORD_CHANNEL_ID = "channel:1549737107205914754"
OPENCLAW_CMD = "openclaw"
NL = chr(10)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("game_watcher")


def load_state():
    """Load watcher state."""
    if STATE_FILE.exists():
        fh = open(STATE_FILE, "r")
        data = json.load(fh)
        fh.close()
        return data
    return {"last_notified_game_id": None}


def save_state(state):
    """Persist watcher state."""
    fh = open(STATE_FILE, "w")
    json.dump(state, fh, indent=2)
    fh.close()


def normalize_civ(civ):
    """Normalize civilization name to match directory naming."""
    if not civ:
        return "unknown"
    return civ.lower().replace(" ", "_").replace("'", "")


def normalize_map(map_name):
    """Normalize map name to match directory naming."""
    if not map_name:
        return "unknown"
    return map_name.lower().replace(" ", "_").replace("'", "")


def fetch_games():
    """Fetch recent games from AoE4 World API."""
    try:
        resp = requests.get(API_GAMES_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("games", [])
    except Exception as e:
        log.warning("API fetch failed: %s", e)
        return []


def detect_active_game(games):
    """Check if there is an ongoing or just-finished game."""
    for game in games:
        if game.get("ongoing") or game.get("just_finished"):
            return game
    return None


def extract_game_info(game):
    """Extract player civ, opponent civ, and map from a game object."""
    teams = game.get("teams", [])
    player_info = None
    opponent_info = None
    for team in teams:
        for entry in team:
            member = entry.get("player", entry)
            pid = str(member.get("profile_id", ""))
            if pid == PLAYER_ID:
                player_info = member
            else:
                opponent_info = member
    if not player_info or not opponent_info:
        return None
    return {
        "game_id": str(game.get("game_id", "")),
        "map": game.get("map", ""),
        "my_civ": player_info.get("civilization", ""),
        "opponent_civ": opponent_info.get("civilization", ""),
        "opponent_name": opponent_info.get("name", ""),
        "ongoing": game.get("ongoing", False),
    }


def find_matching_games(my_civ, opponent_civ, map_name):
    """Find past game markdown files matching the exact matchup and map only."""
    my_civ_norm = normalize_civ(my_civ)
    opp_civ_norm = normalize_civ(opponent_civ)
    map_norm = normalize_map(map_name)
    exact_dir = GAME_HISTORY_DIR / my_civ_norm / opp_civ_norm / map_norm
    exact_files = []
    if exact_dir.exists():
        exact_files = sorted(exact_dir.rglob("*.md"), reverse=True)
    return exact_files


def extract_tips_from_file(filepath):
    """Extract filled-in sections from a game markdown file."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception:
        return None
    sections = {}
    for section_name in [
        "What Should Be Done In The Future",
        "What Went Well",
        "What Went Wrong",
        "Opening Strategy",
        "Opponent Strategy",
    ]:
        pattern = r"## " + re.escape(section_name) + r"\s*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            text = match.group(1).strip()
            if text and text not in ["-", "- ",
                "<Short paragraph about your build and plan>",
                "<What they did, what civ strengths they used>"]:
                split_lines = [l.strip().lstrip("- ").strip() for l in text.split(NL)]
                real_lines = [l for l in split_lines if l and not l.startswith("<") and not l.startswith("[HH:")]
                if real_lines:
                    sections[section_name] = text
    if not sections:
        return None
    result_match = re.search(r"\*\*Result:\*\*\s*(\w+)", content)
    date_match = re.search(r"\*\*Date:\*\*\s*([\d-]+)", content)
    map_match = re.search(r"\*\*Map:\*\*\s*(.+)", content)
    # Build page URL from file path
    page_url = ""
    try:
        rel = filepath.relative_to(DOCS_DIR)
        # Drop .md extension for the URL
        url_path = str(rel).replace(".md", "/")
        page_url = SITE_BASE_URL + "/" + url_path.replace(" ", "%20")
    except ValueError:
        pass
    return {
        "file": str(filepath),
        "result": result_match.group(1) if result_match else "unknown",
        "date": date_match.group(1) if date_match else "unknown",
        "map": map_match.group(1).strip() if map_match else "unknown",
        "sections": sections,
        "url": page_url,
    }


def build_raw_context(game_info, tips):
    """Build raw context from game tips for the AI to distill."""
    map_name = game_info["map"]
    lines = []
    lines.append("=== {} vs {} on {} ===".format(game_info["my_civ"], game_info["opponent_civ"], map_name))
    for tip in tips[:5]:
        lines.append("")
        lines.append("--- {} ({}) ---".format(tip["date"], tip["result"]))
        if tip.get("url"):
            lines.append("Page: {}".format(tip["url"]))
        for section_name in ["Opening Strategy", "Opponent Strategy", "What Went Well", "What Went Wrong", "What Should Be Done In The Future"]:
            if section_name in tip["sections"]:
                lines.append("[{}]".format(section_name))
                lines.append(tip["sections"][section_name])
    return NL.join(lines)


def build_fallback_message(game_info, all_tips):
    """Fallback message if AI formatting fails."""
    my_civ = game_info["my_civ"]
    opp_civ = game_info["opponent_civ"]
    map_name = game_info["map"]
    opponent = game_info["opponent_name"]
    lines = []
    lines.append("**:crossed_swords: {} vs {} on {}**".format(my_civ, opp_civ, map_name))
    lines.append("Opponent: {}".format(opponent))
    lines.append("")
    for tip in all_tips[:5]:
        result_emoji = ":white_check_mark:" if tip["result"] == "win" else ":x:"
        lines.append("{} **{} - {}** ({})".format(result_emoji, tip["date"], tip["map"], tip["result"]))
        if "What Should Be Done In The Future" in tip["sections"]:
            lines.append(tip["sections"]["What Should Be Done In The Future"])
        if tip.get("url"):
            lines.append("<{}>".format(tip["url"]))
        lines.append("")
    return NL.join(lines)


def format_with_ai(raw_context, game_info):
    """Use OpenClaw to format tips with AI. Small token usage."""
    my_civ = game_info["my_civ"]
    opp_civ = game_info["opponent_civ"]
    map_name = game_info["map"]
    opponent = game_info["opponent_name"]
    prompt = (
        "You are an AoE4 coach. Your player is about to play {} vs {} on {}. "
        "Below are notes from their past games with this exact matchup and map, listed newest first. "
        "Write a short Discord message (under 400 words) with the most important actionable tips. "
        "IMPORTANT: Newer games have more refined insights. If advice from newer games contradicts older games, "
        "always prioritize the newer advice. Weight recent conclusions much more heavily. "
        "Prioritize: key mistakes to avoid (What Went Wrong), proven winning strategies (What Went Well), "
        "and future advice (What Should Be Done). "
        "Use bullet points. At the end, list links to the most relevant past games. "
        "Wrap URLs in <> to suppress Discord embeds. "
        "Start with a header line: **:crossed_swords: {} vs {} on {}** (vs {})"
    ).format(my_civ, opp_civ, map_name, my_civ, opp_civ, map_name, opponent) + NL + NL + raw_context
    try:
        result = subprocess.run(
            [OPENCLAW_CMD, "run", "--model", "anthropic-vertex/claude-sonnet-4-20250514", "-p", prompt],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        log.warning("AI formatting failed: %s", e)
    return None


def send_to_discord(message, channel_id, retries=5, retry_delay=60):
    """Send a message to Discord via OpenClaw CLI with retries."""
    if not channel_id:
        log.error("No Discord channel ID configured!")
        return False
    for attempt in range(1, retries + 1):
        try:
            result = subprocess.run(
                [OPENCLAW_CMD, "message", "send", "--channel", "discord",
                 "--target", channel_id, "--json", "--message", message],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                log.info("Message sent to Discord successfully")
                return True
            else:
                log.warning("Discord send attempt %d/%d failed: %s", attempt, retries, result.stderr.strip())
        except Exception as e:
            log.warning("Discord send attempt %d/%d error: %s", attempt, retries, e)
        if attempt < retries:
            log.info("Retrying in %ds...", retry_delay)
            time.sleep(retry_delay)
    log.error("Discord send failed after %d attempts", retries)
    return False


def main():
    log.info("AoE4 Game Watcher started")
    log.info("Watching player ID: %s", PLAYER_ID)
    log.info("Poll interval: %ds", POLL_INTERVAL)
    log.info("Game history dir: %s", GAME_HISTORY_DIR)
    if not DISCORD_CHANNEL_ID:
        log.error("DISCORD_CHANNEL_ID not set!")
        sys.exit(1)
    state = load_state()
    log.info("Last notified game: %s", state.get("last_notified_game_id", "none"))
    while True:
        try:
            games = fetch_games()
            if not games:
                time.sleep(POLL_INTERVAL)
                continue
            # Check for ongoing or just-finished game
            active = detect_active_game(games)
            if not active:
                # Check if newest game is one we have not notified about
                newest = games[0]
                newest_id = str(newest.get("game_id", ""))
                if newest_id and newest_id != state.get("last_notified_game_id"):
                    active = newest
            if not active:
                time.sleep(POLL_INTERVAL)
                continue
            game_info = extract_game_info(active)
            if not game_info:
                time.sleep(POLL_INTERVAL)
                continue
            game_id = game_info["game_id"]
            if game_id == state.get("last_notified_game_id"):
                time.sleep(POLL_INTERVAL)
                continue
            log.info("New game detected: %s vs %s on %s",
                     game_info["my_civ"], game_info["opponent_civ"], game_info["map"])
            # Find matching past games (exact matchup + exact map only)
            exact_files = find_matching_games(
                game_info["my_civ"], game_info["opponent_civ"], game_info["map"]
            )
            # Extract tips
            tips = []
            for f in exact_files:
                tip = extract_tips_from_file(f)
                if tip:
                    tips.append(tip)
            log.info("Found %d tips for %s vs %s on %s",
                     len(tips), game_info["my_civ"], game_info["opponent_civ"], game_info["map"])
            # Format with AI (minimal token usage - only on game detection)
            if tips:
                raw_context = build_raw_context(game_info, tips)
                final_msg = format_with_ai(raw_context, game_info)
                if final_msg is None:
                    final_msg = build_fallback_message(game_info, tips)
            else:
                final_msg = "**:crossed_swords: {} vs {} on {}**{}Opponent: {}{}No past games found for this exact matchup + map. New territory - good luck! :muscle:".format(
                    game_info["my_civ"], game_info["opponent_civ"], game_info["map"],
                    NL, game_info["opponent_name"], NL)
            # Send to Discord - only update state if send succeeds
            if send_to_discord(final_msg, DISCORD_CHANNEL_ID):
                state["last_notified_game_id"] = game_id
                save_state(state)
                log.info("State updated. Last notified: %s", game_id)
            else:
                log.error("NOT updating state - tips never delivered for game %s. Will retry next poll.", game_id)
        except Exception as e:
            log.error("Unexpected error: %s", e)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
