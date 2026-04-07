# Developer Setup

## Prerequisites

- Python 3.9+ (3.11 recommended for builds)
- Git

## Clone and install

```bash
git clone https://github.com/myblueprint/GDE2Acsv.git
cd GDE2Acsv
pip install -r requirements.txt -r requirements-dev.txt
```

## Verify the setup

```bash
# Run all tests
make test

# Validate all district configs
make validate-config

# Lint
make lint

# Auto-fix lint and formatting
make fmt

# Format check only (no changes)
ruff format --check src/ tests/

# Type check (excluding UI pages)
mypy --exclude 'src/ui' src/

# Security scan
bandit -r src/
```

## Environment

No `.env` file is required. The tool reads all configuration from YAML files in `config/mappings/` and from `~/.gde2acsv/config.json` at runtime.

## Makefile targets

| Command | Description |
|---------|-------------|
| `make install` | Install all dependencies |
| `make test` | Run tests |
| `make test-cov` | Run tests with coverage (enforces 80%+) |
| `make lint` | Check with ruff |
| `make fmt` | Auto-fix lint and formatting issues with ruff |
| `make validate-config` | Validate all 5 district YAML configs |
| `make ui` | Start the Streamlit web UI |
| `make docs` | Build MkDocs documentation site |
| `make docs-serve` | Live preview docs at http://localhost:8000 |
| `make build-win` | Build Windows `.exe` (run on Windows) |
| `make clean` | Remove build artefacts |

## Run the CLI locally

```bash
python -m src.main --sis myedbc --input data/input --output data/output
```

Flags:

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview output counts without writing files |
| `--diff` | Compare against existing output CSVs |
| `--quality` | Print a data quality report |
| `--sftp` | Upload output CSVs via SFTP (requires config) |

## Run the web UI

```bash
make ui
# or: streamlit run src/ui/app.py
```

Opens at `http://localhost:8501`.
