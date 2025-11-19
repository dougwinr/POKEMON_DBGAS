from __future__ import annotations

import json
from pathlib import Path

import pytest

import pokedata_cache_manager as pcm


class FakeResponse:
    def __init__(self, status_code=200, headers=None, content=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, payload_map, log):
        self.payload_map = payload_map
        self.log = log

    def head(self, url, timeout=30):
        self.log.append(("head", url))
        return FakeResponse(200, {"ETag": "test-etag"})

    def get(self, url, headers=None, timeout=30):
        self.log.append(("get", url))
        content = self.payload_map[url]
        return FakeResponse(200, {"ETag": "test-etag"}, content)


@pytest.fixture()
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(pcm, "LOCAL_BASE", tmp_path / "pokedata")
    return tmp_path


def test_get_index_html_downloads_once(tmp_cache, monkeypatch):
    payloads = {
        f"{pcm.POKEDATA_BASE}/": b"<html>standings</html>",
    }
    log = []

    def fake_session():
        return FakeSession(payloads, log)

    monkeypatch.setattr(pcm.requests, "Session", fake_session)

    html = pcm.get_index_html(force=True, debug=True)
    assert "standings" in html
    html2 = pcm.get_index_html(force=False, debug=True)
    assert html2 == html
    # Expect HEAD + GET on first call, HEAD (cache hit) on second
    assert log.count(("get", f"{pcm.POKEDATA_BASE}/")) == 1


def test_get_division_json_writes_nested_structure(tmp_cache, monkeypatch):
    tournament_id = "0001000"
    division = "masters"
    url = f"{pcm.POKEDATA_BASE}/{tournament_id}/{division}/{tournament_id}_Masters.json"
    payloads = {
        f"{pcm.POKEDATA_BASE}/": b"",
        f"{pcm.POKEDATA_BASE}/{tournament_id}/": b"",
        url: json.dumps([{"name": "Player"}]).encode(),
    }
    log = []

    def fake_session():
        return FakeSession(payloads, log)

    monkeypatch.setattr(pcm.requests, "Session", fake_session)

    data = pcm.get_division_json(tournament_id, division, force=True)
    assert data[0]["name"] == "Player"
    cached_file = (
        pcm.LOCAL_BASE
        / tournament_id
        / division
        / f"{tournament_id}_Masters.json"
    )
    assert cached_file.exists()
