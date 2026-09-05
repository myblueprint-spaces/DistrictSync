#!/bin/bash
# SessionStart hook for Claude Code on the web (claude.ai/code).
#
# Installs the Python toolchain the repo's gates need (pandas/pydantic/flet at
# runtime; pytest/ruff/mypy/bandit + the two stub packages for dev) so a fresh
# cloud container can run `pytest`, `ruff`, `python -m mypy` and `bandit`
# without a manual install. The container state is cached after this hook
# completes, so subsequent sessions start ready.
#
# Local (non-web) sessions are untouched: the whole script is gated on
# CLAUDE_CODE_REMOTE, because a developer's machine manages its own venv.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# The base image ships a Debian-owned `packaging` without a pip RECORD file, so
# a plain `pip install -r` aborts when a requirement needs a newer one
# ("Cannot uninstall packaging ... RECORD file not found"). Reinstalling that
# one package with --ignore-installed sidesteps the uninstall; idempotent.
python -m pip install -q --ignore-installed packaging

python -m pip install -q -r requirements.txt -r requirements-dev.txt

# mypy needs these stubs (CLAUDE.md "Type Check"); harmless if already present.
python -m pip install -q types-PyYAML types-paramiko

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  # The image also carries a separate uv-tool `mypy`/`ruff` in ~/.local/bin that
  # shadows the pip-installed ones and lacks the stubs. Put the project's tools first.
  echo 'export PATH="/usr/local/bin:$PATH"' >> "$CLAUDE_ENV_FILE"
  # CLAUDE.md: never run the app/CLI without DISTRICTSYNC_DATA_DIR (a bare run
  # writes the real profile's etl_tool.log + history.db). Default it to a scratch
  # dir so an ad-hoc `python -m src.main ...` in a web session is always isolated.
  # Must be ABSOLUTE (a relative value is refused by paths.user_data_dir()).
  echo 'export DISTRICTSYNC_DATA_DIR="/tmp/districtsync-web-profile"' >> "$CLAUDE_ENV_FILE"
fi
mkdir -p /tmp/districtsync-web-profile

python - <<'PY'
import pandas, yaml, pydantic, flet, pytest, ruff, mypy, bandit  # noqa: F401
print("session-start: DistrictSync toolchain ready")
PY
