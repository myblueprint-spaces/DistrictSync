.PHONY: install test test-cov lint fmt typecheck build-win clean validate-config

install:
	pip install -r requirements.txt -r requirements-dev.txt

test:
	python -m pytest tests/ -v

test-cov:
	python -m pytest tests/ --cov=src --cov-report=term-missing --cov-fail-under=80

lint:
	ruff check src/ tests/

fmt:
	ruff check src/ tests/ --fix

# Mirror .github/workflows/ci.yml — keep the --exclude pattern in lockstep.
typecheck:
	mypy src/ --exclude 'src/ui_flet'

validate-config:
	python -c "from src.config.loader import load_config; [(load_config(n), print(n+': OK')) for n in ['myedbc','sd10myedbc','sd27myedbc','sd38myedbc','sd40myedbc','sd48myedbc','sd51myedbc','sd54myedbc','sd60myedbc','sd67myedbc','sd69myedbc','sd71myedbc','sd74myedbc','sd75myedbc','sd83myedbc','unitychristianmyedbc','mbp_all','mbp_core','mbponly','sd51attendance']]"

# The embedded Flutter client flavor. `light` on every OS since 2026-07-28 (D-0037-1)
# — the app plays no audio or video, so the `full` client's media stack is dead
# weight in every district download. EXPORTED because `flet pack` and the warm-up
# both read it from the environment; keep it in lockstep with flet-pack.yml's matrix,
# or a local build will not match the released binary. Override per-invocation:
#   FLET_DESKTOP_FLAVOR=full make build-win
FLET_DESKTOP_FLAVOR ?= light
export FLET_DESKTOP_FLAVOR

# Build the windowed/no-console/offline Flet-default .exe locally (Windows) — THE
# public release binary. Packs src/main.py: no args → the Flet shell, --sis/--input/
# --output → the CLI. Mirrors .github/workflows/flet-pack.yml's Windows `flet pack`
# invocation so a local build matches CI (same target, same flavor, same
# hidden-imports, same raw PyInstaller args, same `;` --add-data separator).
# `flet pack` has no native --paths/--exclude-module, so those go through
# --pyinstaller-build-args (one token per flag; PyInstaller needs `--paths` and `.`
# as separate args).
# Pre-seed the client cache first if offline (the flavor MUST match the build, or
# `flet pack` embeds a client you did not warm):
#   FLET_DESKTOP_FLAVOR=light python -c "import flet_desktop; flet_desktop.ensure_client_cached()"
# Smoke it after (the window smoke, then the four CLI phases against a throwaway
# profile). This is the WINDOWS build target, so the second line is PowerShell — the
# smoke REFUSES to run without DISTRICTSYNC_DATA_DIR, and a POSIX `VAR=x cmd` prefix
# is a syntax error there, not an export:
#   python scripts/ci_flet_pack_smoke.py dist DistrictSync --require-close
#   $env:DISTRICTSYNC_DATA_DIR="$env:TEMP\dsync-smoke"; python scripts/ci_flet_pack_smoke.py dist DistrictSync --cli-smoke
build-win:
	flet pack src/main.py --name DistrictSync \
	  --yes \
	  --icon assets/districtsync.ico \
	  --add-data "config;config" \
	  --add-data "assets;assets" \
	  --hidden-import flet \
	  --hidden-import flet_desktop \
	  --hidden-import src.ui_flet.launcher \
	  --hidden-import src.ui_flet.shell \
	  --hidden-import src.ui_flet.nav \
	  --hidden-import src.ui_flet.tokens \
	  --hidden-import src.ui_flet.theme \
	  --hidden-import tkinter \
	  --hidden-import pandas \
	  --hidden-import pydantic \
	  --hidden-import pydantic_core \
	  --hidden-import yaml \
	  --hidden-import logging.config \
	  --hidden-import src.etl.transformers.registry \
	  --hidden-import src.etl.transformers.context \
	  --hidden-import src.etl.transformers.base \
	  --hidden-import src.etl.transformers.students \
	  --hidden-import src.etl.transformers.staff \
	  --hidden-import src.etl.transformers.family \
	  --hidden-import src.etl.transformers.classes \
	  --hidden-import src.etl.transformers.enrollments \
	  --hidden-import src.etl.transformers.blended \
	  --hidden-import src.etl.transformers.course_info \
	  --hidden-import src.etl.transformers.student_courses \
	  --hidden-import src.etl.transformers.student_attendance \
	  --hidden-import src.config.app_config \
	  --hidden-import src.config.loader \
	  --hidden-import src.utils.paths \
	  --hidden-import src.utils.validators \
	  --hidden-import src.utils.logger \
	  --hidden-import src.utils.version \
	  --hidden-import src.scheduler.windows \
	  --hidden-import src.scheduler.task_com \
	  --hidden-import src.scheduler.elevated_apply \
	  --hidden-import src.scheduler.linux \
	  --hidden-import keyring.backends.Windows \
	  --hidden-import win32com \
	  --hidden-import win32com.client \
	  --hidden-import pythoncom \
	  --hidden-import win32timezone \
	  --pyinstaller-build-args="--paths" --pyinstaller-build-args="." \
	  --pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="streamlit" \
	  --pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="src.ui"

# Linux and macOS builds are produced automatically by GitHub Actions on tag push.
# To release all three platforms: git tag v1.x.0 && git push origin --tags

clean:
	rm -rf build/ dist/ *.spec __pycache__ .pytest_cache .coverage site/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
