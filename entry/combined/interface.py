import json
import importlib.util
import mimetypes
import os
import sys
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8005"))
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENTRY_ROOT = PROJECT_ROOT / "entry"

sys.path.insert(0, str(ENTRY_ROOT))


def load_module(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


battler_app = load_module("combined_battler_app", ENTRY_ROOT / "interface.py")
blind_ranking_app = load_module("combined_blind_ranking_app", ENTRY_ROOT / "blind_ranking" / "interface.py")
bid_war_app = load_module("combined_bid_war_app", ENTRY_ROOT / "bid_war" / "interface.py")


GAMES = [
    {
        "name": "Anime Battler",
        "path": "/battler",
        "description": "Team-vs-team card battler with roles, rounds, and voting.",
        "handler": battler_app.InterfaceHandler,
    },
    {
        "name": "Blind Ranking",
        "path": "/blind-ranking",
        "description": "Rank the same five cards, then vote for the best order.",
        "handler": blind_ranking_app.BlindRankingHandler,
    },
    {
        "name": "Bid War",
        "path": "/bid-war",
        "description": "Auction cards with hidden bids, budgets, teams, and final voting.",
        "handler": bid_war_app.BidWarHandler,
    },
]


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
      --shadow: rgba(0, 0, 0, 0.42);
    }

    * { box-sizing: border-box; }

    body {
      min-height: 100vh;
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(94, 234, 212, 0.12), transparent 34rem),
        radial-gradient(circle at bottom right, rgba(147, 197, 253, 0.09), transparent 32rem),
        var(--bg);
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
    .note {
      border: 1px solid var(--border);
      background: rgba(16, 20, 29, 0.92);
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
      min-height: 190px;
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

    .note {
      padding: 14px 16px;
    }

    @media (max-width: 820px) {
      header { align-items: flex-start; flex-direction: column; }
      .games { grid-template-columns: 1fr; }
      main { padding: 16px; }
    }
"""


def launcher_html():
    cards = []
    for game in GAMES:
        cards.append(f"""
        <section class="game-card">
          <h2>{escape(game["name"])}</h2>
          <p class="muted">{escape(game["description"])}</p>
          <a href="{escape(game["path"])}">Open Game</a>
        </section>""")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AnimeGames Launcher</title>
  <style>{STYLE}</style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>AnimeGames</h1>
        <p class="muted">Choose a game. This hosted version runs all games through one web service.</p>
      </div>
      <span class="chip">Single Port</span>
    </header>
    <section class="games">
      {''.join(cards)}
    </section>
    <section class="note">
      <p class="muted">Only one game should be played at a time on this combined hosted instance.</p>
    </section>
  </main>
</body>
</html>""".encode("utf-8")


def mounted_games():
    return sorted(GAMES, key=lambda game: len(game["path"]), reverse=True)


def normalize_path(path):
    return "/" + path.lstrip("/")


def html_rewrite_prefix(html, prefix):
    html = html.replace(
        'href="http://localhost:8003/" onclick="this.href = `${window.location.protocol}//${window.location.hostname}:8003/`"',
        'href="/"',
    )
    replacements = {
        'href="/': f'href="{prefix}/',
        "href='/": f"href='{prefix}/",
        'src="/': f'src="{prefix}/',
        "src='/": f"src='{prefix}/",
        'action="/': f'action="{prefix}/',
        "action='/": f"action='{prefix}/",
        'fetch("/': f'fetch("{prefix}/',
        "fetch('/": f"fetch('{prefix}/",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    html = html.replace(f'href="{prefix}/"', 'href="/"')
    return html


def mounted_location(location, prefix):
    if not location:
        return prefix + "/"
    parsed = urlparse(location)
    if parsed.scheme or parsed.netloc:
        return location
    if location.startswith(prefix + "/") or location == prefix:
        return location
    return prefix + normalize_path(location)


class CombinedHandler(BaseHTTPRequestHandler):
    mount_prefix = ""
    stripped_path = ""
    active_handler = None

    def __getattr__(self, name):
        if self.active_handler and hasattr(self.active_handler, name):
            attribute = getattr(self.active_handler, name)
            if callable(attribute):
                return attribute.__get__(self, self.__class__)
            return attribute
        raise AttributeError(name)

    def do_GET(self):
        mount = self.find_mount()
        if not mount:
            self.send_launcher()
            return
        self.dispatch_to_mount(mount, "do_GET")

    def do_POST(self):
        mount = self.find_mount()
        if not mount:
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return
        self.dispatch_to_mount(mount, "do_POST")

    def find_mount(self):
        parsed = urlparse(self.path)
        for game in mounted_games():
            prefix = game["path"]
            if parsed.path == prefix or parsed.path.startswith(prefix + "/"):
                return game
        return None

    def dispatch_to_mount(self, mount, method_name):
        original_path = self.path
        parsed = urlparse(original_path)
        prefix = mount["path"]
        stripped = parsed.path[len(prefix):] or "/"
        query = f"?{parsed.query}" if parsed.query else ""

        self.mount_prefix = prefix
        self.stripped_path = stripped + query
        self.active_handler = mount["handler"]
        self.path = self.stripped_path
        try:
            getattr(mount["handler"], method_name)(self)
        finally:
            self.path = original_path
            self.mount_prefix = ""
            self.stripped_path = ""
            self.active_handler = None

    def send_launcher(self):
        body = launcher_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_header(self, keyword, value):
        if keyword.lower() == "location" and self.mount_prefix:
            value = mounted_location(value, self.mount_prefix)
        super().send_header(keyword, value)

    def send_html(self, html, session_id, status=200):
        body = html_rewrite_prefix(html, self.mount_prefix).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"anime_battler_session={session_id}; Path={self.mount_prefix}/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(body)

    def respond_html(self, body, session_id, created=False):
        if isinstance(body, bytes):
            html = body.decode("utf-8")
        elif self.mount_prefix == "/bid-war":
            html, _cookie_header = bid_war_app.page_shell(body, session_id, created)
            html = html.decode("utf-8")
        elif self.mount_prefix == "/blind-ranking":
            html, _cookie_header = blind_ranking_app.page_shell(body, session_id, created)
            html = html.decode("utf-8")
        else:
            html = body
        rewritten = html_rewrite_prefix(html, self.mount_prefix).encode("utf-8")
        self.send_response(200)
        if created:
            cookie = cookies.SimpleCookie()
            cookie_name = "bid_war_session" if self.mount_prefix == "/bid-war" else "blind_ranking_session"
            cookie[cookie_name] = session_id
            cookie[cookie_name]["path"] = f"{self.mount_prefix}/"
            self.send_header("Set-Cookie", cookie.output(header="").strip())
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(rewritten)))
        self.end_headers()
        self.wfile.write(rewritten)

    def send_json(self, data, status=200):
        self.respond_json(data, status=status, content_type="application/json; charset=utf-8")

    def respond_json(self, data, status=200, content_type="application/json"):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location, session_id, created=False):
        self.send_response(303)
        if created:
            cookie = cookies.SimpleCookie()
            cookie_name = "bid_war_session" if self.mount_prefix == "/bid-war" else "blind_ranking_session"
            cookie[cookie_name] = session_id
            cookie[cookie_name]["path"] = f"{self.mount_prefix}/"
            self.send_header("Set-Cookie", cookie.output(header="").strip())
        if self.mount_prefix == "/battler":
            self.send_header("Set-Cookie", f"anime_battler_session={session_id}; Path={self.mount_prefix}/; SameSite=Lax")
        self.send_header("Location", mounted_location(location, self.mount_prefix))
        self.end_headers()

    def send_asset(self):
        self.serve_combined_asset()

    def serve_asset(self, path):
        self.serve_combined_asset(path)

    def serve_combined_asset(self, path=None):
        request_path = urlparse(path or self.path).path
        if not request_path.startswith("/assets/"):
            self.send_error(404)
            return

        relative_parts = [part for part in request_path[len("/assets/"):].split("/") if part]
        asset_root = ENTRY_ROOT / "assets"
        asset_path = asset_root.joinpath(*relative_parts).resolve()
        try:
            asset_path.relative_to(asset_root.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not asset_path.is_file():
            self.send_error(404)
            return

        body = asset_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(asset_path))[0] or "application/octet-stream")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), CombinedHandler)
    print(f"Combined AnimeGames running at http://127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
