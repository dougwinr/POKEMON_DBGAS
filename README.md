# Pokemon Double Battle Genetic Algorithm Simulator
Pokemon Double Battle Genetic Algorithm Simulator is a cross-language research project that will evolve coordinated Pokemon Showdown double battle strategies using multi-objective genetic algorithms, blending a Python simulation core with future Node.js tooling and orchestration layers.

## Python environment (uv)
This repository uses `uv` as the package and project manager so contributors share identical dependency graphs. We primarily target Python 3.14 on Apple Silicon, but any interpreter in the `>=3.11,<3.15` range is supported on macOS, Linux, and Windows.

```bash
# From the project root, create/sync the environment
uv sync

# Run a Python command inside the project environment
uv run python --version

# (Later) run tests once tests are added
uv run pytest
```

Add future dependencies with `uv add <package-name>`. The command records new requirements in both `pyproject.toml` and `uv.lock`, ensuring everyone stays in sync across platforms and CI.
