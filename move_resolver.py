from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

import requests

LOGGER = logging.getLogger(__name__)

MOVES_URL = "https://play.pokemonshowdown.com/data/moves.json"
DATA_DIR = Path("data")
LOCAL_MOVES_PATH = DATA_DIR / "moves.json"
LOCAL_ETAG_PATH = LOCAL_MOVES_PATH.with_suffix(".etag")
HTTP_TIMEOUT = 30

_MOVES_CACHE: Optional[Dict[str, Dict[str, object]]] = None
_ALIASES_CACHE: Optional[Dict[str, str]] = None


def to_id(value: str) -> str:
    """Mimic Pokémon Showdown's toID helper."""

    normalized = (
        unicodedata.normalize("NFKD", value)
        .replace("’", "'")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^A-Za-z0-9]+", "", normalized).lower()


def _read_local_etag() -> Optional[str]:
    try:
        return LOCAL_ETAG_PATH.read_text().strip()
    except FileNotFoundError:
        return None


def _write_local_etag(value: str) -> None:
    LOCAL_ETAG_PATH.write_text(value.strip())


def update_moves_json(force: bool = False, debug: bool = False) -> Path:
    """
    Download the current moves.json from Pokémon Showdown and save it to data/moves.json.
    If the file already exists and force=False, only download if the remote version differs
    (use ETag or HEAD checking if available; otherwise always re-download).
    Return the local Path.
    """

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    local_etag = _read_local_etag()
    remote_etag = None

    if not force and LOCAL_MOVES_PATH.exists():
        try:
            head_resp = session.head(MOVES_URL, timeout=HTTP_TIMEOUT)
            head_resp.raise_for_status()
            remote_etag = head_resp.headers.get("ETag")
            if remote_etag and local_etag and remote_etag == local_etag:
                if debug:
                    LOGGER.info("Local moves.json already up to date (ETag %s).", remote_etag)
                return LOCAL_MOVES_PATH
        except requests.RequestException as exc:
            if debug:
                LOGGER.warning("HEAD request failed (%s); falling back to unconditional GET.", exc)

    headers = {}
    if not force and local_etag:
        headers["If-None-Match"] = local_etag

    response = session.get(MOVES_URL, headers=headers, timeout=HTTP_TIMEOUT)
    if response.status_code == 304 and LOCAL_MOVES_PATH.exists():
        if debug:
            LOGGER.info("Server responded 304 Not Modified; keeping local copy.")
        return LOCAL_MOVES_PATH

    response.raise_for_status()
    LOCAL_MOVES_PATH.write_bytes(response.content)
    remote_etag = response.headers.get("ETag") or remote_etag
    if remote_etag:
        _write_local_etag(remote_etag)
    if debug:
        LOGGER.info("Downloaded moves.json (%s bytes).", len(response.content))
    return LOCAL_MOVES_PATH


def load_moves(force_download: bool = False, debug: bool = False) -> Dict[str, Dict[str, object]]:
    global _MOVES_CACHE
    if force_download or _MOVES_CACHE is None:
        path = update_moves_json(force=force_download, debug=debug)
        with path.open("r", encoding="utf-8") as handle:
            _MOVES_CACHE = json.load(handle)
        global _ALIASES_CACHE
        _ALIASES_CACHE = None
    return _MOVES_CACHE


def _ensure_alias_map() -> Dict[str, str]:
    global _ALIASES_CACHE
    if _ALIASES_CACHE is None:
        moves = load_moves()
        _ALIASES_CACHE = _build_alias_map(moves)
    return _ALIASES_CACHE


def _build_alias_map(moves: Dict[str, Dict[str, object]]) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    for move_id, entry in moves.items():
        names: set[str] = set()
        names.add(move_id)

        if isinstance(entry.get("name"), str):
            names.add(entry["name"])

        aliases = entry.get("aliases")
        if isinstance(aliases, list):
            names.update(filter(None, map(str, aliases)))

        short_desc = entry.get("shortDesc")
        if isinstance(short_desc, str):
            names.add(short_desc)

        expanded: set[str] = set()
        for candidate in names:
            if not candidate:
                continue
            expanded.add(candidate.replace("-", " "))
            expanded.add(candidate.replace(" ", ""))
        names.update(expanded)

        for candidate in names:
            token = to_id(candidate)
            if token and token not in alias_map:
                alias_map[token] = move_id
    return alias_map


PAREN_PATTERN = re.compile(r"\(.*?\)")
STRIP_WORDS = {
    "singles",
    "doubles",
    "triples",
    "battle",
    "mode",
    "form",
    "forms",
}


def normalize_move_name(name: str) -> str:
    """Produce a canonical Showdown-style ID for fuzzy comparisons."""

    text = unicodedata.normalize("NFKD", name).lower()
    text = PAREN_PATTERN.sub(" ", text)
    text = re.sub(r"[\"\[\]\(\)\-_'`]", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [
        token
        for token in text.split()
        if token and token not in STRIP_WORDS
    ]
    if not tokens:
        return ""
    return "".join(tokens)


def _normalize_move_input(name: str) -> List[str]:
    cleaned = name.strip()
    candidates = [cleaned]
    no_paren = PAREN_PATTERN.sub("", cleaned).strip()
    if no_paren and no_paren not in candidates:
        candidates.append(no_paren)
    tokens = cleaned.replace("-", " ").split()
    if len(tokens) > 1:
        without_last = " ".join(tokens[:-1]).strip()
        if without_last:
            candidates.append(without_last)
    normalized = list(dict.fromkeys([candidate.strip() for candidate in candidates if candidate.strip()]))
    return normalized


def resolve_move_id(
    name: str,
    *,
    debug: bool = False,
    force_refresh: bool = False,
) -> Optional[str]:
    """
    Return the best matching canonical Pokémon Showdown move ID for the provided name.
    """

    if force_refresh:
        load_moves(force_download=True, debug=debug)
    alias_map = _ensure_alias_map()

    normalized_inputs = _normalize_move_input(name)
    tokens = {normalize_move_name(candidate) for candidate in normalized_inputs if candidate}
    if not tokens:
        seed = normalize_move_name(name)
        if seed:
            tokens.add(seed)
    for token in tokens:
        if token in alias_map:
            return alias_map[token]

    seed = max(tokens, key=len) if tokens else normalize_move_name(name)
    if not seed:
        return None
    matches = difflib.get_close_matches(seed, alias_map.keys(), n=1, cutoff=0.72)
    if matches:
        if debug:
            LOGGER.debug("Fuzzy matched '%s' -> '%s'", name, matches[0])
        return alias_map[matches[0]]
    if debug:
        LOGGER.debug("Unable to resolve move for '%s'", name)
    return None


__all__ = [
    "update_moves_json",
    "load_moves",
    "resolve_move_id",
]
