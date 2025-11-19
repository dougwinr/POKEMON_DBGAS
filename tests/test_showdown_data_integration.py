from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import tournament_teams_extraction as tte  # noqa: E402
from tournament_teams_extraction import ShowdownData, convert_decklist_entry  # noqa: E402


@pytest.fixture()
def stub_showdown_payloads():
    pokedex = {"testmon": {"name": "Testmon"}}
    moves = {
        "legalmove": {"name": "Legal Move"},
        "illegalmove": {"name": "Illegal Move"},
    }
    items = {"testitem": {"name": "Test Item"}}
    abilities = {"testability": {"name": "Test Ability"}}
    learnsets = {"testmon": {"learnset": {"legalmove": ["9M"]}}}
    formats_data = {"testmon": {}}
    formats = [
        {"name": "[Gen 9] Local", "id": "localvgc", "gameType": "doubles", "banlist": []},
    ]
    return {
        "pokedex": pokedex,
        "moves": moves,
        "items": items,
        "abilities": abilities,
        "learnsets": learnsets,
        "formats_data": formats_data,
        "formats": formats,
    }


def test_load_showdown_data_calls_manager(monkeypatch, stub_showdown_payloads):
    calls = []

    def record(name):
        def inner(*args, **kwargs):
            calls.append(name)
            return stub_showdown_payloads[name]

        return inner

    monkeypatch.setattr(tte, "update_all_showdown_data", lambda debug=False: calls.append("update"))
    monkeypatch.setattr(tte, "load_pokedex", record("pokedex"))
    monkeypatch.setattr(tte, "load_moves", record("moves"))
    monkeypatch.setattr(tte, "load_items", record("items"))
    monkeypatch.setattr(tte, "load_abilities", record("abilities"))
    monkeypatch.setattr(tte, "load_learnsets", record("learnsets"))
    monkeypatch.setattr(tte, "load_formats_data", record("formats_data"))
    monkeypatch.setattr(tte, "load_formats_list", record("formats"))

    data = tte.load_showdown_data(debug=True)
    assert "update" in calls
    assert isinstance(data, ShowdownData)
    assert data.resolve_species_id("Testmon") == "testmon"
    assert data.resolve_move_id("Legal Move") == "legalmove"


def test_convert_decklist_entry_uses_local_showdown_data(stub_showdown_payloads):
    data = ShowdownData.from_payloads(**stub_showdown_payloads)
    slot = {
        "name": "Testmon",
        "ability": "Test Ability",
        "item": "Test Item",
        "teratype": None,
        "badges": ["Legal Move", "Illegal Move"],
    }
    extraction = convert_decklist_entry(slot, data)
    assert extraction.species == "Testmon"
    assert extraction.issues  # illegal move recorded
    assert extraction.moves[0].move_id == "legalmove"
    assert extraction.moves[0].is_legal
    assert extraction.moves[1].move_id == "illegalmove"
    assert not extraction.moves[1].is_legal
