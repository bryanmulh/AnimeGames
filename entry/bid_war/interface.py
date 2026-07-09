import json
import mimetypes
import os
import random
import time
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from urllib.parse import parse_qs
from urllib.parse import urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8002"))
STARTING_BUDGET = 100
TEAM_SIZE = 5
DEFAULT_ROUND_SECONDS = 90
CLIENT_TIMEOUT_SECONDS = 20

BASE_DIR = Path(__file__).resolve().parents[1]
ASSET_ROOT = BASE_DIR / "assets"
CARD_ROOT = ASSET_ROOT / "cards"
DECKS = {
    "BC": "Black Clover",
    "DS": "Demon Slayer",
    "JJK": "Jujutsu Kaisen",
    "MHA": "My Hero Academia",
}
DEFAULT_DECK = "DS"
EXCLUDED_BID_WAR_CARD_TERMS = ("gojo", "sukuna")


state_lock = Lock()
clients = {}
game_state = {}
state_version = {"value": 0}


def bump_state_version():
    state_version["value"] += 1


def valid_deck_key(deck_key):
    return deck_key if deck_key in DECKS else DEFAULT_DECK


def deck_dir(deck_key):
    return CARD_ROOT / valid_deck_key(deck_key)


def bid_war_card_allowed(card):
    searchable = f"{card.get('id', '')} {card.get('name', '')} {card.get('image', '')}".lower()
    return not any(term in searchable for term in EXCLUDED_BID_WAR_CARD_TERMS)


def load_cards(deck_key):
    deck_key = valid_deck_key(deck_key)
    card_dir = deck_dir(deck_key)
    manifest_path = card_dir / "_manifest.json"
    cards = []

    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)

        for index, card in enumerate(manifest):
            file_name = Path(card.get("file", "")).name
            if not file_name:
                continue
            image_path = card_dir / file_name
            if not image_path.exists():
                continue
            cards.append(
                {
                    "id": f"{deck_key.lower()}-{index}-{image_path.stem}",
                    "name": card.get("name") or image_path.stem.replace("_", " ").title(),
                    "image": f"/assets/cards/{deck_key}/{file_name}?v={int(image_path.stat().st_mtime)}",
                }
            )

    if cards:
        return [card for card in cards if bid_war_card_allowed(card)]

    return [
        card
        for image_path in sorted(card_dir.glob("*.png"))
        for card in [
            {
                "id": f"{deck_key.lower()}-{image_path.stem}",
                "name": image_path.stem.replace("_", " ").title(),
                "image": f"/assets/cards/{deck_key}/{image_path.name}?v={int(image_path.stat().st_mtime)}",
            }
        ]
        if bid_war_card_allowed(card)
    ]


def normalized_positive_int(value, fallback):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(1, parsed)


def fresh_state(deck_key=DEFAULT_DECK, starting_budget=STARTING_BUDGET, round_seconds=DEFAULT_ROUND_SECONDS):
    deck_key = valid_deck_key(deck_key)
    cards = load_cards(deck_key)
    random.shuffle(cards)
    round_seconds = normalized_positive_int(round_seconds, DEFAULT_ROUND_SECONDS)
    return {
        "phase": "lobby",
        "deck_key": deck_key,
        "starting_budget": normalized_positive_int(starting_budget, STARTING_BUDGET),
        "round_seconds": round_seconds,
        "auction_deadline": None,
        "deck": cards,
        "current_card": None,
        "round": 1,
        "bids": {},
        "revealed_bids": None,
        "last_result": "",
        "rebid_players": [],
        "final_turn": False,
        "eligible_voters": [],
        "team_votes": {},
        "winner_session": None,
    }


def set_auction_deadline():
    game_state["auction_deadline"] = time.time() + game_state["round_seconds"]


def auction_seconds_left():
    deadline = game_state.get("auction_deadline")
    if not deadline or game_state["phase"] not in ("auction", "rebid"):
        return None
    return max(0, int(deadline - time.time()))


def auction_timer_expired():
    seconds_left = auction_seconds_left()
    return seconds_left == 0


def reset_game(deck_key=DEFAULT_DECK, starting_budget=STARTING_BUDGET, round_seconds=DEFAULT_ROUND_SECONDS):
    global game_state
    game_state = fresh_state(deck_key, starting_budget, round_seconds)
    for client in clients.values():
        if client["role"] == "player":
            client["budget"] = game_state["starting_budget"]
            client["team"] = []
            client["ready"] = False


def start_game():
    if game_state["phase"] != "lobby" or not all_players_ready():
        return
    if not game_state["deck"]:
        game_state["last_result"] = "No cards found for this deck."
        return

    game_state["current_card"] = game_state["deck"].pop()
    game_state["round"] = 1
    game_state["phase"] = "auction"
    game_state["bids"] = {}
    game_state["revealed_bids"] = None
    game_state["last_result"] = ""
    game_state["rebid_players"] = []
    game_state["final_turn"] = len(eligible_bidders()) == 1
    if game_state["final_turn"]:
        game_state["last_result"] = "Final turn - finish the remaining team. The remaining player must buy each card."
    set_auction_deadline()


def configure_game(deck_key, starting_budget, round_seconds):
    current_deck = game_state["deck_key"]
    current_budget = game_state["starting_budget"]
    current_round_seconds = game_state["round_seconds"]
    ready_by_session = {
        client["session_id"]: client.get("ready", False)
        for client in players()
    }
    reset_game(
        deck_key or current_deck,
        starting_budget or current_budget,
        round_seconds or current_round_seconds,
    )
    for client in players():
        client["ready"] = ready_by_session.get(client["session_id"], False)


def next_card_or_voting():
    eligible = eligible_bidders()
    if not eligible or all(player_team_full(player) for player in eligible) or not game_state["deck"]:
        start_team_vote()
        return

    game_state["current_card"] = game_state["deck"].pop()
    game_state["round"] += 1
    game_state["phase"] = "auction"
    game_state["bids"] = {}
    game_state["revealed_bids"] = None
    game_state["final_turn"] = len(eligible) == 1
    game_state["last_result"] = "Final turn - finish the remaining team. The remaining player must buy each card." if game_state["final_turn"] else ""
    game_state["rebid_players"] = []
    set_auction_deadline()


def start_team_vote():
    game_state["phase"] = "voting"
    game_state["current_card"] = None
    game_state["auction_deadline"] = None
    game_state["bids"] = {}
    game_state["revealed_bids"] = None
    game_state["rebid_players"] = []
    game_state["final_turn"] = False
    game_state["eligible_voters"] = [client["session_id"] for client in joined_clients()]
    game_state["team_votes"] = {}
    game_state["winner_session"] = None
    game_state["last_result"] = "Auction complete. Vote for the best team."


def player_team_full(player):
    return len(player.get("team", [])) >= TEAM_SIZE


def remaining_slots_after_current_bid(player):
    return max(0, TEAM_SIZE - len(player.get("team", [])) - 1)


def max_bid_for_player(player):
    return max(0, player["budget"] - remaining_slots_after_current_bid(player))


def players():
    return [
        client
        for client in clients.values()
        if client.get("role") == "player"
    ]


def all_players_ready():
    current_players = players()
    return bool(current_players) and all(player.get("ready") for player in current_players)


def joined_clients():
    return [
        client
        for client in clients.values()
        if client.get("role") in ("player", "spectator")
    ]


def eligible_bidders():
    if game_state["phase"] == "rebid":
        allowed = set(game_state["rebid_players"])
        return [
            client
            for client in players()
            if client["session_id"] in allowed and not player_team_full(client)
        ]
    return [
        client
        for client in players()
        if not player_team_full(client)
    ]


def next_player_number():
    used = {client.get("player_number") for client in players()}
    number = 1
    while number in used:
        number += 1
    return number


def cleanup_stale_clients():
    now = time.time()
    stale_sessions = [
        session_id
        for session_id, client in clients.items()
        if now - client.get("last_seen", now) > CLIENT_TIMEOUT_SECONDS
    ]
    if not stale_sessions:
        return

    for session_id in stale_sessions:
        clients.pop(session_id, None)
        game_state["bids"].pop(session_id, None)
        game_state["team_votes"].pop(session_id, None)
        if session_id in game_state["eligible_voters"]:
            game_state["eligible_voters"].remove(session_id)
        if session_id in game_state["rebid_players"]:
            game_state["rebid_players"].remove(session_id)

    if not clients:
        reset_game(DEFAULT_DECK, STARTING_BUDGET, DEFAULT_ROUND_SECONDS)
    bump_state_version()


def get_or_create_session(handler):
    jar = cookies.SimpleCookie(handler.headers.get("Cookie", ""))
    session_id = jar["bid_war_session"].value if "bid_war_session" in jar else None
    created = False
    if not session_id:
        session_id = token_urlsafe(18)
        created = True

    with state_lock:
        cleanup_stale_clients()
        client = clients.setdefault(
            session_id,
            {
                "session_id": session_id,
                "name": "",
                "role": "",
                "player_number": None,
                "budget": game_state.get("starting_budget", STARTING_BUDGET),
                "team": [],
                "ready": False,
                "last_seen": time.time(),
            },
        )
        client["last_seen"] = time.time()
    return session_id, created


reset_game()


STYLE = """
    :root {
      color-scheme: dark;
      --bg: #090b10;
      --panel: #10141d;
      --panel-strong: #151b27;
      --border: #263244;
      --border-soft: #1c2533;
      --text: #f3f7fb;
      --muted: #8d99aa;
      --accent: #5eead4;
      --accent-two: #93c5fd;
      --danger: #fb7185;
      --shadow: rgba(0, 0, 0, 0.42);
    }

    * {
      box-sizing: border-box;
    }

    body {
      min-height: 100vh;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button,
    input,
    select {
      font: inherit;
    }

    button {
      cursor: pointer;
    }

    .launcher-link {
      position: fixed;
      top: 10px;
      right: 10px;
      z-index: 2000;
      padding: 7px 10px;
      border: 1px solid var(--border);
      background: rgba(16, 20, 29, 0.92);
      color: var(--accent);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-decoration: none;
      text-transform: uppercase;
      box-shadow: 0 10px 28px var(--shadow);
    }

    .launcher-link:hover,
    .launcher-link:focus-visible {
      border-color: var(--accent);
      outline: none;
    }

    .page {
      width: min(1360px, 100%);
      margin: 0 auto;
      padding: 22px;
      display: grid;
      gap: 18px;
    }

    header,
    .panel {
      border: 1px solid var(--border);
      background: var(--panel);
      box-shadow: 0 16px 48px var(--shadow);
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px;
    }

    h1,
    h2,
    h3,
    p {
      margin: 0;
    }

    h1 {
      font-size: clamp(28px, 4vw, 48px);
      line-height: 1;
    }

    h2 {
      margin-bottom: 12px;
      font-size: 15px;
      letter-spacing: 0.07em;
      text-transform: uppercase;
    }

    h3 {
      font-size: 16px;
    }

    .subtitle,
    .muted {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }

    .chip {
      border: 1px solid var(--border);
      padding: 8px 11px;
      color: var(--accent);
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .join {
      width: min(620px, 100%);
      margin: 40px auto;
      padding: 18px;
      display: grid;
      gap: 16px;
    }

    .field {
      display: grid;
      gap: 6px;
    }

    label,
    .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    input,
    select {
      width: 100%;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      padding: 10px 12px;
    }

    .role-grid,
    .controls,
    .actions,
    .vote-options {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .role-card {
      display: block;
      min-width: 150px;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      padding: 12px;
      cursor: pointer;
    }

    .role-card input {
      width: auto;
      margin-right: 8px;
    }

    .primary,
    .secondary,
    .danger {
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      padding: 10px 13px;
      font-weight: 800;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
    }

    .primary {
      border-color: rgba(94, 234, 212, 0.6);
      color: var(--accent);
    }

    .danger {
      border-color: rgba(251, 113, 133, 0.55);
      color: var(--danger);
    }

    .primary:hover,
    .secondary:hover,
    .danger:hover,
    .role-card:hover {
      border-color: var(--accent);
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(260px, 340px) 1fr minmax(280px, 360px);
      gap: 16px;
      align-items: start;
    }

    .panel {
      padding: 16px;
      min-width: 0;
    }

    .status {
      color: var(--accent);
      font-size: 18px;
      font-weight: 900;
      line-height: 1.3;
    }

    .current-card {
      display: grid;
      justify-items: center;
      gap: 10px;
    }

    .card-frame {
      width: min(245px, 100%);
      aspect-ratio: 5 / 7;
      border: 1px solid var(--border);
      background: #070910;
      overflow: hidden;
    }

    .card-frame img,
    .team-card img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .card-name {
      text-align: center;
      font-weight: 900;
    }

    .bid-form {
      margin-top: 14px;
      display: grid;
      gap: 10px;
    }

    .bid-status {
      margin-top: 12px;
      color: var(--accent-two);
      font-weight: 800;
    }

    .players {
      display: grid;
      gap: 12px;
    }

    .player {
      border: 1px solid var(--border-soft);
      background: rgba(255, 255, 255, 0.025);
      padding: 12px;
      display: grid;
      gap: 8px;
    }

    .player-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-weight: 900;
    }

    .money {
      color: var(--accent);
    }

    .team {
      display: grid;
      grid-template-columns: repeat(5, minmax(42px, 1fr));
      gap: 6px;
    }

    .team-card,
    .empty-card {
      aspect-ratio: 5 / 7;
      border: 1px solid var(--border);
      background: #070910;
      overflow: hidden;
    }

    .empty-card {
      display: grid;
      place-items: center;
      color: var(--muted);
      border-style: dashed;
      font-size: 12px;
    }

    .bids {
      display: grid;
      gap: 8px;
    }

    .bid-row,
    .vote-row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--border-soft);
      padding-bottom: 7px;
    }

    .winner {
      color: var(--accent);
      font-weight: 900;
    }

    .timer {
      color: var(--accent-two);
      font-size: 20px;
      font-weight: 900;
    }

    .team-vote-list {
      display: grid;
      gap: 14px;
    }

    .final-teams {
      display: grid;
      gap: 16px;
      margin-top: 16px;
    }

    .final-team {
      border: 1px solid var(--border-soft);
      background: rgba(255, 255, 255, 0.025);
      padding: 14px;
      display: grid;
      gap: 10px;
    }

    .final-team-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-weight: 900;
    }

    .final-team-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(78px, 1fr));
      gap: 10px;
    }

    .vote-team-showcase .final-team-grid {
      grid-template-columns: repeat(5, minmax(96px, 1fr));
      gap: 12px;
    }

    .final-team-grid .team-card,
    .final-team-grid .empty-card {
      min-width: 0;
    }

    .client-status {
      color: var(--accent-two);
      font-size: 12px;
      font-weight: 900;
      text-align: right;
      text-transform: uppercase;
    }

    @media (max-width: 1040px) {
      .layout {
        grid-template-columns: 1fr;
      }

      .card-frame {
        width: min(220px, 70vw);
      }

      .final-team-grid {
        grid-template-columns: repeat(5, minmax(56px, 1fr));
      }
    }

    @media (max-width: 640px) {
      .page {
        padding: 14px;
      }

      header {
        align-items: flex-start;
        flex-direction: column;
      }
    }
"""


def page_shell(body, session_id=None, created=False):
    cookie_header = ""
    if created and session_id:
        cookie = cookies.SimpleCookie()
        cookie["bid_war_session"] = session_id
        cookie["bid_war_session"]["path"] = "/"
        cookie_header = cookie.output(header="").strip()

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bid War</title>
  <style>{STYLE}</style>
</head>
<body>
<a class="launcher-link" href="http://localhost:8003/" onclick="this.href = `${{window.location.protocol}}//${{window.location.hostname}}:8003/`">Choose Game</a>
{body}
<script>
  const currentStateVersion = {state_version["value"]};
  setInterval(async () => {{
    try {{
      const response = await fetch("/state", {{ cache: "no-store" }});
      if (!response.ok) return;
      const data = await response.json();
      document.querySelectorAll("[data-countdown]").forEach((element) => {{
        if (data.seconds_left === null || data.seconds_left === undefined) return;
        element.textContent = `${{data.seconds_left}}s left`;
      }});
      if (data.version !== currentStateVersion) {{
        window.location.reload();
      }}
    }} catch (error) {{}}
  }}, 1000);
</script>
</body>
</html>""".encode("utf-8")
    return html, cookie_header


def role_label(client):
    if client.get("role") == "player":
        return f"Player {client['player_number']}"
    if client.get("role") == "spectator":
        return "Spectator"
    return "Not joined"


def deck_options():
    current = game_state["deck_key"]
    return "".join(
        f'<option value="{escape(key)}"{" selected" if key == current else ""}>{escape(key)} - {escape(name)}</option>'
        for key, name in DECKS.items()
    )


def render_join(session_id, error=""):
    client = clients.get(session_id, {})
    name = escape(client.get("name", ""))
    error_html = f'<p class="muted">{escape(error)}</p>' if error else ""
    body = f"""
<main class="page">
  <header>
    <div>
      <h1>Bid War</h1>
      <p class="subtitle">Join as a player to bid or as a spectator to vote at the end.</p>
    </div>
    <span class="chip">Port 8002</span>
  </header>
  <form class="panel join" method="post" action="/join">
    <h2>Join Game</h2>
    {error_html}
    <div class="field">
      <label for="name">Display Name</label>
      <input id="name" name="name" value="{name}" maxlength="28" autocomplete="name" required>
    </div>
    <div class="field">
      <span class="label">Role</span>
      <div class="role-grid">
        <label class="role-card"><input type="radio" name="role" value="player" required>Player</label>
        <label class="role-card"><input type="radio" name="role" value="spectator" required>Spectator</label>
      </div>
    </div>
    <button class="primary" type="submit">Join</button>
  </form>
</main>"""
    return body


def render_card(card):
    if not card:
        return '<div class="empty-card">No card</div>'
    return f"""<div class="card-frame">
  <img src="{escape(card['image'])}" alt="{escape(card['name'])}">
</div>
<div class="card-name">{escape(card['name'])}</div>"""


def render_team_cards(team):
    cards = []
    for card in team[:TEAM_SIZE]:
        cards.append(f'<div class="team-card"><img src="{escape(card["image"])}" alt="{escape(card["name"])}"></div>')
    cards.extend('<div class="empty-card">Open</div>' for _ in range(max(0, TEAM_SIZE - len(cards))))
    return "".join(cards)


def render_players():
    if not players():
        return '<p class="muted">No players have joined yet.</p>'

    rows = []
    for player in sorted(players(), key=lambda item: item["player_number"]):
        full = " Full" if player_team_full(player) else ""
        ready = " Ready" if player.get("ready") else ""
        rows.append(f"""
        <div class="player">
          <div class="player-head">
            <span>Player {player['player_number']}: {escape(player['name'])}</span>
            <span class="money">${player['budget']}</span>
          </div>
          <div class="muted">{len(player['team'])} / {TEAM_SIZE} team slots{full}{ready}</div>
          <div class="team">{render_team_cards(player['team'])}</div>
        </div>""")
    return "".join(rows)


def render_lobby(session_id):
    client = clients[session_id]
    ready_disabled = "" if client.get("role") == "player" else " disabled"
    ready_text = "Unready" if client.get("ready") else "Ready"
    start_disabled = "" if all_players_ready() else " disabled"
    player_count = len(players())
    ready_count = len([player for player in players() if player.get("ready")])

    return f"""
    <section class="panel">
      <h2>Lobby</h2>
      <p class="status">Waiting for players to ready up.</p>
      <p class="muted">{ready_count} of {player_count} players ready. Start unlocks once every joined player is ready.</p>
      <div class="actions">
        <form method="post" action="/ready">
          <button class="primary" type="submit"{ready_disabled}>{ready_text}</button>
        </form>
        <form class="controls" method="post" action="/start">
          <div class="field">
            <label for="deck">Deck</label>
            <select id="deck" name="deck">{deck_options()}</select>
          </div>
          <div class="field">
            <label for="budget">Starting Budget</label>
            <input id="budget" name="budget" type="number" min="1" value="{game_state['starting_budget']}">
          </div>
          <div class="field">
            <label for="round_seconds">Round Timer Seconds</label>
            <input id="round_seconds" name="round_seconds" type="number" min="1" value="{game_state['round_seconds']}">
          </div>
          <button class="secondary" type="submit"{start_disabled}>Start Game</button>
        </form>
      </div>
    </section>"""


def render_final_teams():
    if not players():
        return '<p class="muted">No teams to show.</p>'

    rows = []
    for player in sorted(players(), key=lambda item: item["player_number"]):
        rows.append(f"""
        <div class="final-team">
          <div class="final-team-head">
            <span>Player {player['player_number']}: {escape(player['name'])}</span>
            <span class="money">${player['budget']}</span>
          </div>
          <div class="final-team-grid">{render_team_cards(player['team'])}</div>
        </div>""")
    teams_html = "".join(rows)
    return f'<div class="final-teams vote-team-showcase">{teams_html}</div>'


def current_client_bid(session_id):
    if session_id in game_state["bids"]:
        return game_state["bids"][session_id]
    return None


def render_auction_controls(session_id, client):
    if client.get("role") != "player":
        return '<p class="muted">Spectators can watch the auction and vote at the end.</p>'
    if player_team_full(client):
        return '<p class="bid-status">Your team is full. You cannot bid.</p>'

    eligible = eligible_bidders()
    if client not in eligible:
        return '<p class="bid-status">Waiting for tied players to rebid.</p>'

    own_bid = current_client_bid(session_id)
    if own_bid is not None:
        return f'<p class="bid-status">Hidden bid submitted: ${own_bid}</p>'

    max_bid = max_bid_for_player(client)
    min_bid = 1 if game_state.get("final_turn") else 0
    if max_bid < min_bid:
        return '<p class="bid-status">You do not have enough remaining budget to bid this round.</p>'

    label = "Rebid" if game_state["phase"] == "rebid" else "Hidden Bid"
    reserve = remaining_slots_after_current_bid(client)
    if game_state.get("final_turn"):
        rule_text = f"Final turn: you must bid at least $1. Max bid is ${max_bid} so you keep ${reserve} for remaining slots."
    else:
        rule_text = f"You can bid up to ${max_bid}. This keeps ${reserve} reserved for remaining slots."
    return f"""
    <form class="bid-form" method="post" action="/bid">
      <div class="field">
        <label for="amount">{label}</label>
        <input id="amount" name="amount" type="number" min="{min_bid}" max="{max_bid}" value="{min_bid}" required>
      </div>
      <button class="primary" type="submit">Submit Bid</button>
      <p class="muted">{escape(rule_text)}</p>
    </form>"""


def render_bid_status():
    eligible = eligible_bidders()
    submitted = len([player for player in eligible if player["session_id"] in game_state["bids"]])
    if not eligible:
        return '<p class="muted">No eligible bidders.</p>'
    seconds_left = auction_seconds_left()
    timer_html = f'<p class="timer" data-countdown>{seconds_left}s left</p>' if seconds_left is not None else ""
    return f'{timer_html}<p class="muted">{submitted} of {len(eligible)} eligible players have submitted bids.</p>'


def waiting_bidder_names():
    waiting = [
        player["name"]
        for player in eligible_bidders()
        if player["session_id"] not in game_state["bids"]
    ]
    return ", ".join(waiting)


def top_status_text():
    phase = game_state["phase"]
    if phase == "lobby":
        return "Lobby open. Players choose Ready when set."
    if phase == "rebid":
        waiting = waiting_bidder_names()
        tie_text = game_state["last_result"] or "Tie bid. Rebidding now."
        return f"{tie_text} Waiting for: {waiting}." if waiting else f"{tie_text} Revealing rebid."
    if phase == "revealed":
        return game_state["last_result"] or "Bids revealed."
    if phase == "voting":
        return "Auction complete. Vote for best team."
    if phase == "complete":
        return "Game complete."
    if game_state.get("final_turn"):
        waiting = waiting_bidder_names()
        return f"Final turn. Waiting for: {waiting}." if waiting else "Final turn bid submitted."

    waiting = waiting_bidder_names()
    return f"Round {game_state['round']}: waiting for bids from {waiting}." if waiting else "All bids submitted. Revealing."


def bids_ready_to_reveal():
    eligible = eligible_bidders()
    if game_state.get("final_turn"):
        return bool(eligible) and all(player["session_id"] in game_state["bids"] for player in eligible)
    return bool(eligible) and (
        all(player["session_id"] in game_state["bids"] for player in eligible)
        or auction_timer_expired()
    )


def auto_reveal_if_ready():
    if game_state["phase"] in ("auction", "rebid") and bids_ready_to_reveal():
        reveal_bids()
        bump_state_version()
        return True
    return False


def render_revealed_bids():
    if not game_state["revealed_bids"]:
        return ""

    rows = []
    for bid in game_state["revealed_bids"]:
        rows.append(f"""
        <div class="bid-row">
          <span>{escape(bid['name'])}</span>
          <strong>${bid['amount']}</strong>
        </div>""")
    return f"""
    <div class="bids">
      <h2>Revealed Bids</h2>
      {''.join(rows)}
    </div>"""


def render_actions(client):
    if game_state["phase"] == "lobby":
        return ""

    if game_state["phase"] == "revealed":
        if game_state.get("final_turn") and all(player_team_full(player) for player in eligible_bidders()):
            button_text = "Go to Best Team Vote"
        elif game_state.get("final_turn"):
            button_text = "Continue Final Turn"
        else:
            button_text = "Next Auction"
        return f"""
        <form method="post" action="/next">
          <button class="primary" type="submit">{button_text}</button>
        </form>"""

    return ""


def render_vote_panel(session_id):
    already_voted = session_id in game_state["team_votes"]
    vote_count = len(game_state["team_votes"])
    total = len(game_state["eligible_voters"])

    if game_state["phase"] == "complete":
        winner = clients.get(game_state["winner_session"], {})
        winner_text = "Tie"
        if winner:
            winner_text = f"Best team: Player {winner['player_number']} {winner['name']}"
        return f"""
        <section class="panel">
          <h2>Voting Complete</h2>
          <p class="status">{escape(winner_text)}</p>
          {render_final_teams()}
        </section>"""

    if already_voted:
        return f"""
        <section class="panel">
          <h2>Best Team Vote</h2>
          <p class="status">Vote submitted.</p>
          <p class="muted">Waiting on {vote_count} of {total} voters.</p>
          {render_final_teams()}
        </section>"""

    options = []
    for player in sorted(players(), key=lambda item: item["player_number"]):
        options.append(f"""
        <form method="post" action="/team-vote">
          <input type="hidden" name="winner" value="{escape(player['session_id'])}">
          <button class="secondary" type="submit">Vote Player {player['player_number']}: {escape(player['name'])}</button>
        </form>""")

    return f"""
    <section class="panel">
      <h2>Best Team Vote</h2>
      <p class="muted">Each player or spectator gets one vote. {vote_count} of {total} submitted.</p>
      {render_final_teams()}
      <div class="vote-options">{''.join(options)}</div>
    </section>"""


def render_controls():
    return """
    <form method="post" action="/reset">
      <button class="danger" type="submit">New Game</button>
    </form>"""


def render_game(session_id):
    client = clients[session_id]
    phase = game_state["phase"]
    status = top_status_text()
    if phase == "lobby":
        detail_text = "Set deck, budget, and timer here before starting."
    elif phase in ("auction", "rebid"):
        detail_text = "Highest valid bid wins the current character. If every bid is $0, no one buys it."
    else:
        detail_text = ""

    center = ""
    seconds_left = auction_seconds_left()
    timer_html = f'<p class="timer" data-countdown>{seconds_left}s left</p>' if seconds_left is not None else ""
    if phase == "lobby":
        center = render_lobby(session_id)
    elif phase in ("auction", "rebid", "revealed"):
        center = f"""
        <section class="panel">
          <h2>Current Character</h2>
          {timer_html}
          <div class="current-card">{render_card(game_state['current_card'])}</div>
          {render_auction_controls(session_id, client) if phase in ('auction', 'rebid') else ''}
        </section>"""
    else:
        center = render_vote_panel(session_id)

    body = f"""
<main class="page">
  <header>
    <div>
      <h1>Bid War</h1>
      <p class="subtitle">Deck: {escape(DECKS[game_state['deck_key']])}</p>
    </div>
    <span class="chip">{escape(role_label(client))}: {escape(client.get('name') or 'Guest')}</span>
  </header>

  <section class="panel">
    <div class="status">{escape(status)}</div>
    {f'<p class="muted">{escape(detail_text)}</p>' if detail_text else ''}
    <div class="actions">
      {render_actions(client)}
      {render_controls()}
      <form method="post" action="/leave"><button class="secondary" type="submit">Change Role</button></form>
    </div>
  </section>

  <div class="layout">
    <section class="panel">
      <h2>Players</h2>
      <div class="players">{render_players()}</div>
    </section>
    {center}
    <section class="panel">
      <h2>Auction Status</h2>
      {render_bid_status() if phase in ('auction', 'rebid') else ''}
      {render_revealed_bids()}
      <h2 style="margin-top: 16px;">Clients</h2>
      {render_clients()}
    </section>
  </div>
</main>"""
    return body


def render_clients():
    rows = []
    for client in joined_clients():
        rows.append(f"""<div class="vote-row">
          <span>{escape(client["name"])}</span>
          <span>{escape(role_label(client))} <span class="client-status">{escape(client_status(client))}</span></span>
        </div>""")
    return "".join(rows) or '<p class="muted">No joined clients.</p>'


def client_status(client):
    phase = game_state["phase"]
    session_id = client["session_id"]
    role = client.get("role")

    if phase == "lobby":
        if role == "player":
            return "Ready" if client.get("ready") else "Not ready"
        return "Watching lobby"

    if phase in ("auction", "rebid"):
        if role != "player":
            return "Watching"
        if player_team_full(client):
            return "Team full"
        if client not in eligible_bidders():
            return "Waiting"
        return "Bid set" if session_id in game_state["bids"] else "Bidding"

    if phase == "revealed":
        return "Reviewing"

    if phase == "voting":
        if session_id not in game_state["eligible_voters"]:
            return "Not voting"
        return "Voted" if session_id in game_state["team_votes"] else "Voting"

    if phase == "complete":
        return "Done"

    return ""


def reveal_bids():
    eligible = eligible_bidders()
    revealed = []
    for player in eligible:
        amount = game_state["bids"].get(player["session_id"], 0)
        amount = min(amount, max_bid_for_player(player))
        revealed.append(
            {
                "session_id": player["session_id"],
                "name": f"Player {player['player_number']} {player['name']}",
                "amount": amount,
            }
        )

    game_state["revealed_bids"] = sorted(revealed, key=lambda bid: (-bid["amount"], bid["name"]))
    if not revealed:
        game_state["last_result"] = "No valid bids submitted."
        game_state["phase"] = "revealed"
        return

    high = max(bid["amount"] for bid in revealed)
    if high <= 0:
        game_state["phase"] = "revealed"
        game_state["last_result"] = f"No one bought {game_state['current_card']['name']}. All bids were $0."
        game_state["bids"] = {}
        return

    tied = [bid for bid in revealed if bid["amount"] == high]
    if len(tied) > 1:
        game_state["phase"] = "rebid"
        game_state["rebid_players"] = [bid["session_id"] for bid in tied]
        game_state["bids"] = {}
        set_auction_deadline()
        names = ", ".join(bid["name"] for bid in tied)
        game_state["last_result"] = f"Tie at ${high}: {names}. Rebid required."
        return

    winner_bid = tied[0]
    winner = clients[winner_bid["session_id"]]
    winner["budget"] -= winner_bid["amount"]
    winner["team"].append(game_state["current_card"])
    game_state["phase"] = "revealed"
    game_state["last_result"] = f"{winner_bid['name']} wins {game_state['current_card']['name']} for ${winner_bid['amount']}."
    game_state["bids"] = {}


class BidWarHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            self.serve_asset(parsed.path)
            return
        if parsed.path == "/state":
            get_or_create_session(self)
            with state_lock:
                auto_reveal_if_ready()
                seconds_left = auction_seconds_left()
                version = state_version["value"]
            self.respond_json({"version": version, "seconds_left": seconds_left})
            return
        if parsed.path not in ("/", "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()
            return

        session_id, created = get_or_create_session(self)
        with state_lock:
            body = render_game(session_id) if clients[session_id].get("role") else render_join(session_id)
        self.respond_html(body, session_id, created)

    def do_POST(self):
        session_id, created = get_or_create_session(self)
        parsed = urlparse(self.path)

        if parsed.path == "/join":
            self.handle_join(session_id)
        elif parsed.path == "/ready":
            self.handle_ready(session_id)
        elif parsed.path == "/start":
            self.handle_start()
        elif parsed.path == "/bid":
            self.handle_bid(session_id)
        elif parsed.path == "/reveal":
            self.handle_reveal()
        elif parsed.path == "/next":
            self.handle_next()
        elif parsed.path == "/team-vote":
            self.handle_team_vote(session_id)
        elif parsed.path == "/reset":
            self.handle_reset()
        elif parsed.path == "/leave":
            self.handle_leave(session_id)
        else:
            self.send_error(404)
            return

        self.redirect("/", session_id, created)

    def read_form(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        return parse_qs(self.rfile.read(content_length).decode("utf-8"))

    def handle_join(self, session_id):
        form = self.read_form()
        name = form.get("name", [""])[0].strip()[:28]
        role = form.get("role", [""])[0]
        if not name or role not in ("player", "spectator"):
            return

        with state_lock:
            client = clients[session_id]
            client["name"] = name
            client["role"] = role
            client["ready"] = False
            if role == "player" and not client.get("player_number"):
                client["player_number"] = next_player_number()
                client["budget"] = game_state["starting_budget"]
                client["team"] = []
            elif role == "spectator":
                client["player_number"] = None
                client["team"] = []
            bump_state_version()

    def handle_ready(self, session_id):
        with state_lock:
            client = clients.get(session_id)
            if not client or client.get("role") != "player" or game_state["phase"] != "lobby":
                return
            client["ready"] = not client.get("ready")
            bump_state_version()

    def handle_start(self):
        form = self.read_form()
        deck_key = form.get("deck", [game_state["deck_key"]])[0]
        budget = form.get("budget", [game_state["starting_budget"]])[0]
        round_seconds = form.get("round_seconds", [game_state["round_seconds"]])[0]
        with state_lock:
            if game_state["phase"] == "lobby":
                configure_game(deck_key, budget, round_seconds)
            start_game()
            bump_state_version()

    def handle_bid(self, session_id):
        form = self.read_form()
        try:
            amount = int(form.get("amount", [""])[0])
        except ValueError:
            return

        with state_lock:
            client = clients.get(session_id)
            if not client or client.get("role") != "player" or game_state["phase"] not in ("auction", "rebid"):
                return
            if client not in eligible_bidders() or player_team_full(client):
                return
            if game_state.get("final_turn") and amount < 1:
                return
            if amount < 0 or amount > max_bid_for_player(client):
                return
            game_state["bids"][session_id] = amount
            auto_reveal_if_ready()

    def handle_reveal(self):
        with state_lock:
            if game_state["phase"] in ("auction", "rebid") and bids_ready_to_reveal():
                reveal_bids()
                bump_state_version()

    def handle_next(self):
        with state_lock:
            if game_state["phase"] == "revealed":
                next_card_or_voting()
                bump_state_version()

    def handle_team_vote(self, session_id):
        form = self.read_form()
        winner = form.get("winner", [""])[0]
        with state_lock:
            if game_state["phase"] != "voting" or session_id not in game_state["eligible_voters"]:
                return
            if winner not in [player["session_id"] for player in players()]:
                return
            game_state["team_votes"][session_id] = winner
            if len(game_state["team_votes"]) >= len(game_state["eligible_voters"]):
                counts = {}
                for vote in game_state["team_votes"].values():
                    counts[vote] = counts.get(vote, 0) + 1
                high = max(counts.values()) if counts else 0
                winners = [player_id for player_id, count in counts.items() if count == high]
                game_state["winner_session"] = winners[0] if len(winners) == 1 else None
                game_state["phase"] = "complete"
            bump_state_version()

    def handle_reset(self):
        with state_lock:
            reset_game(game_state["deck_key"], game_state["starting_budget"], game_state["round_seconds"])
            bump_state_version()

    def handle_leave(self, session_id):
        with state_lock:
            client = clients.get(session_id)
            if client:
                client["role"] = ""
                client["name"] = ""
                client["player_number"] = None
                client["team"] = []
            game_state["bids"].pop(session_id, None)
            game_state["team_votes"].pop(session_id, None)
            if session_id in game_state["eligible_voters"]:
                game_state["eligible_voters"].remove(session_id)
            bump_state_version()

    def redirect(self, location, session_id, created=False):
        self.send_response(303)
        if created:
            cookie = cookies.SimpleCookie()
            cookie["bid_war_session"] = session_id
            cookie["bid_war_session"]["path"] = "/"
            self.send_header("Set-Cookie", cookie.output(header="").strip())
        self.send_header("Location", location)
        self.end_headers()

    def respond_html(self, body, session_id, created=False):
        html, cookie_header = page_shell(body, session_id, created)
        self.send_response(200)
        if cookie_header:
            self.send_header("Set-Cookie", cookie_header)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def respond_json(self, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_asset(self, path):
        relative_parts = [part for part in path[len("/assets/") :].split("/") if part]
        asset_path = ASSET_ROOT.joinpath(*relative_parts).resolve()
        try:
            asset_path.relative_to(ASSET_ROOT.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not asset_path.is_file():
            self.send_error(404)
            return

        body = asset_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"{self.client_address[0]} - {format % args}")


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), BidWarHandler)
    print(f"Bid War running at http://127.0.0.1:{PORT}")
    print(f"Network access: http://<your-computer-ip>:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
