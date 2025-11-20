# Pokemon Double Battle Genetic Algorithm Simulator

## Overview
Pokemon Double Battle Genetic Algorithm Simulator (version 0.1.0) is a Python-first research platform for evolving coordinated Pokémon Showdown double-battle strategies. The current focus is an offline-first tournament pipeline: scrape PokeData standings into a local cache, resolve every species/move/item/format using cached Showdown datasets, validate legality, and emit structured team exports for downstream genetic algorithms. Node.js orchestration will arrive later, but the repository already provides the Python tooling required for reproducible data ingestion.

## Installation
- Install Python `>=3.11,<3.15` (Python 3.14.0 on Apple Silicon is the reference interpreter).
- Install [`uv`](https://github.com/astral-sh/uv) globally.
- Clone the repository and bootstrap the environment:

```bash
uv sync --extra dev
uv run python --version
```

`uv sync` provisions `.venv`, installs runtime + dev dependencies, and keeps `pyproject.toml` and `uv.lock` aligned across macOS, Linux, and Windows/WSL.

## Building & Development
- Use `uv add <package>` / `uv remove <package>` to manage dependencies; the lockfile updates automatically.
- Run tools via `uv run <command>` instead of activating the virtualenv manually.
- Seed or refresh the Showdown data cache when needed:

```bash
uv run python -c "from showdown_manager import ShowdownManager; manager = ShowdownManager(); manager.download_or_update_all(debug=True)"
```

- Seed or refresh PokeData standings locally (optional before large runs):

```bash
uv run python -c "from pokedata_manager import update_index; update_index(force=True, debug=True)"
```

## Data Collection / Scraping
### `tournament_teams_extraction.py`
CLI that consumes only local caches (PokeData HTML/JSON + Showdown datasets), validates every team, and emits `tournament_teams.json` for analysis.

**Examples**
```bash
# Latest tournaments, masters division only
uv run python tournament_teams_extraction.py --limit 5 --divisions masters

# Custom output, multi-division, refresh caches, verbose logs
uv run python tournament_teams_extraction.py \
  --limit 2 \
  --divisions masters,seniors \
  --output data/tournament_teams.json \
  --refresh-pokedata \
  --debug
```

**Key options**
- `--limit <int>`: Number of tournaments to process (default: scrape all listings).
- `--divisions <csv>`: Comma-separated list (e.g., `masters,seniors,juniors`; default `masters`).
- `--output <path>`: Destination JSON file (default: `tournament_teams.json`).
- `--workers <int>`: Thread pool size for tournament processing (default: CPU count).
- `--debug`: Enables verbose logging for caching, parsing, and legality checks.
- `--refresh-pokedata`: Forces re-download of all PokeData HTML/JSON before processing.

Behind the scenes, the CLI:
1. Creates a `ShowdownManager` instance and calls `download_or_update_all()` once to ensure local Showdown files are current.
2. Uses `pokedata_manager` to cache every tournament/division page under `data/pokedata/`.
3. Resolves species, moves, abilities, items, and format legality exclusively from disk using the `ShowdownManager`.

## Debug & Logging
- Pass `--debug` to `tournament_teams_extraction.py` to print detailed status covering cache hits/misses, Showdown resolution steps, and validation failures.
- Logging uses Python’s `logging` module; adjust verbosity globally through the CLI flag or environment variables such as `LOG_LEVEL`.

## Running Tests
All tests run offline using stubbed data and the local cache infrastructure.

```bash
uv run pytest
```

Add coverage in `tests/` whenever new functionality lands (e.g., additional cache managers or validators). Continuous integration should execute the same command to ensure parity with local development.
