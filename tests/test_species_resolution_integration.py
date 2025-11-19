from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import tournament_teams_extraction as tte  # noqa: E402
from tournament_teams_extraction import ShowdownData, convert_decklist_entry  # noqa: E402


def build_test_showdown_data() -> ShowdownData:
    pokedex = {
        "slowbrogalar": {"name": "Slowbro-Galar"},
        "taurospaldeaaqua": {"name": "Tauros-Paldea-Aqua"},
        "sinistchaunremarkable": {"name": "Sinistcha-Unremarkable"},
        "basculegionf": {"name": "Basculegion-F"},
    }
    learnsets = {key: {"learnset": {}} for key in pokedex}
    formats_data = {key: {} for key in pokedex}
    formats = [
        {
            "name": "[Gen 9] VGC 2025",
            "id": "gen9vgc2025",
            "gameType": "doubles",
            "banlist": [],
        }
    ]
    return ShowdownData.from_payloads(
        pokedex=pokedex,
        moves={},
        items={},
        abilities={},
        learnsets=learnsets,
        formats_data=formats_data,
        formats=formats,
    )


@pytest.mark.parametrize(
    ("raw_name", "expected_id", "resolved_name"),
    [
        ("Slowbro [Galarian Form]", "slowbrogalar", "Slowbro-Galar"),
        ("Tauros [Paldean Form - Aqua Breed]", "taurospaldeaaqua", "Tauros-Paldea-Aqua"),
        ("Sinistcha [Unremarkable Form]", "sinistchaunremarkable", "Sinistcha-Unremarkable"),
        ("Basculegion [Female]", "basculegionf", "Basculegion-F"),
    ],
)
def test_convert_decklist_entry_uses_species_resolver(monkeypatch, raw_name, expected_id, resolved_name):
    showdown_data = build_test_showdown_data()

    mapping = {
        "Slowbro [Galarian Form]": "slowbrogalar",
        "Tauros [Paldean Form - Aqua Breed]": "taurospaldeaaqua",
        "Sinistcha [Unremarkable Form]": "sinistchaunremarkable",
        "Basculegion [Female]": "basculegionf",
    }

    def fake_resolver(name: str, debug: bool = False):
        return mapping.get(name)

    monkeypatch.setattr(tte, "resolve_species_name", fake_resolver)

    slot = {
        "name": raw_name,
        "badges": [],
        "ability": None,
        "item": None,
        "teratype": None,
    }
    pokemon = convert_decklist_entry(slot, showdown_data)

    assert pokemon.raw_species == raw_name
    assert pokemon.showdown_id == expected_id
    assert pokemon.species == resolved_name
    assert not any("Unable to resolve species" in issue for issue in pokemon.issues)
