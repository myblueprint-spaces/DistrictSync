# 0045 — macOS release ships a `.dmg` of the `.app` (the bare binary stays for headless)

- **Status:** Draft
- **Roadmap item:** none yet — field-reported by a district on 2026-08-31 (see Problem); a ROADMAP entry lands with the slice for the deferred items below.
- **References:** `docs/claugentic-ARCHITECTURE_TREE.md` · `docs/claugentic-DECISIONS.md` (2026-06-29 PLAT-3 offline-embed evidence · 2026-07-28 light flavor) · `.claude/plans/0041-legit-distribution-com-scheduler-onedir-installer.md` (§B distribution shape; "Linux/macOS artifacts unchanged" — this plan supersedes that row for macOS only) · `docs/FLET_1.0_CONVENTIONS.md:117,160` (macOS embed verified)

## Problem

A district downloaded the macOS release and macOS opened it in a text editor.

The cause is that we build the right artifact and publish the wrong one. `flet pack` on `macos-latest` produces **both**:

- `dist/DistrictSync.app` — the real macOS application bundle
- `dist/DistrictSync` — the bare onefile Unix binary

[`.github/workflows/release.yml:58`](.github/workflows/release.yml) takes the bare binary (`mv artifacts/macos/DistrictSync artifacts/DistrictSync-macos`) and publishes it as the sole macOS asset. Confirmed against the live release: `DistrictSync-macos`, 98 435 408 bytes, no extension. Confirmed against the v3.13.0 macOS pack job (run `32152759748`, job `95764993871`): `dist/DistrictSync.app/Contents` exists and the size step measured it at 98 548 773 bytes.

That published file cannot work by double-click on any Mac. It has no extension, and a browser download does not carry the executable bit, so LaunchServices has nothing to identify it with and Finder falls back to opening it as text. This is not a Gatekeeper problem — the app never gets as far as being blocked.

Two related findings from the same evidence:

1. **The `.app` has never been smoke-tested.** [`scripts/ci_flet_pack_smoke.py:122`](scripts/ci_flet_pack_smoke.py) orders `resolve_artifact` candidates `.exe` → bare → `.app/Contents/MacOS/<name>`, so the bare binary always wins on macOS. The job log confirms `artifact: dist/DistrictSync`. Every macOS embed/close/CLI smoke to date has exercised the artifact we are about to stop shipping, and none has exercised the one we are about to start shipping.
2. **PyInstaller warns about this exact shape**, twice per macOS pack: *"Onefile mode in combination with macOS .app bundles (windowed mode) don't make sense … and clashes with macOS's security. Please migrate to onedir mode. This will become an error in v7.0."* We pin `pyinstaller>=6,<7` (`requirements-dev.txt:35`), so v7 cannot arrive silently — but whether the onefile `.app` *launches* on a current macOS is unproven, and finding 1 is why.

Finding 2 is the load-bearing risk: this plan's whole value depends on the `.app` working, and nothing in the repo currently proves it does.

## Goals / Non-goals

- **Goal:** the macOS release asset is `DistrictSync-macos.dmg` — a compressed disk image containing `DistrictSync.app` beside an `/Applications` symlink, so the download opens to the drag-to-install gesture Mac users expect.
- **Goal:** the bare binary keeps shipping as `DistrictSync-macos` (owner decision, 2026-09-01) for a future headless-mac CLI user, on the same `chmod +x` footing as the Linux asset.
- **Goal:** CI proves the shipped thing works — the `.app` inside the built DMG launches with the Flet client embedded, and its inner executable carries the executable bit **as delivered**, not merely as built.
- **Goal:** every partner-facing surface that names the macOS download is corrected in the same change (permalink break accepted by the owner, 2026-09-01).
- **Non-goal:** code-signing / notarization. Unsigned is unchanged by this plan; the Gatekeeper copy is corrected for accuracy but the posture is plan 0041 §C's.
- **Non-goal:** a `.pkg` installer. The owner's ask is the shape the district named; a `.pkg` buys nothing we can deliver unsigned.
- **Non-goal:** migrating macOS to `--onedir` (finding 2's real fix). Blocked on `flet pack --onedir` exiting 1 on macOS (plan 0041 §Goals row 7) and not needed while the `<7` pin holds. Deferred to ROADMAP with the evidence this slice produces.
- **Non-goal:** an `.icns` app icon. Real polish, genuinely separate, and it would make this slice's diff about two things. ROADMAP.
- **Non-goal:** Windows and Linux artifacts. Untouched.

## Approach

**Build the DMG on the macOS runner, in `flet-pack.yml`, before `upload-artifact`.**

That placement is not incidental — it is forced. `actions/upload-artifact` does not preserve POSIX permission bits. The current `path: dist/DistrictSync*` glob already uploads the `.app` as loose files, and anything reassembled from that download has a non-executable `Contents/MacOS/DistrictSync`. Packing the bundle into a disk image on the runner is what carries the mode bits through to the district, because from that point on the `.app` travels as opaque image content. Assembling the DMG in `release.yml` (where the other renames happen) would package an already-broken bundle.

Mechanics, all stock macOS tooling, no new dependency:

```
staging/DistrictSync.app   (moved from dist/)
staging/Applications       (symlink -> /Applications)
hdiutil create -volname DistrictSync -srcfolder staging -ov -format UDZO dist/DistrictSync-macos.dmg
```

`UDZO` is the compressed read-only format. The `/Applications` symlink is what makes the mounted window a drag target rather than a folder of files.

**Prove it as delivered, not as built.** A new macOS-only smoke step, run against the finished `.dmg`:

1. `hdiutil verify` — the image is structurally sound.
2. `hdiutil attach -nobrowse -readonly -mountpoint <temp>` — an **explicit mountpoint**, not `/Volumes` auto-naming, so a stale `DistrictSync 1` volume can never make the asserts inspect the wrong image and so detach is deterministic. Then assert `DistrictSync.app` exists on the volume, assert `Contents/MacOS/DistrictSync` carries the executable bit, and assert `Applications` is a symlink (which also catches `hdiutil` having followed it and copied the real `/Applications` into the image).
3. Run the existing GUI/embed smoke against the **mounted** `.app` — this is the first time any macOS run proves the bundle launches with `~/.flet` moved aside and `FLET_CLIENT_URL` unreachable. Read-only mount is fine: the onefile bootloader extracts to `$TMPDIR` and the app writes only to `DISTRICTSYNC_DATA_DIR`.
4. `hdiutil detach` in an `if: always()` step, so a bad run cannot leave a mounted volume wedging the runner.

Attach/verify/detach stay in the workflow's shell, where `if: always()` cleanup belongs. Only the **assertions** move into the smoke script (`--assert-mounted-app <mountpoint> <name>`), because that is the part worth unit-testing against synthetic trees — a shell `test -x` cannot carry the negative twin the exec-bit assert needs. A full Python `--dmg` mode owning the mount lifecycle was considered and cut as machinery for its own sake.

Step 3 is the answer to finding 2, and it is deliberately **gating**. If the onefile `.app` does not launch, this plan must not land — better a red CI run than a DMG that fails differently for the same district.

**Keep the bare binary honest.** macOS uploads exactly two things: `DistrictSync-macos.dmg` and the bare `DistrictSync`. The loose `.app` is the permission-stripped trap and it is now redundant with the DMG, so it is **removed from `dist/` after the DMG smoke passes** — which leaves the existing `path: dist/DistrictSync*` glob correct as written, with no per-OS `upload_path` matrix key, no `!` exclusion patterns, and no multiline path. Ordering is load-bearing: size step → build DMG → smoke the mounted DMG → `rm -rf dist/DistrictSync.app` → upload.

**Smoke both macOS artifacts, for what each one is.** The DMG's `.app` gets the GUI/embed smoke (above). The bare binary keeps the CLI smoke phases it already runs — that is the artifact the headless population would use, and its contract is `--sis/--input/--output`, not a window. This is a reordering of coverage, not an addition: today one artifact gets both, and the shipped-to-districts one gets neither.

`resolve_artifact` needs an explicit selector rather than a reordered candidate list. Reordering would silently repoint the existing single call site and make the bare binary untestable; a `--artifact {auto,binary,bundle}` argument keeps both addressable and keeps `auto` byte-identical to today for Windows and Linux.

**Alternatives considered:**

- **`.zip` of the `.app`** — also correct, also preserves mode bits via `ditto`, less CI code. Rejected: the district asked for a DMG, and Safari's auto-unzip lands the app in `Downloads`, which is exactly the transient location `setup.py`'s `_TRANSIENT_LOCATION_WARNING` exists to warn about. The DMG's `/Applications` symlink pushes toward the stable path the scheduler wants.
- **`.pkg` installer** — real installer, correct shape for managed fleets. Rejected: unsigned `.pkg` is a worse Gatekeeper experience than an unsigned `.app`, and it is scope this problem does not need.
- **Ship the `.app` as loose files and tell users to fix permissions** — rejected as a non-fix; it replaces one broken download with a fiddlier one.
- **Assemble the DMG in `release.yml`** — rejected: the bundle's mode bits are already gone by then (see above).
- **Fix the deprecation first by moving macOS to `--onedir`** — rejected as this slice's scope: blocked upstream, unnecessary under the `<7` pin, and it would gate a live district-facing bug behind an unrelated packaging migration.

## Affected files

| Path | Change |
|---|---|
| `.github/workflows/flet-pack.yml` | macOS: staging + `hdiutil create` step; verify/attach/assert/smoke/detach(`always()`) steps; `rm -rf dist/DistrictSync.app` before upload; route the GUI smoke at the mounted bundle and the CLI smokes explicitly at the bare binary |
| `.github/workflows/release.yml` | download → new macOS asset names; `DistrictSync-macos.dmg` + `DistrictSync-macos` through rename/`chmod`/checksums/`files:`; downloads-table rows + Gatekeeper copy |
| `scripts/ci_flet_pack_smoke.py` | `resolve_artifact` gains an explicit kind selector; `--artifact {auto,binary,bundle}`; `--assert-mounted-app <mountpoint> <name>` |
| `tests/test_ci_flet_pack_smoke.py` | Rows for the selector (all three kinds × the platform layouts) and for the mounted-app assert helper, including its non-executable negative twin |
| `README.md` | macOS download row: new filename, new note |
| `docs/index.md` | Same row |
| `docs/developer/release.md` | Permalink block; the `publish-release` prose at `:60` describing three renamed binaries |
| `docs/partner/installation.md` | macOS steps, if it names the file (verify during implementation) |
| `docs/claugentic-DECISIONS.md` | Dated entry: the bug, the DMG decision, the accepted permalink break, the kept bare binary, the deferred onedir/icns items |
| `docs/claugentic-ROADMAP.md` | Deferred: macOS `--onedir` migration (PyInstaller 7 blocker), `.icns` icon, signing pointer to 0041 §C |
| `CHANGELOG.md` | Release-facing row |

`scripts/` and `.github/` are outside the architecture-tree gate's scope (`src/**/*.py` + `config/mappings/*.yaml`), so no tree entry is required — consistent with PLAT-3's precedent.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| **The onefile `.app` does not launch** (PyInstaller's "clashes with macOS's security"). This is the plan-invalidating risk. | The mounted-DMG embed smoke is gating and runs before anything ships. If it fails, the slice stops and we escalate to the onedir question with real evidence instead of shipping a second broken download. Verified on a `flet-verify.yml` dispatch **before** the PR is merged, not after. |
| A DMG built but never mounted could still be unusable | Mount-and-launch is the assertion, not `hdiutil create` exiting 0. The exec-bit assert is the specific regression that produced this bug. |
| Mounted volume left attached on a failed run wedges the runner | `detach` in an `if: always()` step, by device node captured at attach. |
| Permalink `…/latest/download/DistrictSync-macos` 404s | Accepted by the owner (2026-09-01). Every in-repo occurrence updated in this change; DECISIONS records it so a future reader knows it was chosen. Note the bare name survives for the headless artifact, so that URL keeps resolving — to the binary, as before. |
| `flet-verify.yml` shares this workflow; a macOS-only step could break PR runs | The DMG steps are `if: runner.os == 'macOS'`; both callers exercise the same path, which is the anti-rot property the shared workflow exists for. |
| CI artifact size / retention grows | Net down: the loose `.app` (~100 MB, permission-stripped and now redundant) leaves the upload; the compressed DMG replaces it. |
| SD74 snapshot / ETL output | Untouched. No `src/` change in this plan. |

## Test strategy

**Deterministic (local):** `tests/test_ci_flet_pack_smoke.py` rows for the new pure helpers — the `resolve_artifact` kind selector across Windows/Linux/macOS layouts (including `bundle` on a dist with no `.app` → `None`, and `binary` never resolving to the bundle), and the DMG mount-assert predicates fed synthetic trees. `scripts/` is outside `--cov=src`, so these are correctness rows, not coverage.

Full local gate set unchanged: suite + SD74 golden + config validation + tree-check + ruff/mypy/bandit + the no-plaintext-email scan.

**Live (the real proof):** a `flet-verify.yml` `workflow_dispatch` on this branch before merge, read and quoted per the 2026-07-30 land gate — three OSes green, and specifically the macOS job's DMG verify + mount + exec-bit assert + embed smoke. A local Windows green is explicitly not evidence for any of this.

**Manual (owner, one row):** download the DMG from the dispatch artifact on a real Mac, double-click, drag to Applications, launch, clear Gatekeeper via System Settings › Privacy & Security › Open Anyway, confirm the Setup wizard paints. CI can prove the bundle executes; it cannot prove the Finder experience, which is the thing the district actually reported.

**No vacuous greens:** the exec-bit assert gets its negative twin — a unit row proving the predicate returns False on a non-executable inner binary, so "the assert passed" cannot mean "the assert never looked".

## Decomposition (slices)

One slice. It is a single release-contract change across build + publish + docs, and splitting it opens a tag window where `publish-release` references an asset the pack job no longer produces — the exact failure mode plan 0041 §B calls out and folds its release bridge into one PR to avoid.

- [ ] **Slice 1 — macOS ships a DMG** · `hdiutil` build + gating mounted-DMG smoke in `flet-pack.yml`, `release.yml` reconciled in the same PR, smoke-script selector + `--dmg` mode + unit rows, all partner-facing docs, DECISIONS + ROADMAP + CHANGELOG · lands complete because a tag on the day it merges publishes a mountable, launchable DMG plus the unchanged bare binary, with the three-OS dispatch quoted as evidence.

---

## Review  _(Stage 3)_

- **Reviewer:** orchestrator self-review, not the `plan-reviewer` specialist — subagents are disabled in this session. Recorded honestly: this is one perspective, not an adversarial panel, and it is the weakest link in this plan's process.
- **Verdict:** PASS after three revisions, applied above.

**Required changes (applied):**

1. **The upload-exclusion design was over-built.** The draft added a per-OS `upload_path` matrix key to keep the permission-stripped `.app` out of the artifact. Deleting `dist/DistrictSync.app` after the DMG smoke achieves the same thing and leaves the existing glob untouched — no new matrix dimension in a workflow two callers share. Revised.
2. **`/Volumes` auto-naming is a real trap.** Attaching without an explicit mountpoint lands at `/Volumes/DistrictSync`, or `/Volumes/DistrictSync 1` if anything is already mounted there — so the asserts could inspect a different image than the one just built, and detach could target the wrong device. Revised to an explicit `-mountpoint`.
3. **The `--dmg` script mode was machinery for its own sake.** Mount lifecycle belongs in the workflow shell where `if: always()` cleanup lives; only the assertions benefit from Python, and only because the exec-bit check needs a unit-testable negative twin. Narrowed to `--assert-mounted-app`.

**Considered and deliberately kept:**

- **`hdiutil create -srcfolder` and the `/Applications` symlink.** Flagged as a possible footgun (does it follow the link and copy the real `/Applications`?). Kept: `hdiutil` preserves symlinks rather than dereferencing them, and this is the standard construction. Not left to belief, though — the mounted assert checks `Applications` **is a symlink**, so if that assumption is ever wrong the job goes red instead of shipping a multi-gigabyte image.
- **One slice, not two.** Splitting build from publish opens a tag window where `publish-release` names an asset the pack job stopped producing. Plan 0041 §B hit exactly this and folded its release bridge into one PR; same reasoning applies.

**Sizing/completeness:** one slice, comfortably inside one session. No `src/` change, so no SD74/ETL exposure. The gating live check (`flet-verify.yml` dispatch) is inside the slice, not deferred past it.

**Harness impact:** `scripts/` and `.github/` are outside the tree-check globs, so no `ARCHITECTURE_TREE` entry — matching the PLAT-3 precedent. DECISIONS + ROADMAP + CHANGELOG entries are in the slice.

**Standing risk the review cannot discharge:** whether the onefile `.app` launches at all. No amount of planning settles it; the gating mounted-DMG smoke is the instrument, and its first real run is the moment this plan is either confirmed or invalidated.

---

## Spec — Slice 1

### `.github/workflows/flet-pack.yml` (macOS-only steps, all `if: runner.os == 'macOS'`)

Inserted after the existing size step, before the CLI smokes:

- **Build the DMG** — stage `dist/DistrictSync.app` + an `Applications` symlink into `$RUNNER_TEMP/dmg-staging`, then
  `hdiutil create -volname DistrictSync -srcfolder <staging> -ov -format UDZO dist/DistrictSync-macos.dmg`.
  Echo the resulting size into `$GITHUB_STEP_SUMMARY` alongside the existing artifact-size row.
- **Verify + attach** — `hdiutil verify`, then
  `hdiutil attach -nobrowse -readonly -mountpoint "$RUNNER_TEMP/dmg-mnt" dist/DistrictSync-macos.dmg`.
- **Assert the mounted bundle** — `python scripts/ci_flet_pack_smoke.py --assert-mounted-app "$RUNNER_TEMP/dmg-mnt" DistrictSync`.
- **Smoke the mounted `.app`** (gating) — `python scripts/ci_flet_pack_smoke.py "$RUNNER_TEMP/dmg-mnt" DistrictSync --artifact bundle`.
- **Detach** — `if: always()`, `hdiutil detach "$RUNNER_TEMP/dmg-mnt"` (tolerating an unmounted state so cleanup never masks the real failure).
- **Drop the loose bundle** — `rm -rf dist/DistrictSync.app`, after the smoke, before upload.

Two existing steps change: the "Smoke the packed exe (Windows / macOS)" step becomes Windows-only (macOS is now covered by the mounted-DMG smoke against the artifact it actually ships), and the CLI-smoke step passes `--artifact binary` on macOS so the headless artifact's coverage is explicit rather than an accident of candidate ordering.

### `scripts/ci_flet_pack_smoke.py`

```python
ArtifactKind = Literal["auto", "binary", "bundle"]

def resolve_artifact(dist: Path, name: str, kind: ArtifactKind = "auto") -> Path | None:
    """... `auto` preserves today's candidate order exactly; `binary` never
    resolves to the bundle; `bundle` resolves only the .app inner executable."""

def mounted_app_problems(mount: Path, name: str) -> list[str]:
    """Pure. Returns human-readable problems with a mounted DMG's layout —
    empty list means the image is shippable. Checks: <name>.app exists;
    Contents/MacOS/<name> exists AND is executable; `Applications` is a symlink."""
```

`--artifact {auto,binary,bundle}` threads through `run_smoke`; `--assert-mounted-app MOUNT NAME` prints each problem and exits 1 if any.

### `.github/workflows/release.yml`

- Download step unchanged (artifact name is still `DistrictSync-macos-latest`).
- Rename step: `mv artifacts/macos/DistrictSync-macos.dmg artifacts/DistrictSync-macos.dmg` alongside the existing bare-binary rename; `chmod +x` still applies to the bare binary only.
- `sha256sum` line, `files:` list, and the downloads table all gain the `.dmg` row; the macOS note becomes drag-to-Applications + the corrected Gatekeeper path (**System Settings > Privacy & Security > Open Anyway** — the Control-click->Open shortcut is gone on macOS 15+). The bare binary keeps a row, described as the headless/CLI artifact needing `chmod +x`.

### Docs

`README.md`, `docs/index.md` — macOS row -> `DistrictSync-macos.dmg` + drag-to-Applications note, with the bare binary named as the headless option.
`docs/developer/release.md` — the permalink block and the `publish-release` prose at `:60`.
`docs/partner/installation.md`, `docs/partner/headless-sftp-setup.md` — audited during implementation; updated only where they name the macOS file.
`docs/claugentic-DECISIONS.md` — dated entry (bug · DMG · accepted permalink break · bare binary kept · onedir/icns deferred).
`docs/claugentic-ROADMAP.md` — macOS `--onedir` migration (PyInstaller 7 blocker), `.icns` icon.
`CHANGELOG.md` — release-facing row.

### Tests to add — `tests/test_ci_flet_pack_smoke.py`

- `resolve_artifact` × kind: `auto` unchanged on all three layouts (the existing rows keep passing untouched); `binary` returns the bare path and **never** the bundle even when only the bundle exists; `bundle` returns the inner executable and `None` when no `.app` exists.
- `mounted_app_problems`: clean synthetic tree -> `[]`; missing `.app` -> flagged; **inner binary present but not executable -> flagged** (the negative twin for the assert that exists to catch this exact bug); `Applications` a real directory instead of a symlink -> flagged.

### Acceptance criteria

1. The macOS pack job produces `dist/DistrictSync-macos.dmg`; it verifies, mounts, and the mounted `.app`'s inner executable carries the exec bit.
2. The mounted `.app` passes the offline-embed smoke — **the first evidence in this repo that the macOS bundle launches at all**.
3. The uploaded macOS artifact contains exactly the DMG and the bare binary; no loose `.app`.
4. A tag publishes `DistrictSync-macos.dmg` + `DistrictSync-macos`, both in `SHA256SUMS.txt`.
5. No in-repo reference to the macOS download is stale.
6. Local gates green; a three-OS `flet-verify.yml` dispatch read and quoted before merge (2026-07-30 land gate).
7. Owner confirms on a real Mac: mount -> drag -> launch -> Gatekeeper cleared -> Setup wizard paints.
