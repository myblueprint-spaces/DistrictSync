.PHONY: install test test-cov lint fmt typecheck ui build-win clean validate-config docs docs-serve

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
	mypy src/ --exclude 'src/ui|src/ui_flet'

ui:
	streamlit run src/ui/Home.py

validate-config:
	python -c "from src.config.loader import load_config; [(load_config(n), print(n+': OK')) for n in ['myedbc','sd40myedbc','sd48myedbc','sd51myedbc','sd54myedbc','sd74myedbc','mbp_all','mbp_core','mbponly','sd51attendance']]"

# Build Windows .exe locally (must run on Windows).
# Mirrors .github/workflows/release.yml:build-windows so local builds
# match CI.
#
# Why --paths=. + explicit --hidden-import for every src.* submodule:
#   Streamlit pages (src/ui/pages/*.py) are exec()'d at runtime, so
#   PyInstaller's static analyzer (which starts at src/main.py) never
#   sees `from src.scheduler.windows import ...`, `from src.ui.brand
#   import ...`, etc. --paths=. lets PyInstaller resolve the `src`
#   package from the repo root, and --collect-submodules=src scoops
#   up every submodule. The explicit --hidden-import lines below are
#   belt-and-suspenders: if a future pyinstaller changes how
#   --collect-submodules discovers packages, the listed modules are
#   still guaranteed to be bundled.
#
#   paramiko + keyring are top-level imports in src/sftp/uploader.py
#   so PyInstaller picks them up automatically; only
#   keyring.backends.Windows still needs --hidden-import because
#   keyring discovers credential-store backends dynamically.
build-win:
	pyinstaller --onefile --name DistrictSync \
	  --add-data "config;config" \
	  --add-data "src/ui;src/ui" \
	  --add-data "docs;docs" \
	  --collect-all streamlit \
	  --collect-submodules src \
	  --paths=. \
	  --hidden-import=pandas \
	  --hidden-import=yaml \
	  --hidden-import=logging.config \
	  --hidden-import=pydantic \
	  --hidden-import=pydantic_core \
	  --hidden-import=keyring.backends.Windows \
	  --hidden-import=src.scheduler.windows \
	  --hidden-import=src.scheduler.linux \
	  --hidden-import=src.ui.brand \
	  --hidden-import=src.ui.mapping_helpers \
	  --hidden-import=src.ui.launcher \
	  --hidden-import=src.ui.folder_picker \
	  --hidden-import=tkinter \
	  --hidden-import=src.etl.transformers.base \
	  --hidden-import=src.etl.transformers.classes \
	  --hidden-import=src.etl.transformers.enrollments \
	  --hidden-import=src.etl.transformers.blended \
	  --hidden-import=src.etl.transformers.students \
	  --hidden-import=src.etl.transformers.staff \
	  --hidden-import=src.etl.transformers.family \
	  --hidden-import=src.etl.transformers.course_info \
	  --hidden-import=src.etl.transformers.student_courses \
	  --hidden-import=src.etl.transformers.student_attendance \
	  --hidden-import=src.etl.transformers.registry \
	  --hidden-import=src.etl.transformers.context \
	  src/main.py

# Linux and macOS builds are produced automatically by GitHub Actions on tag push.
# To release all three platforms: git tag v1.x.0 && git push origin --tags

docs:
	mkdocs build

docs-serve:
	mkdocs serve

clean:
	rm -rf build/ dist/ *.spec __pycache__ .pytest_cache .coverage site/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
