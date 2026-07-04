import json
from pathlib import Path
from random import sample
from threading import Lock


ROUND_PLANS = {
    1: ["player1", "player2"],
    2: ["player2", "player1"],
    3: ["player1", "player2"],
    4: ["player2", "player1"],
    5: ["player1", "player2"],
    6: [],
}

state_lock = Lock()
state_version = {"value": 0}
ASSET_ROOT = Path(__file__).resolve().parent / "assets"
CARD_SET = "JJK"


def new_game_state():
    return {
        "round": 1,
        "phase": "placing",
        "placement_index": 0,
        "placements": [],
        "votes": {},
        "eligible_voters": [],
        "scores": {"player1": 0, "player2": 0},
        "round_results": [],
        "deck": [],
    }


game_state = new_game_state()


def load_card_deck():
    manifest_path = ASSET_ROOT / "cards" / CARD_SET / "_manifest.json"
    if not manifest_path.exists():
        return [
            {
                "id": str(number),
                "name": str(number),
                "image": "",
            }
            for number in range(1, 51)
        ]

    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    deck = []
    for item in manifest:
        file_name = Path(item["file"]).name
        card_id = Path(file_name).stem
        asset_path = ASSET_ROOT / "cards" / CARD_SET / file_name
        version = int(asset_path.stat().st_mtime) if asset_path.exists() else 0
        deck.append({
            "id": card_id,
            "name": item["name"],
            "image": f"/assets/cards/{CARD_SET}/{file_name}?v={version}",
            "ability": item.get("ability", ""),
        })
    return deck


CARD_DECK = load_card_deck()


def bump_state_version():
    state_version["value"] += 1


def role_display(role):
    return {
        "player1": "Player 1",
        "player2": "Player 2",
        "spectator": "Spectator",
    }.get(role, role)


def draw_hand():
    if not game_state["deck"]:
        game_state["deck"] = sample(CARD_DECK, len(CARD_DECK))

    hand_size = min(10, len(game_state["deck"]))
    hand = game_state["deck"][:hand_size]
    del game_state["deck"][:hand_size]
    return hand


def active_role():
    plan = ROUND_PLANS[game_state["round"]]
    if game_state["phase"] != "placing" or game_state["placement_index"] >= len(plan):
        return None
    return plan[game_state["placement_index"]]


def round_matchups():
    if game_state["round"] == 6:
        return [
            {"index": 0, "label": "Winning Team", "points": 2},
        ]

    return [
        {"index": 0, "label": f"Round {game_state['round']} Duel", "points": 1},
    ]


def start_voting(clients):
    game_state["phase"] = "voting"
    game_state["eligible_voters"] = list(clients.keys())
    game_state["votes"] = {matchup["index"]: {} for matchup in round_matchups()}


def advance_round(clients):
    if game_state["round"] >= 6:
        game_state["phase"] = "complete"
        return

    game_state["round"] += 1
    game_state["placement_index"] = 0
    game_state["votes"] = {}
    game_state["eligible_voters"] = []

    if game_state["round"] == 6:
        start_voting(clients)
    else:
        game_state["phase"] = "placing"


def vote_complete():
    eligible = game_state["eligible_voters"]
    if not eligible:
        return False

    for matchup in round_matchups():
        matchup_votes = game_state["votes"].get(matchup["index"], {})
        if any(voter not in matchup_votes for voter in eligible):
            return False
    return True


def tally_votes():
    results = []
    for matchup in round_matchups():
        matchup_votes = game_state["votes"].get(matchup["index"], {})
        player_one_votes = list(matchup_votes.values()).count("player1")
        player_two_votes = list(matchup_votes.values()).count("player2")

        if player_one_votes == player_two_votes:
            winner = "tie"
            game_state["scores"]["player1"] += 1
            game_state["scores"]["player2"] += 1
        else:
            winner = "player1" if player_one_votes > player_two_votes else "player2"
            game_state["scores"][winner] += matchup["points"]

        results.append({
            "round": game_state["round"],
            "label": matchup["label"],
            "winner": winner,
            "points": matchup["points"],
            "player1_votes": player_one_votes,
            "player2_votes": player_two_votes,
        })

    game_state["round_results"].extend(results)


def reset_match():
    game_state.clear()
    game_state.update(new_game_state())
