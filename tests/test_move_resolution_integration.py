from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from tournament_teams_extraction import ShowdownData, convert_decklist_entry  # noqa: E402


def build_showdown_data() -> ShowdownData:
    pokedex = {
        "conkeldurr": {"name": "Conkeldurr"},
        "urshifusinglestrike": {
            "name": "Urshifu-Single-Strike",
            "baseSpecies": "Urshifu",
            "forme": "Single-Strike",
        },
        "calyrexshadow": {
            "name": "Calyrex-Shadow",
            "baseSpecies": "Calyrex",
            "forme": "Shadow",
        },
        "calyrexice": {
            "name": "Calyrex-Ice",
            "baseSpecies": "Calyrex",
            "forme": "Ice",
        },
    }
    moves = {
        "wideguard": {"name": "Wide Guard"},
        "surgingstrikes": {"name": "Surging Strikes"},
        "darkpulse": {"name": "Dark Pulse"},
        "glaciallance": {"name": "Glacial Lance"},
        "shadowball": {"name": "Shadow Ball"},
    }
    abilities = {}
    items = {}
    learnsets = {
        "conkeldurr": {"learnset": {}},
        "urshifusinglestrike": {"learnset": {}},
        "calyrexshadow": {"learnset": {"shadowball": ["9M"]}},
        "calyrexice": {"learnset": {}},
    }
    formats_data = {key: {} for key in pokedex}
    formats = [
        {"name": "[Gen 9] VGC 2025", "id": "vgc2025", "gameType": "doubles", "banlist": []},
    ]
    return ShowdownData.from_payloads(
        pokedex=pokedex,
        moves=moves,
        items=items,
        abilities=abilities,
        learnsets=learnsets,
        formats_data=formats_data,
        formats=formats,
    )


def test_move_resolution_stores_raw_and_resolved():
    showdown_data = build_showdown_data()
    slot = {
        "name": "Calyrex [Shadow Rider]",
        "badges": ["Shdow Ball"],
        "ability": None,
        "item": None,
        "teratype": None,
    }
    pokemon = convert_decklist_entry(slot, showdown_data)
    move = pokemon.moves[0]
    assert move.raw_move == "Shdow Ball"
    assert move.move_id == "shadowball"
    assert move.move_name == "Shadow Ball"
    assert move.is_legal
    assert not pokemon.issues


@pytest.mark.parametrize(
    ("species_name", "move_name", "expected_fragment"),
    [
        ("Conkeldurr", "Wide Guard", "Conkeldurr cannot learn Wide Guard"),
        ("Urshifu [Single Strike Style]", "Surging Strikes", "Urshifu-Single-Strike cannot learn Surging Strikes"),
        ("Calyrex [Shadow Rider]", "Dark Pulse", "Calyrex-Shadow cannot learn Dark Pulse"),
        ("Calyrex [Ice Rider]", "Glacial Lance", "Calyrex-Ice cannot learn Glacial Lance"),
    ],
)
def test_illegal_moves_are_flagged(monkeypatch, species_name, move_name, expected_fragment):
    showdown_data = build_showdown_data()
    slot = {
        "name": species_name,
        "badges": [move_name],
        "ability": None,
        "item": None,
        "teratype": None,
    }
    pokemon = convert_decklist_entry(slot, showdown_data)
    assert any(expected_fragment in issue for issue in pokemon.issues)
    assert not pokemon.moves[0].is_legal
