import json
from pathlib import Path


ENTRY_ROOT = Path(__file__).resolve().parent
ASSET_ROOT = ENTRY_ROOT / "assets"
CARD_ROOT = ASSET_ROOT / "cards"
CONFIG_PATH = ENTRY_ROOT / "deck_config.json"

DECKS = {
    "BC": "Black Clover",
    "DS": "Demon Slayer",
    "JJK": "Jujutsu Kaisen",
    "MHA": "My Hero Academia",
}


def load_config():
    if not CONFIG_PATH.exists():
        return {"disabled": {}}
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        return {"disabled": {}}
    disabled = data.get("disabled")
    if not isinstance(disabled, dict):
        data["disabled"] = {}
    return data


def save_config(config):
    disabled = {}
    for deck_key, card_keys in config.get("disabled", {}).items():
        valid_keys = sorted({str(card_key) for card_key in card_keys if str(card_key).strip()})
        if valid_keys:
            disabled[deck_key] = valid_keys
    CONFIG_PATH.write_text(
        json.dumps({"disabled": disabled}, indent=2) + "\n",
        encoding="utf-8",
    )


def deck_dir(deck_key):
    return CARD_ROOT / deck_key


def deck_name(deck_key):
    return DECKS.get(deck_key, deck_key)


def available_decks():
    keys = sorted({path.name for path in CARD_ROOT.iterdir() if path.is_dir()} | set(DECKS))
    return [(key, deck_name(key)) for key in keys if deck_dir(key).exists()]


def card_key_from_file(file_name):
    return Path(file_name).stem


def disabled_cards(deck_key):
    config = load_config()
    return set(config.get("disabled", {}).get(deck_key, []))


def card_enabled(deck_key, card_key):
    return card_key not in disabled_cards(deck_key)


def toggle_card(deck_key, card_key):
    config = load_config()
    disabled = config.setdefault("disabled", {})
    card_keys = set(disabled.get(deck_key, []))
    if card_key in card_keys:
        card_keys.remove(card_key)
        enabled = True
    else:
        card_keys.add(card_key)
        enabled = False
    disabled[deck_key] = sorted(card_keys)
    save_config(config)
    return enabled


def load_cards(deck_key, include_disabled=False):
    card_dir = deck_dir(deck_key)
    manifest_path = card_dir / "_manifest.json"
    disabled = disabled_cards(deck_key)
    cards = []

    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8-sig") as file:
            manifest = json.load(file)

        for item in manifest:
            file_name = Path(item.get("file", "")).name
            if not file_name:
                continue
            image_path = card_dir / file_name
            if not image_path.exists():
                continue
            card_key = card_key_from_file(file_name)
            if not include_disabled and card_key in disabled:
                continue
            version = int(image_path.stat().st_mtime)
            card = {
                "id": f"{deck_key.lower()}-{card_key}",
                "key": card_key,
                "deck": deck_key,
                "name": item.get("name") or card_key.replace("_", " ").title(),
                "image": f"/assets/cards/{deck_key}/{file_name}?v={version}",
                "enabled": card_key not in disabled,
            }
            if item.get("ability"):
                card["ability"] = item["ability"]
            cards.append(card)

    if cards:
        return cards

    for image_path in sorted(card_dir.glob("*.png")):
        card_key = image_path.stem
        if not include_disabled and card_key in disabled:
            continue
        cards.append(
            {
                "id": f"{deck_key.lower()}-{card_key}",
                "key": card_key,
                "deck": deck_key,
                "name": card_key.replace("_", " ").title(),
                "image": f"/assets/cards/{deck_key}/{image_path.name}?v={int(image_path.stat().st_mtime)}",
                "enabled": card_key not in disabled,
            }
        )
    return cards


def load_many(deck_keys, include_disabled=False):
    cards = []
    for deck_key in deck_keys:
        cards.extend(load_cards(deck_key, include_disabled=include_disabled))
    return cards
