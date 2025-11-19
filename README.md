# Pokemon Double Battle Genetic Algorithm Simulator

## Overview
Pokemon Double Battle Genetic Algorithm Simulator (version 0.1.0) is an in-progress research platform that combines a Python simulation core with future Node.js orchestration to evolve coordinated Pokemon Showdown double battle strategies. The current focus is a uv-managed Python toolchain that scrapes tournament data, validates teams against Showdown rulesets, and emits JSON artifacts for downstream modeling.

## Installation
- Install Python `>=3.11,<3.15` (Python 3.14.0 on Apple Silicon is the reference interpreter).
- Install [`uv`](https://github.com/astral-sh/uv) globally for dependency and environment management.
- Clone the repository and run:

```bash
uv sync --extra dev
uv run python --version
```

`uv sync` creates `.venv`, installs runtime/optional dependencies, and keeps `pyproject.toml` + `uv.lock` aligned across macOS, Linux, and Windows/WSL.

## Building & Development
- Use `uv add <package>` / `uv remove <package>` to manage dependencies; both files stay in sync automatically.
- Activate shells ad-hoc via `uv run <command>` rather than sourcing `.venv` manually.
- Future Node.js tooling will live under `ui/`; for now, the Python data pipeline is the only executable component.

## Data Collection / Scraping
### `tournament_teams_extraction.py`
Command-line utility that downloads VGC tournament standings from [pokedata.ovh](https://www.pokedata.ovh/standingsVGC/), enriches each player’s roster using Pokemon Showdown reference data, and writes `tournament_teams.json`.

**Examples**
```bash
# Basic run: latest tournaments, masters division only
uv run python tournament_teams_extraction.py --limit 5 --divisions masters

# Save to a custom path and enable verbose debug logging
uv run python tournament_teams_extraction.py \
  --limit 2 \
  --divisions masters,seniors \
  --output data/tournament_teams.json \
  --debug
```

**Key options**
- `--limit <int>`: cap how many tournaments from the landing page are processed (default: all).
- `--divisions <csv>`: comma-separated list such as `masters,seniors,juniors` (default: `masters`).
- `--output <path>`: JSON destination (default: `tournament_teams.json`).
- `--workers <int>`: thread count for fetching tournaments (default: CPU count).
- `--debug`: enable verbose logging for HTTP requests, parsing, and validation steps.

The scraper automatically downloads Showdown pokedex/move/item/format datasets, validates each Pokémon’s species/moves/held items, emits Showdown teamstrings, and reports legality issues per player.

## Debug & Logging
- Pass `--debug` to any uv-run CLI (e.g., `uv run python tournament_teams_extraction.py --debug`) to turn on detailed logging.
- Logs are emitted via the standard library `logging` module and include fetch URLs, parsing decisions, and validation results for easier troubleshooting.

## Running Tests
Unit tests are written with Pytest and run entirely offline using stubbed datasets.

```bash
uv run pytest
```

Add new tests under `tests/` to cover parsing, validation, and future simulator logic. Continuous integration should run the same command to ensure parity with local development.
