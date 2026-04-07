# Developer Setup

## Prerequisites

- Python 3.9+ (3.11 recommended for builds)
- Git
- Docker (only needed for `make build-linux`)

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
| `make validate-config` | Validate all 4 district YAML configs |
| `make ui` | Start the Streamlit web UI |
| `make build-win` | Build Windows `.exe` (run on Windows) |
| `make build-linux` | Build Linux binary via Docker |
| `make docs` | Build MkDocs documentation site |
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
