import json
import mimetypes
import os
import random
import sys
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
PORT = int(os.environ.get("PORT", "8001"))
BASE_DIR = Path(__file__).resolve().parents[1]
ASSET_ROOT = BASE_DIR / "assets"
CARD_ROOT = ASSET_ROOT / "cards"
sys.path.insert(0, str(BASE_DIR))
from deck_config import DECKS
from deck_config import load_cards as load_deck_config_cards

DEFAULT_DECK = "DS"
SLOT_COUNT = 5
CLIENT_TIMEOUT_SECONDS = 20


state_lock = Lock()
clients = {}
state_version = {"value": 0}
game_state = {}


def bump_state_version():
    state_version["value"] += 1


def valid_deck_key(deck_key):
    return deck_key if deck_key in DECKS else DEFAULT_DECK


def deck_dir(deck_key):
    return CARD_ROOT / valid_deck_key(deck_key)


def load_cards(deck_key):
    deck_key = valid_deck_key(deck_key)
    return load_deck_config_cards(deck_key)


def fresh_state(deck_key=DEFAULT_DECK):
    cards = load_cards(valid_deck_key(deck_key))
    random.shuffle(cards)
    return {
        "phase": "lobby",
        "deck_key": valid_deck_key(deck_key),
        "deck": cards,
        "current_card": None,
        "round": 1,
        "placements": {},
        "eligible_voters": [],
        "votes": {},
        "winner_session": None,
    }


def reset_game(deck_key=None):
    global game_state
    deck_key = valid_deck_key(deck_key or game_state.get("deck_key", DEFAULT_DECK))
    game_state = fresh_state(deck_key)
    for client in clients.values():
        client["ready"] = False
        if client.get("role") == "player":
            client["board"] = [None] * SLOT_COUNT


def start_game(deck_key):
    if game_state["phase"] != "lobby" or not all_players_ready():
        return

    game_state["deck_key"] = valid_deck_key(deck_key)
    cards = load_cards(game_state["deck_key"])
    random.shuffle(cards)
    game_state["deck"] = cards
    game_state["current_card"] = game_state["deck"].pop() if game_state["deck"] else None
    game_state["round"] = 1
    game_state["phase"] = "ranking" if game_state["current_card"] else "voting"
    game_state["placements"] = {}
    game_state["votes"] = {}
    game_state["winner_session"] = None
    for player in players():
        player["board"] = [None] * SLOT_COUNT


def all_players_ready():
    current_players = players()
    return bool(current_players) and all(player.get("ready") for player in current_players)


def players():
    return [client for client in clients.values() if client.get("role") == "player"]


def joined_clients():
    return [client for client in clients.values() if client.get("role") in ("player", "spectator")]


def next_player_number():
    used = {client.get("player_number") for client in players()}
    number = 1
    while number in used:
        number += 1
    return number


def start_voting():
    game_state["phase"] = "voting"
    game_state["current_card"] = None
    game_state["placements"] = {}
    game_state["eligible_voters"] = [client["session_id"] for client in joined_clients()]
    game_state["votes"] = {}
    game_state["winner_session"] = None


def advance_round_if_ready():
    if game_state["phase"] != "ranking":
        return
    player_ids = [player["session_id"] for player in players()]
    if not player_ids or any(player_id not in game_state["placements"] for player_id in player_ids):
        return

    if game_state["round"] >= SLOT_COUNT:
        start_voting()
        return

    game_state["round"] += 1
    game_state["placements"] = {}
    game_state["current_card"] = game_state["deck"].pop() if game_state["deck"] else None
    if not game_state["current_card"]:
        start_voting()


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
        game_state["placements"].pop(session_id, None)
        game_state["votes"].pop(session_id, None)
        if session_id in game_state["eligible_voters"]:
            game_state["eligible_voters"].remove(session_id)

    if not clients:
        reset_game(DEFAULT_DECK)
    bump_state_version()


def get_or_create_session(handler):
    jar = cookies.SimpleCookie(handler.headers.get("Cookie", ""))
    session_id = jar["blind_ranking_session"].value if "blind_ranking_session" in jar else None
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
                "ready": False,
                "board": [None] * SLOT_COUNT,
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

    * { box-sizing: border-box; }

    body {
      min-height: 100vh;
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button, input, select { font: inherit; }

    button { cursor: pointer; }

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
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--border-soft);
      background: rgba(9, 11, 16, 0.92);
    }

    h1, h2, p { margin: 0; }

    h1 {
      font-size: clamp(24px, 3vw, 38px);
      line-height: 1;
    }

    h2 {
      margin-bottom: 14px;
      font-size: 16px;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    main {
      width: min(1300px, 100%);
      margin: 0 auto;
      padding: 18px 24px 24px;
      display: grid;
      gap: 14px;
    }

    .panel, .status-panel, .join {
      border: 1px solid var(--border);
      background: var(--panel);
      padding: 16px;
      box-shadow: 0 16px 48px var(--shadow);
    }

    .status-panel {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px;
    }

    .status {
      color: var(--accent);
      font-weight: 900;
      font-size: 17px;
      line-height: 1.3;
    }

    .meta, .muted {
      margin-top: 5px;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }

    .chip {
      border: 1px solid var(--border);
      color: var(--accent);
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      white-space: nowrap;
    }

    .controls, .actions, .role-grid, .vote-options {
      display: flex;
      align-items: end;
      gap: 10px;
      flex-wrap: wrap;
    }

    .field {
      display: grid;
      gap: 5px;
    }

    .field label, .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    input, select, .button, .reset-button, .slot-button {
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
    }

    input, select {
      min-width: 170px;
      padding: 9px 11px;
    }

    .button, .reset-button {
      padding: 10px 13px;
      font-weight: 800;
    }

    .danger { color: var(--danger); border-color: rgba(251, 113, 133, 0.55); }

    .button:hover, .reset-button:hover, .slot-button:hover, select:hover {
      border-color: var(--accent);
    }

    .join {
      width: min(620px, 100%);
      margin: 36px auto;
      display: grid;
      gap: 16px;
    }

    .role-card {
      display: block;
      min-width: 150px;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      padding: 12px;
    }

    .role-card input {
      width: auto;
      min-width: auto;
      margin-right: 8px;
    }

    .game-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(260px, 330px);
      gap: 18px;
      align-items: start;
    }

    .current-card {
      display: grid;
      gap: 12px;
      justify-items: center;
    }

    .card-frame {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #070910;
      aspect-ratio: 5 / 7;
      width: min(230px, 72%);
      box-shadow: 0 18px 40px var(--shadow);
    }

    .game-grid > .panel .current-card .card-frame {
      width: min(210px, 80%);
    }

    .card-frame img, .slot-card img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .card-name {
      color: var(--text);
      font-weight: 900;
      text-align: center;
    }

    .boards {
      display: grid;
      gap: 12px;
    }

    .board {
      border: 1px solid var(--border-soft);
      background: rgba(255, 255, 255, 0.025);
      padding: 12px;
      display: grid;
      gap: 10px;
      min-width: 0;
    }

    .board-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-weight: 900;
    }

    .slot-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(96px, 1fr));
      gap: 10px;
      min-width: 0;
    }

    .rank-slot {
      min-width: 0;
    }

    .slot-button {
      width: 100%;
      min-height: 100%;
      padding: 8px;
      display: grid;
      gap: 8px;
      text-align: center;
    }

    .slot-number {
      color: var(--accent);
      font-weight: 900;
      font-size: 20px;
    }

    .slot-empty, .locked-message, .slot-hidden {
      border: 1px dashed var(--border);
      color: var(--muted);
      background: rgba(255, 255, 255, 0.02);
      aspect-ratio: 5 / 7;
      display: grid;
      place-items: center;
      padding: 10px;
      text-align: center;
    }

    .slot-hidden {
      border-style: solid;
      background:
        linear-gradient(135deg, rgba(94, 234, 212, 0.08), rgba(147, 197, 253, 0.04)),
        rgba(255, 255, 255, 0.025);
      color: var(--accent-two);
      font-weight: 900;
      text-transform: uppercase;
    }

    .slot-card {
      position: relative;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #070910;
      aspect-ratio: 5 / 7;
    }

    .info-button {
      position: absolute;
      top: 7px;
      right: 7px;
      z-index: 1001;
      display: grid;
      place-items: center;
      width: 25px;
      height: 25px;
      border: 1px solid rgba(191, 219, 254, 0.7);
      border-radius: 50%;
      background: rgba(15, 23, 42, 0.92);
      color: #bfdbfe;
      font-size: 14px;
      font-weight: 900;
      cursor: help;
    }

    .ability-tooltip {
      position: absolute;
      top: 31px;
      right: 0;
      z-index: 1002;
      width: min(280px, 72vw);
      padding: 12px;
      border: 1px solid rgba(96, 165, 250, 0.55);
      background: rgba(10, 15, 26, 0.98);
      color: var(--text);
      box-shadow: 0 20px 44px var(--shadow);
      text-align: left;
      font-size: 13px;
      line-height: 1.4;
      opacity: 0;
      pointer-events: none;
      transform: translateY(-4px);
      transition: opacity 120ms ease, transform 120ms ease;
    }

    .card-frame:has(.info-button:hover), .slot-card:has(.info-button:hover) {
      z-index: 1000;
      overflow: visible;
    }

    .info-button:hover .ability-tooltip {
      opacity: 1;
      transform: translateY(0);
    }

    .ability-tooltip strong {
      color: #bfdbfe;
      display: block;
      margin-bottom: 4px;
    }

    .ability-tooltip p, .ability-tooltip ul { margin: 0; }

    .ability-tooltip ul {
      padding-left: 1rem;
      margin-top: 5px;
    }

    .slot-label, .client-status {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }

    .client-status { color: var(--accent-two); }

    .clients, .votes {
      display: grid;
      gap: 8px;
    }

    .row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--border-soft);
      padding-bottom: 7px;
    }

    @media (max-width: 1000px) {
      .game-grid { grid-template-columns: 1fr; }
      .card-frame { width: min(210px, 64vw); }
      .slot-grid {
        grid-template-columns: repeat(5, minmax(78px, 1fr));
        overflow-x: auto;
        padding-bottom: 6px;
      }
    }

    @media (max-width: 620px) {
      header, .status-panel { align-items: flex-start; flex-direction: column; }
      main { padding: 14px; }
    }
"""


def page_shell(body, session_id=None, created=False):
    cookie_header = ""
    if created and session_id:
        cookie = cookies.SimpleCookie()
        cookie["blind_ranking_session"] = session_id
        cookie["blind_ranking_session"]["path"] = "/"
        cookie_header = cookie.output(header="").strip()

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Blind Ranking</title>
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


def render_join(session_id):
    client = clients.get(session_id, {})
    name = escape(client.get("name", ""))
    return f"""
<main class="page">
  <header>
    <div>
      <h1>Blind Ranking</h1>
      <p class="muted">Join as a player to rank cards or as a spectator to vote at the end.</p>
    </div>
    <span class="chip">Port 8001</span>
  </header>
  <form class="join" method="post" action="/join">
    <h2>Join Game</h2>
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
    <button class="button" type="submit">Join</button>
  </form>
</main>"""


def render_card_info(card):
    ability = card.get("ability") if card else None
    if not ability:
        return ""

    if isinstance(ability, dict):
        parts = []
        summary = ability.get("summary")
        if summary:
            parts.append(f"<p>{escape(str(summary))}</p>")
        strengths = ability.get("strengths") or []
        if strengths:
            parts.append(f"<strong>Strengths</strong><ul>{''.join(f'<li>{escape(str(item))}</li>' for item in strengths)}</ul>")
        weaknesses = ability.get("weaknesses") or []
        if weaknesses:
            parts.append(f"<strong>Weaknesses</strong><ul>{''.join(f'<li>{escape(str(item))}</li>' for item in weaknesses)}</ul>")
        tooltip = "".join(parts)
    else:
        tooltip = f"<p>{escape(str(ability))}</p>"

    if not tooltip:
        return ""

    return f"""<span class="info-button" aria-label="Card ability info">i
      <span class="ability-tooltip"><strong>Ability</strong>{tooltip}</span>
    </span>"""


def render_card(card):
    if not card:
        return '<div class="locked-message">No card</div>'
    return f"""<div class="card-frame"><img src="{escape(card['image'])}" alt="{escape(card['name'])}">{render_card_info(card)}</div>
<div class="card-name">{escape(card['name'])}</div>"""


def render_board(client, interactive=False, hide_cards=False):
    slot_html = []
    board = client.get("board", [None] * SLOT_COUNT)
    for index, card in enumerate(board, start=1):
        if hide_cards:
            content = f"""<div class="slot-hidden">Hidden</div>
<div class="slot-label">Rank {index}</div>"""
        elif card:
            content = f"""<div class="slot-card"><img src="{escape(card['image'])}" alt="{escape(card['name'])}">{render_card_info(card)}</div>
<div class="slot-label">Rank {index}</div>"""
        elif interactive and game_state["current_card"] and client["session_id"] not in game_state["placements"]:
            content = f"""<form method="post" action="/place">
  <input type="hidden" name="slot" value="{index}">
  <button class="slot-button" type="submit">
    <span class="slot-number">{index}</span>
    <span class="slot-empty">Place here</span>
  </button>
</form>"""
            slot_html.append(f'<div class="rank-slot">{content}</div>')
            continue
        else:
            content = f"""<div class="slot-empty">Open</div>
<div class="slot-label">Rank {index}</div>"""
        slot_html.append(f'<div class="rank-slot">{content}</div>')

    status = board_status(client)
    return f"""
    <div class="board">
      <div class="board-head">
        <span>{escape(role_label(client))}: {escape(client['name'])}</span>
        <span class="client-status">{escape(status)}</span>
      </div>
      <div class="slot-grid">{''.join(slot_html)}</div>
    </div>"""


def board_status(client):
    if game_state["phase"] == "lobby":
        return "Ready" if client.get("ready") else "Not ready"
    if game_state["phase"] == "ranking":
        return "Placed" if client["session_id"] in game_state["placements"] else "Ranking"
    if game_state["phase"] == "voting":
        return "Voted" if client["session_id"] in game_state["votes"] else "Voting"
    if game_state["phase"] == "complete":
        return "Done"
    return ""


def client_status(client):
    if client.get("role") == "spectator":
        if game_state["phase"] == "lobby":
            return "Watching lobby"
        if game_state["phase"] == "voting":
            return "Voted" if client["session_id"] in game_state["votes"] else "Voting"
        return "Watching"
    if client.get("role") == "player":
        return board_status(client)
    return ""


def top_status():
    phase = game_state["phase"]
    if phase == "lobby":
        ready_count = len([player for player in players() if player.get("ready")])
        return f"Lobby: {ready_count} of {len(players())} players ready."
    if phase == "ranking":
        waiting = [player["name"] for player in players() if player["session_id"] not in game_state["placements"]]
        return f"Round {game_state['round']}: waiting for {', '.join(waiting)}." if waiting else "All players placed. Advancing."
    if phase == "voting":
        return f"Voting for best order: {len(game_state['votes'])} of {len(game_state['eligible_voters'])} votes submitted."
    if phase == "complete":
        winner = clients.get(game_state["winner_session"], {})
        return f"Best order: {winner['name']} wins." if winner else "Best order vote ended in a tie."
    return ""


def render_lobby(session_id):
    client = clients[session_id]
    ready_disabled = "" if client.get("role") == "player" else " disabled"
    ready_text = "Unready" if client.get("ready") else "Ready"
    start_disabled = "" if all_players_ready() else " disabled"
    return f"""
    <section class="panel">
      <h2>Lobby</h2>
      <p class="muted">Single-player is allowed. Spectators can join and vote after all five cards are ranked.</p>
      <div class="actions">
        <form method="post" action="/ready">
          <button class="button" type="submit"{ready_disabled}>{ready_text}</button>
        </form>
        <form class="controls" method="post" action="/deck">
          <div class="field">
            <label for="deck">Deck</label>
            <select id="deck" name="deck" onchange="this.form.submit()">{deck_options()}</select>
          </div>
        </form>
        <form method="post" action="/start">
          <button class="button" type="submit"{start_disabled}>Start Game</button>
        </form>
      </div>
    </section>"""


def render_vote_panel(session_id):
    if game_state["phase"] == "complete":
        return f"""
        <section class="panel">
          <h2>Final Boards</h2>
          <div class="boards">{''.join(render_board(player) for player in players())}</div>
        </section>"""

    already_voted = session_id in game_state["votes"]
    if already_voted:
        return f"""
        <section class="panel">
          <h2>Best Order Vote</h2>
          <p class="status">Vote submitted.</p>
          <div class="boards">{''.join(render_board(player) for player in players())}</div>
        </section>"""

    options = []
    for player in players():
        options.append(f"""
        <form method="post" action="/vote">
          <input type="hidden" name="winner" value="{escape(player['session_id'])}">
          <button class="button" type="submit">Vote {escape(player['name'])}</button>
        </form>""")
    return f"""
    <section class="panel">
      <h2>Best Order Vote</h2>
      <div class="boards">{''.join(render_board(player) for player in players())}</div>
      <div class="vote-options">{''.join(options)}</div>
    </section>"""


def render_player_boards(session_id):
    boards = []
    for player in players():
        is_self = player["session_id"] == session_id
        interactive = (
            is_self
            and game_state["phase"] == "ranking"
            and session_id not in game_state["placements"]
        )
        hide_cards = game_state["phase"] == "ranking" and not is_self
        boards.append(render_board(player, interactive=interactive, hide_cards=hide_cards))
    return "".join(boards) or '<p class="muted">No players have joined yet.</p>'


def render_clients():
    rows = []
    for client in joined_clients():
        rows.append(f"""<div class="row">
          <span>{escape(client['name'])}</span>
          <span>{escape(role_label(client))} <span class="client-status">{escape(client_status(client))}</span></span>
        </div>""")
    return "".join(rows) or '<p class="muted">No joined clients.</p>'


def render_game(session_id):
    client = clients[session_id]
    role = role_label(client)
    phase = game_state["phase"]

    if phase == "lobby":
        main_panel = render_lobby(session_id)
    elif phase == "ranking":
        main_panel = f"""
        <section class="panel">
          <h2>Player Boards</h2>
          <div class="boards">{render_player_boards(session_id)}</div>
        </section>"""
    else:
        main_panel = render_vote_panel(session_id)

    if phase == "ranking":
        side_panel = f"""
        <section class="panel">
          <h2>Current Card</h2>
          <div class="current-card">{render_card(game_state['current_card'])}</div>
          <h2 style="margin-top: 16px;">Clients</h2>
          <div class="clients">{render_clients()}</div>
        </section>"""
    else:
        side_panel = f"""
        <section class="panel">
          <h2>Clients</h2>
          <div class="clients">{render_clients()}</div>
        </section>"""

    body = f"""
<main class="page">
  <header>
    <div>
      <h1>Blind Ranking</h1>
      <p class="muted">Deck: {escape(DECKS[game_state['deck_key']])}</p>
    </div>
    <span class="chip">{escape(role)}: {escape(client.get('name') or 'Guest')}</span>
  </header>
  <section class="status-panel">
    <div>
      <div class="status">{escape(top_status())}</div>
    </div>
    <div class="actions">
      <form method="post" action="/reset"><button class="reset-button danger" type="submit">New Game</button></form>
      <form method="post" action="/leave"><button class="reset-button" type="submit">Change Role</button></form>
    </div>
  </section>
  <div class="game-grid">
    {main_panel}
    {side_panel}
  </div>
</main>"""
    return body


class BlindRankingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            self.serve_asset(parsed.path)
            return
        if parsed.path == "/state":
            get_or_create_session(self)
            self.respond_json({"version": state_version["value"]})
            return
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
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
        elif parsed.path == "/deck":
            self.handle_deck()
        elif parsed.path == "/start":
            self.handle_start()
        elif parsed.path == "/place":
            self.handle_place(session_id)
        elif parsed.path == "/vote":
            self.handle_vote(session_id)
        elif parsed.path == "/reset":
            self.handle_reset()
        elif parsed.path == "/leave":
            self.handle_leave(session_id)
        else:
            self.send_error(404)
            return

        self.redirect("/", session_id, created)

    def read_form(self):
        content_length = int(self.headers.get("Content-Length", 0))
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
            if role == "player":
                if not client.get("player_number"):
                    client["player_number"] = next_player_number()
                client["board"] = [None] * SLOT_COUNT
            else:
                client["player_number"] = None
            bump_state_version()

    def handle_ready(self, session_id):
        with state_lock:
            client = clients.get(session_id)
            if not client or client.get("role") != "player" or game_state["phase"] != "lobby":
                return
            client["ready"] = not client.get("ready")
            bump_state_version()

    def handle_start(self):
        with state_lock:
            start_game(game_state["deck_key"])
            bump_state_version()

    def handle_deck(self):
        form = self.read_form()
        deck_key = form.get("deck", [game_state["deck_key"]])[0]
        with state_lock:
            if game_state["phase"] != "lobby":
                return
            game_state["deck_key"] = valid_deck_key(deck_key)
            bump_state_version()

    def handle_place(self, session_id):
        form = self.read_form()
        try:
            slot = int(form.get("slot", ["0"])[0]) - 1
        except ValueError:
            return

        with state_lock:
            client = clients.get(session_id)
            if not client or client.get("role") != "player" or game_state["phase"] != "ranking":
                return
            if session_id in game_state["placements"] or not game_state["current_card"]:
                return
            if slot < 0 or slot >= SLOT_COUNT or client["board"][slot] is not None:
                return

            client["board"][slot] = game_state["current_card"]
            game_state["placements"][session_id] = slot
            advance_round_if_ready()
            bump_state_version()

    def handle_vote(self, session_id):
        form = self.read_form()
        winner = form.get("winner", [""])[0]
        with state_lock:
            if game_state["phase"] != "voting" or session_id not in game_state["eligible_voters"]:
                return
            if winner not in [player["session_id"] for player in players()]:
                return
            game_state["votes"][session_id] = winner
            if len(game_state["votes"]) >= len(game_state["eligible_voters"]):
                counts = {}
                for vote in game_state["votes"].values():
                    counts[vote] = counts.get(vote, 0) + 1
                high = max(counts.values()) if counts else 0
                winners = [player_id for player_id, count in counts.items() if count == high]
                game_state["winner_session"] = winners[0] if len(winners) == 1 else None
                game_state["phase"] = "complete"
            bump_state_version()

    def handle_reset(self):
        with state_lock:
            reset_game(game_state["deck_key"])
            bump_state_version()

    def handle_leave(self, session_id):
        with state_lock:
            client = clients.get(session_id)
            if client:
                client["name"] = ""
                client["role"] = ""
                client["player_number"] = None
                client["ready"] = False
                client["board"] = [None] * SLOT_COUNT
            game_state["placements"].pop(session_id, None)
            game_state["votes"].pop(session_id, None)
            if session_id in game_state["eligible_voters"]:
                game_state["eligible_voters"].remove(session_id)
            bump_state_version()

    def redirect(self, location, session_id, created=False):
        self.send_response(303)
        if created:
            cookie = cookies.SimpleCookie()
            cookie["blind_ranking_session"] = session_id
            cookie["blind_ranking_session"]["path"] = "/"
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
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"{self.client_address[0]} - {format % args}")


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), BlindRankingHandler)
    print(f"Blind Ranking running at http://127.0.0.1:{PORT}")
    print(f"Network access: http://<your-computer-ip>:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
