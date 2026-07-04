import json
import os
import sys
import time
from html import escape
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

try:
    from .game_state import (
        ROUND_PLANS,
        active_role,
        advance_round,
        bump_state_version,
        draw_hand,
        game_state,
        reset_match,
        role_display,
        round_matchups,
        start_voting,
        state_lock,
        state_version,
        tally_votes,
        vote_complete,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from game_state import (
        ROUND_PLANS,
        active_role,
        advance_round,
        bump_state_version,
        draw_hand,
        game_state,
        reset_match,
        role_display,
        round_matchups,
        start_voting,
        state_lock,
        state_version,
        tally_votes,
        vote_complete,
    )


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8000"))
ASSET_ROOT = Path(__file__).resolve().parent / "assets"
ASSET_CACHE_ENABLED = True
CLIENT_TIMEOUT_SECONDS = 20

clients = {}


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
      --accent-one: #5eead4;
      --accent-two: #f472b6;
      --card-back: #2b3143;
      --danger: #fb7185;
      --shadow: rgba(0, 0, 0, 0.42);
    }

    * {
      box-sizing: border-box;
    }

    body {
      min-height: 100vh;
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(94, 234, 212, 0.13), transparent 34rem),
        radial-gradient(circle at top right, rgba(244, 114, 182, 0.11), transparent 32rem),
        var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    button,
    input {
      font: inherit;
    }

    .launcher-link {
      position: fixed;
      top: 10px;
      right: 10px;
      z-index: 2000;
      padding: 7px 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: rgba(16, 20, 29, 0.9);
      color: var(--accent-one);
      font-size: 0.78rem;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-decoration: none;
      text-transform: uppercase;
      box-shadow: 0 10px 28px var(--shadow);
    }

    .launcher-link:hover,
    .launcher-link:focus-visible {
      border-color: var(--accent-one);
      outline: none;
    }

    .page {
      width: min(1180px, calc(100% - 32px));
      min-height: 100vh;
      margin: 0 auto;
      padding: 24px 0;
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 18px;
    }

    .header {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 20px;
    }

    .title-group {
      display: grid;
      gap: 8px;
    }

    h1,
    h2,
    p {
      margin: 0;
    }

    h1 {
      font-size: clamp(1.8rem, 4vw, 3.4rem);
      line-height: 0.95;
      font-weight: 800;
    }

    .subtitle {
      max-width: 54ch;
      color: var(--muted);
      font-size: 1rem;
      line-height: 1.6;
    }

    .battle-status,
    .player-chip {
      min-width: 156px;
      padding: 12px 16px;
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: rgba(16, 20, 29, 0.74);
      box-shadow: 0 16px 40px var(--shadow);
      text-align: right;
    }

    .status-label {
      color: var(--muted);
      display: block;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .status-value {
      display: block;
      margin-top: 4px;
      font-weight: 700;
    }

    .lobby {
      width: min(520px, 100%);
      margin: auto;
      padding: 24px;
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(21, 27, 39, 0.94), rgba(13, 17, 25, 0.94));
      box-shadow: 0 22px 70px var(--shadow);
      display: grid;
      gap: 20px;
    }

    .lobby h2 {
      font-size: 1.4rem;
    }

    .field {
      display: grid;
      gap: 8px;
    }

    .field label,
    .role-label {
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }

    .field input {
      width: 100%;
      min-height: 44px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      outline: none;
    }

    .field input:focus {
      border-color: var(--accent-one);
      box-shadow: 0 0 0 3px rgba(94, 234, 212, 0.12);
    }

    .roles {
      display: grid;
      gap: 10px;
    }

    .role-options {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }

    .role-option {
      position: relative;
    }

    .role-option input {
      position: absolute;
      opacity: 0;
      pointer-events: none;
    }

    .role-card {
      min-height: 78px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      display: grid;
      align-content: center;
      gap: 4px;
      cursor: pointer;
    }

    .role-name {
      font-weight: 800;
    }

    .role-state {
      color: var(--muted);
      font-size: 0.82rem;
    }

    .role-option input:checked + .role-card {
      border-color: var(--accent-one);
      box-shadow: 0 0 0 3px rgba(94, 234, 212, 0.12);
    }

    .role-option input:disabled + .role-card {
      cursor: not-allowed;
      opacity: 0.44;
    }

    .error {
      padding: 10px 12px;
      border: 1px solid rgba(251, 113, 133, 0.4);
      border-radius: 8px;
      background: rgba(251, 113, 133, 0.1);
      color: #fecdd3;
      font-size: 0.92rem;
    }

    .button-row {
      display: flex;
      justify-content: flex-end;
      gap: 10px;
    }

    .primary-button,
    .secondary-button {
      min-height: 42px;
      padding: 10px 16px;
      border-radius: 8px;
      font-weight: 800;
      cursor: pointer;
    }

    .primary-button {
      border: 0;
      background: var(--accent-one);
      color: #06110f;
    }

    .secondary-button {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--text);
    }

    .game-meta {
      display: grid;
      gap: 16px;
      padding: 12px 16px;
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: rgba(16, 20, 29, 0.7);
    }

    .game-meta p {
      color: var(--muted);
      font-size: 0.95rem;
    }

    .game-layout {
      display: grid;
      grid-template-columns: minmax(230px, 280px) minmax(0, 1fr) minmax(220px, 260px);
      gap: 18px;
      align-items: start;
    }

    .side-panel {
      display: grid;
      gap: 14px;
    }

    .round-panel {
      padding: 16px;
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: rgba(16, 20, 29, 0.7);
      display: grid;
      gap: 14px;
    }

    .scoreboard {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .score {
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--panel);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .score-name {
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .score-value {
      font-size: 1.35rem;
      font-weight: 900;
    }

    .round-actions,
    .vote-options {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .duel-vote .vote-options {
      display: grid;
      grid-template-columns: 1fr;
    }

    .duel-vote .vote-options button {
      width: 100%;
    }

    .round-status {
      color: var(--accent-one);
      font-weight: 900;
    }

    .vote-card {
      padding: 12px;
      border: 1px solid rgba(125, 211, 252, 0.5);
      border-radius: 8px;
      background:
        linear-gradient(135deg, rgba(125, 211, 252, 0.18), rgba(59, 130, 246, 0.08)),
        rgba(9, 11, 16, 0.32);
      display: grid;
      gap: 10px;
    }

    .vote-card strong {
      color: #bfdbfe;
    }

    .duel-vote {
      width: min(720px, 100%);
      padding: 14px;
    }

    .duel-title {
      color: #bfdbfe;
      font-size: 1rem;
      font-weight: 900;
      margin-bottom: 12px;
    }

    .duel-cards {
      display: grid;
      grid-template-columns: 1fr;
      justify-items: center;
      align-items: center;
      gap: 10px;
    }

    .duel-vs {
      color: var(--accent-one);
      font-weight: 900;
      letter-spacing: 0.08em;
      text-align: center;
      text-transform: uppercase;
    }

    .duel-choice {
      width: min(178px, 100%);
      padding: 0;
      border: 1px solid rgba(125, 211, 252, 0.5);
      border-radius: 8px;
      background: var(--panel);
      color: var(--text);
      cursor: pointer;
      overflow: hidden;
    }

    .duel-choice:hover,
    .duel-choice:focus-visible {
      border-color: var(--accent-one);
      box-shadow: 0 0 0 3px rgba(94, 234, 212, 0.14);
      outline: none;
    }

    .duel-image {
      position: relative;
      aspect-ratio: 5 / 7;
      width: min(170px, 100%);
      margin: 0 auto;
      display: grid;
      place-items: center;
      background: rgba(9, 11, 16, 0.7);
      overflow: visible;
    }

    .duel-image img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }

    .duel-image .card-name {
      padding: 10px;
      color: var(--text);
      font-size: 0.82rem;
      font-weight: 900;
      text-align: center;
    }

    .vote-footer {
      display: flex;
      justify-content: stretch;
      margin-top: 12px;
    }

    .vote-footer form,
    .vote-footer button {
      width: 100%;
    }

    .team-vote-preview {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
      max-width: 100%;
      overflow: hidden;
    }

    .preview-row {
      display: grid;
      grid-template-columns: 26px repeat(5, minmax(0, 1fr));
      align-items: center;
      gap: 4px;
      min-width: 0;
    }

    .preview-label {
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .preview-card {
      position: relative;
      aspect-ratio: 5 / 7;
      min-width: 0;
      border: 1px solid rgba(125, 211, 252, 0.28);
      border-radius: 6px;
      background: var(--panel);
      overflow: hidden;
    }

    .preview-card img {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }

    .preview-vs {
      color: var(--accent-one);
      font-size: 0.76rem;
      font-weight: 900;
      letter-spacing: 0.12em;
      text-align: center;
      text-transform: uppercase;
    }

    .history {
      display: grid;
      gap: 6px;
    }

    .history p {
      color: var(--muted);
      font-size: 0.9rem;
    }

    .table-area {
      min-width: 0;
    }

    .roster {
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: rgba(16, 20, 29, 0.7);
      box-shadow: 0 16px 48px var(--shadow);
      overflow: hidden;
    }

    .roster-header {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border-soft);
      color: var(--text);
      font-size: 0.86rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    .roster-list {
      margin: 0;
      padding: 12px 14px;
      list-style: none;
      display: grid;
      gap: 10px;
    }

    .roster-list li {
      display: grid;
      gap: 2px;
    }

    .roster-name {
      font-weight: 800;
    }

    .roster-role {
      color: var(--muted);
      font-size: 0.82rem;
    }

    .hand-area {
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: rgba(16, 20, 29, 0.7);
      box-shadow: 0 16px 48px var(--shadow);
      overflow: visible;
    }

    .hand-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border-soft);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }

    .hand-title {
      color: var(--text);
      font-size: 0.9rem;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.12em;
    }

    .hand-count {
      color: var(--muted);
      font-size: 0.84rem;
      white-space: nowrap;
    }

    .hand {
      padding: 14px 16px 16px;
      display: grid;
      grid-template-columns: repeat(10, minmax(58px, 1fr));
      gap: 10px;
    }

    .hand form {
      display: contents;
    }

    .hand-card {
      position: relative;
      z-index: 1;
      width: 100%;
      aspect-ratio: 5 / 7;
      min-height: 82px;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: visible;
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.22);
      display: grid;
      place-items: center;
      color: var(--text);
      font-size: clamp(1rem, 2.2vw, 1.65rem);
      font-weight: 900;
    }

    .hand form:has(.info-button:hover),
    .hand-card:has(.info-button:hover),
    .card-slot:has(.info-button:hover),
    .duel-choice:has(.info-button:hover) {
      z-index: 1000;
    }

    .hand-card:hover,
    .card-slot:hover,
    .duel-choice:hover {
      z-index: 1000;
    }

    .hand-card img,
    .card-slot img {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      object-fit: contain;
      border-radius: inherit;
    }

    .info-button {
      position: absolute;
      top: 7px;
      right: 7px;
      z-index: 1001;
      width: 24px;
      height: 24px;
      border: 1px solid rgba(191, 219, 254, 0.72);
      border-radius: 999px;
      background: rgba(9, 11, 16, 0.78);
      color: #bfdbfe;
      display: grid;
      place-items: center;
      font-size: 0.82rem;
      font-weight: 900;
      line-height: 1;
    }

    .ability-tooltip {
      position: absolute;
      top: 30px;
      right: 0;
      z-index: 1002;
      width: min(260px, 72vw);
      padding: 10px;
      border: 1px solid rgba(125, 211, 252, 0.5);
      border-radius: 8px;
      background: rgba(9, 11, 16, 0.96);
      color: var(--text);
      box-shadow: 0 18px 40px rgba(0, 0, 0, 0.42);
      font-size: 0.78rem;
      font-weight: 500;
      line-height: 1.35;
      text-align: left;
      opacity: 0;
      pointer-events: none;
      transform: translateY(-4px);
      transition: opacity 120ms ease, transform 120ms ease;
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

    .ability-tooltip p,
    .ability-tooltip ul {
      margin: 0;
    }

    .ability-tooltip ul {
      padding-left: 1rem;
      margin-top: 5px;
    }

    button.hand-card {
      cursor: pointer;
      appearance: none;
      padding: 0;
    }

    button.hand-card:hover,
    button.hand-card:focus-visible {
      border-color: var(--accent-one);
      box-shadow: 0 0 0 3px rgba(94, 234, 212, 0.14), 0 12px 28px rgba(0, 0, 0, 0.22);
      outline: none;
      transform: translateY(-2px);
    }

    .hand-card.face {
      background:
        linear-gradient(145deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.02)),
        var(--panel-strong);
    }

    .hand-card.back {
      background:
        radial-gradient(circle at center, rgba(220, 38, 38, 0.55), transparent 30%),
        linear-gradient(135deg, rgba(99, 102, 241, 0.48), transparent 42%),
        linear-gradient(315deg, rgba(220, 38, 38, 0.42), transparent 40%),
        #111827;
    }

    .hand-card.back::before {
      content: "";
      position: absolute;
      inset: 10px;
      border: 1px solid rgba(248, 113, 113, 0.55);
      border-radius: 6px;
    }

    .hand-card.back::after {
      content: "JJK";
      position: absolute;
      inset: 20px;
      border-radius: 50%;
      border: 2px solid rgba(191, 219, 254, 0.55);
      display: grid;
      place-items: center;
      color: rgba(254, 202, 202, 0.82);
      font-size: clamp(1.2rem, 3vw, 2.6rem);
      font-weight: 900;
    }

    .player-hand .hand {
      grid-template-columns: repeat(10, minmax(76px, 1fr));
      gap: 12px;
    }

    .player-hand .hand-card {
      min-height: 112px;
    }

    .teams {
      display: grid;
      gap: 12px;
    }

    .team {
      border: 1px solid var(--border-soft);
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(21, 27, 39, 0.94), rgba(13, 17, 25, 0.94));
      box-shadow: 0 22px 70px var(--shadow);
      overflow: visible;
    }

    .team-header {
      padding: 14px 16px;
      border-bottom: 1px solid var(--border-soft);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
    }

    .team h2 {
      font-size: 1.15rem;
      line-height: 1.2;
      font-weight: 800;
    }

    .team-count {
      color: var(--muted);
      font-size: 0.86rem;
      white-space: nowrap;
    }

    .slots {
      padding: 12px;
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
    }

    .card-slot {
      position: relative;
      z-index: 1;
      min-height: 94px;
      aspect-ratio: 5 / 7;
      border: 1px solid var(--border);
      border-radius: 8px;
      background:
        linear-gradient(135deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.01)),
        var(--panel);
      display: grid;
      place-items: center;
      overflow: visible;
    }

    .card-slot::before {
      content: "";
      position: absolute;
      inset: 10px;
      border: 1px dashed rgba(141, 153, 170, 0.28);
      border-radius: 6px;
    }

    .card-slot::after {
      content: attr(aria-label);
      position: relative;
      z-index: 1;
      color: rgba(243, 247, 251, 0.42);
      font-size: 0.84rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      text-align: center;
    }

    .card-slot.occupied {
      border-color: rgba(94, 234, 212, 0.55);
      background:
        linear-gradient(145deg, rgba(94, 234, 212, 0.16), rgba(244, 114, 182, 0.08)),
        var(--panel-strong);
      color: var(--text);
      font-size: clamp(1.1rem, 2.6vw, 1.8rem);
      font-weight: 900;
    }

    .card-slot.active {
      border-color: var(--accent-one);
      box-shadow: 0 0 0 3px rgba(94, 234, 212, 0.18), 0 0 24px rgba(94, 234, 212, 0.22);
    }

    .card-slot.active::before {
      border-color: rgba(94, 234, 212, 0.68);
    }

    .status-dot {
      display: inline-flex;
      width: 0.55rem;
      height: 0.55rem;
      border-radius: 999px;
      background: var(--muted);
      margin-right: 6px;
      vertical-align: 1px;
    }

    .status-dot.ready {
      background: var(--accent-one);
    }

    .status-dot.waiting {
      background: #fbbf24;
    }

    .roster-status {
      color: var(--muted);
      font-size: 0.78rem;
    }

    .card-slot.occupied::after {
      content: "";
      color: rgba(243, 247, 251, 0.72);
    }

    .team-one .team-header {
      box-shadow: inset 4px 0 0 var(--accent-one);
    }

    .team-two .team-header {
      box-shadow: inset 4px 0 0 var(--accent-two);
    }

    @media (max-width: 760px) {
      .page {
        width: min(100% - 24px, 560px);
        padding: 18px 0;
      }

      .header,
      .game-meta {
        align-items: stretch;
      }

      .battle-status,
      .player-chip {
        text-align: left;
      }

      .role-options,
      .scoreboard,
      .game-layout,
      .table-area {
        grid-template-columns: 1fr;
      }

      .slots {
        grid-template-columns: repeat(5, minmax(48px, 1fr));
        overflow-x: auto;
      }

      .hand {
        grid-template-columns: repeat(10, 64px);
        overflow-x: auto;
      }

      .player-hand .hand {
        grid-template-columns: repeat(10, 84px);
      }
    }
"""


def page_shell(body, auto_update=False):
    update_script = ""
    if auto_update:
        update_script = f"""
<script>
  const currentStateVersion = {state_version["value"]};
  const votePanel = document.querySelector("[data-vote-panel]");
  if (votePanel) {{
    window.requestAnimationFrame(() => {{
      votePanel.scrollIntoView({{ behavior: "smooth", block: "center" }});
    }});
  }}

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
"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Anime Battler</title>
  <style>{STYLE}</style>
</head>
<body>
<a class="launcher-link" href="http://localhost:8003/" onclick="this.href = `${{window.location.protocol}}//${{window.location.hostname}}:8003/`">Choose Game</a>
{body}
{update_script}
</body>
</html>
"""


def role_owner(role):
    for session in clients.values():
        if session["role"] == role:
            return session["name"]
    return None


def role_available(role, session_id):
    owner = role_owner(role)
    current = clients.get(session_id)
    return role == "spectator" or owner is None or (current and current["role"] == role)


def deal_player_hands():
    for client in clients.values():
        if client["role"] in ("player1", "player2"):
            client["hand"] = draw_hand()
        else:
            client["hand"] = []


def remove_client(session_id):
    clients.pop(session_id, None)
    if session_id in game_state["eligible_voters"]:
        game_state["eligible_voters"].remove(session_id)
    for votes in game_state["votes"].values():
        votes.pop(session_id, None)
    if game_state["phase"] == "voting" and vote_complete():
        tally_votes()
        advance_round(clients)


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
        remove_client(session_id)

    if not clients:
        reset_match()
    bump_state_version()


def touch_client(session_id):
    if session_id in clients:
        clients[session_id]["last_seen"] = time.time()


def render_lobby(session_id, error=""):
    player_one = role_owner("player1")
    player_two = role_owner("player2")
    current = clients.get(session_id, {})
    current_name = escape(current.get("name", ""))
    player_one_disabled = "" if role_available("player1", session_id) else "disabled"
    player_two_disabled = "" if role_available("player2", session_id) else "disabled"
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""

    body = f"""
  <main class="page">
    <header class="header">
      <div class="title-group">
        <h1>Anime Battler</h1>
        <p class="subtitle">Choose your seat before joining the match.</p>
      </div>
      <div class="battle-status" aria-label="Battle status">
        <span class="status-label">Status</span>
        <span class="status-value">Lobby</span>
      </div>
    </header>

    <form class="lobby" method="post" action="/join">
      <div>
        <h2>Join Game</h2>
        <p class="subtitle">Player seats are exclusive. Spectators are unlimited.</p>
      </div>
      {error_html}
      <div class="field">
        <label for="name">Name</label>
        <input id="name" name="name" value="{current_name}" autocomplete="name" maxlength="24" required>
      </div>
      <div class="roles">
        <span class="role-label">Role</span>
        <div class="role-options">
          <label class="role-option">
            <input type="radio" name="role" value="player1" {player_one_disabled} required>
            <span class="role-card">
              <span class="role-name">Player 1</span>
              <span class="role-state">{escape(player_one) if player_one else "Open"}</span>
            </span>
          </label>
          <label class="role-option">
            <input type="radio" name="role" value="player2" {player_two_disabled} required>
            <span class="role-card">
              <span class="role-name">Player 2</span>
              <span class="role-state">{escape(player_two) if player_two else "Open"}</span>
            </span>
          </label>
          <label class="role-option">
            <input type="radio" name="role" value="spectator" required>
            <span class="role-card">
              <span class="role-name">Spectator</span>
              <span class="role-state">Always open</span>
            </span>
          </label>
        </div>
      </div>
      <div class="button-row">
        <button class="primary-button" type="submit">Join</button>
      </div>
    </form>
  </main>
"""
    return page_shell(body)


def render_card_info(card):
    if not isinstance(card, dict) or not card.get("ability"):
        return ""

    ability = card["ability"]
    if isinstance(ability, dict):
        parts = ['<strong>Ability</strong>']
        summary = ability.get("summary")
        if summary:
            parts.append(f"<p>{escape(str(summary))}</p>")

        list_items = []
        strengths = ability.get("strengths") or []
        weaknesses = ability.get("weaknesses") or []
        if strengths:
            list_items.append(f"<li><b>Strengths:</b> {escape(', '.join(str(item) for item in strengths))}</li>")
        if weaknesses:
            list_items.append(f"<li><b>Weaknesses:</b> {escape(', '.join(str(item) for item in weaknesses))}</li>")
        if list_items:
            parts.append(f"<ul>{''.join(list_items)}</ul>")
        tooltip = "".join(parts)
    else:
        tooltip = f"<strong>Ability</strong><p>{escape(str(ability))}</p>"

    return f"""<span class="info-button" aria-label="Card ability info" onpointerdown="event.preventDefault(); event.stopPropagation();" onclick="event.preventDefault(); event.stopPropagation();">i
      <span class="ability-tooltip">{tooltip}</span>
    </span>"""


def cards(class_name, card_values, label, playable=False):
    html = []
    for index, value in enumerate(card_values, start=1):
        if isinstance(value, dict):
            card_name = escape(value["name"])
            image = escape(value["image"])
            content = f'<img src="{image}" alt="{card_name}">{render_card_info(value)}'
            aria_label = f"{label} {index}: {card_name}"
        else:
            content = escape(str(value)) if value is not None else ""
            aria_label = f"{label} {index}"

        if playable:
            html.append(f"""        <form method="post" action="/place">
          <input type="hidden" name="card_index" value="{index - 1}">
          <button class="hand-card {class_name}" type="submit" aria-label="Play {aria_label}">{content}</button>
        </form>""")
        else:
            html.append(
                f'        <div class="hand-card {class_name}" aria-label="{aria_label}">{content}</div>'
            )
    return "\n".join(html)


def slots(role, label):
    placed_cards = [placement for placement in game_state["placements"] if placement["role"] == role]
    active_index = None
    if game_state["phase"] == "placing" and active_role() == role:
        active_index = len(placed_cards) + 1

    html = []
    for index in range(1, 6):
        slot_label = f"{label} {index}"
        if index <= len(placed_cards):
            card = placed_cards[index - 1]["card"]
            if isinstance(card, dict):
                card_name = escape(card["name"])
                image = escape(card["image"])
                content = f'<img src="{image}" alt="{card_name}">{render_card_info(card)}'
                aria_label = f"{slot_label}: {card_name}"
            else:
                content = escape(str(card))
                aria_label = slot_label
            html.append(f'          <div class="card-slot occupied" aria-label="{aria_label}">{content}</div>')
        else:
            active_class = " active" if index == active_index else ""
            html.append(f'          <div class="card-slot{active_class}" aria-label="{slot_label}"></div>')
    return "\n".join(html)


def current_round_card(role):
    for placement in reversed(game_state["placements"]):
        if placement["role"] == role and placement.get("round") == game_state["round"]:
            return placement["card"]
    return None


def latest_card_for_role(role):
    for placement in reversed(game_state["placements"]):
        if placement["role"] == role:
            return placement["card"]
    return None


def render_duel_choice(card, role, player_name, matchup_index, disabled):
    if isinstance(card, dict):
        card_name = escape(card["name"])
        image = escape(card["image"])
        content = f"""
              <div class="duel-image">
                <img src="{image}" alt="{card_name}">
                {render_card_info(card)}
              </div>
"""
    else:
        card_name = role_display(role)
        content = f"""
              <div class="duel-image">
                <span class="card-name">{escape(card_name)}</span>
              </div>
"""

    return f"""
            <form method="post" action="/vote">
              <input type="hidden" name="matchup" value="{matchup_index}">
              <input type="hidden" name="winner" value="{role}">
              <button class="duel-choice" type="submit"{disabled} aria-label="Vote {escape(player_name)}">
{content}
              </button>
            </form>
"""


def render_preview_card(card):
    if isinstance(card, dict):
        card_name = escape(card["name"])
        image = escape(card["image"])
        return f'<div class="preview-card"><img src="{image}" alt="{card_name}"></div>'
    return '<div class="preview-card"></div>'


def render_team_vote_preview():
    rows = []
    for role, label in (("player1", "P1"), ("player2", "P2")):
        placed_cards = [placement["card"] for placement in game_state["placements"] if placement["role"] == role]
        cards_html = "".join(render_preview_card(card) for card in placed_cards[:5])
        cards_html += "".join('<div class="preview-card"></div>' for _ in range(max(0, 5 - len(placed_cards))))
        rows.append(f"""
          <div class="preview-row">
            <span class="preview-label">{label}</span>
            {cards_html}
          </div>
""")

    return f"""
          <div class="team-vote-preview" aria-label="Current team board preview">
            {rows[0]}
            <div class="preview-vs">vs</div>
            {rows[1]}
          </div>
"""


def render_history():
    if not game_state["round_results"]:
        return '<div class="history"><p>No points awarded yet.</p></div>'

    rows = []
    for result in game_state["round_results"][-4:]:
        if result["winner"] == "tie":
            outcome = "Tie, both players +1"
        elif result["winner"]:
            outcome = f"{role_display(result['winner'])} +{result['points']}"
        else:
            outcome = "Tie, no points"
        rows.append(
            f"<p>Round {result['round']} - {escape(result['label'])}: "
            f"{outcome} ({result['player1_votes']} to {result['player2_votes']})</p>"
        )
    return f'<div class="history">{"".join(rows)}</div>'


def render_round_actions(session_id, session):
    role = session["role"]
    player_one_name = role_owner("player1") or "Player 1"
    player_two_name = role_owner("player2") or "Player 2"

    if game_state["phase"] == "complete":
        winner = "Tie"
        if game_state["scores"]["player1"] > game_state["scores"]["player2"]:
            winner = "Player 1 wins"
        elif game_state["scores"]["player2"] > game_state["scores"]["player1"]:
            winner = "Player 2 wins"
        return f"""
      <p class="subtitle round-status">Game complete. {winner}.</p>
      <form method="post" action="/reset">
        <button class="secondary-button" type="submit">Reset Match</button>
      </form>
"""

    if game_state["phase"] == "placing":
        current_role = active_role()
        if role == current_role:
            return f"""
      <p class="subtitle round-status">Round {game_state['round']}: choose a card from your hand to place.</p>
"""
        return f"""
      <p class="subtitle round-status">Round {game_state['round']}: waiting for {role_display(current_role)} to place a card.</p>
"""

    matchup = round_matchups()[0]
    matchup_votes = game_state["votes"].get(matchup["index"], {})
    already_voted = session_id in matchup_votes
    total_voters = len(game_state["eligible_voters"])
    progress = len(matchup_votes)

    if already_voted:
        return f"""
      <p class="subtitle round-status">Vote submitted. Waiting on {progress} of {total_voters} voters.</p>
"""

    player_one_card = current_round_card("player1") or latest_card_for_role("player1")
    player_two_card = current_round_card("player2") or latest_card_for_role("player2")
    disabled = ""

    if game_state["round"] == 6:
        vote_panel = f"""
        <div class="vote-card duel-vote" data-vote-panel>
          <div class="duel-title">Round 6 Vote Winning Team</div>
{render_team_vote_preview()}
          <div class="vote-options">
            <form method="post" action="/vote">
              <input type="hidden" name="matchup" value="{matchup["index"]}">
              <input type="hidden" name="winner" value="player1">
              <button class="secondary-button" type="submit">Vote {escape(player_one_name)} (P1)</button>
            </form>
            <form method="post" action="/vote">
              <input type="hidden" name="matchup" value="{matchup["index"]}">
              <input type="hidden" name="winner" value="player2">
              <button class="secondary-button" type="submit">Vote {escape(player_two_name)} (P2)</button>
            </form>
            <form method="post" action="/vote">
              <input type="hidden" name="matchup" value="{matchup["index"]}">
              <input type="hidden" name="winner" value="abstain">
              <button class="secondary-button" type="submit">Skip vote</button>
            </form>
          </div>
        </div>
"""
    else:
        vote_panel = f"""
        <div class="vote-card duel-vote" data-vote-panel>
          <div class="duel-title">Round {game_state["round"]} Vote</div>
          <div class="duel-cards">
{render_duel_choice(player_one_card, "player1", f"{player_one_name} (P1)", matchup["index"], disabled)}
            <span class="duel-vs">vs</span>
{render_duel_choice(player_two_card, "player2", f"{player_two_name} (P2)", matchup["index"], disabled)}
          </div>
          <div class="vote-footer">
            <form method="post" action="/vote">
              <input type="hidden" name="matchup" value="{matchup["index"]}">
              <input type="hidden" name="winner" value="abstain">
              <button class="secondary-button" type="submit">Skip vote</button>
            </form>
          </div>
        </div>
"""

    return f"""
      <p class="subtitle round-status">Round {game_state['round']}: voting is open. Waiting on {progress} of {total_voters} voters.</p>
{vote_panel}
"""


def render_round_panel(session_id, session):
    return f"""
    <section class="round-panel" aria-label="Round status">
      <div class="round-actions">
{render_round_actions(session_id, session)}
      </div>
      {render_history()}
    </section>
"""


def render_roster():
    role_order = {"player1": 0, "player2": 1, "spectator": 2}
    sessions = sorted(
        clients.values(),
        key=lambda item: (role_order.get(item["role"], 99), item["name"].lower()),
    )

    if not sessions:
        rows = '<li><span class="roster-name">No one online</span></li>'
    else:
        rows = "".join(
            f"""<li>
        <span class="roster-name">{escape(client["name"])}</span>
        <span class="roster-role">{role_display(client["role"])}</span>
        <span class="roster-status">{render_client_status(client)}</span>
      </li>"""
            for client in sessions
        )

    return f"""
      <aside class="roster" aria-label="Online players">
        <div class="roster-header">Online</div>
        <ul class="roster-list">
      {rows}
        </ul>
      </aside>
"""


def render_client_status(client):
    if game_state["phase"] == "voting":
        session_id = client.get("session_id")
        if session_id not in game_state["eligible_voters"]:
            return '<span class="status-dot"></span>Not in vote'

        has_voted = all(
            session_id in game_state["votes"].get(matchup["index"], {})
            for matchup in round_matchups()
        )
        if has_voted:
            return '<span class="status-dot ready"></span>Voted'
        return '<span class="status-dot waiting"></span>Waiting to vote'

    current_role = active_role()
    if game_state["phase"] == "placing" and client["role"] == current_role:
        return '<span class="status-dot waiting"></span>Turn to play'

    if game_state["phase"] == "complete":
        return '<span class="status-dot ready"></span>Game complete'

    return '<span class="status-dot"></span>Online'


def render_game(session_id, session):
    name = escape(session["name"])
    role = role_display(session["role"])
    player_one = role_owner("player1") or "Open"
    player_two = role_owner("player2") or "Open"
    player_one_cards = sum(1 for placement in game_state["placements"] if placement["role"] == "player1")
    player_two_cards = sum(1 for placement in game_state["placements"] if placement["role"] == "player2")
    own_hand = session.get("hand", []) if session["role"] in ("player1", "player2") else [None] * 10
    can_play_from_hand = game_state["phase"] == "placing" and session["role"] == active_role()

    body = f"""
  <main class="page">
    <header class="header">
      <div class="title-group">
        <h1>Anime Battler</h1>
        <p class="subtitle">Draft two teams, place five cards per side, and prepare the match board.</p>
      </div>
      <div class="player-chip" aria-label="Current player">
        <span class="status-label">{escape(role)}</span>
        <span class="status-value">{name}</span>
      </div>
    </header>

    <section class="game-layout" aria-label="Game table and status">
      <aside class="side-panel side-panel-left" aria-label="Round status">
{render_round_panel(session_id, session)}
      </aside>

      <section class="table-area" aria-label="Battle table">
        <div class="teams" aria-label="Team card slots">
          <article class="team team-one" aria-labelledby="team-one-title">
            <div class="team-header">
              <h2 id="team-one-title">Team {escape(player_one)} - {game_state["scores"]["player1"]}</h2>
              <span class="team-count">{player_one_cards} / 5 placed</span>
            </div>
            <div class="slots">
{slots("player1", "Team 1 card slot")}
            </div>
          </article>

          <article class="team team-two" aria-labelledby="team-two-title">
            <div class="team-header">
              <h2 id="team-two-title">Team {escape(player_two)} - {game_state["scores"]["player2"]}</h2>
              <span class="team-count">{player_two_cards} / 5 placed</span>
            </div>
            <div class="slots">
{slots("player2", "Team 2 card slot")}
            </div>
          </article>
        </div>
      </section>

      <aside class="side-panel side-panel-right" aria-label="Players and online roster">
        <section class="game-meta" aria-label="Game seats">
          <p>Player 1: {escape(player_one)} | Player 2: {escape(player_two)}</p>
          <form method="post" action="/leave">
            <button class="secondary-button" type="submit">Change Role</button>
          </form>
        </section>
{render_roster()}
      </aside>
    </section>

    <section class="hand-area player-hand" aria-label="Your hand">
      <div class="hand-header">
        <span class="hand-title">Your Hand</span>
        <span class="hand-count">{len(own_hand)} cards</span>
      </div>
      <div class="hand">
{cards("face", own_hand, "Your card", playable=can_play_from_hand)}
      </div>
    </section>
  </main>
"""
    return page_shell(body, auto_update=True)


class InterfaceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state":
            session_id = self.get_or_create_session_id()
            with state_lock:
                cleanup_stale_clients()
                touch_client(session_id)
                self.send_json({"version": state_version["value"]})
            return

        if self.path.startswith("/assets/"):
            self.send_asset()
            return

        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return

        session_id = self.get_or_create_session_id()
        with state_lock:
            cleanup_stale_clients()
            touch_client(session_id)
            session = clients.get(session_id)
            html = render_game(session_id, session) if session else render_lobby(session_id)
        self.send_html(html, session_id)

    def do_POST(self):
        session_id = self.get_or_create_session_id()
        with state_lock:
            cleanup_stale_clients()
            touch_client(session_id)

        if self.path == "/join":
            self.handle_join(session_id)
            return

        if self.path == "/leave":
            with state_lock:
                remove_client(session_id)
                bump_state_version()
            self.redirect("/", session_id)
            return

        if self.path == "/place":
            self.handle_place(session_id)
            return

        if self.path == "/vote":
            self.handle_vote(session_id)
            return

        if self.path == "/reset":
            self.handle_reset(session_id)
            return

        self.send_error(404)

    def handle_join(self, session_id):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(body)
        name = form.get("name", [""])[0].strip()
        role = form.get("role", [""])[0].strip()

        with state_lock:
            if not name:
                self.send_html(render_lobby(session_id, "Enter a name before joining."), session_id, status=400)
                return

            if role not in ("player1", "player2", "spectator"):
                self.send_html(render_lobby(session_id, "Choose Player 1, Player 2, or Spectator."), session_id, status=400)
                return

            if not role_available(role, session_id):
                self.send_html(render_lobby(session_id, "That player seat is already taken."), session_id, status=409)
                return

            clients[session_id] = {
                "name": name[:24],
                "role": role,
                "session_id": session_id,
                "hand": draw_hand() if role in ("player1", "player2") else [],
                "last_seen": time.time(),
            }
            bump_state_version()
        self.redirect("/", session_id)

    def handle_place(self, session_id):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(body)

        try:
            card_index = int(form.get("card_index", [""])[0])
        except ValueError:
            self.redirect("/", session_id)
            return

        with state_lock:
            session = clients.get(session_id)
            if not session or game_state["phase"] != "placing" or session["role"] != active_role():
                self.redirect("/", session_id)
                return

            if card_index < 0 or card_index >= len(session.get("hand", [])):
                self.redirect("/", session_id)
                return

            card = session["hand"].pop(card_index)

            game_state["placements"].append({
                "round": game_state["round"],
                "role": session["role"],
                "name": session["name"],
                "card": card,
            })
            game_state["placement_index"] += 1

            if game_state["placement_index"] >= len(ROUND_PLANS[game_state["round"]]):
                start_voting(clients)

            bump_state_version()

        self.redirect("/", session_id)

    def handle_vote(self, session_id):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        form = parse_qs(body)

        try:
            matchup = int(form.get("matchup", [""])[0])
        except ValueError:
            self.redirect("/", session_id)
            return

        winner = form.get("winner", [""])[0]

        with state_lock:
            if (
                session_id not in clients
                or game_state["phase"] != "voting"
                or session_id not in game_state["eligible_voters"]
                or matchup not in game_state["votes"]
                or winner not in ("player1", "player2", "abstain")
            ):
                self.redirect("/", session_id)
                return

            game_state["votes"][matchup][session_id] = winner

            if vote_complete():
                tally_votes()
                advance_round(clients)

            bump_state_version()

        self.redirect("/", session_id)

    def handle_reset(self, session_id):
        with state_lock:
            if session_id in clients:
                reset_match()
                deal_player_hands()
                bump_state_version()
        self.redirect("/", session_id)

    def get_or_create_session_id(self):
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(cookie_header)
        if "anime_battler_session" in jar:
            return jar["anime_battler_session"].value
        return token_urlsafe(24)

    def send_html(self, html, session_id, status=200):
        content = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Set-Cookie", f"anime_battler_session={session_id}; Path=/; SameSite=Lax")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, data, status=200):
        content = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_asset(self):
        asset_url_path = urlparse(self.path).path
        relative_path = unquote(asset_url_path[len("/assets/"):]).replace("/", "\\")
        asset_path = (ASSET_ROOT / relative_path).resolve()
        try:
            asset_path.relative_to(ASSET_ROOT.resolve())
        except ValueError:
            self.send_error(404)
            return

        if not asset_path.is_file():
            self.send_error(404)
            return

        content_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(asset_path.suffix.lower(), "application/octet-stream")

        content = asset_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        if ASSET_CACHE_ENABLED:
            self.send_header("Cache-Control", "public, max-age=3600")
        else:
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, path, session_id):
        self.send_response(303)
        self.send_header("Location", path)
        self.send_header("Set-Cookie", f"anime_battler_session={session_id}; Path=/; SameSite=Lax")
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_server():
    server = ThreadingHTTPServer((HOST, PORT), InterfaceHandler)
    print(f"Serving Anime Battler interface at http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run_server()
