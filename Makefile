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
	streamlit run src/ui/app.py

validate-config:
	python -c "from src.config.loader import load_config; [print(n+': OK') or load_config(n) for n in ['myedbc','sd48myedbc','sd51myedbc','sd74myedbc']]"

# Build Windows .exe locally (must run on Windows)
build-win:
	pyinstaller --onefile --name GDE2Acsv --add-data "config;config" \
	  --hidden-import=pandas --hidden-import=yaml --hidden-import=logging.config \
	  --hidden-import=pydantic --hidden-import=pydantic_core src/main.py

# Linux and macOS builds are produced automatically by GitHub Actions on tag push.
# To release all three platforms: git tag v1.x.0 && git push origin --tags

docs:
	mkdocs build

docs-serve:
	mkdocs serve

clean:
	rm -rf build/ dist/ *.spec __pycache__ .pytest_cache .coverage site/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
