.PHONY: install test test-cov lint fmt ui build-win clean validate-config docs docs-serve

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

ui:
	streamlit run src/ui/Home.py

validate-config:
	python -c "from src.config.loader import load_config; [(load_config(n), print(n+': OK')) for n in ['myedbc','sd40myedbc','sd48myedbc','sd51myedbc','sd74myedbc']]"

# Build Windows .exe locally (must run on Windows).
# Mirrors .github/workflows/release.yml:build-windows so local builds
# match CI. paramiko + keyring are now top-level imports in
# src/sftp/uploader.py so PyInstaller picks them up automatically;
# only keyring.backends.Windows still needs --hidden-import because
# keyring discovers backends dynamically at runtime.
build-win:
	pyinstaller --onefile --name GDE2Acsv \
	  --add-data "config;config" \
	  --add-data "src/ui;src/ui" \
	  --add-data "docs;docs" \
	  --collect-all streamlit \
	  --collect-submodules src \
	  --hidden-import=pandas \
	  --hidden-import=yaml \
	  --hidden-import=logging.config \
	  --hidden-import=pydantic \
	  --hidden-import=pydantic_core \
	  --hidden-import=keyring.backends.Windows \
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
