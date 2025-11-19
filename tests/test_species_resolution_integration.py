from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from tournament_teams_extraction import ShowdownData, convert_decklist_entry  # noqa: E402


def build_test_showdown_data() -> ShowdownData:
    pokedex = {
        "slowbrogalar": {"name": "Slowbro-Galar", "baseSpecies": "Slowbro", "forme": "Galar"},
        "taurospaldeaaqua": {
            "name": "Tauros-Paldea-Aqua",
            "baseSpecies": "Tauros",
            "forme": "Paldea-Aqua",
        },
        "sinistchaunremarkable": {
            "name": "Sinistcha-Unremarkable",
            "baseSpecies": "Sinistcha",
            "forme": "Unremarkable",
        },
        "basculegionf": {"name": "Basculegion-F", "baseSpecies": "Basculegion", "forme": "F"},
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
def test_convert_decklist_entry_uses_species_resolver(raw_name, expected_id, resolved_name):
    showdown_data = build_test_showdown_data()
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
