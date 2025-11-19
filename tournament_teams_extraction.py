from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from functools import partial
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import json5
import requests
from tqdm.auto import tqdm

LOGGER = logging.getLogger(__name__)

POKEDATA_BASE = "https://www.pokedata.ovh/standingsVGC"
SHOWDOWN_BASE = "https://play.pokemonshowdown.com/data"
TERA_TYPES = {
    "Bug",
    "Dark",
    "Dragon",
    "Electric",
    "Fairy",
    "Fighting",
    "Fire",
    "Flying",
    "Ghost",
    "Grass",
    "Ground",
    "Ice",
    "Normal",
    "Poison",
    "Psychic",
    "Rock",
    "Steel",
    "Water",
    "Stellar",
}
DEFAULT_DIVISIONS = ("masters", "seniors", "juniors")
REQUEST_TIMEOUT = 30
CATEGORY_TAGS = {
    "Restricted Legendary",
    "Sub-Legendary",
    "Mythical",
    "Paradox",
    "Ultra Beast",
}


@dataclass
class TournamentSummary:
    """High-level metadata scraped from Pokedata."""

    tournament_id: str
    name: str
    date_text: str
    url: str


@dataclass
class PokemonExtraction:
    """Structured representation for a single Pokémon slot."""

    species: str
    showdown_id: str
    tera_type: Optional[str]
    ability: Optional[str]
    item: Optional[str]
    moves: List[str]
    valid_formats: List[str]
    issues: List[str] = field(default_factory=list)


@dataclass
class PlayerExtraction:
    """Structured representation of a player's team export."""

    player_name: str
    country: Optional[str]
    placing: Optional[int]
    record: Dict[str, Any]
    showdown_team: str
    pokemon: List[PokemonExtraction]
    is_valid: bool
    issues: List[str]


@dataclass
class TournamentDivisionResult:
    """Aggregated output for a tournament + division combination."""

    tournament: TournamentSummary
    division: str
    players: List[PlayerExtraction]


class ExtractionError(RuntimeError):
    """Raised when required remote data cannot be fetched."""


def configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def to_id(value: str) -> str:
    """Mimic Pokémon Showdown's toID helper."""

    normalized = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return re.sub(r"[^a-z0-9]+", "", normalized)


def normalize_species_label(label: str) -> str:
    """Map Pokedata naming quirks to Showdown-style species names."""

    label = label.strip()
    if "[" not in label or "]" not in label:
        return label
    base, descriptor = label.split("[", 1)
    descriptor = descriptor.rstrip("]")
    descriptor = descriptor.replace("Forme", "").replace("Form", "")
    descriptor = descriptor.replace("Style", "").replace("Aspect", "")
    descriptor = descriptor.strip()
    if not descriptor or descriptor.lower() in {"incarnate", "standard"}:
        return base.strip()
    descriptor = descriptor.replace("Rider", "").strip()
    mapped = {
        "Shadow": "Shadow",
        "Ice": "Ice",
        "Therian": "Therian",
        "Attack": "Attack",
        "Defense": "Defense",
        "Speed": "Speed",
        "Midnight": "Midnight",
        "Midday": "Midday",
        "Dusk": "Dusk",
        "Dawn-Wings": "Dawn-Wings",
        "Dusk-Mane": "Dusk-Mane",
        "Origin": "Origin",
        "Hero": "Hero",
        "Aqua": "Aqua",
        "Blaze": "Blaze",
        "Sky": "Sky",
    }
    clean = descriptor.replace(" ", "-")
    for key, canonical in mapped.items():
        if clean.lower().startswith(key.lower()):
            clean = canonical
            break
    return f"{base.strip()}-{clean}"


def split_player_name(raw_name: str) -> Tuple[str, Optional[str]]:
    """Split 'Name [CC]' into 'Name' + ISO country if provided."""

    match = re.match(r"^(?P<name>.+?)\s*\[(?P<country>[A-Z]{2})\]$", raw_name.strip())
    if not match:
        return raw_name.strip(), None
    return match.group("name").strip(), match.group("country")


def fetch_text(session: requests.Session, url: str) -> str:
    LOGGER.debug("Fetching %s", url)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise ExtractionError(f"Failed to fetch {url}: {response.status_code}")
    return response.text


def fetch_json(session: requests.Session, url: str) -> Any:
    LOGGER.debug("Fetching JSON %s", url)
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    if response.status_code != 200:
        raise ExtractionError(f"Failed to fetch {url}: {response.status_code}")
    return response.json()


TOURNAMENT_BUTTON_RE = re.compile(
    r'onclick\s*=\s*"location\.href=\'(?P<slug>[^/]+)/\'"[^>]*>(?P<label>.*?)</button>',
    re.S,
)
DIVISION_BUTTON_RE = re.compile(
    r'onclick\s*=\s*"location\.href=\'(?P<division>[a-z]+)/\'"',
    re.I,
)


def parse_tournament_list(html_text: str) -> List[TournamentSummary]:
    """Extract tournament summaries from the landing page."""

    results: List[TournamentSummary] = []
    for match in TOURNAMENT_BUTTON_RE.finditer(html_text):
        slug = match.group("slug")
        label = html.unescape(match.group("label"))
        label_clean = re.sub(r"<.*?>", " ", label)
        lines = [part.strip() for part in label_clean.splitlines() if part.strip()]
        title = lines[0] if lines else slug
        date_text = ""
        if len(lines) > 1:
            second = lines[1]
            date_text = second[2:].strip() if second.startswith("-") else second
        url = f"{POKEDATA_BASE}/{slug}/"
        results.append(
            TournamentSummary(
                tournament_id=slug,
                name=title,
                date_text=date_text,
                url=url,
            )
        )
    return results


def parse_divisions(html_text: str) -> List[str]:
    """Find available divisions for a tournament."""

    return sorted({match.group("division").lower() for match in DIVISION_BUTTON_RE.finditer(html_text)})


def _session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.setdefault(
        "User-Agent",
        "pokemon-dbgas-data-pipeline/0.1 (+https://github.com/dougwinr/POKEMON_DBGAS)",
    )
    return session


def fetch_tournament_list(session: requests.Session) -> List[TournamentSummary]:
    html_text = fetch_text(session, POKEDATA_BASE + "/")
    return parse_tournament_list(html_text)


def fetch_divisions(session: requests.Session, tournament_id: str) -> List[str]:
    html_text = fetch_text(session, f"{POKEDATA_BASE}/{tournament_id}/")
    divisions = parse_divisions(html_text)
    return divisions or ["masters"]


def fetch_division_payload(
    session: requests.Session, tournament_id: str, division: str
) -> List[Dict[str, Any]]:
    file_division = division.capitalize()
    url = f"{POKEDATA_BASE}/{tournament_id}/{division}/{tournament_id}_{file_division}.json"
    data = fetch_json(session, url)
    if not isinstance(data, list):
        raise ExtractionError(f"Unexpected payload for {url}")
    return data


@dataclass
class ShowdownData:
    """Container around the Showdown static data set."""

    pokedex: Dict[str, Dict[str, Any]]
    moves: Dict[str, Dict[str, Any]]
    items: Dict[str, Dict[str, Any]]
    abilities: Dict[str, Dict[str, Any]]
    learnsets: Dict[str, Dict[str, Any]]
    formats_data: Dict[str, Dict[str, Any]]
    formats: List[Dict[str, Any]]
    alias_map: Dict[str, str]

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
        alias_map = cls._build_alias_map(pokedex)
        for fmt in formats:
            fmt.setdefault("id", to_id(fmt.get("name", "")))
        return cls(pokedex, moves, items, abilities, learnsets, formats_data, formats, alias_map)

    @classmethod
    def from_remote(cls, session: requests.Session) -> "ShowdownData":
        endpoints = {
            "pokedex": f"{SHOWDOWN_BASE}/pokedex.json",
            "moves": f"{SHOWDOWN_BASE}/moves.json",
            "items": f"{SHOWDOWN_BASE}/text/items.json5",
            "abilities": f"{SHOWDOWN_BASE}/text/abilities.json5",
            "learnsets": f"{SHOWDOWN_BASE}/learnsets.json",
            "formats_data": f"{SHOWDOWN_BASE}/formats-data.js",
            "formats": f"{SHOWDOWN_BASE}/formats.js",
        }
        parsed: Dict[str, Any] = {}

        def _download(name: str, url: str) -> Tuple[str, Any]:
            text = fetch_text(session, url)
            if name in {"items", "abilities"}:
                return name, json5.loads(text)
            if name in {"pokedex", "moves", "learnsets"}:
                return name, json.loads(text)
            if name == "formats_data":
                payload = text.split("=", 1)[1].strip()
                if payload.endswith(";"):
                    payload = payload[:-1]
                return name, json5.loads(payload)
            if name == "formats":
                payload = text.split("=", 1)[1].strip()
                if payload.endswith(";"):
                    payload = payload[:-1]
                return name, json5.loads(payload)
            raise AssertionError(f"Unhandled endpoint {name}")

        max_workers = min(8, os.cpu_count() or 4)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_download, name, url) for name, url in endpoints.items()]
            for future in tqdm(
                concurrent.futures.as_completed(futures),
                total=len(futures),
                desc="Showdown data",
                leave=False,
            ):
                name, value = future.result()
                parsed[name] = value
        return cls.from_payloads(**parsed)

    @staticmethod
    def _build_alias_map(pokedex: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for sid, entry in pokedex.items():
            names = {entry.get("name", "")}
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
            for name in names:
                token = to_id(name)
                if token and token not in alias_map:
                    alias_map[token] = sid
        return alias_map

    def resolve_species_id(self, label: str) -> Optional[str]:
        normalized = normalize_species_label(label)
        token = to_id(normalized)
        if token in self.alias_map:
            return self.alias_map[token]
        fallback = to_id(label)
        return self.alias_map.get(fallback)

    def resolve_item_id(self, name: str) -> Optional[str]:
        token = to_id(name)
        if token in self.items:
            return token
        for item_id, payload in self.items.items():
            if to_id(payload.get("name", "")) == token:
                return item_id
        return None

    def resolve_ability_id(self, name: str) -> Optional[str]:
        token = to_id(name)
        if token in self.abilities:
            return token
        for ability_id, payload in self.abilities.items():
            if to_id(payload.get("name", "")) == token:
                return ability_id
        return None

    def resolve_move_id(self, name: str) -> Optional[str]:
        token = to_id(name)
        if token in self.moves:
            return token
        for move_id, payload in self.moves.items():
            if to_id(payload.get("name", "")) == token:
                return move_id
        return None


def validate_move_learnset(showdown_data: ShowdownData, species_id: str, move_id: str) -> bool:
    learnset = showdown_data.learnsets.get(species_id, {}).get("learnset", {})
    if move_id in learnset:
        return True
    base_species = showdown_data.pokedex.get(species_id, {}).get("baseSpecies")
    if base_species:
        base_id = showdown_data.alias_map.get(to_id(base_species))
        if base_id:
            learnset = showdown_data.learnsets.get(base_id, {}).get("learnset", {})
            return move_id in learnset
    return False


def determine_valid_formats(
    showdown_data: ShowdownData, species_id: str, *, restrict_to_vgc: bool = True
) -> List[str]:
    entry = showdown_data.pokedex.get(species_id, {})
    formats_meta = showdown_data.formats_data.get(species_id, {})
    if entry.get("isNonstandard") in {"Past", "Future", "Unobtainable"}:
        return []
    if formats_meta.get("isNonstandard") in {"Past", "Future", "Unobtainable"}:
        return []
    tags = set(entry.get("tags") or [])
    valid: List[str] = []
    for fmt in showdown_data.formats:
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


def convert_decklist_entry(
    slot: Dict[str, Any],
    showdown_data: ShowdownData,
) -> PokemonExtraction:
    species_label = slot.get("name") or "Unknown"
    tera_type = slot.get("teratype")
    ability = slot.get("ability")
    item = slot.get("item")
    moves = [move for move in slot.get("badges", []) if move]
    species_id = showdown_data.resolve_species_id(species_label)
    issues: List[str] = []
    showdown_id = species_id or to_id(species_label)
    if tera_type and tera_type not in TERA_TYPES:
        issues.append(f"Unknown Tera Type '{tera_type}'")
    if ability and not showdown_data.resolve_ability_id(ability):
        issues.append(f"Ability '{ability}' not found in Showdown data")
    if item and not showdown_data.resolve_item_id(item):
        issues.append(f"Item '{item}' not found in Showdown data")
    resolved_moves: List[str] = []
    for move in moves:
        move_id = showdown_data.resolve_move_id(move)
        if not move_id:
            issues.append(f"Move '{move}' not found in Showdown data")
            continue
        if species_id and not validate_move_learnset(showdown_data, species_id, move_id):
            issues.append(f"{slot.get('name')} cannot learn {move}")
        resolved_moves.append(showdown_data.moves[move_id]["name"])
    valid_formats: List[str] = []
    if species_id:
        valid_formats = determine_valid_formats(showdown_data, species_id)
    else:
        issues.append(f"Unable to resolve species '{species_label}'")
    return PokemonExtraction(
        species=species_label,
        showdown_id=showdown_id,
        tera_type=tera_type,
        ability=ability,
        item=item,
        moves=resolved_moves,
        valid_formats=valid_formats,
        issues=issues,
    )


def build_showdown_team_string(pokemon: Sequence[PokemonExtraction]) -> str:
    blocks: List[str] = []
    for slot in pokemon:
        lines: List[str] = []
        header = slot.species
        if slot.item:
            header = f"{header} @ {slot.item}"
        lines.append(header)
        if slot.ability:
            lines.append(f"Ability: {slot.ability}")
        if slot.tera_type:
            lines.append(f"Tera Type: {slot.tera_type}")
        lines.append("Level: 50")
        for move in slot.moves:
            lines.append(f"- {move}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def transform_player(
    player: Dict[str, Any],
    showdown_data: ShowdownData,
) -> PlayerExtraction:
    player_name, country = split_player_name(player.get("name", "Unknown"))
    placing = player.get("placing")
    decklist: List[Dict[str, Any]] = player.get("decklist") or []
    pokemon = [convert_decklist_entry(slot, showdown_data) for slot in decklist]
    issues = [issue for slot in pokemon for issue in slot.issues]
    record = player.get("record") or {}
    showdown_team = build_showdown_team_string(pokemon)
    is_valid = not issues
    return PlayerExtraction(
        player_name=player_name,
        country=country,
        placing=placing,
        record=record,
        showdown_team=showdown_team,
        pokemon=pokemon,
        is_valid=is_valid,
        issues=issues,
    )


def process_tournament(
    summary: TournamentSummary,
    *,
    divisions: Sequence[str],
    showdown_data: ShowdownData,
    session_factory: Callable[[], requests.Session],
    process_pool: concurrent.futures.ProcessPoolExecutor,
) -> List[TournamentDivisionResult]:
    session = session_factory()
    try:
        available_divisions = fetch_divisions(session, summary.tournament_id)
        target_divisions = [div for div in divisions if div in available_divisions]
        results: List[TournamentDivisionResult] = []
        for division in target_divisions:
            players_payload = fetch_division_payload(session, summary.tournament_id, division)
            player_futures = [
                process_pool.submit(transform_player, player, showdown_data) for player in players_payload
            ]
            extracted: List[PlayerExtraction] = []
            for future in concurrent.futures.as_completed(player_futures):
                extracted.append(future.result())
            extracted.sort(key=lambda p: (p.placing if p.placing is not None else 9999))
            results.append(
                TournamentDivisionResult(
                    tournament=summary,
                    division=division,
                    players=extracted,
                )
            )
        return results
    finally:
        session.close()


def process_tournaments(
    summaries: Sequence[TournamentSummary],
    *,
    divisions: Sequence[str],
    showdown_data: ShowdownData,
    max_workers: int,
) -> List[TournamentDivisionResult]:
    results: List[TournamentDivisionResult] = []
    session_factory = _session_factory
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=max(os.cpu_count() or 2, 2)
    ) as process_pool:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    process_tournament,
                    summary,
                    divisions=divisions,
                    showdown_data=showdown_data,
                    session_factory=session_factory,
                    process_pool=process_pool,
                ): summary
                for summary in summaries
            }
            progress = tqdm(total=len(futures), desc="Tournaments")
            for future in concurrent.futures.as_completed(futures):
                progress.update(1)
                summary = futures[future]
                try:
                    results.extend(future.result())
                except Exception as exc:
                    LOGGER.error("Failed to process %s: %s", summary.tournament_id, exc)
            progress.close()
    return results


def serialize_output(results: Sequence[TournamentDivisionResult]) -> Dict[str, Any]:
    payload = {
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "source": POKEDATA_BASE,
        "tournaments": [],
    }
    for entry in results:
        payload["tournaments"].append(
            {
                "tournament_id": entry.tournament.tournament_id,
                "name": entry.tournament.name,
                "date": entry.tournament.date_text,
                "division": entry.division,
                "players": [
                    {
                        "player_name": player.player_name,
                        "country": player.country,
                        "placing": player.placing,
                        "record": player.record,
                        "showdown_team": player.showdown_team,
                        "pokemon": [asdict(pokemon) for pokemon in player.pokemon],
                        "is_valid": player.is_valid,
                        "issues": player.issues,
                    }
                    for player in entry.players
                ],
            }
        )
    return payload


def write_output(payload: Dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2))
    LOGGER.info("Wrote %s", output_path)


def run_pipeline(args: argparse.Namespace) -> None:
    session = _session_factory()
    summaries = fetch_tournament_list(session)
    if args.limit:
        summaries = summaries[: args.limit]
    showdown_data = ShowdownData.from_remote(session)
    divisions = args.divisions.split(",") if isinstance(args.divisions, str) else list(args.divisions)
    divisions = [div.strip().lower() for div in divisions if div.strip()]
    if not divisions:
        divisions = list(DEFAULT_DIVISIONS)
    max_workers = args.workers or (os.cpu_count() or 4)
    results = process_tournaments(
        summaries,
        divisions=divisions,
        showdown_data=showdown_data,
        max_workers=max_workers,
    )
    payload = serialize_output(results)
    write_output(payload, args.output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract tournament teams from Pokedata into structured JSON."
    )
    parser.add_argument("--limit", type=int, help="Limit number of tournaments to process.")
    parser.add_argument(
        "--divisions",
        default="masters",
        help="Comma-separated list of divisions to extract (default: masters).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tournament_teams.json"),
        help="Output JSON path.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=os.cpu_count() or 4,
        help="Number of threads for tournament downloads.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.debug)
    try:
        run_pipeline(args)
    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        return 1
    except ExtractionError as exc:
        LOGGER.error("Fatal extraction error: %s", exc)
        return 2
    except Exception:
        LOGGER.exception("Unexpected failure")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
