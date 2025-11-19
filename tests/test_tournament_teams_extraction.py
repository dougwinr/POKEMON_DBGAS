from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import tournament_teams_extraction as tte  # noqa: E402
from tournament_teams_extraction import (  # noqa: E402
    ShowdownData,
    build_showdown_team_string,
    convert_decklist_entry,
    normalize_species_label,
    parse_tournament_list,
    split_player_name,
)


@pytest.fixture()
def showdown_data() -> ShowdownData:
    pokedex = {
        "brutebonnet": {
            "name": "Brute Bonnet",
            "num": 986,
            "abilities": {"0": "Protosynthesis"},
            "tags": ["Paradox"],
        },
        "fluttermane": {
            "name": "Flutter Mane",
            "num": 987,
            "abilities": {"0": "Protosynthesis"},
            "tags": ["Paradox"],
        },
    }
    moves = {
        "shadowball": {"name": "Shadow Ball"},
        "moonblast": {"name": "Moonblast"},
    }
    items = {
        "choicescarf": {"name": "Choice Scarf"},
        "focussash": {"name": "Focus Sash"},
    }
    abilities = {
        "protosynthesis": {"name": "Protosynthesis"},
    }
    learnsets = {
        "brutebonnet": {
            "learnset": {
                "shadowball": ["9M"],
            }
        },
        "fluttermane": {
            "learnset": {
                "shadowball": ["9M"],
                "moonblast": ["9M"],
            }
        },
    }
    formats_data = {
        "brutebonnet": {},
        "fluttermane": {},
    }
    formats = [
        {
            "name": "[Gen 9] VGC 2025 Reg I",
            "gameType": "doubles",
            "banlist": ["Mythical"],
        },
        {
            "name": "[Gen 9] VGC 2025 Reg H",
            "gameType": "doubles",
            "banlist": ["Paradox"],
        },
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


def test_parse_tournament_list_handles_button_blocks():
    sample_html = """
    <div class="flex-parent jc-center">
        <button onclick="location.href='0001234/'" type="button">Sample Championship
         - January 1-2, 2025</button>
    </div>
    """
    tournaments = parse_tournament_list(sample_html)
    assert len(tournaments) == 1
    tournament = tournaments[0]
    assert tournament.tournament_id == "0001234"
    assert tournament.name == "Sample Championship"
    assert "January" in tournament.date_text


def test_split_player_name_extracts_country():
    name, country = split_player_name("Jane Doe [US]")
    assert name == "Jane Doe"
    assert country == "US"
    assert split_player_name("Solo Player") == ("Solo Player", None)


def test_build_showdown_team_string_formats_entries():
    pokemon = [
        convert_decklist_entry(
            {
                "name": "Brute Bonnet",
                "teratype": "Water",
                "ability": "Protosynthesis",
                "item": "Choice Scarf",
                "badges": ["Shadow Ball"],
            },
            showdown_data=ShowdownData.from_payloads(
                pokedex={
                    "brutebonnet": {
                        "name": "Brute Bonnet",
                        "abilities": {"0": "Protosynthesis"},
                    }
                },
                moves={"shadowball": {"name": "Shadow Ball"}},
                items={"choicescarf": {"name": "Choice Scarf"}},
                abilities={"protosynthesis": {"name": "Protosynthesis"}},
                learnsets={"brutebonnet": {"learnset": {"shadowball": ["9M"]}}},
                formats_data={"brutebonnet": {}},
                formats=[
                    {"name": "[Gen 9] VGC 2025 Reg I", "gameType": "doubles", "banlist": []}
                ],
            ),
        )
    ]
    team_string = build_showdown_team_string(pokemon)
    assert "Brute Bonnet @ Choice Scarf" in team_string
    assert "Ability: Protosynthesis" in team_string
    assert "- Shadow Ball" in team_string


def test_convert_decklist_entry_detects_illegal_move(showdown_data: ShowdownData):
    slot = {
        "name": "Flutter Mane",
        "teratype": "Stellar",
        "ability": "Protosynthesis",
        "item": "Focus Sash",
        "badges": ["Shadow Ball", "Moonblast", "Nonexistent Move"],
    }
    extraction = convert_decklist_entry(slot, showdown_data)
    # Moonblast is valid, Nonexistent Move should raise issue, and Paradox tag bans VGC Reg H.
    assert any("Nonexistent Move" in issue for issue in extraction.issues)
    assert extraction.valid_formats == ["gen9vgc2025regi"]
    assert extraction.moves[0].move_id == "shadowball"
    assert extraction.moves[0].is_legal
    assert extraction.moves[1].move_id == "moonblast"
    assert extraction.moves[1].is_legal
    assert extraction.moves[2].move_id is None
    assert not extraction.moves[2].is_legal


def test_normalize_species_label_bracket_variants():
    assert normalize_species_label("Calyrex [Shadow Rider]") == "Calyrex-Shadow"
    assert normalize_species_label("Landorus [Incarnate Forme]") == "Landorus"
