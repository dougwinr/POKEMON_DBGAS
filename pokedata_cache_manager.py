from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

LOGGER = logging.getLogger(__name__)

POKEDATA_BASE = "https://www.pokedata.ovh/standingsVGC"
LOCAL_BASE = Path("data/pokedata")
REQUEST_TIMEOUT = 30


def _etag_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".etag")


def _read_etag(path: Path) -> Optional[str]:
    etag_file = _etag_path(path)
    if etag_file.exists():
        return etag_file.read_text().strip()
    return None


def _write_etag(path: Path, etag: str) -> None:
    _etag_path(path).write_text(etag.strip())


def _download_file(
    url: str,
    local_path: Path,
    *,
    force: bool = False,
    debug: bool = False,
    session: Optional[requests.Session] = None,
) -> Path:
    session = session or requests.Session()
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_etag = _read_etag(local_path)
    remote_etag = None

    if not force and local_path.exists():
        try:
            head_resp = session.head(url, timeout=REQUEST_TIMEOUT)
            head_resp.raise_for_status()
            remote_etag = head_resp.headers.get("ETag")
            if remote_etag and local_etag == remote_etag:
                if debug:
                    LOGGER.debug("Cache hit for %s (ETag %s).", url, remote_etag)
                return local_path
        except requests.RequestException as exc:
            if debug:
                LOGGER.warning("HEAD failed for %s (%s); falling back to GET.", url, exc)

    headers: Dict[str, str] = {}
    if not force and local_etag:
        headers["If-None-Match"] = local_etag

    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    if response.status_code == 304 and local_path.exists():
        if debug:
            LOGGER.debug("Server returned 304 for %s; keeping cached copy.", url)
        return local_path
    response.raise_for_status()
    local_path.write_bytes(response.content)
    new_etag = response.headers.get("ETag") or remote_etag
    if new_etag:
        _write_etag(local_path, new_etag)
    if debug:
        LOGGER.debug("Downloaded %s -> %s (%d bytes).", url, local_path, len(response.content))
    return local_path


def get_index_html(*, force: bool = False, debug: bool = False, session: Optional[requests.Session] = None) -> str:
    path = _download_file(
        f"{POKEDATA_BASE}/",
        LOCAL_BASE / "index.html",
        force=force,
        debug=debug,
        session=session,
    )
    return path.read_text(encoding="utf-8")


def get_tournament_html(
    tournament_id: str,
    *,
    force: bool = False,
    debug: bool = False,
    session: Optional[requests.Session] = None,
) -> str:
    path = _download_file(
        f"{POKEDATA_BASE}/{tournament_id}/",
        LOCAL_BASE / tournament_id / "index.html",
        force=force,
        debug=debug,
        session=session,
    )
    return path.read_text(encoding="utf-8")


def get_division_json(
    tournament_id: str,
    division: str,
    *,
    force: bool = False,
    debug: bool = False,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    division_slug = division.lower().strip("/")
    file_division = division_slug.capitalize()
    local_path = (
        LOCAL_BASE
        / tournament_id
        / division_slug
        / f"{tournament_id}_{file_division}.json"
    )
    url = f"{POKEDATA_BASE}/{tournament_id}/{division_slug}/{tournament_id}_{file_division}.json"
    path = _download_file(url, local_path, force=force, debug=debug, session=session)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError(f"Unexpected JSON payload for {tournament_id}/{division}")
        return data


__all__ = [
    "POKEDATA_BASE",
    "LOCAL_BASE",
    "get_index_html",
    "get_tournament_html",
    "get_division_json",
]
