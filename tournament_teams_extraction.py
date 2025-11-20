from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tqdm.auto import tqdm

from pokedata_manager import (
    POKEDATA_BASE,
    load_local_index,
    load_tournament_json,
    load_tournament_page,
    parse_divisions,
    parse_tournament_list,
    update_index,
    update_tournament_json_files,
    update_tournament_page,
)
from showdown_manager import (
    ShowdownManager,
    normalize_species_label,
    to_id,
)

LOGGER = logging.getLogger(__name__)

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


def split_player_name(raw_name: str) -> Tuple[str, Optional[str]]:
    """Split 'Name [CC]' into 'Name' + ISO country if provided."""

    match = re.match(r"^(?P<name>.+?)\s*\[(?P<country>[A-Z]{2})\]$", raw_name.strip())
    if not match:
        return raw_name.strip(), None
    return match.group("name").strip(), match.group("country")



def load_tournament_summaries(*, force: bool = False, debug: bool = False) -> List[TournamentSummary]:
    update_index(force=force, debug=debug)
    html_text = load_local_index()
    entries = parse_tournament_list(html_text)
    summaries: List[TournamentSummary] = []
    for entry in entries:
        summaries.append(
            TournamentSummary(
                tournament_id=entry["tournament_id"],
                name=entry["name"],
                date_text=entry["date_text"],
                url=entry["url"],
            )
        )
    return summaries


def load_available_divisions(tournament_id: str, *, force: bool = False, debug: bool = False) -> List[str]:
    update_tournament_page(tournament_id, force=force, debug=debug)
    html_text = load_tournament_page(tournament_id)
    divisions = parse_divisions(html_text)
    return divisions or ["masters"]


def load_division_payload(
    tournament_id: str,
    division: str,
    *,
    force: bool = False,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    update_tournament_json_files(tournament_id, [division], force=force, debug=debug)
    return load_tournament_json(tournament_id, division)


def convert_decklist_entry(
    slot: Dict[str, Any],
    showdown: ShowdownManager,
) -> PokemonExtraction:
    raw_species_label = slot.get("name") or "Unknown"
    tera_type = slot.get("teratype")
    ability = slot.get("ability")
    item = slot.get("item")
    moves = [move for move in slot.get("badges", []) if move]
    issues: List[str] = []

    species_id = showdown.resolve_species_id(raw_species_label)
    species_label = raw_species_label
    if species_id:
        species_entry = showdown.pokedex.get(species_id, {})
        species_label = species_entry.get("name", species_label)
    showdown_id = species_id or to_id(species_label)
    if tera_type and tera_type not in TERA_TYPES:
        issues.append(f"Unknown Tera Type '{tera_type}'")
    if ability and not showdown.resolve_ability_id(ability):
        issues.append(f"Ability '{ability}' not found in Showdown data")
    if item and not showdown.resolve_item_id(item):
        issues.append(f"Item '{item}' not found in Showdown data")
    move_entries: List[MoveExtraction] = []
    for move in moves:
        move_id = showdown.resolve_move_id(move)
        move_name = move
        if move_id:
            move_meta = showdown.moves.get(move_id)
            if move_meta:
                move_name = move_meta.get("name", move_name)
            else:
                issues.append(f"Move '{move}' not found in Showdown data")
                move_id = None
        else:
            issues.append(f"Move '{move}' not found in Showdown data")
        is_legal = False
        if move_id and species_id:
            if showdown.can_species_learn_move(species_id, move_id):
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
        valid_formats = showdown.determine_valid_formats(species_id)
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
    showdown_data_dir: str,
) -> PlayerExtraction:
    # Create a fresh ShowdownManager instance for this process
    # (avoids pickling issues and each process reads from shared local files)
    showdown = ShowdownManager(data_dir=showdown_data_dir)
    player_name, country = split_player_name(player.get("name", "Unknown"))
    placing = player.get("placing")
    decklist: List[Dict[str, Any]] = player.get("decklist") or []
    pokemon = [convert_decklist_entry(slot, showdown) for slot in decklist]
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
    showdown_data_dir: str,
    process_pool: concurrent.futures.ProcessPoolExecutor,
    cache_force: bool,
    debug: bool,
) -> List[TournamentDivisionResult]:
    available_divisions = load_available_divisions(summary.tournament_id, force=cache_force, debug=debug)
    target_divisions = [div for div in divisions if div in available_divisions]
    results: List[TournamentDivisionResult] = []
    for division in target_divisions:
        players_payload = load_division_payload(
            summary.tournament_id,
            division,
            force=cache_force,
            debug=debug,
        )
        player_futures = [
            process_pool.submit(transform_player, player, showdown_data_dir) for player in players_payload
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
    showdown_data_dir: str,
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
                    showdown_data_dir=showdown_data_dir,
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
    showdown = ShowdownManager()
    showdown.download_or_update_all(debug=args.debug)
    showdown_data_dir = str(showdown.data_dir)
    force_pokedata = args.refresh_pokedata
    summaries = load_tournament_summaries(force=force_pokedata, debug=args.debug)
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
        showdown_data_dir=showdown_data_dir,
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
