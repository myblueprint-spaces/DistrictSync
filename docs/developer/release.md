# Release Process

All builds and releases are automated via GitHub Actions. The process is:

1. Push a version tag → Actions runs tests → packs one Flet exe per OS → creates a GitHub Release with all three files (+ checksums) attached.

There is **no manual version bump** — the exe's `--version` is stamped from the git tag at build time (see [Versioning](#versioning) below).

---

## Tagging a release

```bash
git tag v1.x.0
git push origin main --tags
```

The tag must start with `v` to trigger the release workflow. That's it — no file to edit or commit first.

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
       ▼
┌────────────────────────────────────────┐
│  build-flet job                        │
│  uses: ./.github/workflows/flet-pack.yml│
│  reusable matrix: windows-latest /      │
│  ubuntu-22.04 / macos-latest            │
│  → one DistrictSync exe per OS          │
└──────────────────┬──────────────────────┘
                    ▼
          ┌──────────────────────┐
          │  publish-release job │
          │  softprops/action-gh-release
          │  attaches 3 binaries +
          │  SHA256SUMS.txt       │
          └──────────────────────┘
```

`build-flet` calls the reusable `.github/workflows/flet-pack.yml` workflow (the same workflow used for PR/dispatch smoke runs via `flet-verify.yml`). For each OS in the matrix it:

1. Checks out the repo.
2. Stamps `src/_version.py` from the tag (see [Versioning](#versioning)).
3. Installs Python 3.13 + `requirements.txt` + `requirements-dev.txt` (single source of pins — no inline `pip install`).
4. Runs `flet pack src/main.py --name DistrictSync` — one windowed, no-console, offline exe that packs **both** the Flet UI and the CLI (no-argv → UI; `--sis`/`--input`/`--output` → CLI).
5. Asserts the packed exe actually embeds the Flet client (offline guarantee) and smoke-tests the real exe (Windows/macOS directly, Linux under `xvfb-run`).
6. Uploads the binary as a build artifact (`DistrictSync-<matrix.os>`, retained 5 days).

`publish-release` downloads all three artifacts, renames them to `DistrictSync-windows.exe` / `DistrictSync-linux` / `DistrictSync-macos`, computes `SHA256SUMS.txt`, and creates the GitHub Release with auto-generated release notes plus a fixed downloads table.

There are no separate per-platform PyInstaller jobs and no Streamlit build step — one `flet pack` invocation per OS produces the entire app.

---

## Flet pack hidden imports

`flet-pack.yml` lists hidden imports explicitly (belt-and-braces alongside `registry.py`'s static imports of the transformers):

```
--hidden-import flet
--hidden-import flet_desktop
--hidden-import src.ui_flet.launcher
--hidden-import src.ui_flet.shell
--hidden-import src.ui_flet.nav
--hidden-import src.ui_flet.tokens
--hidden-import src.ui_flet.theme
--hidden-import tkinter
--hidden-import pandas
--hidden-import pydantic
--hidden-import pydantic_core
--hidden-import yaml
--hidden-import logging.config
--hidden-import src.etl.transformers.registry
--hidden-import src.etl.transformers.context
--hidden-import src.etl.transformers.base
--hidden-import src.etl.transformers.students
--hidden-import src.etl.transformers.staff
--hidden-import src.etl.transformers.family
--hidden-import src.etl.transformers.classes
--hidden-import src.etl.transformers.enrollments
--hidden-import src.etl.transformers.blended
--hidden-import src.etl.transformers.course_info
--hidden-import src.etl.transformers.student_courses
--hidden-import src.etl.transformers.student_attendance
--hidden-import src.config.app_config
--hidden-import src.config.loader
--hidden-import src.utils.paths
--hidden-import src.utils.validators
--hidden-import src.utils.logger
--hidden-import src.utils.version
--hidden-import src._version
--hidden-import src.scheduler.windows   # Windows runners only
--hidden-import src.scheduler.linux     # Linux/macOS runners; also always added
--hidden-import keyring.backends.Windows        # Windows
--hidden-import keyring.backends.macOS          # macOS
--hidden-import keyring.backends.SecretService  # Linux
--hidden-import keyring.backends.libsecret      # Linux only
```

Plus two `--pyinstaller-build-args` (passed through since `flet pack` has no native `--paths`/`--exclude-module`):

```
--pyinstaller-build-args="--paths" --pyinstaller-build-args="."
--pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="streamlit"
--pyinstaller-build-args="--exclude-module" --pyinstaller-build-args="src.ui"
```

The `streamlit` / `src.ui` excludes are belt-and-braces — both were removed at CUT-1, so this just guarantees a stray transitive dependency can never re-bloat the exe with the dead UI stack.

If you add a new dependency that PyInstaller's static analysis misses, add a `--hidden-import` for it in `flet-pack.yml`.

---

## Bundled config files

```
--add-data "config;config"   (Windows — semicolon separator)
--add-data "config:config"   (Linux/macOS — colon separator)
```

The `config/mappings/` YAML files are embedded in the executable. Partners do not need a separate config directory. If a new district config YAML is added, it is included automatically on the next release.

---

## Versioning

There is **no version field to bump manually** — the git tag is the single source of truth for the released version.

`flet-pack.yml` stamps a small `src/_version.py` module immediately before packing:

```bash
if [ "$GITHUB_REF_TYPE" = "tag" ]; then
  echo "version = '${GITHUB_REF_NAME#v}'" > src/_version.py   # e.g. v1.4.0 -> '1.4.0'
else
  echo "version = 'dev'" > src/_version.py                    # PR / manual dispatch
fi
```

That file is bundled into the exe via `--hidden-import src._version` and is git-ignored — it only exists inside a build. At runtime, `app_version()` (`src/utils/version.py`) resolves the version in this order:

1. `src/_version.py` — present in any PyInstaller build; this is what a frozen exe reports.
2. `importlib.metadata.version("districtsync")` — an editable/`pip install` from a source checkout.
3. `"dev"` — an unbuilt, uninstalled source checkout (the fallback you'll see running from a plain clone).

```bash
DistrictSync.exe --version
# DistrictSync 1.4.0        (built from tag v1.4.0)

python -m src.main --version
# DistrictSync dev          (source checkout, not built)
```

Use [semantic versioning](https://semver.org/) for the tag itself:

- **Patch** (`v1.0.1`) — bug fix, no behaviour change
- **Minor** (`v1.1.0`) — new feature, backward compatible (new district config, new CLI flag)
- **Major** (`v2.0.0`) — breaking change (output CSV schema change, renamed flags)

---

## Checklist before tagging

- [ ] All tests pass locally: `python -m pytest tests/ -v`
- [ ] Coverage is still ≥ 80%: `python -m pytest tests/ --cov=src --cov-fail-under=80`
- [ ] Configs validate: `make validate-config`
- [ ] Lint passes: `ruff check src/ tests/`
- [ ] Format check passes: `ruff format --check src/ tests/`
- [ ] Type check passes: `mypy src/ --exclude 'src/ui_flet'`
- [ ] Security scan passes: `bandit -r src/`
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

These URLs never change and are safe to use in documentation, scripts, or partner emails. Each release also attaches `SHA256SUMS.txt` so a download can be verified (e.g. `sha256sum -c SHA256SUMS.txt`); binaries are not yet code-signed.

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
