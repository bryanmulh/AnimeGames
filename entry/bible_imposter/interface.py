import json
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
PORT = int(os.environ.get("PORT", "8004"))
ROUND_SECONDS_DEFAULT = 120
CLIENT_TIMEOUT_SECONDS = 20
MANIFEST_PATH = Path(__file__).resolve().parent / "bible_manifest.json"


state_lock = Lock()
clients = {}
state_version = {"value": 0}
game_state = {}


def bump_state_version():
    state_version["value"] += 1


def load_manifest():
    if not MANIFEST_PATH.exists():
        return []

    with MANIFEST_PATH.open("r", encoding="utf-8-sig") as file:
        raw_entries = json.load(file)

    entries = []
    for index, entry in enumerate(raw_entries):
        text = str(entry.get("text", "")).strip()
        category = str(entry.get("category", "")).strip() or "General"
        difficulty = str(entry.get("difficulty", "")).strip() or "Normal"
        hint = str(entry.get("hint", "")).strip()
        if not text:
            continue
        entries.append(
            {
                "id": f"entry-{index}",
                "text": text,
                "category": category,
                "difficulty": difficulty,
                "hint": hint,
            }
        )
    return entries


def manifest_entries():
    return load_manifest()


def manifest_categories():
    categories = sorted({entry["category"] for entry in manifest_entries()})
    return categories or ["General"]


def manifest_difficulties():
    difficulties = sorted({entry["difficulty"] for entry in manifest_entries()})
    return difficulties or ["Normal"]


def manifest_difficulties_for_category(category):
    difficulties = sorted(
        {
            entry["difficulty"]
            for entry in manifest_entries()
            if entry["category"] == category
        }
    )
    return difficulties or manifest_difficulties()


def fresh_state():
    entries = manifest_entries()
    default_entry = entries[0] if entries else {"category": "General", "difficulty": "Normal"}
    return {
        "phase": "lobby",
        "settings": {
            "imposter_count": 1,
            "category": default_entry["category"],
            "difficulty": default_entry["difficulty"],
            "round_seconds": ROUND_SECONDS_DEFAULT,
            "show_hints": True,
        },
        "entry": None,
        "imposters": [],
        "first_player": None,
        "round_started": None,
        "round_deadline": None,
        "votes": {},
        "winner_text": "",
    }


def reset_game():
    global game_state
    game_state = fresh_state()
    for client in clients.values():
        client["ready"] = False


def joined_players():
    return [
        client
        for client in clients.values()
        if client.get("name")
    ]


def all_ready():
    players = joined_players()
    return bool(players) and all(player.get("ready") for player in players)


def round_seconds_left():
    deadline = game_state.get("round_deadline")
    if not deadline or game_state["phase"] != "round":
        return None
    return max(0, int(deadline - time.time()))


def round_expired():
    return round_seconds_left() == 0


def eligible_entries():
    settings = game_state["settings"]
    return [
        entry
        for entry in manifest_entries()
        if entry["category"] == settings["category"]
        and entry["difficulty"] == settings["difficulty"]
    ]


def normalized_int(value, fallback, minimum, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def update_settings(form):
    players = joined_players()
    max_imposters = max(1, len(players) - 1)
    categories = manifest_categories()

    current = game_state["settings"]
    category = form.get("category", [current["category"]])[0]
    difficulty = form.get("difficulty", [current["difficulty"]])[0]

    current["category"] = category if category in categories else categories[0]
    difficulties = manifest_difficulties_for_category(current["category"])
    current["difficulty"] = difficulty if difficulty in difficulties else difficulties[0]
    current["imposter_count"] = normalized_int(
        form.get("imposter_count", [current["imposter_count"]])[0],
        current["imposter_count"],
        1,
        max_imposters,
    )
    current["round_seconds"] = normalized_int(
        form.get("round_seconds", [current["round_seconds"]])[0],
        current["round_seconds"],
        10,
        3600,
    )
    current["show_hints"] = "show_hints" in form


def can_start_game():
    players = joined_players()
    settings = game_state["settings"]
    return (
        game_state["phase"] == "lobby"
        and len(players) >= 2
        and all_ready()
        and settings["imposter_count"] < len(players)
        and bool(eligible_entries())
    )


def start_round():
    if not can_start_game():
        return

    players = joined_players()
    entry = random.choice(eligible_entries())
    imposter_count = game_state["settings"]["imposter_count"]
    imposters = random.sample([player["session_id"] for player in players], imposter_count)
    first_player = random.choice(players)
    now = time.time()

    game_state["phase"] = "round"
    game_state["entry"] = entry
    game_state["imposters"] = imposters
    game_state["first_player"] = first_player["session_id"]
    game_state["round_started"] = now
    game_state["round_deadline"] = now + game_state["settings"]["round_seconds"]
    game_state["votes"] = {}
    game_state["winner_text"] = ""


def start_voting():
    game_state["phase"] = "voting"
    game_state["round_deadline"] = None
    game_state["votes"] = {}


def voting_complete():
    voters = [player["session_id"] for player in joined_players()]
    return bool(voters) and all(voter in game_state["votes"] for voter in voters)


def finish_vote_if_ready():
    if not voting_complete():
        return

    counts = {}
    for picks in game_state["votes"].values():
        for picked in picks:
            counts[picked] = counts.get(picked, 0) + 1

    imposters = set(game_state["imposters"])
    top_count = max(counts.values()) if counts else 0
    top_picks = {session_id for session_id, count in counts.items() if count == top_count}
    caught = bool(imposters) and imposters.issubset(top_picks)

    if caught:
        game_state["winner_text"] = "Players caught the imposter team."
    else:
        game_state["winner_text"] = "Imposter team survives."
    game_state["phase"] = "results"


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
        game_state["votes"].pop(session_id, None)
        game_state["imposters"] = [
            imposter_id for imposter_id in game_state["imposters"] if imposter_id != session_id
        ]
        if game_state.get("first_player") == session_id:
            game_state["first_player"] = None

    if not clients:
        reset_game()
    bump_state_version()


def get_or_create_session(handler):
    jar = cookies.SimpleCookie(handler.headers.get("Cookie", ""))
    session_id = jar["bible_imposter_session"].value if "bible_imposter_session" in jar else None
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

    * { box-sizing: border-box; }

    body {
      min-height: 100vh;
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(94, 234, 212, 0.12), transparent 34rem),
        radial-gradient(circle at bottom right, rgba(147, 197, 253, 0.1), transparent 32rem),
        var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button, input, select { font: inherit; }
    button { cursor: pointer; }

    .page {
      width: min(1180px, 100%);
      margin: 0 auto;
      padding: 22px;
      display: grid;
      gap: 16px;
    }

    header, .panel {
      border: 1px solid var(--border);
      background: rgba(16, 20, 29, 0.9);
      box-shadow: 0 16px 48px var(--shadow);
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px;
    }

    h1, h2, h3, p { margin: 0; }

    h1 {
      font-size: clamp(28px, 4vw, 48px);
      line-height: 1;
    }

    h2 {
      margin-bottom: 12px;
      color: var(--text);
      font-size: 15px;
      letter-spacing: 0.07em;
      text-transform: uppercase;
    }

    .subtitle, .muted {
      color: var(--muted);
      font-size: 14px;
      line-height: 1.45;
    }

    .chip {
      border: 1px solid var(--border);
      padding: 8px 11px;
      color: var(--accent);
      font-size: 13px;
      font-weight: 900;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .panel {
      padding: 16px;
      min-width: 0;
    }

    .layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 360px);
      gap: 16px;
      align-items: start;
    }

    .join {
      width: min(560px, 100%);
      margin: 36px auto;
      display: grid;
      gap: 14px;
    }

    .field {
      display: grid;
      gap: 6px;
    }

    label, .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    input, select {
      width: 100%;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      padding: 10px 12px;
    }

    input[type="checkbox"] {
      width: auto;
      margin-right: 8px;
    }

    .controls {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .actions, .vote-options {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 12px;
    }

    .primary, .secondary, .danger {
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      padding: 10px 13px;
      font-weight: 900;
    }

    .primary {
      border-color: rgba(94, 234, 212, 0.6);
      color: var(--accent);
    }

    .danger {
      border-color: rgba(251, 113, 133, 0.55);
      color: var(--danger);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
    }

    .status {
      color: var(--accent);
      font-size: 20px;
      font-weight: 900;
      line-height: 1.3;
    }

    .secret {
      margin: 16px 0;
      padding: 28px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(21, 27, 39, 0.98), rgba(9, 11, 16, 0.98));
      display: grid;
      gap: 8px;
      place-items: center;
      text-align: center;
    }

    .secret-word {
      color: var(--accent);
      font-size: clamp(34px, 8vw, 72px);
      font-weight: 950;
      line-height: 1;
    }

    .timer {
      color: var(--accent-two);
      font-size: 28px;
      font-weight: 950;
    }

    .players {
      display: grid;
      gap: 9px;
    }

    .player-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--border-soft);
      padding-bottom: 8px;
    }

    .player-name {
      font-weight: 900;
    }

    .player-status {
      color: var(--accent-two);
      font-size: 12px;
      font-weight: 900;
      text-align: right;
      text-transform: uppercase;
    }

    .vote-card {
      border: 1px solid var(--border-soft);
      background: rgba(255, 255, 255, 0.025);
      padding: 12px;
      display: grid;
      gap: 8px;
    }

    .vote-card label {
      color: var(--text);
      letter-spacing: 0;
      text-transform: none;
    }

    .result-list {
      display: grid;
      gap: 8px;
      margin-top: 14px;
    }

    @media (max-width: 800px) {
      .page { padding: 14px; }
      header { align-items: flex-start; flex-direction: column; }
      .layout, .controls { grid-template-columns: 1fr; }
    }
"""


def page_shell(body, session_id=None, created=False):
    cookie_header = ""
    if created and session_id:
        cookie = cookies.SimpleCookie()
        cookie["bible_imposter_session"] = session_id
        cookie["bible_imposter_session"]["path"] = "/"
        cookie_header = cookie.output(header="").strip()

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bible Imposter</title>
  <style>{STYLE}</style>
</head>
<body>
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


def option_html(values, current):
    return "".join(
        f'<option value="{escape(value)}"{" selected" if value == current else ""}>{escape(value)}</option>'
        for value in values
    )


def player_display(session_id):
    player = clients.get(session_id, {})
    return player.get("name") or "Unknown"


def top_status():
    phase = game_state["phase"]
    if phase == "lobby":
        ready_count = len([player for player in joined_players() if player.get("ready")])
        return f"Lobby: {ready_count} of {len(joined_players())} players ready."
    if phase == "round":
        first = player_display(game_state["first_player"])
        return f"Round active. {first} goes first."
    if phase == "voting":
        return f"Vote for imposter: {len(game_state['votes'])} of {len(joined_players())} players submitted."
    if phase == "results":
        return game_state["winner_text"]
    return ""


def render_join(client):
    name = escape(client.get("name", ""))
    return f"""
<form class="panel join" method="post" action="/join">
  <h2>Join Game</h2>
  <div class="field">
    <label for="name">Display Name</label>
    <input id="name" name="name" value="{name}" maxlength="28" autocomplete="name" required>
  </div>
  <button class="primary" type="submit">Join Lobby</button>
</form>"""


def render_settings():
    settings = game_state["settings"]
    checked = " checked" if settings["show_hints"] else ""
    start_disabled = "" if can_start_game() else " disabled"
    warning = ""
    if not eligible_entries():
        warning = '<p class="muted">No manifest entries match this category and difficulty.</p>'
    elif len(joined_players()) < 2:
        warning = '<p class="muted">At least two players are needed to start.</p>'
    elif settings["imposter_count"] >= len(joined_players()):
        warning = '<p class="muted">Imposters must be fewer than total players.</p>'

    return f"""
<section class="panel">
  <h2>Lobby Settings</h2>
  <form method="post" action="/settings">
    <div class="controls">
      <div class="field">
        <label for="imposter_count">Number of Imposters</label>
        <input id="imposter_count" name="imposter_count" type="number" min="1" value="{settings['imposter_count']}" onchange="this.form.submit()">
      </div>
      <div class="field">
        <label for="round_seconds">Round Time</label>
        <input id="round_seconds" name="round_seconds" type="number" min="10" value="{settings['round_seconds']}" onchange="this.form.submit()">
      </div>
      <div class="field">
        <label for="category">Category</label>
        <select id="category" name="category" onchange="this.form.submit()">{option_html(manifest_categories(), settings['category'])}</select>
      </div>
      <div class="field">
        <label for="difficulty">Difficulty</label>
        <select id="difficulty" name="difficulty" onchange="this.form.submit()">{option_html(manifest_difficulties_for_category(settings['category']), settings['difficulty'])}</select>
      </div>
    </div>
    <div class="actions">
      <label><input type="checkbox" name="show_hints" value="1"{checked} onchange="this.form.submit()">Show imposter hints</label>
    </div>
  </form>
  {warning}
  <div class="actions">
    <form method="post" action="/start">
      <button class="primary" type="submit"{start_disabled}>Start Round</button>
    </form>
  </div>
</section>"""


def render_players():
    rows = []
    for player in sorted(joined_players(), key=lambda item: item["name"].lower()):
        status = client_status(player)
        first = " Goes first" if game_state.get("first_player") == player["session_id"] else ""
        rows.append(f"""
<div class="player-row">
  <span class="player-name">{escape(player['name'])}{escape(first)}</span>
  <span class="player-status">{escape(status)}</span>
</div>""")
    return "".join(rows) or '<p class="muted">No players have joined yet.</p>'


def client_status(client):
    phase = game_state["phase"]
    session_id = client["session_id"]
    if phase == "lobby":
        return "Ready" if client.get("ready") else "Not ready"
    if phase == "round":
        return "In round"
    if phase == "voting":
        return "Voted" if session_id in game_state["votes"] else "Voting"
    if phase == "results":
        return "Done"
    return ""


def render_lobby(session_id):
    client = clients[session_id]
    ready_text = "Unready" if client.get("ready") else "Ready"
    ready_disabled = "" if client.get("name") else " disabled"
    return f"""
<div class="layout">
  <div>
    {render_join(client) if not client.get('name') else render_settings()}
    {'' if not client.get('name') else f'<section class="panel"><form method="post" action="/ready"><button class="primary" type="submit"{ready_disabled}>{ready_text}</button></form></section>'}
  </div>
  <section class="panel">
    <h2>Players</h2>
    <div class="players">{render_players()}</div>
  </section>
</div>"""


def render_secret(session_id):
    entry = game_state["entry"] or {}
    is_imposter = session_id in game_state["imposters"]
    if is_imposter:
        word = "Imposter"
        detail = entry.get("hint", "") if game_state["settings"]["show_hints"] else ""
    else:
        word = entry.get("text", "No prompt")
        detail = "Do not say the code word directly."

    seconds = round_seconds_left()
    return f"""
<section class="panel">
  <div class="status">{escape(top_status())}</div>
  <p class="timer" data-countdown>{seconds}s left</p>
  <div class="secret">
    <div class="secret-word">{escape(word)}</div>
    {f'<p class="subtitle">{escape(detail)}</p>' if detail else ''}
  </div>
  <p class="muted">Category: {escape(entry.get('category', ''))} | Difficulty: {escape(entry.get('difficulty', ''))}</p>
</section>"""


def render_vote_form(session_id):
    already_voted = session_id in game_state["votes"]
    if already_voted:
        return '<p class="status">Vote submitted.</p>'

    max_votes = game_state["settings"]["imposter_count"]
    choices = []
    for player in sorted(joined_players(), key=lambda item: item["name"].lower()):
        if player["session_id"] == session_id:
            continue
        choices.append(f"""
<label>
  <input type="checkbox" name="suspect" value="{escape(player['session_id'])}">
  {escape(player['name'])}
</label>""")

    return f"""
<form class="vote-card" method="post" action="/vote">
  <p class="muted">Choose up to {max_votes} player(s).</p>
  {''.join(choices)}
  <button class="primary" type="submit">Submit Vote</button>
</form>"""


def render_results():
    counts = {}
    for picks in game_state["votes"].values():
        for picked in picks:
            counts[picked] = counts.get(picked, 0) + 1

    rows = []
    for player in sorted(joined_players(), key=lambda item: item["name"].lower()):
        role = "Imposter" if player["session_id"] in game_state["imposters"] else "Player"
        rows.append(f"""
<div class="player-row">
  <span class="player-name">{escape(player['name'])}</span>
  <span class="player-status">{escape(role)} | {counts.get(player['session_id'], 0)} votes</span>
</div>""")

    entry = game_state["entry"] or {}
    return f"""
<section class="panel">
  <div class="status">{escape(game_state['winner_text'])}</div>
  <p class="subtitle">Code word: {escape(entry.get('text', ''))}</p>
  <div class="result-list">{''.join(rows)}</div>
  <div class="actions">
    <form method="post" action="/reset"><button class="primary" type="submit">New Game</button></form>
  </div>
</section>"""


def render_game(session_id):
    client = clients[session_id]
    phase = game_state["phase"]
    if phase == "lobby":
        main = render_lobby(session_id)
    elif phase == "round":
        main = f"""
<div class="layout">
  {render_secret(session_id)}
  <section class="panel"><h2>Players</h2><div class="players">{render_players()}</div></section>
</div>"""
    elif phase == "voting":
        main = f"""
<div class="layout">
  <section class="panel"><h2>Vote for Imposter</h2>{render_vote_form(session_id)}</section>
  <section class="panel"><h2>Players</h2><div class="players">{render_players()}</div></section>
</div>"""
    else:
        main = render_results()

    body = f"""
<main class="page">
  <header>
    <div>
      <h1>Bible Imposter</h1>
      <p class="subtitle">Local multiplayer word deduction.</p>
    </div>
    <span class="chip">{escape(client.get('name') or 'Guest')}</span>
  </header>
  <section class="panel">
    <div class="status">{escape(top_status())}</div>
    <div class="actions">
      <form method="post" action="/reset"><button class="danger" type="submit">Reset</button></form>
      <form method="post" action="/leave"><button class="secondary" type="submit">Leave</button></form>
    </div>
  </section>
  {main}
</main>"""
    return body


class BibleImposterHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/state":
            get_or_create_session(self)
            with state_lock:
                if game_state["phase"] == "round" and round_expired():
                    start_voting()
                    bump_state_version()
                version = state_version["value"]
                seconds_left = round_seconds_left()
            self.respond_json({"version": version, "seconds_left": seconds_left})
            return

        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
            return

        session_id, created = get_or_create_session(self)
        with state_lock:
            body = render_game(session_id)
        self.respond_html(body, session_id, created)

    def do_POST(self):
        session_id, created = get_or_create_session(self)
        parsed = urlparse(self.path)

        if parsed.path == "/join":
            self.handle_join(session_id)
        elif parsed.path == "/settings":
            self.handle_settings()
        elif parsed.path == "/ready":
            self.handle_ready(session_id)
        elif parsed.path == "/start":
            self.handle_start()
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
        content_length = int(self.headers.get("Content-Length", "0"))
        return parse_qs(self.rfile.read(content_length).decode("utf-8"))

    def handle_join(self, session_id):
        form = self.read_form()
        name = form.get("name", [""])[0].strip()[:28]
        if not name:
            return
        with state_lock:
            clients[session_id]["name"] = name
            clients[session_id]["ready"] = False
            bump_state_version()

    def handle_settings(self):
        form = self.read_form()
        with state_lock:
            if game_state["phase"] != "lobby":
                return
            update_settings(form)
            bump_state_version()

    def handle_ready(self, session_id):
        with state_lock:
            client = clients.get(session_id)
            if not client or not client.get("name") or game_state["phase"] != "lobby":
                return
            client["ready"] = not client.get("ready")
            bump_state_version()

    def handle_start(self):
        with state_lock:
            start_round()
            bump_state_version()

    def handle_vote(self, session_id):
        form = self.read_form()
        with state_lock:
            if game_state["phase"] != "voting" or session_id not in [player["session_id"] for player in joined_players()]:
                return

            max_votes = game_state["settings"]["imposter_count"]
            valid_ids = {player["session_id"] for player in joined_players() if player["session_id"] != session_id}
            picks = []
            for suspect in form.get("suspect", []):
                if suspect in valid_ids and suspect not in picks:
                    picks.append(suspect)
                if len(picks) >= max_votes:
                    break
            if not picks:
                return

            game_state["votes"][session_id] = picks
            finish_vote_if_ready()
            bump_state_version()

    def handle_reset(self):
        with state_lock:
            reset_game()
            bump_state_version()

    def handle_leave(self, session_id):
        with state_lock:
            if session_id in clients:
                clients[session_id]["name"] = ""
                clients[session_id]["ready"] = False
            game_state["votes"].pop(session_id, None)
            bump_state_version()

    def redirect(self, location, session_id, created=False):
        self.send_response(303)
        if created:
            cookie = cookies.SimpleCookie()
            cookie["bible_imposter_session"] = session_id
            cookie["bible_imposter_session"]["path"] = "/"
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

    def log_message(self, format, *args):
        print(f"{self.client_address[0]} - {format % args}")


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), BibleImposterHandler)
    print(f"Bible Imposter running at http://127.0.0.1:{PORT}")
    print(f"Network access: http://<your-computer-ip>:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
