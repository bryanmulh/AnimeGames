import atexit
import mimetypes
import os
import signal
import socket
import subprocess
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8003"))
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRY_ROOT = PROJECT_ROOT / "entry"
sys.path.insert(0, str(ENTRY_ROOT))

from deck_config import ASSET_ROOT
from deck_config import available_decks
from deck_config import deck_name
from deck_config import load_cards
from deck_config import toggle_card

GAMES = [
    {
        "name": "Anime Battler",
        "description": "Team-vs-team card battler with roles, rounds, and voting.",
        "port": 8000,
        "script": PROJECT_ROOT / "entry" / "interface.py",
    },
    {
        "name": "Blind Ranking",
        "description": "Rank the same five cards, then vote for the best order.",
        "port": 8001,
        "script": PROJECT_ROOT / "entry" / "blind_ranking" / "interface.py",
    },
    {
        "name": "Bid War",
        "description": "Auction cards with hidden bids, budgets, teams, and final voting.",
        "port": 8002,
        "script": PROJECT_ROOT / "entry" / "bid_war" / "interface.py",
    },
]

started_processes = []


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

    main {
      width: min(1120px, 100%);
      margin: 0 auto;
      padding: 28px;
      display: grid;
      gap: 18px;
    }

    header,
    .game-card,
    .status-panel {
      border: 1px solid var(--border);
      background: var(--panel);
      box-shadow: 0 16px 48px var(--shadow);
    }

    header {
      padding: 20px;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }

    h1, h2, p { margin: 0; }

    h1 {
      font-size: clamp(30px, 5vw, 52px);
      line-height: 1;
    }

    .muted {
      margin-top: 7px;
      color: var(--muted);
      line-height: 1.45;
    }

    .chip {
      border: 1px solid var(--border);
      color: var(--accent);
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .games {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }

    .top-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }

    .game-card {
      padding: 16px;
      display: grid;
      gap: 12px;
      min-height: 210px;
      align-content: start;
    }

    .game-card h2 {
      font-size: 18px;
    }

    .game-card a {
      display: inline-flex;
      justify-content: center;
      width: 100%;
      margin-top: 8px;
      border: 1px solid rgba(94, 234, 212, 0.58);
      background: var(--panel-strong);
      color: var(--accent);
      padding: 11px 13px;
      font-weight: 900;
      text-decoration: none;
    }

    .game-card a:hover {
      border-color: var(--accent);
    }

    .deck-tabs {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .deck-tab,
    .back-link {
      display: inline-flex;
      justify-content: center;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--text);
      padding: 9px 12px;
      font-weight: 900;
      text-decoration: none;
    }

    .deck-tab.active,
    .back-link {
      border-color: rgba(94, 234, 212, 0.58);
      color: var(--accent);
    }

    .deck-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
      gap: 12px;
    }

    .deck-card {
      border: 1px solid var(--border);
      background: var(--panel);
      padding: 10px;
      display: grid;
      gap: 8px;
      box-shadow: 0 16px 48px var(--shadow);
    }

    .deck-card.disabled {
      opacity: 0.52;
    }

    .deck-card img {
      display: block;
      width: 100%;
      aspect-ratio: 5 / 7;
      object-fit: cover;
      background: #070910;
      border: 1px solid var(--border-soft);
    }

    .card-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .card-name {
      min-width: 0;
      font-size: 13px;
      font-weight: 900;
      line-height: 1.25;
    }

    .toggle-button {
      width: 34px;
      height: 34px;
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      border: 1px solid var(--border);
      background: var(--panel-strong);
      color: var(--accent);
      font-size: 19px;
      font-weight: 900;
      cursor: pointer;
    }

    .toggle-button.off {
      color: var(--danger);
    }

    .status-panel {
      padding: 14px 16px;
    }

    .status-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }

    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border-bottom: 1px solid var(--border-soft);
      padding-bottom: 7px;
    }

    .ok { color: var(--accent); font-weight: 900; }
    .warn { color: var(--danger); font-weight: 900; }

    @media (max-width: 820px) {
      header { align-items: flex-start; flex-direction: column; }
      .games { grid-template-columns: 1fr; }
      main { padding: 16px; }
    }
"""


def port_open(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def start_game_servers():
    for game in GAMES:
        if port_open(game["port"]):
            game["status"] = "Already running"
            game["started_by_launcher"] = False
            continue

        if not game["script"].exists():
            game["status"] = "Script missing"
            game["started_by_launcher"] = False
            continue

        process = subprocess.Popen(
            [sys.executable, str(game["script"])],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0,
        )
        started_processes.append(process)
        game["status"] = "Started by launcher"
        game["started_by_launcher"] = True


def stop_started_servers():
    for process in started_processes:
        if process.poll() is None:
            process.terminate()
    for process in started_processes:
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def stop_and_exit(signum, frame):
    stop_started_servers()
    raise SystemExit(0)


def host_for_links(handler):
    host = handler.headers.get("Host", f"127.0.0.1:{PORT}")
    if ":" in host:
        return host.rsplit(":", 1)[0]
    return host


def game_url(handler, port):
    return f"http://{host_for_links(handler)}:{port}"


def render_page(handler):
    cards = []
    rows = []
    for game in GAMES:
        is_running = port_open(game["port"])
        status = "Running" if is_running else game.get("status", "Not running")
        status_class = "ok" if is_running else "warn"
        url = game_url(handler, game["port"])
        cards.append(f"""
        <section class="game-card">
          <h2>{game['name']}</h2>
          <p class="muted">{game['description']}</p>
          <p class="muted">Port {game['port']}</p>
          <a href="{url}">Open Game</a>
        </section>""")
        rows.append(f"""
        <div class="row">
          <span>{game['name']}</span>
          <span class="{status_class}">{status}</span>
        </div>""")

    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Game Launcher</title>
  <style>{STYLE}</style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Game Launcher</h1>
        <p class="muted">Choose which local game to open. The launcher starts each game server if it is not already running.</p>
      </div>
      <div class="top-actions">
        <a class="back-link" href="/decks">Edit Decks</a>
        <span class="chip">Port {PORT}</span>
      </div>
    </header>
    <section class="games">
      {''.join(cards)}
    </section>
    <section class="status-panel">
      <h2>Server Status</h2>
      <div class="status-list">{''.join(rows)}</div>
    </section>
  </main>
</body>
</html>"""
    return body.encode("utf-8")


def selected_deck_from_query(handler):
    query = parse_qs(urlparse(handler.path).query)
    requested = query.get("deck", [""])[0]
    deck_keys = [key for key, _name in available_decks()]
    if requested in deck_keys:
        return requested
    return deck_keys[0] if deck_keys else ""


def render_deck_editor(handler):
    selected = selected_deck_from_query(handler)
    deck_tabs = []
    for key, name in available_decks():
        active = " active" if key == selected else ""
        deck_tabs.append(f'<a class="deck-tab{active}" href="/decks?deck={escape(key)}">{escape(key)} - {escape(name)}</a>')

    cards = []
    if selected:
        for card in load_cards(selected, include_disabled=True):
            enabled = card.get("enabled", True)
            button_class = "toggle-button" if enabled else "toggle-button off"
            button_text = "✓" if enabled else "✕"
            state_class = "" if enabled else " disabled"
            cards.append(f"""
            <section class="deck-card{state_class}">
              <img src="{escape(card['image'])}" alt="{escape(card['name'])}">
              <div class="card-row">
                <span class="card-name">{escape(card['name'])}</span>
                <form method="post" action="/decks/toggle">
                  <input type="hidden" name="deck" value="{escape(selected)}">
                  <input type="hidden" name="card" value="{escape(card['key'])}">
                  <button class="{button_class}" type="submit" title="Toggle card">{button_text}</button>
                </form>
              </div>
            </section>""")

    deck_title = deck_name(selected) if selected else "No Decks"
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Deck Editor</title>
  <style>{STYLE}</style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Deck Editor</h1>
        <p class="muted">Disable cards here to remove them from Anime Battler, Blind Ranking, and Bid War after the next reset or start.</p>
      </div>
      <a class="back-link" href="/">Back to Games</a>
    </header>
    <section class="status-panel">
      <h2>Decks</h2>
      <div class="deck-tabs">{''.join(deck_tabs)}</div>
    </section>
    <section class="status-panel">
      <h2>{escape(deck_title)}</h2>
      <div class="deck-grid">{''.join(cards) if cards else '<p class="muted">No cards found.</p>'}</div>
    </section>
  </main>
</body>
</html>"""
    return body.encode("utf-8")


def serve_asset(handler, request_path):
    relative_parts = [part for part in request_path[len("/assets/"):].split("/") if part]
    asset_path = ASSET_ROOT.joinpath(*relative_parts).resolve()
    try:
        asset_path.relative_to(ASSET_ROOT.resolve())
    except ValueError:
        handler.send_error(404)
        return
    if not asset_path.is_file():
        handler.send_error(404)
        return

    body = asset_path.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream")
    handler.send_header("Cache-Control", "public, max-age=3600")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_form(handler):
    content_length = int(handler.headers.get("Content-Length", "0"))
    return parse_qs(handler.rfile.read(content_length).decode("utf-8"))


class LauncherHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/assets/"):
            serve_asset(self, parsed.path)
            return
        if parsed.path == "/decks":
            body = render_deck_editor(self)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
            return

        body = render_page(self)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/decks/toggle":
            self.send_error(404)
            return

        form = read_form(self)
        deck = form.get("deck", [""])[0]
        card = form.get("card", [""])[0]
        if deck and card:
            toggle_card(deck, card)
        self.send_response(303)
        self.send_header("Location", f"/decks?deck={deck}")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"{self.client_address[0]} - {format % args}")


def run_server():
    start_game_servers()
    atexit.register(stop_started_servers)
    signal.signal(signal.SIGINT, stop_and_exit)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop_and_exit)

    server = ThreadingHTTPServer((HOST, PORT), LauncherHandler)
    print(f"Launcher running at http://127.0.0.1:{PORT}")
    print(f"Network access: http://<your-computer-ip>:{PORT}")
    print("Started game servers on ports 8000, 8001, and 8002 if they were not already running.")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        stop_started_servers()


if __name__ == "__main__":
    run_server()
