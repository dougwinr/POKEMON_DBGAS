from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import difflib
import html
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from tqdm.auto import tqdm

from pokedata_cache_manager import (
    get_division_json,
    get_index_html,
    get_tournament_html,
)
from showdown_data_manager import (
    load_abilities_data as load_abilities,
    load_formats_data,
    load_formats_list,
    load_items_data as load_items,
    load_learnsets_data as load_learnsets,
    load_moves_data as load_moves,
    load_pokedex_data as load_pokedex,
    update_all_showdown_data,
)

LOGGER = logging.getLogger(__name__)

POKEDATA_BASE = "https://www.pokedata.ovh/standingsVGC"
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
MOVE_STRIP_WORDS = {
    "doubles",
    "singles",
    "triples",
    "battle",
    "mode",
}


@dataclass
class TournamentSummary:
    """High-level metadata scraped from Pokedata."""

    tournament_id: str
    name: str
    date_text: str
    url: str


@dataclass
class MoveExtraction:
    """Structured representation for a single move entry."""

    raw_move: str
    move_id: Optional[str]
    move_name: str
    is_legal: bool


@dataclass
class PokemonExtraction:
    """Structured representation for a single Pokémon slot."""

    raw_species: str
    species: str
    showdown_id: str
    tera_type: Optional[str]
    ability: Optional[str]
    item: Optional[str]
    moves: List[MoveExtraction]
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
    "incarnate": "",
    "standard": "",
    "single": "Single",
    "strike": "Strike",
}


def normalize_species_label(label: str) -> str:
    """Map Pokedata naming quirks to Showdown-style species names."""

    label = label.strip()
    if "[" not in label or "]" not in label:
        return label
    base, descriptor = label.split("[", 1)
    descriptor = descriptor.rstrip("]")
    descriptor = descriptor.strip()
    if not descriptor:
        return base.strip()
    tokens = [token for token in re.split(r"[^\w]+", descriptor.lower()) if token]
    mapped_tokens: List[str] = []
    for token in tokens:
        replacement = DESCRIPTOR_TOKEN_MAP.get(token)
        if replacement is None:
            mapped_tokens.append(token.capitalize())
        elif replacement:
            mapped_tokens.append(replacement)
    if not mapped_tokens:
        return base.strip()
    # collapse duplicate words like Dawn + Wings -> "Dawn-Wings"
    combined: List[str] = []
    skip_next = False
    for idx, token in enumerate(mapped_tokens):
        if skip_next:
            skip_next = False
            continue
        if token in {"Dawn", "Dusk"} and idx + 1 < len(mapped_tokens) and mapped_tokens[idx + 1] in {"Wings", "Mane"}:
            combined.append(f"{token}-{mapped_tokens[idx + 1]}")
            skip_next = True
        else:
            combined.append(token)
    suffix = "-".join(combined)
    return f"{base.strip()}-{suffix}"


def split_player_name(raw_name: str) -> Tuple[str, Optional[str]]:
    """Split 'Name [CC]' into 'Name' + ISO country if provided."""

    match = re.match(r"^(?P<name>.+?)\s*\[(?P<country>[A-Z]{2})\]$", raw_name.strip())
    if not match:
        return raw_name.strip(), None
    return match.group("name").strip(), match.group("country")


def normalize_move_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).lower()
    text = re.sub(r"[\"'`\[\]\(\)\-]", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [token for token in text.split() if token and token not in MOVE_STRIP_WORDS]
    if not tokens:
        return ""
    return "".join(tokens)



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


def fetch_tournament_list(*, force: bool = False, debug: bool = False) -> List[TournamentSummary]:
    html_text = get_index_html(force=force, debug=debug)
    return parse_tournament_list(html_text)


def fetch_divisions(tournament_id: str, *, force: bool = False, debug: bool = False) -> List[str]:
    html_text = get_tournament_html(tournament_id, force=force, debug=debug)
    divisions = parse_divisions(html_text)
    return divisions or ["masters"]


def fetch_division_payload(
    tournament_id: str,
    division: str,
    *,
    force: bool = False,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    data = get_division_json(tournament_id, division, force=force, debug=debug)
    if not isinstance(data, list):
        raise ExtractionError(f"Unexpected payload for {tournament_id}/{division}")
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
        species_alias_map = cls._build_species_alias_map(pokedex)
        move_alias_map = cls._build_simple_alias_map(moves, normalizer=normalize_move_name)
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
        alias_map: Dict[str, str] = {}
        for sid, entry in pokedex.items():
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
            for name in names:
                token = to_id(name)
                if token and token not in alias_map:
                    alias_map[token] = sid
        return alias_map

    @staticmethod
    def _build_simple_alias_map(
        entries: Dict[str, Dict[str, Any]], *, normalizer: Callable[[str], str] = to_id
    ) -> Dict[str, str]:
        alias_map: Dict[str, str] = {}
        for entry_id, entry in entries.items():
            names = {entry_id}
            if isinstance(entry, dict):
                name_value = entry.get("name")
                if isinstance(name_value, str):
                    names.add(name_value)
                aliases = entry.get("aliases")
                if isinstance(aliases, list):
                    names.update(filter(None, map(str, aliases)))
            for name in names:
                token = normalizer(name)
                if token and token not in alias_map:
                    alias_map[token] = entry_id
        return alias_map

    def resolve_species_id(self, label: str) -> Optional[str]:
        normalized = normalize_species_label(label)
        token = to_id(normalized)
        if token in self.species_alias_map:
            return self.species_alias_map[token]
        fallback = to_id(label)
        return self.species_alias_map.get(fallback)

    def resolve_item_id(self, name: str) -> Optional[str]:
        token = to_id(name)
        return self.item_alias_map.get(token)

    def resolve_ability_id(self, name: str) -> Optional[str]:
        token = to_id(name)
        return self.ability_alias_map.get(token)

    def resolve_move_id(self, name: str) -> Optional[str]:
        token = normalize_move_name(name)
        if token in self.move_alias_map:
            return self.move_alias_map[token]
        fallback = to_id(name)
        if fallback in self.move_alias_map:
            return self.move_alias_map[fallback]
        if self.move_alias_map:
            matches = difflib.get_close_matches(token, self.move_alias_map.keys(), n=1, cutoff=0.72)
            if matches:
                return self.move_alias_map[matches[0]]
        return None


def load_showdown_data(debug: bool = False) -> ShowdownData:
    update_all_showdown_data(debug=debug)
    return ShowdownData.from_payloads(
        pokedex=load_pokedex(debug=debug),
        moves=load_moves(debug=debug),
        items=load_items(debug=debug),
        abilities=load_abilities(debug=debug),
        learnsets=load_learnsets(debug=debug),
        formats_data=load_formats_data(debug=debug),
        formats=load_formats_list(debug=debug),
    )


def validate_move_learnset(showdown_data: ShowdownData, species_id: str, move_id: str) -> bool:
    learnset = showdown_data.learnsets.get(species_id, {}).get("learnset", {})
    if move_id in learnset:
        return True
    base_species = showdown_data.pokedex.get(species_id, {}).get("baseSpecies")
    if base_species:
        base_id = showdown_data.species_alias_map.get(to_id(base_species))
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
    raw_species_label = slot.get("name") or "Unknown"
    tera_type = slot.get("teratype")
    ability = slot.get("ability")
    item = slot.get("item")
    moves = [move for move in slot.get("badges", []) if move]
    issues: List[str] = []

    species_id = showdown_data.resolve_species_id(raw_species_label)
    species_label = raw_species_label
    if species_id:
        species_entry = showdown_data.pokedex.get(species_id, {})
        species_label = species_entry.get("name", species_label)
    showdown_id = species_id or to_id(species_label)
    if tera_type and tera_type not in TERA_TYPES:
        issues.append(f"Unknown Tera Type '{tera_type}'")
    if ability and not showdown_data.resolve_ability_id(ability):
        issues.append(f"Ability '{ability}' not found in Showdown data")
    if item and not showdown_data.resolve_item_id(item):
        issues.append(f"Item '{item}' not found in Showdown data")
    move_entries: List[MoveExtraction] = []
    for move in moves:
        move_id = showdown_data.resolve_move_id(move)
        move_name = move
        if move_id:
            move_meta = showdown_data.moves.get(move_id)
            if move_meta:
                move_name = move_meta.get("name", move_name)
            else:
                issues.append(f"Move '{move}' not found in Showdown data")
                move_id = None
        else:
            issues.append(f"Move '{move}' not found in Showdown data")
        is_legal = False
        if move_id and species_id:
            if validate_move_learnset(showdown_data, species_id, move_id):
                is_legal = True
            else:
                issues.append(f"{species_label} cannot learn {move_name}")
        elif move_id and not species_id:
            issues.append(f"Unable to validate move '{move}' without species data")
        move_entries.append(
            MoveExtraction(
                raw_move=move,
                move_id=move_id,
                move_name=move_name,
                is_legal=is_legal,
            )
        )
    valid_formats: List[str] = []
    if species_id:
        valid_formats = determine_valid_formats(showdown_data, species_id)
    else:
        issues.append(f"Unable to resolve species '{species_label}'")
    return PokemonExtraction(
        raw_species=raw_species_label,
        species=species_label,
        showdown_id=showdown_id,
        tera_type=tera_type,
        ability=ability,
        item=item,
        moves=move_entries,
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
            lines.append(f"- {move.move_name}")
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
    process_pool: concurrent.futures.ProcessPoolExecutor,
    cache_force: bool,
    debug: bool,
) -> List[TournamentDivisionResult]:
    available_divisions = fetch_divisions(summary.tournament_id, force=cache_force, debug=debug)
    target_divisions = [div for div in divisions if div in available_divisions]
    results: List[TournamentDivisionResult] = []
    for division in target_divisions:
        players_payload = fetch_division_payload(
            summary.tournament_id,
            division,
            force=cache_force,
            debug=debug,
        )
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


def process_tournaments(
    summaries: Sequence[TournamentSummary],
    *,
    divisions: Sequence[str],
    showdown_data: ShowdownData,
    max_workers: int,
    cache_force: bool,
    debug: bool,
) -> List[TournamentDivisionResult]:
    results: List[TournamentDivisionResult] = []
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
                    process_pool=process_pool,
                    cache_force=cache_force,
                    debug=debug,
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
    showdown_data = load_showdown_data(debug=args.debug)
    force_pokedata = args.refresh_pokedata
    summaries = fetch_tournament_list(force=force_pokedata, debug=args.debug)
    if args.limit:
        summaries = summaries[: args.limit]
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
        cache_force=force_pokedata,
        debug=args.debug,
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
    parser.add_argument(
        "--refresh-pokedata",
        action="store_true",
        help="Redownload cached PokeData HTML/JSON before processing.",
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
