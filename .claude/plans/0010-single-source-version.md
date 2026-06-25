# 0010 — Single-source the version from the git tag (setuptools-scm)

- **Status:** SUPERSEDED — descoped to the minimal tag-stamp fix (no setuptools-scm). What actually landed: each release build job writes `src/_version.py` from `$GITHUB_REF_NAME` + `main.py._resolve_version()` reads it; the CI guard was reverted. See `docs/claugentic-DECISIONS.md` (2026-06-25). Kept for the investigation record (the "exe reports `dev`" finding + the rejected setuptools-scm design).
- **Roadmap item:** n/a (started as v3.3.1 release-prep follow-up: pyproject↔tag drift)
- **References:** `docs/claugentic-DECISIONS.md` · `docs/developer/release.md` · `.github/workflows/release.yml` · `Makefile` · `src/main.py`

## Problem

Two defects, one root cause (the version is typed by hand in `pyproject.toml` and the runtime read is disconnected from the build):

1. **Drift (reported):** `pyproject.toml` `[project].version` said `3.2.0` after `v3.3.0` was tagged — the release branch cut the tag but never bumped the literal. A manual checklist line ([docs/developer/release.md:124](docs/developer/release.md)) existed for exactly this and was skipped.
2. **Bigger, discovered during this work — the distributed exe can't report its version at all.** [src/main.py:197](src/main.py) reads `importlib.metadata.version("districtsync")` with a `"dev"` fallback. The package is **never installed** (no `setup.py`; `requirements.txt` has no `-e .`; the build runs only `pip install -r requirements.txt pyinstaller`) and the build never `--copy-metadata`s it. Verified empirically: `python -m src.main --version` → `DistrictSync dev`, and `importlib.metadata.version("districtsync")` raises `PackageNotFoundError`. So the v3.3.0 `.exe` reported `dev`, not `3.2.0` (the `3.2.0` seen was a stale dev-shell `pip install -e .`). **Any real fix must route the version *into* the bundle, not merely stop two literals disagreeing.**

## Goals / Non-goals

- **Goal:** The git tag is the single source of truth for the version. No hand-typed version literal exists anywhere to drift.
- **Goal:** The frozen exe (and `python -m src.main`) reports the correct released version — `DistrictSync 3.3.1` for a `v3.3.1` build.
- **Goal:** Remove the now-redundant CI guard added earlier this session (drift becomes structurally impossible).
- **Goal:** Simplify the documented release process (the "bump pyproject" step disappears — just tag).
- **Non-goal:** Making the project `pip install .`-able as a proper wheel. The flat `src/`-as-package layout (`from src...`, `[project.scripts] districtsync = "src.main:main"`) is pre-existing and untouched. We deliberately **avoid** `pip install .` entirely (see Approach).
- **Non-goal:** Changing any ETL behavior. SD74 snapshot must stay byte-identical (the version path is outside the ETL pipeline).
- **Non-goal:** Runtime version derivation from git in unbuilt dev source (local `--version` stays `dev` when `src/_version.py` is absent — unchanged behavior).

## Approach

Adopt **setuptools-scm** with a written `version_file`, and generate that file in CI with the **standalone CLI** rather than a package build/install.

Why this shape (verified in a **throwaway venv** — `setuptools-scm` is NOT installed in the repo env; these are not in-repo-reproducible without installing it):
- `setuptools-scm` computes the version from `git describe`. On a **clean tag checkout** (what a release build is) it yields the bare tag, e.g. `3.3.1`. In the throwaway venv, run against *this* worktree it produced `3.3.2.dev0+g…d<date>` — that `.devN+…d<date>` is the **`--dirty`/local-version** marker from this worktree's uncommitted plan/workflow/doc edits, **not** "ahead of tag" (HEAD is *exactly* on `v3.3.1`: `git describe --tags` → `v3.3.1`). A clean checkout of the tag yields the bare `3.3.1`.
- **`python -m setuptools_scm --force-write-version-files`** writes the configured `version_file` standalone — **no `pip install .` needed** (verified in the throwaway venv: the flag is present in `--help`). This sidesteps the flat-`src`-layout packaging risk completely. The build just needs `setuptools-scm` pip-installed + git tags present.
- The written `src/_version.py` is a normal module already bundled by the existing `--collect-submodules src` (belt-and-suspenders `--hidden-import=src._version` added), so the exe reads the version with **zero metadata dependency**.
- `main.py` resolves version in order: `src._version` → `importlib.metadata` → `"dev"`. Frozen exe hits branch 1; a future `pip install` hits branch 2; unbuilt dev source hits branch 3.

Alternatives rejected:
- **CI guard (the work done earlier this session):** catches drift but doesn't fix the "exe reports dev" bug, and leaves a hand-typed literal. User chose the root-cause fix. → removed by this plan.
- **Minimal: CI writes `_version.py` from `$GITHUB_REF_NAME`, pyproject stays static:** no new dep, but the static `pyproject` literal can still cosmetically drift → not a complete single-source. Rejected per user choice.
- **`pip install .` / `--copy-metadata` to feed `importlib.metadata`:** drags in the flat-`src`-layout wheel-build problem; metadata-in-onefile is fiddlier than a bundled module. Rejected.

## Affected files

- `pyproject.toml` — `build-system.requires` += `setuptools-scm>=8`; `[project]` drop `version`, add `dynamic = ["version"]`; new `[tool.setuptools_scm]` (`version_file = "src/_version.py"` — **no `fallback_version`**, so a build that can't compute the version fails loudly per #5); `[project.optional-dependencies].dev` += `setuptools-scm>=8`.
- `.gitignore` — add `src/_version.py` (generated).
- `src/main.py` — add a **module-level** `_resolve_version() -> str` (src._version → importlib.metadata → "dev"); the `if __name__ == "__main__":` block (lines 196–212, **not** the legacy `def main()` shim at 60–62) calls it to build the argparse `version=` string. Module-level so `tests/test_version.py` can import it.
- `.github/workflows/release.yml` — (a) **remove** the guard step added earlier in the `test` job; (b) each build job (windows/linux/macos): `checkout` with `fetch-depth: 0`, add `setuptools-scm` to the `pip install`, add a `python -m setuptools_scm --force-write-version-files` step **before** `pyinstaller` (ordering non-negotiable), add `--hidden-import=src._version`, then a **fail-loud assertion step** that the written `src/_version.py` version equals the tag (`${GITHUB_REF_NAME#v}`) — the spirit of the deleted guard, now derived from the generated file (resolves #5).
- `Makefile` — `build-win`: add `python -m setuptools_scm --force-write-version-files` before pyinstaller + `--hidden-import=src._version`; the recipe must **fail loud** with a clear message if `setuptools_scm` is missing (no `-` ignore-errors prefix; a guard line that errors with guidance), never silently bundle a stale `src/_version.py`.
- `scripts/check_release_version.py` — **delete** (guard, now redundant).
- `tests/test_check_release_version.py` — **delete** (guard test).
- `tests/test_version.py` — **new** — unit-tests `_resolve_version()` across all three branches.
- `docs/developer/release.md` — revert the guard edits; rewrite "Versioning"/"Tagging"/"Checklist"/"Hotfix" to the tag-derived flow (no pyproject bump step); **also** add `src._version` to the "PyInstaller hidden imports" list (lines ~62–82) which is already stale (still lists `paramiko`/`keyring` the workflow no longer passes) — note the staleness so it stops drifting.
- `docs/claugentic-DECISIONS.md` — supersede the earlier guard entry: record the **incident** (v3.3.0 shipped `--version dev`; the guard was an incomplete fix) + the **decision** (tag = single source via setuptools-scm standalone CLI, no `pip install .`, no `fallback_version`). State explicitly that ARCHITECTURE_TREE needs no change (gitignored `src/_version.py` + `scripts/`/`tests/` are out of the gate's scope).
- `CHANGELOG.md` — one line under the existing `[Unreleased]` heading (line 10).

## Risks & mitigations

- **Shallow checkout → setuptools-scm can't `git describe` → wrong/failed version.** → `fetch-depth: 0` on the three build jobs. **No `fallback_version`** (a `0.0.0` fallback would silently ship a degenerate build — violates fail-loudly); instead the post-write release step asserts the derived version == the tag and fails the build otherwise.
- **A misconfigured build derives a `.devN` version on a release tag (e.g. dirty/extra commit).** → the post-`force-write` assertion (`src/_version.py` version == `${GITHUB_REF_NAME#v}`) fails the build loudly rather than shipping `3.3.2.dev0+…` as a release.
- **PyInstaller misses `src/_version.py`.** → already collected by `--collect-submodules src`; add explicit `--hidden-import=src._version` (both `release.yml` ×3 and `Makefile`).
- **mypy can't see the generated `src._version`.** → `pyproject` mypy has `ignore_missing_imports = true`, which already suppresses the missing-import error; the `from src._version import version` sits inside a `try/except ImportError`. No `# type: ignore` needed (confirm during impl).
- **`setuptools-scm` not installed in a build env running the CLI.** → added to each build job's `pip install` and to `[dev]` for local `make build-win`.
- **Can't fully exercise a real frozen-exe build here** (slow, cross-platform). → mechanism verified by-construction + locally (force-write + runtime read smoke test, see Test strategy); a real `--version` check on the built exe is an explicit release-time DoD item, not silently claimed as proven.
- **SD74 snapshot / ETL output.** → version path is entirely outside the ETL pipeline; no transformer/loader/config touched → byte-identical. Stated, and the snapshot test run proves it.
- **Local dev `--version` still says `dev`.** → unchanged, acceptable (no `_version.py` until a build runs).

## Test strategy

- **New `tests/test_version.py`:** monkeypatch `sys.modules["src._version"]` (fake `version="9.9.9"`) → `_resolve_version() == "9.9.9"`; remove it + patch `importlib.metadata.version` to return `"1.2.3"` → `"1.2.3"`; patch it to raise `PackageNotFoundError` → `"dev"`.
- **Local mechanism smoke (manual, recorded in impl):** in a venv with setuptools-scm, `python -m setuptools_scm --force-write-version-files` → assert `src/_version.py` exists and `python -c "from src._version import version"` imports; `python -m src.main --version` now reads it; then delete the file (gitignored).
- **Regression:** full `pytest` green (640+), SD74 snapshot byte-identical, ruff/mypy/bandit clean, `make validate-config` green.
- **Removed:** the guard's 14 tests go away with the script.
- **Coverage gap (explicit, per review #3):** `tests/test_cli.py` bypasses argparse, so `--version` and the `if __name__ == "__main__":` wiring are **not** exercised by any unit test — `tests/test_version.py` covers only `_resolve_version()`. **Therefore the real-exe `--version` check is a MANDATORY release-time DoD item, not optional:** the first `v*` tag built after this lands must be downloaded and `DistrictSync --version` confirmed to print the tag version (`DistrictSync X.Y.Z`, not `dev`/`0.0.0`). The CI assertion step (derived-version == tag) backstops this at build time.

## Decomposition (slices)

Single slice — the pieces are interdependent (pyproject dynamic version + generated file + runtime read + build wiring must land together or the exe breaks) and total well under one session with no debt.

- [ ] **Slice 1 — Single-source version end-to-end.** pyproject + .gitignore + `src/main.py` `_resolve_version()` + `release.yml`/`Makefile` build wiring + delete guard (script/test) + `tests/test_version.py` + docs (`release.md`, `DECISIONS.md`, `CHANGELOG.md`). Lands complete because it's the minimal set that keeps the exe reporting a correct version while removing every drift-able literal; verified by the unit tests + local smoke + full gate run, with the real-exe `--version` check called out as the release-time DoD item.

---

## Review  _(filled by plan-reviewer, Stage 3)_

_Reviewer: `plan-reviewer` (Opus 4.x, clean-context). Same model family as a likely Opus builder — treat as a rubber-stamp-risk reduction, not an independent oracle._

- **Verdict:** CHANGES REQUIRED (small, mostly precision/completeness — the core approach is sound)

The setuptools-scm + standalone `--force-write-version-files` shape is **correct and well-chosen** for this repo. I independently verified the load-bearing facts:
- Runtime read is `importlib.metadata.version("districtsync")` at [src/main.py:197](src/main.py); the package is never installed (no `setup.py`; `requirements.txt`/`requirements-dev.txt` have no `-e .`; builds run `pip install -r requirements.txt pyinstaller`). The "exe reports `dev`" diagnosis is real.
- No `[tool.setuptools]`/packages config exists, so `dynamic = ["version"]` won't collide with packaging config. `build-system.requires` already pins `setuptools>=61.0` (satisfies setuptools-scm v8). Nothing reads `[project].version` statically except `scripts/check_release_version.py` (being deleted) — confirmed via grep across `src/`, `scripts/`, `tests/`, `mkdocs.yml`. So making the version dynamic is safe.
- Architecture-tree gate: `in_scope_files()` uses `git ls-files --others --exclude-standard` ([scripts/claugentic-check_architecture_tree.py:329](scripts/claugentic-check_architecture_tree.py)) and `INCLUDE_GLOBS = [":(glob)src/**/*.py", ...]` (line 74). A **gitignored** `src/_version.py` is therefore out of scope — confirmed. `scripts/` and `tests/` are also outside the globs, so deleting the guard script/test and adding `tests/test_version.py` need no tree edit (the existing uncommitted DECISIONS entry already states this).
- `fetch-depth: 0` on a tag push resolves the shallow-checkout problem: this repo is non-shallow and `git describe --tags` returns exactly `v3.3.1` on a clean HEAD.

**Required changes (all minor):**

1. **Fix the inaccurate empirical claim in Approach (line 29).** It says setuptools-scm "against this dirty worktree it correctly produced `3.3.2.dev0+g…`" and calls the worktree "dirty/ahead-of-tag." Two problems: (a) `setuptools_scm` is **not installed in this environment** (`python -m setuptools_scm` → `No module named setuptools_scm`), so that exact output string cannot have been produced here — if it was produced in a throwaway venv, say so explicitly and don't present it as reproducible in-repo; (b) HEAD is **exactly on** `v3.3.1` (`git describe --tags` = `v3.3.1`), not "ahead of tag" — the `.dev0` bump comes purely from the working tree being **dirty** (uncommitted plan/workflow/doc edits via `--dirty`), not from commits ahead. Reword to "a dirty working tree yields a `.devN` local-version; a clean tag checkout yields the bare tag." The conclusion is right; the reasoning is imprecise and overstates what was verified. (Honesty-register: don't launder an unrun/elsewhere-run check into an in-repo "Verified.")

2. **Name the exact code location and the `__main__`-vs-`main()` subtlety.** The version read and the `--version` argument live entirely inside the `if __name__ == "__main__":` block ([src/main.py:196-212](src/main.py)) — **not** inside `def main()` (which is the unrelated 3-arg legacy shim at [src/main.py:60-62](src/main.py) that `[project.scripts]` points at). The plan's "lines ~196–199" is right but the spec must state that `_resolve_version()` is a **module-level** function (so `tests/test_version.py` can import it) and that the `__main__` block calls it to build the argparse `version=` string. Leaving this implicit risks an implementer tucking it inside `__main__` where it can't be unit-tested.

3. **State that `--version` has no existing test and the `__main__` path stays uncovered.** `tests/test_cli.py` deliberately calls `run_pipeline()` directly to bypass argparse, so `--version` is currently untested and the `__main__` block is not exercised by the suite. The new `tests/test_version.py` covers `_resolve_version()` (good), but the plan should explicitly note the `__main__` wiring itself remains unverified by unit tests — which is why the **real-exe `--version` release-time DoD check is load-bearing, not optional**. Make that DoD item mandatory in the spec, not a footnote.

4. **Cover the `make build-win` local path failing loud.** The build jobs install `requirements.txt pyinstaller` (release.yml:36/91/133); the plan adds setuptools-scm to "each build job's `pip install`." That works. But `make build-win` does **not** run `make install` first, so a developer's env needs setuptools-scm from `[dev]` (plan lists `[dev] += setuptools-scm` — good). The Makefile target should still **fail loud with a clear message** if `setuptools_scm` is missing (or the `--force-write-version-files` step is absent) rather than emit a confusing PyInstaller error or silently bundle a stale `src/_version.py`. Add that to the spec.

5. **`fallback_version` choice will mask a broken build silently.** `fallback_version = "0.0.0"` means a release build where `git describe` fails (no tags fetched, detached weirdness) ships an exe reporting `0.0.0` **instead of failing** — the opposite of "fail loudly" (CLAUDE.md non-negotiable). Given the whole point is correctness of the shipped version, prefer **no `fallback_version`** in CI (let setuptools-scm error and fail the build) OR keep the fallback but add a release-job assertion that the written `src/_version.py` version equals the tag (re-using the spirit of the deleted guard, now derived from the generated file). Decide and document; do not silently accept `0.0.0` as a release outcome.

6. **Verify `--collect-submodules src` actually bundles a gitignored, freshly-written `src/_version.py`.** PyInstaller scans the filesystem, so it should — but the file must exist **before** PyInstaller runs and the `--force-write-version-files` step must be ordered first (plan says so — keep that ordering explicit and non-negotiable in the spec). The explicit `--hidden-import=src._version` is the right belt-and-suspenders; list it for **all three** release jobs AND the Makefile. Confirm the Linux/macOS jobs (which lack `--add-data "src/ui;src/ui"`) still bundle it (they will — it's a code submodule, not data).

7. **Also update `docs/developer/release.md` "PyInstaller hidden imports" section, not just Versioning/Tagging/Checklist/Hotfix.** That doc's hidden-import list (lines 74-82) is already stale — it lists `paramiko`/`keyring` which the real workflow no longer passes. The plan should add `src._version` there too (and ideally note the pre-existing staleness) so the doc doesn't drift further.

**Sizing/completeness check:**
- **Slice 1 — OK as a single slice. Do not split.** The pieces are genuinely interdependent (dynamic pyproject + generated file + runtime read + build wiring must land together or the exe regresses to `dev`/breaks). File count ~10 but each edit is small and mechanical; well within one ≤1M-context session. No half-done state if landed together. Deleting the guard (script + 14-case test) and reverting the docs are correctly bundled so the repo isn't left with a now-wrong guard.
- **One completeness gap before it's "lands complete":** the spec must resolve **change #5** (fallback behavior). As written, a degenerate CI build could ship `0.0.0` and the slice would still pass its gates — that's latent debt, not done.

**Harness impact:**
- **DECISIONS.md:** the plan correctly supersedes the 2026-06-25 guard entry. Ensure the new entry records the **incident** (v3.3.0 shipped `--version dev`; guard was an incomplete fix) so it's un-cargo-cultable, plus the **decision** (tag = single source via setuptools-scm standalone CLI, no `pip install .`). Note the `fallback_version` resolution from #5.
- **ARCHITECTURE_TREE.md:** **no change required** — `src/_version.py` is gitignored (out of scope), `scripts/`+`tests/` are outside INCLUDE_GLOBS. Confirmed against the gate. State this explicitly in the plan rather than only implying it via a risk row.
- **No new STANDARD or agent needed.** Build/release-plumbing change, not a new cross-cutting pattern.
- **CHANGELOG:** add under the existing `[Unreleased]` heading (CHANGELOG.md:10).

**No YAGNI / over-engineering concerns.** Standalone `--force-write-version-files` (vs `pip install .` / `--copy-metadata`) is the *simpler* correct path and avoids the flat-`src` wheel-build trap — a good KISS call. The three-branch resolution chain (`src._version` → metadata → `dev`) is appropriately minimal, not speculative.

---

## Spec  _(Slice 1 — Stage 4; all 7 review items folded in)_

### `pyproject.toml`
- `[build-system].requires`: `["setuptools>=61.0", "setuptools-scm>=8"]`.
- `[project]`: **delete** `version = "3.3.1"`; **add** `dynamic = ["version"]` (with a comment: version derived from git tag by setuptools-scm).
- **Add** table:
  ```toml
  [tool.setuptools_scm]
  version_file = "src/_version.py"
  ```
  **No `fallback_version`** (review #5 — fail loud, don't ship `0.0.0`).
- `[project.optional-dependencies].dev`: append `"setuptools-scm>=8"`.

### `.gitignore`
- Add (under "Build and distribution folders"):
  ```
  # Generated at build time by setuptools-scm
  /src/_version.py
  ```

### `src/main.py`  (review #2 — module-level, not inside `__main__`)
- Add a **module-level** function (place above the `if __name__ == "__main__":` block):
  ```python
  def _resolve_version() -> str:
      """App version: generated _version.py (frozen exe / post-build) →
      installed package metadata → 'dev' (unbuilt source checkout)."""
      try:
          from src._version import version
          return version
      except ImportError:
          pass
      try:
          return importlib.metadata.version("districtsync")
      except importlib.metadata.PackageNotFoundError:
          return "dev"
  ```
- In `if __name__ == "__main__":` (lines 196–212) replace the inline `try/except` (196–199) with `version = _resolve_version()`. Leave `import importlib.metadata` (still used). Confirm mypy stays green (`ignore_missing_imports = true` covers the missing `src._version`); add no `# type: ignore` unless mypy actually complains.

### `tests/test_version.py`  (new)
- Three tests on `src.main._resolve_version`:
  1. generated file wins — `monkeypatch.setitem(sys.modules, "src._version", <fake module with version="9.9.9">)` → `== "9.9.9"`.
  2. metadata fallback — `monkeypatch.setitem(sys.modules, "src._version", None)` (forces `ImportError` regardless of any on-disk file) + `monkeypatch.setattr(importlib.metadata, "version", lambda n: "1.2.3")` → `== "1.2.3"`.
  3. dev fallback — same `None` for `src._version` + `importlib.metadata.version` patched to raise `PackageNotFoundError` → `== "dev"`.

### `.github/workflows/release.yml`
- **`test` job:** delete the "Verify pyproject version matches the release tag" step (the session-added guard). Leave the rest.
- **Each build job (`build-windows`, `build-linux`, `build-macos`):**
  - `uses: actions/checkout@v4` → add `with: { fetch-depth: 0 }`.
  - pip install line → append `setuptools-scm` (e.g. `pip install -r requirements.txt pyinstaller setuptools-scm`).
  - **Before** the `pyinstaller` step, add (ordering non-negotiable, review #6):
    ```yaml
      - name: Generate version file from tag
        run: python -m setuptools_scm --force-write-version-files
      - name: Assert generated version matches tag
        shell: bash
        run: |
          GEN=$(python -c "from src._version import version; print(version)")
          TAG="${GITHUB_REF_NAME#v}"
          [ "$GEN" = "$TAG" ] || { echo "ERROR: generated version '$GEN' != tag '$TAG'"; exit 1; }
          echo "OK: version $GEN matches tag $TAG"
    ```
    (`shell: bash` is required so the POSIX `${VAR#v}` works on `windows-latest`.)
  - Add `--hidden-import=src._version` to each `pyinstaller` invocation.

### `Makefile`  (review #4 — fail loud)
- `build-win`: prepend a guard + generate line before `pyinstaller`:
  ```makefile
  build-win:
  	@python -c "import setuptools_scm" || { echo "ERROR: setuptools-scm missing — run: pip install -e \".[dev]\""; exit 1; }
  	python -m setuptools_scm --force-write-version-files
  	pyinstaller --onefile --name DistrictSync \
  	  ... (existing args) ...
  	  --hidden-import=src._version \
  	  ...
  ```
  No `-` ignore-errors prefixes; never silently bundle a stale `src/_version.py`.

### Deletions
- `scripts/check_release_version.py` and `tests/test_check_release_version.py` (guard + its 14 tests).

### `docs/developer/release.md`
- Revert the session's guard edits (Drift-guard blockquote + `check_release_version.py` references in Tagging/Checklist/Hotfix).
- **Tagging a release:** version is now tag-derived — drop "Bump version in pyproject.toml first"; flow is just `git tag vX.Y.Z` + `git push --tags`.
- **Versioning:** explain `dynamic`/setuptools-scm; `--version` reads bundled `src/_version.py`; no manual bump.
- **Checklist before tagging:** remove the "version in pyproject.toml matches the tag" line (no longer applicable).
- **Hotfix process:** remove the pyproject bump step.
- **PyInstaller hidden imports** (lines ~62–82): add `src._version`; note the section is already stale (lists `paramiko`/`keyring` the workflow no longer passes) so it stops drifting.

### `docs/claugentic-DECISIONS.md`
- Replace the session's 2026-06-25 guard entry with a superseding entry recording: the **incident** (v3.3.0 exe shipped `--version dev`; the importlib.metadata read is dead in distribution; the guard was an incomplete fix), the **decision** (git tag = single source via setuptools-scm `version_file` + standalone `--force-write-version-files`, **no** `pip install .`, **no** `fallback_version`, CI asserts derived==tag), and that **ARCHITECTURE_TREE needs no change** (gitignored `src/_version.py` + `scripts/`/`tests/` are out of the gate's scope).

### `CHANGELOG.md`
- One line under `[Unreleased]` (line 10): version now derived from the git tag; `--version` reports the real release version instead of `dev`.

### Acceptance criteria
- `pyproject.toml`: no static `version`; `dynamic=["version"]`; `[tool.setuptools_scm]` present; setuptools-scm in build-system + `[dev]`.
- Unbuilt source: `python -m src.main --version` → `DistrictSync dev` (unchanged). After `--force-write-version-files` in a setuptools-scm env: `src/_version.py` exists and `--version` prints the computed version.
- `tests/test_version.py` (3) pass; guard script+test gone; **full `pytest` green**; **SD74 snapshot byte-identical**; ruff + mypy (`--exclude src/ui`) + bandit clean; `make validate-config` green.
- `release.yml`: `test` job guard-free; 3 build jobs have `fetch-depth: 0`, setuptools-scm installed, force-write **before** pyinstaller, `--hidden-import=src._version`, and the derived-version==tag assertion. `Makefile build-win` fails loud without setuptools-scm.
- Docs/DECISIONS/CHANGELOG updated as above.
- **MANDATORY release-time DoD (review #3, not optional):** the first `v*` build after this lands is downloaded and `DistrictSync --version` confirmed to print the tag version (not `dev`/`0.0.0`). Until then the runtime-read-in-frozen-exe path is verified only by-construction + the CI assertion, **not** by a passing unit test — state this honestly when landing.
