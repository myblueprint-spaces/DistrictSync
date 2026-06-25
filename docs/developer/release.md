# Release Process

All builds and releases are automated via GitHub Actions. The process is:

1. Push a version tag → Actions runs tests → builds 3 platform binaries → creates a GitHub Release with all three files attached.

---

## Tagging a release

```bash
# The version comes from the tag — no pyproject.toml bump needed.
git tag v1.x.0
git push origin main --tags
```

The tag must start with `v` to trigger the release workflow. The build stamps that tag into the executable's `--version` automatically.

---

## What the pipeline does

The release workflow (`.github/workflows/release.yml`) runs in this order:

```
push tag v*
    │
    ▼
┌──────────────┐
│  test job    │  python -m pytest + 80% coverage gate
└──────┬───────┘
       │ (must pass)
       ├──────────────────────┬───────────────────────┐
       ▼                      ▼                       ▼
┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ build-windows│   │   build-linux    │   │   build-macos    │
│ windows-latest│  │  ubuntu-latest   │   │  macos-latest    │
│ → .exe        │  │  → DistrictSync-linux│   │  → DistrictSync-macos│
└──────┬────────┘  └────────┬─────────┘   └──────────┬───────┘
       └───────────────────┬┘                         │
                           ▼─────────────────────────-┘
                   ┌──────────────────────┐
                   │  publish-release job │
                   │  softprops/action-gh-release
                   │  attaches 3 binaries │
                   └──────────────────────┘
```

Each build job:
1. Checks out the repo
2. Installs Python 3.11 + `requirements.txt` + `pyinstaller`
3. Stamps the version from the tag into `src/_version.py` (bundled, so `--version` is correct)
4. Runs `pyinstaller --onefile` with `config/` bundled via `--add-data`
5. Uploads the binary as a build artifact (retained 5 days)

The publish job downloads all three artifacts, renames them (`DistrictSync-windows.exe`, `DistrictSync-linux`, `DistrictSync-macos`), and creates the GitHub Release with auto-generated release notes.

---

## PyInstaller hidden imports

These are required because PyInstaller's static analysis misses some imports:

```
--hidden-import=pandas
--hidden-import=yaml
--hidden-import=logging.config
--hidden-import=pydantic
--hidden-import=pydantic_core
--hidden-import=src._version
```

If you add a new dependency that PyInstaller silently misses, add it here in `release.yml`.

---

## Bundled config files

```
--add-data "config;config"   (Windows — semicolon separator)
--add-data "config:config"   (Linux/macOS — colon separator)
```

The `config/mappings/` YAML files are embedded in the executable. Partners do not need a separate config directory. If a new district config YAML is added, it is included automatically on the next release.

---

## Versioning

The version reported by the built executable is **stamped from the git tag at build time** — each release build job writes `src/_version.py` from the pushed tag (e.g. `v1.2.0` → `version = '1.2.0'`), and the CLI reads it:

```bash
DistrictSync.exe --version
# DistrictSync 1.2.0
```

So the **git tag is the single source of truth** for the released version — there is no manual `pyproject.toml` bump to forget. (The frozen exe cannot read installed package metadata, so it relies on this stamped file; an unbuilt source checkout reports `dev`.)

Use [semantic versioning](https://semver.org/):

- **Patch** (`1.0.1`) — bug fix, no behaviour change
- **Minor** (`1.1.0`) — new feature, backward compatible (new district config, new CLI flag)
- **Major** (`2.0.0`) — breaking change (output CSV schema change, renamed flags)

---

## Checklist before tagging

- [ ] All tests pass locally: `python -m pytest tests/ -v`
- [ ] Coverage is still ≥ 80%: `python -m pytest tests/ --cov=src --cov-fail-under=80`
- [ ] Configs validate: `make validate-config`
- [ ] Lint passes: `ruff check src/ tests/`
- [ ] Format check passes: `ruff format --check src/ tests/`
- [ ] Type check passes: `mypy --exclude 'src/ui' src/`
- [ ] Security scan passes: `bandit -r src/`
- [ ] You're tagging the intended version (the exe's `--version` is derived from the tag automatically — no `pyproject.toml` bump needed)
- [ ] CHANGELOG or commit messages are meaningful (Actions generates release notes from commit history)
- [ ] If output CSV schema changed — partner guide and FAQ are updated

---

## Downloading from a release

The stable permalink pattern always points to the latest release:

```
https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-windows.exe
https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-linux
https://github.com/myblueprint-spaces/DistrictSync/releases/latest/download/DistrictSync-macos
```

These URLs never change and are safe to use in documentation, scripts, or partner emails.

---

## Hotfix process

For urgent bug fixes on the current release:

```bash
git checkout -b hotfix/1.x.1
# ... make fix ...
git add -p
git commit -m "Fix: <description>"
git checkout main
git merge hotfix/1.x.1
git tag v1.x.1
git push origin main --tags
```
