from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

LOGGER = logging.getLogger(__name__)

POKEDEX_URL = "https://play.pokemonshowdown.com/data/pokedex.json"
DATA_DIR = Path("data")
LOCAL_POKEDEX_PATH = DATA_DIR / "pokedex.json"
LOCAL_ETAG_PATH = LOCAL_POKEDEX_PATH.with_suffix(".etag")
HTTP_TIMEOUT = 30

_POKEDEX_CACHE: Optional[Dict[str, Dict[str, object]]] = None
_ALIAS_CACHE: Optional[Dict[str, str]] = None


def to_id(value: str) -> str:
    """Replicate Pokémon Showdown's toID helper."""

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


def update_pokedex_json(force: bool = False, debug: bool = False) -> Path:
    """
    Download the current pokedex.json from Pokémon Showdown.
    Save it to data/pokedex.json.
    If the file already exists and force=False, only download if server version
    differs (use HTTP HEAD or ETag check).
    Return the local file path.
    """

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    local_etag = _read_local_etag()
    remote_etag = None

    if not force and LOCAL_POKEDEX_PATH.exists():
        try:
            head_resp = session.head(POKEDEX_URL, timeout=HTTP_TIMEOUT)
            head_resp.raise_for_status()
            remote_etag = head_resp.headers.get("ETag")
            if remote_etag and local_etag and remote_etag == local_etag:
                if debug:
                    LOGGER.info("Local pokedex.json already up to date (ETag %s).", remote_etag)
                return LOCAL_POKEDEX_PATH
        except requests.RequestException as exc:
            if debug:
                LOGGER.warning("HEAD request failed (%s); falling back to unconditional GET.", exc)

    headers = {}
    if not force and local_etag:
        headers["If-None-Match"] = local_etag

    response = session.get(POKEDEX_URL, headers=headers, timeout=HTTP_TIMEOUT)
    if response.status_code == 304 and LOCAL_POKEDEX_PATH.exists():
        if debug:
            LOGGER.info("Server responded 304 Not Modified; keeping local copy.")
        return LOCAL_POKEDEX_PATH

    response.raise_for_status()
    LOCAL_POKEDEX_PATH.write_bytes(response.content)
    remote_etag = response.headers.get("ETag") or remote_etag
    if remote_etag:
        _write_local_etag(remote_etag)
    if debug:
        LOGGER.info("Downloaded pokedex.json (%s bytes).", len(response.content))
    return LOCAL_POKEDEX_PATH


def load_pokedex(force_download: bool = False, debug: bool = False) -> Dict[str, Dict[str, object]]:
    """Ensure the local pokedex.json exists and return it as a dict."""

    global _POKEDEX_CACHE
    if force_download or _POKEDEX_CACHE is None:
        path = update_pokedex_json(force=force_download, debug=debug)
        with path.open("r", encoding="utf-8") as handle:
            _POKEDEX_CACHE = json.load(handle)
        # Reset alias cache so subsequent resolutions use fresh data.
        global _ALIAS_CACHE
        _ALIAS_CACHE = None
    return _POKEDEX_CACHE


def _ensure_alias_map() -> Dict[str, str]:
    global _ALIAS_CACHE
    if _ALIAS_CACHE is None:
        pokedex = load_pokedex()
        _ALIAS_CACHE = _build_alias_map(pokedex)
    return _ALIAS_CACHE


def _build_alias_map(pokedex: Dict[str, Dict[str, object]]) -> Dict[str, str]:
    alias_map: Dict[str, str] = {}
    for species_id, entry in pokedex.items():
        names: set[str] = set()
        names.add(species_id)

        name = entry.get("name")
        if isinstance(name, str):
            names.add(name)

        base = entry.get("baseSpecies")
        forme = entry.get("forme")
        if isinstance(base, str):
            names.add(base)
            if isinstance(forme, str):
                names.add(f"{base}-{forme}")
                names.add(f"{base} {forme}")

        for key in ("otherFormes", "formeOrder", "cosmeticFormes", "aliases"):
            values = entry.get(key)
            if isinstance(values, list):
                names.update(filter(None, map(str, values)))

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
                alias_map[token] = species_id
    return alias_map


DESCRIPTOR_KEYWORDS = {
    "alola",
    "alolan",
    "galar",
    "galarian",
    "hisui",
    "hisuian",
    "paldea",
    "paldean",
    "form",
    "forme",
    "breed",
    "style",
    "aspect",
    "pattern",
    "family",
    "male",
    "female",
    "wash",
    "heat",
    "frost",
    "fan",
    "mow",
    "rotom",
    "midday",
    "midnight",
    "dusk",
    "amp",
    "amped",
    "lowkey",
    "droopy",
    "curly",
    "stretchy",
    "bloodmoon",
    "hero",
    "crowned",
    "sky",
    "wash",
    "fan",
    "frost",
    "mow",
    "aqua",
    "blaze",
    "combat",
    "aqua",
    "paldean",
    "family",
    "four",
    "three",
}

SKIP_WORDS = {"form", "forme", "mode", "style", "aspect", "pattern", "breed", "of", "the"}

FORM_WORD_MAP = {
    "alolan": "Alola",
    "alola": "Alola",
    "galarian": "Galar",
    "galar": "Galar",
    "hisuian": "Hisui",
    "hisui": "Hisui",
    "paldean": "Paldea",
    "paldea": "Paldea",
    "midday": "Midday",
    "midnight": "Midnight",
    "dusk": "Dusk",
    "dawn": "Dawn",
    "wash": "Wash",
    "heat": "Heat",
    "frost": "Frost",
    "fan": "Fan",
    "mow": "Mow",
    "amped": "Amped",
    "lowkey": "LowKey",
    "droopy": "Droopy",
    "curly": "Curly",
    "stretchy": "Stretchy",
    "bloodmoon": "Bloodmoon",
    "hero": "Hero",
    "crowned": "Crowned",
    "sky": "Sky",
    "aqua": "Aqua",
    "blaze": "Blaze",
    "combat": "Combat",
    "four": "Four",
    "three": "Three",
    "masterpiece": "Masterpiece",
    "unremarkable": "Unremarkable",
}

GENDERED_FORMS = {
    "indeedee": {"female": "-F", "male": ""},
    "meowstic": {"female": "-F", "male": ""},
    "oinkologne": {"female": "-F", "male": ""},
    "basculegion": {"female": "-F", "male": ""},
    "basculin": {"female": "-F", "male": ""},
    "pikachu": {"female": "-F", "male": ""},
}


def _looks_like_descriptor(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in DESCRIPTOR_KEYWORDS)


def _extract_base_and_descriptor(label: str) -> Tuple[str, str]:
    text = label.strip()
    for opener, closer in (("[", "]"), ("(", ")")):
        if opener in text and closer in text:
            base, _, trailing = text.partition(opener)
            descriptor, _, _ = trailing.partition(closer)
            return base.strip(), descriptor.strip()

    tokens = text.split()
    if len(tokens) > 1:
        for idx in range(len(tokens) - 1, 0, -1):
            candidate = " ".join(tokens[idx:])
            if _looks_like_descriptor(candidate):
                base = " ".join(tokens[:idx])
                return base.strip(), candidate.strip()
    return text, ""


def _apply_gender_form(base: str, descriptor: str) -> Optional[str]:
    base_token = to_id(base)
    lower_desc = descriptor.lower()
    mapping = GENDERED_FORMS.get(base_token)
    if not mapping:
        return None
    if "female" in lower_desc or "♀" in lower_desc:
        suffix = mapping.get("female")
    else:
        suffix = mapping.get("male", "")
    if suffix is None:
        suffix = ""
    return f"{base}{suffix}"


def _combine_descriptor(base: str, descriptor: str) -> str:
    descriptor = descriptor.replace("–", "-")
    descriptor = descriptor.replace("’", "'")
    descriptor_lower = descriptor.lower()

    gendered = _apply_gender_form(base, descriptor_lower)
    if gendered:
        return gendered

    if "bloodmoon" in descriptor_lower:
        return f"{base}-Bloodmoon"

    if "paldea" in descriptor_lower or "paldean" in descriptor_lower:
        suffix = "Paldea"
        if "blaze" in descriptor_lower:
            suffix = "Paldea-Blaze"
        elif "aqua" in descriptor_lower:
            suffix = "Paldea-Aqua"
        elif "combat" in descriptor_lower:
            suffix = "Paldea-Combat"
        return f"{base}-{suffix}"

    words = [word for word in re.split(r"[^a-z0-9]+", descriptor_lower) if word]
    cleaned: List[str] = []
    base_token = to_id(base)
    for word in words:
        if word in SKIP_WORDS:
            continue
        if word == base_token:
            continue
        mapped = FORM_WORD_MAP.get(word, word.capitalize())
        if mapped:
            cleaned.append(mapped)

    if cleaned:
        suffix = "-".join(cleaned)
        return f"{base}-{suffix}"
    return base


def _normalize_species_label(label: str) -> str:
    base, descriptor = _extract_base_and_descriptor(label)
    if not descriptor:
        return base.strip()
    return _combine_descriptor(base.strip(), descriptor.strip())


def resolve_species_id(name: str, *, debug: bool = False, force_refresh: bool = False) -> Optional[str]:
    """
    Return the best matching canonical Pokémon Showdown species ID for the provided name.
    """

    if force_refresh:
        load_pokedex(force_download=True, debug=debug)
    alias_map = _ensure_alias_map()

    candidates = [
        _normalize_species_label(name),
        name,
    ]
    tokens = {to_id(candidate) for candidate in candidates if candidate}
    for token in tokens:
        if token in alias_map:
            return alias_map[token]

    # Try stripping descriptors entirely.
    base, _ = _extract_base_and_descriptor(name)
    stripped_token = to_id(base)
    if stripped_token in alias_map:
        return alias_map[stripped_token]

    # Fallback to fuzzy matching on alias tokens.
    matches = difflib.get_close_matches(next(iter(tokens), stripped_token), alias_map.keys(), n=1, cutoff=0.72)
    if matches:
        if debug:
            LOGGER.debug("Fuzzy matched '%s' -> '%s'", name, matches[0])
        return alias_map[matches[0]]
    if debug:
        LOGGER.debug("Unable to resolve species for '%s'", name)
    return None


__all__ = [
    "update_pokedex_json",
    "load_pokedex",
    "resolve_species_id",
]
