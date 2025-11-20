from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import difflib
import json5
import requests

LOGGER = logging.getLogger(__name__)

SHOWDOWN_BASE = "https://play.pokemonshowdown.com/data"


# Normalization helpers
DESCRIPTOR_TOKEN_MAP = {
    "shadow": "Shadow",
    "ice": "Ice",
    "therian": "Therian",
    "attack": "Attack",
    "defense": "Defense",
    "speed": "Speed",
    "midnight": "Midnight",
    "midday": "Midday",
    "dusk": "Dusk",
    "dawn": "Dawn",
    "wings": "Wings",
    "mane": "Mane",
    "origin": "Origin",
    "hero": "Hero",
    "aqua": "Aqua",
    "blaze": "Blaze",
    "sky": "Sky",
    "paldean": "Paldea",
    "paldea": "Paldea",
    "galarian": "Galar",
    "galar": "Galar",
    "alolan": "Alola",
    "alola": "Alola",
    "hisuian": "Hisui",
    "hisui": "Hisui",
    "female": "F",
    "male": "M",
    "unremarkable": "Unremarkable",
    "family": "Family",
    "breed": "",
    "style": "",
    "form": "",
    "forme": "",
    "aspect": "",
    "rider": "",
    "of": "",
    "the": "",
    "single": "Single",
    "strike": "Strike",
    "standard": "",
    "incarnate": "",
}

MOVE_STRIP_WORDS = {
    "singles",
    "doubles",
    "triples",
    "battle",
    "mode",
    "form",
    "forms",
}

CATEGORY_TAGS = {
    "Restricted Legendary",
    "Sub-Legendary",
    "Mythical",
    "Paradox",
    "Ultra Beast",
}


def to_id(value: str) -> str:
    """Convert a string to a Showdown-style ID (lowercase, alphanumeric only)."""
    normalized = (
        unicodedata.normalize("NFKD", value)
        .replace("'", "'")
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return re.sub(r"[^A-Za-z0-9]+", "", normalized).lower()


def normalize_move_label(label: str) -> str:
    """Normalize a move label for matching against move IDs."""
    text = unicodedata.normalize("NFKD", label).lower()
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[\"'\[\]\(\)\-_'`]", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [token for token in text.split() if token and token not in MOVE_STRIP_WORDS]
    if not tokens:
        return ""
    return "".join(tokens)


def normalize_species_label(label: str) -> str:
    """Normalize a species label with brackets to Showdown format (e.g., 'Slowbro [Galarian Form]' -> 'Slowbro-Galar')."""
    label = label.strip()
    if "[" not in label or "]" not in label:
        return label

    base, descriptor = label.split("[", 1)
    descriptor = descriptor.rstrip("]").strip()
    if not descriptor:
        return base.strip()

    # Handle special cases like "Maushold [Family of Four]" / "Maushold [Family of Three]"
    if "maushold" in base.lower():
        if "four" in descriptor.lower():
            return "Maushold-Four"
        elif "three" in descriptor.lower():
            return "Maushold"
        # Default to Family form if no specific number
        if "family" in descriptor.lower():
            return "Maushold-Four"

    # Handle special cases like "Tauros [Paldean Form - Aqua Breed]"
    if "tauros" in label.lower() and "paldean" in descriptor.lower():
        if "aqua" in descriptor.lower():
            return "Tauros-Paldea-Aqua"
        elif "blaze" in descriptor.lower():
            return "Tauros-Paldea-Blaze"
        elif "combat" in descriptor.lower():
            return "Tauros-Paldea-Combat"

    tokens = [token for token in re.split(r"[^\w]+", descriptor.lower()) if token]
    mapped: List[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        replacement = DESCRIPTOR_TOKEN_MAP.get(token)
        if replacement is None:
            mapped.append(token.capitalize())
        elif replacement:
            # Combine Dawn + Wings, etc.
            if replacement in {"Dawn", "Dusk"} and idx + 1 < len(tokens):
                next_token = DESCRIPTOR_TOKEN_MAP.get(tokens[idx + 1], tokens[idx + 1].capitalize())
                if next_token in {"Wings", "Mane"}:
                    mapped.append(f"{replacement}-{next_token}")
                    idx += 1
                else:
                    mapped.append(replacement)
            else:
                mapped.append(replacement)
        idx += 1

    if not mapped:
        return base.strip()
    suffix = "-".join(mapped)
    return f"{base.strip()}-{suffix}"


@dataclass
class ShowdownData:
    """Internal data structure for Showdown datasets. Used by ShowdownManager."""

    pokedex: Dict[str, Dict[str, Any]]
    moves: Dict[str, Dict[str, Any]]
    items: Dict[str, Dict[str, Any]]
    abilities: Dict[str, Dict[str, Any]]
    learnsets: Dict[str, Dict[str, Any]]
    formats_data: Dict[str, Dict[str, Any]]
    formats: List[Dict[str, Any]]
    species_alias_map: Dict[str, str]
    move_alias_map: Dict[str, str]
    item_alias_map: Dict[str, str]
    ability_alias_map: Dict[str, str]

    @classmethod
    def from_payloads(
        cls,
        *,
        pokedex: Dict[str, Dict[str, Any]],
        moves: Dict[str, Dict[str, Any]],
        items: Dict[str, Dict[str, Any]],
        abilities: Dict[str, Dict[str, Any]],
        learnsets: Dict[str, Dict[str, Any]],
        formats_data: Dict[str, Dict[str, Any]],
        formats: List[Dict[str, Any]],
    ) -> "ShowdownData":
        """Create ShowdownData from raw payloads, building alias maps."""
        species_alias_map = cls._build_species_alias_map(pokedex)
        move_alias_map = cls._build_simple_alias_map(moves, normalizer=normalize_move_label)
        item_alias_map = cls._build_simple_alias_map(items)
        ability_alias_map = cls._build_simple_alias_map(abilities)
        for fmt in formats:
            fmt.setdefault("id", to_id(fmt.get("name", "")))
        return cls(
            pokedex,
            moves,
            items,
            abilities,
            learnsets,
            formats_data,
            formats,
            species_alias_map,
            move_alias_map,
            item_alias_map,
            ability_alias_map,
        )

    @staticmethod
    def _build_species_alias_map(pokedex: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        """Build alias map from pokedex entries."""
        alias_map: Dict[str, str] = {}
        # First pass: process base forms (entries without baseSpecies or entries that are their own base)
        base_entries = {}
        forme_entries = {}
        for sid, entry in pokedex.items():
            base = entry.get("baseSpecies")
            # Entry is a base form if it has no baseSpecies or baseSpecies == sid (itself)
            if not base or base == sid or to_id(base) == sid:
                base_entries[sid] = entry
            else:
                forme_entries[sid] = entry
        
        # Process base forms first
        for sid, entry in base_entries.items():
            names = {entry.get("name", ""), sid}
            base = entry.get("baseSpecies")
            forme = entry.get("forme")
            if base:
                names.add(base)
            if base and forme:
                names.add(f"{base}-{forme}")
                names.add(f"{base} {forme}")
                names.add(f"{base} [{forme}]")
                names.add(f"{base} ({forme})")
                if forme.lower() == "shadow":
                    names.add(f"{base} Shadow Rider")
                if forme.lower() == "ice":
                    names.add(f"{base} Ice Rider")
            other = entry.get("otherFormes") or entry.get("formeOrder") or []
            if isinstance(other, list):
                names.update(other)
            for candidate in names:
                token = to_id(candidate)
                if token and token not in alias_map:
                    alias_map[token] = sid
        
        # Then process formes (they won't overwrite base form mappings)
        for sid, entry in forme_entries.items():
            names = {entry.get("name", ""), sid}
            base = entry.get("baseSpecies")
            forme = entry.get("forme")
            if base:
                names.add(base)
            if base and forme:
                names.add(f"{base}-{forme}")
                names.add(f"{base} {forme}")
                names.add(f"{base} [{forme}]")
                names.add(f"{base} ({forme})")
                if forme.lower() == "shadow":
                    names.add(f"{base} Shadow Rider")
                if forme.lower() == "ice":
                    names.add(f"{base} Ice Rider")
            other = entry.get("otherFormes") or entry.get("formeOrder") or []
            if isinstance(other, list):
                names.update(other)
            for candidate in names:
                token = to_id(candidate)
                if token and token not in alias_map:
                    alias_map[token] = sid
        return alias_map

    @staticmethod
    def _build_simple_alias_map(
        entries: Dict[str, Dict[str, Any]], *, normalizer: Callable[[str], str] = to_id
    ) -> Dict[str, str]:
        """Build alias map from entries (moves, items, abilities)."""
        alias_map: Dict[str, str] = {}
        for entry_id, entry in entries.items():
            names = {entry_id}
            if isinstance(entry, dict):
                base_name = entry.get("name")
                if isinstance(base_name, str):
                    names.add(base_name)
                aliases = entry.get("aliases")
                if isinstance(aliases, list):
                    names.update(filter(None, map(str, aliases)))
            for name in names:
                token = normalizer(name)
                if token and token not in alias_map:
                    alias_map[token] = entry_id
        return alias_map


class ShowdownManager:
    """Manages Showdown data downloads, caching, and resolution."""

    def __init__(self, data_dir: Path | str = "./data/showdown") -> None:
        """Initialize ShowdownManager with optional data directory."""
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, Any] = {}
        self._showdown_data: Optional[ShowdownData] = None
        self._file_map = self._build_file_map()

    def _build_file_map(self) -> Dict[str, Dict[str, Any]]:
        """Build file metadata map."""
        return {
            "pokedex": {
                "url": f"{SHOWDOWN_BASE}/pokedex.json",
                "path": self.data_dir / "pokedex.json",
                "parser": "json",
            },
            "moves": {
                "url": f"{SHOWDOWN_BASE}/moves.json",
                "path": self.data_dir / "moves.json",
                "parser": "json",
            },
            "items": {
                "url": f"{SHOWDOWN_BASE}/text/items.json5",
                "path": self.data_dir / "items.json5",
                "parser": "json5",
            },
            "abilities": {
                "url": f"{SHOWDOWN_BASE}/text/abilities.json5",
                "path": self.data_dir / "abilities.json5",
                "parser": "json5",
            },
            "learnsets": {
                "url": f"{SHOWDOWN_BASE}/learnsets.json",
                "path": self.data_dir / "learnsets.json",
                "parser": "json",
            },
            "formats_data": {
                "url": f"{SHOWDOWN_BASE}/formats-data.js",
                "path": self.data_dir / "formats-data.js",
                "parser": "formats_data",
            },
            "formats": {
                "url": f"{SHOWDOWN_BASE}/formats.js",
                "path": self.data_dir / "formats.js",
                "parser": "formats",
            },
        }

    # Download / Update methods
    def download_or_update_all(self, debug: bool = False) -> None:
        """Download or update all Showdown datasets to local files."""
        session = requests.Session()
        for name, meta in self._file_map.items():
            try:
                self._download_file(session, name, meta, debug=debug)
            except Exception as exc:
                LOGGER.error("Failed to download/update %s: %s", name, exc)
                raise

    def _download_file(
        self, session: requests.Session, name: str, meta: Dict[str, Any], *, debug: bool = False
    ) -> Path:
        """Download a single file with ETag caching."""
        local_path: Path = meta["path"]
        remote_url: str = meta["url"]
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_etag = self._read_etag(local_path)
        remote_etag = None

        if local_path.exists():
            try:
                head = session.head(remote_url, timeout=30)
                head.raise_for_status()
                remote_etag = head.headers.get("ETag")
                if remote_etag and local_etag and remote_etag == local_etag:
                    if debug:
                        LOGGER.debug("%s already cached (ETag %s).", name, remote_etag)
                    return local_path
            except requests.RequestException as exc:
                if debug:
                    LOGGER.warning("HEAD failed for %s (%s); retrying with GET.", name, exc)

        headers = {}
        if local_etag:
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
            self._write_etag(local_path, new_etag)
        if debug:
            LOGGER.debug("Downloaded %s (%d bytes).", name, len(response.content))
        return local_path

    def _etag_path(self, local_path: Path) -> Path:
        """Get ETag file path for a data file."""
        return local_path.with_suffix(local_path.suffix + ".etag")

    def _read_etag(self, local_path: Path) -> Optional[str]:
        """Read ETag from file if it exists."""
        etag_file = self._etag_path(local_path)
        if etag_file.exists():
            return etag_file.read_text().strip()
        return None

    def _write_etag(self, local_path: Path, value: str) -> None:
        """Write ETag to file."""
        self._etag_path(local_path).write_text(value.strip())

    # Loader / Accessor methods
    def load_pokedex(self) -> Dict[str, Any]:
        """Load pokedex.json, caching in memory."""
        if "pokedex" not in self._cache:
            self._cache["pokedex"] = self._parse_json(self._file_map["pokedex"]["path"])
        return self._cache["pokedex"]

    def load_moves(self) -> Dict[str, Any]:
        """Load moves.json, caching in memory."""
        if "moves" not in self._cache:
            self._cache["moves"] = self._parse_json(self._file_map["moves"]["path"])
        return self._cache["moves"]

    def load_items(self) -> Dict[str, Any]:
        """Load items.json5, caching in memory."""
        if "items" not in self._cache:
            self._cache["items"] = self._parse_json5(self._file_map["items"]["path"])
        return self._cache["items"]

    def load_abilities(self) -> Dict[str, Any]:
        """Load abilities.json5, caching in memory."""
        if "abilities" not in self._cache:
            self._cache["abilities"] = self._parse_json5(self._file_map["abilities"]["path"])
        return self._cache["abilities"]

    def load_learnsets(self) -> Dict[str, Any]:
        """Load learnsets.json, caching in memory."""
        if "learnsets" not in self._cache:
            self._cache["learnsets"] = self._parse_json(self._file_map["learnsets"]["path"])
        return self._cache["learnsets"]

    def load_formats_data(self) -> Dict[str, Any]:
        """Load formats-data.js, caching in memory."""
        if "formats_data" not in self._cache:
            self._cache["formats_data"] = self._parse_formats_data_payload(
                self._file_map["formats_data"]["path"]
            )
        return self._cache["formats_data"]

    def load_formats(self) -> List[Dict[str, Any]]:
        """Load formats.js, caching in memory."""
        if "formats" not in self._cache:
            self._cache["formats"] = self._parse_formats_payload(self._file_map["formats"]["path"])
        return self._cache["formats"]

    def _get_showdown_data(self) -> ShowdownData:
        """Get or create ShowdownData instance with all datasets loaded."""
        if self._showdown_data is None:
            self._showdown_data = ShowdownData.from_payloads(
                pokedex=self.load_pokedex(),
                moves=self.load_moves(),
                items=self.load_items(),
                abilities=self.load_abilities(),
                learnsets=self.load_learnsets(),
                formats_data=self.load_formats_data(),
                formats=self.load_formats(),
            )
        return self._showdown_data

    # Parser helpers
    def _parse_json(self, path: Path) -> Any:
        """Parse JSON file."""
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _parse_json5(self, path: Path) -> Any:
        """Parse JSON5 file."""
        with path.open("r", encoding="utf-8") as handle:
            return json5.load(handle)

    _JS_OBJECT_PATTERN = re.compile(r"=\s*([\[{].*[\]}])\s*;?\s*$", re.S)

    def _parse_js_json(self, filepath: Path) -> Any:
        """Parse JavaScript file containing JSON object."""
        text = filepath.read_text(encoding="utf-8")
        match = self._JS_OBJECT_PATTERN.search(text)
        if not match:
            raise ValueError(f"Unable to parse JS payload in {filepath}")
        payload = match.group(1)
        return json5.loads(payload)

    def _parse_formats_payload(self, path: Path) -> Any:
        """Parse formats.js payload."""
        data = self._parse_js_json(path)
        if isinstance(data, list):
            return data
        raise ValueError(f"Unexpected formats payload structure in {path}")

    def _parse_formats_data_payload(self, path: Path) -> Any:
        """Parse formats-data.js payload."""
        data = self._parse_js_json(path)
        if isinstance(data, dict):
            return data
        raise ValueError(f"Unexpected formats-data payload structure in {path}")

    # Normalization helpers
    def normalize_species_label(self, label: str) -> str:
        """Normalize species label (delegate to module function for consistency)."""
        return normalize_species_label(label)

    def normalize_move_label(self, label: str) -> str:
        """Normalize move label (delegate to module function for consistency)."""
        return normalize_move_label(label)

    # Species resolver
    def resolve_species_id(self, raw_label: str) -> Optional[str]:
        """Resolve raw species label to canonical Showdown species ID."""
        data = self._get_showdown_data()
        normalized = normalize_species_label(raw_label)
        token = to_id(normalized)
        if token in data.species_alias_map:
            return data.species_alias_map[token]
        fallback = to_id(raw_label)
        return data.species_alias_map.get(fallback)

    # Move resolver
    def resolve_move_id(self, raw_label: str) -> Optional[str]:
        """Resolve raw move label to canonical move ID."""
        data = self._get_showdown_data()
        token = normalize_move_label(raw_label)
        if token in data.move_alias_map:
            return data.move_alias_map[token]
        fallback = to_id(raw_label)
        if fallback in data.move_alias_map:
            return data.move_alias_map[fallback]
        if data.move_alias_map:
            matches = difflib.get_close_matches(token, data.move_alias_map.keys(), n=1, cutoff=0.72)
            if matches:
                return data.move_alias_map[matches[0]]
        return None

    # Item/Ability resolvers
    def resolve_item_id(self, name: str) -> Optional[str]:
        """Resolve item name to canonical item ID."""
        data = self._get_showdown_data()
        token = to_id(name)
        return data.item_alias_map.get(token)

    def resolve_ability_id(self, name: str) -> Optional[str]:
        """Resolve ability name to canonical ability ID."""
        data = self._get_showdown_data()
        token = to_id(name)
        return data.ability_alias_map.get(token)

    # Learnset / legality helpers
    def can_species_learn_move(self, species_id: str, move_id: str) -> bool:
        """Check if a species can learn a move."""
        data = self._get_showdown_data()
        learnset = data.learnsets.get(species_id, {}).get("learnset", {})
        if move_id in learnset:
            return True
        # Check base species if this is a forme
        base_species = data.pokedex.get(species_id, {}).get("baseSpecies")
        if base_species:
            base_id = data.species_alias_map.get(to_id(base_species))
            if base_id:
                learnset = data.learnsets.get(base_id, {}).get("learnset", {})
                return move_id in learnset
        return False

    def determine_valid_formats(
        self, species_id: str, *, restrict_to_vgc: bool = True
    ) -> List[str]:
        """Determine valid formats for a species."""
        data = self._get_showdown_data()
        entry = data.pokedex.get(species_id, {})
        formats_meta = data.formats_data.get(species_id, {})
        if entry.get("isNonstandard") in {"Past", "Future", "Unobtainable"}:
            return []
        if formats_meta.get("isNonstandard") in {"Past", "Future", "Unobtainable"}:
            return []
        tags = set(entry.get("tags") or [])
        valid: List[str] = []
        for fmt in data.formats:
            if restrict_to_vgc and "vgc" not in fmt.get("name", "").lower():
                continue
            fmt_id = fmt.get("id") or to_id(fmt.get("name", ""))
            if not fmt_id:
                continue
            if fmt.get("gameType") and fmt["gameType"] != "doubles":
                continue
            banlist_entries = fmt.get("banlist", [])
            banlist = {to_id(item) for item in banlist_entries}
            if species_id in banlist:
                continue
            banned = False
            for tag_entry in banlist_entries:
                if tag_entry in CATEGORY_TAGS and tag_entry in tags:
                    banned = True
                    break
            if banned:
                continue
            valid.append(fmt_id)
        return sorted(valid)

    # Properties for backward compatibility (if needed)
    @property
    def pokedex(self) -> Dict[str, Dict[str, Any]]:
        """Access pokedex data."""
        return self.load_pokedex()

    @property
    def moves(self) -> Dict[str, Dict[str, Any]]:
        """Access moves data."""
        return self.load_moves()

    @property
    def items(self) -> Dict[str, Dict[str, Any]]:
        """Access items data."""
        return self.load_items()

    @property
    def abilities(self) -> Dict[str, Dict[str, Any]]:
        """Access abilities data."""
        return self.load_abilities()

    @property
    def learnsets(self) -> Dict[str, Dict[str, Any]]:
        """Access learnsets data."""
        return self.load_learnsets()

    @property
    def formats_data(self) -> Dict[str, Dict[str, Any]]:
        """Access formats_data."""
        return self.load_formats_data()

    @property
    def formats(self) -> List[Dict[str, Any]]:
        """Access formats list."""
        return self.load_formats()


__all__ = [
    "ShowdownManager",
    "ShowdownData",
    "normalize_species_label",
    "to_id",
]
