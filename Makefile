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
	python -c "from src.config.loader import load_config; [(load_config(n), print(n+': OK')) for n in ['myedbc','sd40myedbc','sd48myedbc','sd51myedbc','sd54myedbc','sd60myedbc','sd74myedbc','mbp_all','mbp_core','mbponly','sd51attendance']]"

# Build the windowed/no-console/offline Flet-default .exe locally (Windows) — THE
# public release binary. Packs src/main.py: no args → the Flet shell, --sis/--input/
# --output → the CLI. Mirrors .github/workflows/flet-pack.yml's Windows `flet pack`
# invocation so a local build matches CI (same target, same hidden-imports, same raw
# PyInstaller args, same `;` --add-data separator). `flet pack` has no native
# --paths/--exclude-module, so those go through --pyinstaller-build-args (one token
# per flag; PyInstaller needs `--paths` and `.` as separate args).
# Pre-seed the client cache first if offline:
#   python -c "import flet_desktop; flet_desktop.ensure_client_cached()"
# Smoke it after:
#   python scripts/ci_flet_pack_smoke.py dist DistrictSync --require-close
build-win:
	flet pack src/main.py --name DistrictSync \
	  --yes \
	  --add-data "config;config" \
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
	  --hidden-import src.scheduler.linux \
	  --hidden-import keyring.backends.Windows \
	  --pyinstaller-build-args="--paths" --pyinstaller-build-args="." \
	  --pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="streamlit" \
	  --pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="src.ui"

# Linux and macOS builds are produced automatically by GitHub Actions on tag push.
# To release all three platforms: git tag v1.x.0 && git push origin --tags

clean:
	rm -rf build/ dist/ *.spec __pycache__ .pytest_cache .coverage site/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
