from __future__ import annotations

import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import json5
import requests


LOGGER = logging.getLogger(__name__)

SHOWDOWN_BASE = "https://play.pokemonshowdown.com/data"
DATA_DIR = Path("data/showdown")


def _build_showdown_file_map(base_dir: Path) -> Dict[str, Dict[str, Any]]:
    return {
        "pokedex": {
            "url": f"{SHOWDOWN_BASE}/pokedex.json",
            "path": base_dir / "pokedex.json",
            "parser": "json",
        },
        "moves": {
            "url": f"{SHOWDOWN_BASE}/moves.json",
            "path": base_dir / "moves.json",
            "parser": "json",
        },
        "items": {
            "url": f"{SHOWDOWN_BASE}/text/items.json5",
            "path": base_dir / "items.json5",
            "parser": "json5",
        },
        "abilities": {
            "url": f"{SHOWDOWN_BASE}/text/abilities.json5",
            "path": base_dir / "abilities.json5",
            "parser": "json5",
        },
        "learnsets": {
            "url": f"{SHOWDOWN_BASE}/learnsets.json",
            "path": base_dir / "learnsets.json",
            "parser": "json",
        },
        "formats_data": {
            "url": f"{SHOWDOWN_BASE}/formats-data.js",
            "path": base_dir / "formats-data.js",
            "parser": "formats_data",
        },
        "formats": {
            "url": f"{SHOWDOWN_BASE}/formats.js",
            "path": base_dir / "formats.js",
            "parser": "formats",
        },
    }


SHOWDOWN_FILES = _build_showdown_file_map(DATA_DIR)

_DATA_CACHE: Dict[str, Any] = {}
_ALIAS_CACHE: Dict[str, Optional[Dict[str, str]]] = {
    "species": None,
    "moves": None,
    "items": None,
    "abilities": None,
}
_FORMAT_ID_MAP: Optional[Dict[str, Dict[str, Any]]] = None


def to_id(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value)
        .replace("’", "'")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^A-Za-z0-9]+", "", normalized).lower()


def _etag_path(local_path: Path) -> Path:
    return local_path.with_suffix(local_path.suffix + ".etag")


def _read_etag(local_path: Path) -> Optional[str]:
    etag_file = _etag_path(local_path)
    if etag_file.exists():
        return etag_file.read_text().strip()
    return None


def _write_etag(local_path: Path, value: str) -> None:
    _etag_path(local_path).write_text(value.strip())


def _download_file(
    session: requests.Session,
    name: str,
    meta: Dict[str, Any],
    *,
    force: bool,
    debug: bool,
) -> Path:
    local_path: Path = meta["path"]
    remote_url: str = meta["url"]
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_etag = _read_etag(local_path)
    remote_etag = None

    if not force and local_path.exists():
        try:
            head = session.head(remote_url, timeout=30)
            head.raise_for_status()
            remote_etag = head.headers.get("ETag")
            if remote_etag and local_etag and remote_etag == local_etag:
                if debug:
                    LOGGER.debug("%s is up to date (ETag %s).", name, remote_etag)
                return local_path
        except requests.RequestException as exc:
            if debug:
                LOGGER.warning("HEAD failed for %s (%s); continuing with GET.", name, exc)

    headers = {}
    if not force and local_etag:
        headers["If-None-Match"] = local_etag

    response = session.get(remote_url, headers=headers, timeout=30)
    if response.status_code == 304 and local_path.exists():
        if debug:
            LOGGER.debug("Server returned 304 for %s; keeping local copy.", name)
        return local_path
    response.raise_for_status()
    local_path.write_bytes(response.content)
    new_etag = response.headers.get("ETag") or remote_etag
    if new_etag:
        _write_etag(local_path, new_etag)
    if debug:
        LOGGER.debug("Downloaded %s (%d bytes).", name, len(response.content))
    return local_path


def update_all_showdown_data(force: bool = False, debug: bool = False) -> Dict[str, Path]:
    """
    Downloads or updates all Pokémon Showdown data files.
    Creates data/showdown/ directory if missing.
    Returns a dict mapping data names → local paths.
    If force=False, only download when missing or remote ETag changed.
    """

    session = requests.Session()
    results: Dict[str, Path] = {}
    for name, meta in SHOWDOWN_FILES.items():
        results[name] = _download_file(session, name, meta, force=force, debug=debug)
    return results


def _parse_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_json5(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json5.load(handle)


JS_OBJECT_PATTERN = re.compile(r"=\s*([\[{].*[\]}])\s*;?\s*$", re.S)


def parse_js_json(filepath: Path) -> Any:
    """
    Read a .js file containing a JS object export (e.g. module.exports = {...};).
    Strip JS syntax, extract the {...}, and parse into Python dict.
    Must tolerate trailing commas.
    """

    text = filepath.read_text(encoding="utf-8")
    match = JS_OBJECT_PATTERN.search(text)
    if not match:
        raise ValueError(f"Unable to parse JS payload in {filepath}")
    payload = match.group(1)
    return json5.loads(payload)


def _parse_formats_payload(path: Path) -> Any:
    data = parse_js_json(path)
    if isinstance(data, list):
        return data
    raise ValueError(f"Unexpected formats payload structure in {path}")


def _parse_formats_data_payload(path: Path) -> Any:
    data = parse_js_json(path)
    if isinstance(data, dict):
        return data
    raise ValueError(f"Unexpected formats-data payload structure in {path}")


def _load_dataset(name: str, parser: Callable[[Path], Any], *, force: bool, debug: bool) -> Any:
    if force or name not in _DATA_CACHE:
        meta = SHOWDOWN_FILES[name]
        path = meta["path"]
        if not path.exists() or force:
            _download_file(requests.Session(), name, meta, force=True, debug=debug)
        _DATA_CACHE[name] = parser(path)
        if name in _ALIAS_CACHE:
            _ALIAS_CACHE[name] = None
        if name == "formats":
            global _FORMAT_ID_MAP
            _FORMAT_ID_MAP = None
    return _DATA_CACHE[name]


def load_pokedex_data(force: bool = False, debug: bool = False) -> Dict[str, Any]:
    return _load_dataset("pokedex", _parse_json, force=force, debug=debug)


def load_moves_data(force: bool = False, debug: bool = False) -> Dict[str, Any]:
    return _load_dataset("moves", _parse_json, force=force, debug=debug)


def load_items_data(force: bool = False, debug: bool = False) -> Dict[str, Any]:
    return _load_dataset("items", _parse_json5, force=force, debug=debug)


def load_abilities_data(force: bool = False, debug: bool = False) -> Dict[str, Any]:
    return _load_dataset("abilities", _parse_json5, force=force, debug=debug)


def load_learnsets_data(force: bool = False, debug: bool = False) -> Dict[str, Any]:
    return _load_dataset("learnsets", _parse_json, force=force, debug=debug)


def load_formats_data(force: bool = False, debug: bool = False) -> Dict[str, Any]:
    return _load_dataset("formats_data", _parse_formats_data_payload, force=force, debug=debug)


def load_formats_list(force: bool = False, debug: bool = False) -> List[Dict[str, Any]]:
    return _load_dataset("formats", _parse_formats_payload, force=force, debug=debug)


def _get_alias_map(kind: str) -> Dict[str, str]:
    if _ALIAS_CACHE[kind]:
        return _ALIAS_CACHE[kind] or {}

    alias_map: Dict[str, str] = {}

    if kind == "species":
        pokedex = load_pokedex_data()
        for species_id, entry in pokedex.items():
            names = {species_id}
            if isinstance(entry, dict):
                for key in ("name", "baseSpecies"):
                    value = entry.get(key)
                    if isinstance(value, str):
                        names.add(value)
                forme = entry.get("forme")
                base = entry.get("baseSpecies")
                if isinstance(base, str) and isinstance(forme, str):
                    names.add(f"{base}-{forme}")
                    names.add(f"{base} {forme}")
            for candidate in names:
                token = to_id(candidate)
                if token and token not in alias_map:
                    alias_map[token] = species_id
    elif kind in {"moves", "items", "abilities"}:
        loader = {
            "moves": load_moves_data,
            "items": load_items_data,
            "abilities": load_abilities_data,
        }[kind]
        data = loader()
        for key, entry in data.items():
            names = {key}
            if isinstance(entry, dict):
                name_value = entry.get("name")
                if isinstance(name_value, str):
                    names.add(name_value)
                aliases = entry.get("aliases")
                if isinstance(aliases, list):
                    names.update(filter(None, map(str, aliases)))
            for candidate in names:
                token = to_id(candidate)
                if token and token not in alias_map:
                    alias_map[token] = key
    _ALIAS_CACHE[kind] = alias_map
    return alias_map


def resolve_species_id(name: str) -> Optional[str]:
    alias_map = _get_alias_map("species")
    return alias_map.get(to_id(name))


def resolve_move_id(name: str) -> Optional[str]:
    alias_map = _get_alias_map("moves")
    return alias_map.get(to_id(name))


def resolve_item_id(name: str) -> Optional[str]:
    alias_map = _get_alias_map("items")
    return alias_map.get(to_id(name))


def resolve_ability_id(name: str) -> Optional[str]:
    alias_map = _get_alias_map("abilities")
    return alias_map.get(to_id(name))


def validate_species(name: str) -> bool:
    return resolve_species_id(name) is not None


def validate_move(name: str) -> bool:
    return resolve_move_id(name) is not None


def validate_item(name: str) -> bool:
    return resolve_item_id(name) is not None


def validate_ability(name: str) -> bool:
    return resolve_ability_id(name) is not None


def validate_species_move(species_name: str, move_name: str) -> bool:
    species_id = resolve_species_id(species_name)
    move_id = resolve_move_id(move_name)
    if not species_id or not move_id:
        return False
    learnsets = load_learnsets_data()
    entry = learnsets.get(species_id, {})
    learnset = entry.get("learnset", {})
    return move_id in learnset


def _get_format_entry(format_name: str) -> Optional[Dict[str, Any]]:
    global _FORMAT_ID_MAP
    if _FORMAT_ID_MAP is None:
        formats = load_formats_list()
        _FORMAT_ID_MAP = {}
        for fmt in formats:
            fmt_id = fmt.get("id") or to_id(fmt.get("name", ""))
            if fmt_id:
                _FORMAT_ID_MAP[fmt_id] = fmt
    return _FORMAT_ID_MAP.get(to_id(format_name))


def validate_species_in_format(species_name: str, format_name: str) -> bool:
    species_id = resolve_species_id(species_name)
    if not species_id:
        return False
    fmt_entry = _get_format_entry(format_name)
    if not fmt_entry:
        return False
    banlist = {to_id(item) for item in fmt_entry.get("banlist", [])}
    if species_id in banlist:
        return False
    formats_meta = load_formats_data()
    species_meta = formats_meta.get(species_id, {})
    if species_meta.get("isNonstandard") in {"Past", "Illegal"}:
        return False
    return True


__all__ = [
    "update_all_showdown_data",
    "load_pokedex_data",
    "load_moves_data",
    "load_items_data",
    "load_abilities_data",
    "load_learnsets_data",
    "load_formats_data",
    "load_formats_list",
    "validate_species",
    "validate_move",
    "validate_item",
    "validate_ability",
    "validate_species_move",
    "validate_species_in_format",
    "resolve_species_id",
    "resolve_move_id",
    "resolve_item_id",
    "resolve_ability_id",
    "_build_showdown_file_map",
]
