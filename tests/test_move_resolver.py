from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import move_resolver as mr  # noqa: E402


@pytest.fixture(autouse=True)
def sample_moves(monkeypatch):
    moves = {
        "hydropump": {"name": "Hydro Pump"},
        "shadowball": {"name": "Shadow Ball"},
        "icywind": {"name": "Icy Wind"},
        "fakeout": {"name": "Fake Out"},
        "popbomb": {"name": "Population Bomb"},
        "electroshot": {"name": "Electro Shot"},
    }
    monkeypatch.setattr(mr, "_MOVES_CACHE", moves)
    alias_map = mr._build_alias_map(moves)  # pylint: disable=protected-access
    monkeypatch.setattr(mr, "_ALIASES_CACHE", alias_map)
    yield
    monkeypatch.setattr(mr, "_MOVES_CACHE", None)
    monkeypatch.setattr(mr, "_ALIASES_CACHE", None)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Hydro Pump", "hydropump"),
        ("shadow ball", "shadowball"),
        ("Fake Out", "fakeout"),
    ],
)
def test_resolve_move_id_exact(query, expected):
    assert mr.resolve_move_id(query) == expected


def test_resolve_move_id_removes_parenthetical():
    assert mr.resolve_move_id("Icy Wind (Doubles)") == "icywind"


def test_resolve_move_id_fuzzy_match():
    assert mr.resolve_move_id("Hidro Pomp") == "hydropump"
    assert mr.resolve_move_id("Shdow Ball") == "shadowball"


def test_resolve_move_id_strips_mode_keywords():
    assert mr.resolve_move_id("Electro Shot (Singles)") == "electroshot"


def test_resolve_move_id_returns_none_for_unknown():
    assert mr.resolve_move_id("Totally Made Up Move") is None
