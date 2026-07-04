import atexit
import os
import signal
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8003"))
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
      <span class="chip">Port {PORT}</span>
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


class LauncherHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
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
