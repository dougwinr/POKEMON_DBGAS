# Pokemon Double Battle Genetic Algorithm Simulator
Pokemon Double Battle Genetic Algorithm Simulator is a cross-language research project that evolves coordinated Pokemon Showdown double battle strategies using multi-objective genetic algorithms. The long-term architecture blends a Python simulation core with Node.js tooling and orchestration layers, but today the repository contains the documentation scaffold and a uv-managed Python workspace.

## Requirements
- Python `>=3.11,<3.15` (3.14.0 on Apple Silicon is the primary target)
- [`uv`](https://github.com/astral-sh/uv) installed globally for dependency and environment management
- Git + a Bash-compatible shell (macOS, Linux, or Windows via WSL/PowerShell)

## Setup & Start Instructions
From a fresh clone, run the following commands in order:

```bash
# 1. Install/sync dependencies and create .venv managed by uv
uv sync

# 2. Confirm the interpreter inside the project environment
uv run python --version

# 3. (Later) execute the future simulator/tests via uv
uv run pytest
```

These commands create a local `.venv` directory, install the dev dependency set (currently Pytest), and ensure future commands run inside the managed environment. On Windows, run the same steps in PowerShell; uv detects the platform automatically.

## Example: validate the environment
While the simulator code is still being implemented, you can verify the toolchain with a simple inline script:

```bash
uv run python -c "print('Pokemon GA environment ready for evolution!')"
```

If the message prints successfully, uv resolved the interpreter and executed Python inside the project context. Replace the inline command with future modules (for example, `uv run python -m sim.cli`) once they exist.

## Managing Dependencies with uv
Add new packages with:

```bash
uv add <package-name>
```

`uv add` updates both `pyproject.toml` and `uv.lock`, so every contributor and CI runner installs the same dependency graph. Remove packages via `uv remove <package-name>` when needed.
