from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import showdown_data_manager as sdm  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, content_map, etag="test-etag"):
        self.content_map = content_map
        self.etag = etag

    def head(self, url, timeout=30):
        return FakeResponse(200, {"ETag": self.etag})

    def get(self, url, headers=None, timeout=30):
        content = self.content_map[url]
        return FakeResponse(200, {"ETag": self.etag}, content)


@pytest.fixture()
def sample_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "showdown"
    monkeypatch.setattr(sdm, "DATA_DIR", data_dir)
    monkeypatch.setattr(sdm, "SHOWDOWN_FILES", sdm._build_showdown_file_map(data_dir))
    monkeypatch.setattr(sdm, "_DATA_CACHE", {})
    monkeypatch.setattr(sdm, "_ALIAS_CACHE", {"species": None, "moves": None, "items": None, "abilities": None})
    monkeypatch.setattr(sdm, "_FORMAT_ID_MAP", None)

    pokedex = {
        "conkeldurr": {"name": "Conkeldurr"},
        "urshifusinglestrike": {"name": "Urshifu-Single-Strike"},
        "calyrexshadow": {"name": "Calyrex-Shadow"},
    }
    moves = {
        "shadowball": {"name": "Shadow Ball"},
        "wideguard": {"name": "Wide Guard"},
    }
    items = {"choicescarf": {"name": "Choice Scarf"}}
    abilities = {"guts": {"name": "Guts"}}
    learnsets = {
        "conkeldurr": {"learnset": {}},
        "calyrexshadow": {"learnset": {"shadowball": ["9M"]}},
    }
    formats_data = "exports.BattleFormatsData = {conkeldurr:{tier:\"OU\"},urshifusinglestrike:{tier:\"Uber\"}};"
    formats = json.dumps(
        [
            {
                "name": "[Gen 9] VGC",
                "id": "gen9vgc",
                "banlist": ["Urshifu-Single-Strike"],
            }
        ]
    )
    file_map = {
        f"{sdm.SHOWDOWN_BASE}/pokedex.json": json.dumps(pokedex).encode(),
        f"{sdm.SHOWDOWN_BASE}/moves.json": json.dumps(moves).encode(),
        f"{sdm.SHOWDOWN_BASE}/text/items.json5": json.dumps(items).encode(),
        f"{sdm.SHOWDOWN_BASE}/text/abilities.json5": json.dumps(abilities).encode(),
        f"{sdm.SHOWDOWN_BASE}/learnsets.json": json.dumps(learnsets).encode(),
        f"{sdm.SHOWDOWN_BASE}/formats-data.js": formats_data.encode(),
        f"{sdm.SHOWDOWN_BASE}/formats.js": f"exports.Formats = {formats};".encode(),
    }

    session = FakeSession(file_map)
    monkeypatch.setattr(sdm.requests, "Session", lambda: session)
    return file_map


def test_update_all_showdown_data_writes_files(sample_data):
    paths = sdm.update_all_showdown_data(force=True)
    assert set(paths) == set(sdm.SHOWDOWN_FILES)
    for name, path in paths.items():
        assert path.exists(), f"{name} missing"


def test_validation_interface(sample_data):
    sdm.update_all_showdown_data(force=True)

    assert sdm.validate_species("Conkeldurr")
    assert sdm.validate_move("Shadow Ball")
    assert sdm.validate_item("Choice Scarf")
    assert sdm.validate_ability("Guts")

    assert sdm.validate_species_move("Calyrex-Shadow", "Shadow Ball")
    assert not sdm.validate_species_move("Conkeldurr", "Wide Guard")

    assert sdm.validate_species_in_format("Conkeldurr", "[Gen 9] VGC")
    assert not sdm.validate_species_in_format("Urshifu-Single-Strike", "[Gen 9] VGC")
