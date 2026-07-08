"""Pure config-catalog derivation — "which district config is active, and what does it produce?"

PURE + COUNTED (no ``flet`` import): given a SIS id, load the district mapping config and
derive a PII-free ``ConfigSummary`` — the friendly district name, the plain-language list of
output CSVs it emits (from ``enabled_entities``), and the count of distinct GDE source files it
reads. The Mapping surface (``screens/mapping.py``) renders these to let an admin REVIEW the
active mapping and SWITCH to a different pre-built one, seeing what each produces first.

**Single-sourced with the pipeline.** ``output_labels`` is derived by the SAME empty-means-all
rule the core uses to decide which entities (→ CSVs) a config emits (``MappingConfig``:
``set(enabled_entities) if enabled_entities else set(mappings.keys())`` — empty/absent = all),
ordered by ``home_status``'s rostering-then-myBlueprint entity tuples and labelled through the
single-source ``home_status.ENTITY_LABELS`` map — so the Mapping summary can never disagree
with Home / Run History / the actual output CSV set.

**TOTAL over a failing config (reliability-resilience).** ``load_config`` is strict at the
boundary — a partner-authored broken YAML in ``~/.districtsync/mappings/`` raises
``FileNotFoundError`` / ``ValueError``. ``summarize_config`` wraps it: a raise → a SAFE degraded
``ConfigSummary`` (``loaded_ok=False``, ``district_name`` = the raw id via
``friendly_district_name``'s fallback, ``output_labels=()``, ``source_file_count=0``), NEVER a
crash. ``list_configs`` therefore always returns one summary per enumerated id, some degraded.

**Privacy (LIVE/top).** A ``ConfigSummary`` carries only config STRUCTURE — a district name,
output-CSV labels, a file count. It carries NO student PII (a config is a column-name mapping,
not data) and NEVER interpolates a raw exception string (a Pydantic/OS error text) into any
admin-facing field — a load failure is named by category (``loaded_ok=False``), never echoed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.loader import available_configs, load_config
from src.ui_flet.home_status import (
    _MYBLUEPRINT_ENTITIES,
    _ROSTERING_ENTITIES,
    ENTITY_LABELS,
)
from src.ui_flet.humanize import friendly_district_name

# The canonical entity ORDER for the output-CSV summary — rostering entities first, then the
# myBlueprint+ / attendance keys — reusing `home_status`'s entity tuples so the label order
# matches Home / Run History. Any enabled entity NOT in these tuples (a non-standard key) is
# appended after, so a partner-defined extra entity still surfaces (total).
_ENTITY_ORDER: tuple[str, ...] = tuple(_ROSTERING_ENTITIES) + tuple(_MYBLUEPRINT_ENTITIES)


@dataclass(frozen=True)
class ConfigSummary:
    """A PII-free structural summary of one district mapping config.

    ``sis_type`` is the raw id (a secondary technical hint only — never the primary label).
    ``district_name`` is the friendly label (or the raw id when the config has no
    ``district_name`` / failed to load). ``output_labels`` is the plain-language list of output
    CSVs it emits, in the canonical order. ``source_file_count`` is how many distinct GDE files
    it reads. ``loaded_ok`` is ``False`` when the config failed to load — a SAFE degraded
    summary the view renders calmly (Apply disabled), NEVER a crash / a raw error.
    """

    sis_type: str
    district_name: str
    output_labels: tuple[str, ...]
    source_file_count: int
    loaded_ok: bool


def _degraded(sis_type: str, *, config_dir: Path | None) -> ConfigSummary:
    """The safe degraded summary for a config that failed to load — no PII, no raw error text.

    ``district_name`` falls back to the raw id via ``friendly_district_name``'s totality (itself
    total — a nested load failure returns the raw id, never raises).
    """
    return ConfigSummary(
        sis_type=sis_type,
        district_name=friendly_district_name(sis_type, config_dir=config_dir) or sis_type,
        output_labels=(),
        source_file_count=0,
        loaded_ok=False,
    )


def summarize_config(sis_type: str, *, config_dir: Path | None = None) -> ConfigSummary:
    """Summarize one district config — TOTAL: a load failure → a safe degraded summary, never a raise.

    ``config_dir`` is a test seam passed straight through to ``load_config`` /
    ``friendly_district_name`` (overriding the ``~/.districtsync`` search dirs), so this is
    unit-testable against a fixture mappings dir with no home dependency.
    """
    try:
        cfg = load_config(sis_type, config_dir)
        enabled = (
            set(cfg.global_config.enabled_entities) if cfg.global_config.enabled_entities else set(cfg.mappings.keys())
        )
        # Intersect with the DEFINED entities: an entity enabled but absent from `mappings`
        # produces no CSV (the pipeline's own enforcement gates on `entity in mappings` too),
        # so the summary reflects only what actually gets produced (truthful, never a phantom CSV).
        produced = enabled & set(cfg.mappings.keys())
        output_labels = _output_labels(produced)
        source_file_count = _source_file_count(cfg, produced)
        return ConfigSummary(
            sis_type=sis_type,
            district_name=friendly_district_name(sis_type, config_dir=config_dir) or sis_type,
            output_labels=output_labels,
            source_file_count=source_file_count,
            loaded_ok=True,
        )
    except Exception:  # noqa: BLE001 - total: any load failure degrades, never surfaces the raw error
        return _degraded(sis_type, config_dir=config_dir)


def _output_labels(enabled: set[str]) -> tuple[str, ...]:
    """Map the enabled entity keys to plain-language CSV labels, in the canonical order.

    Canonical keys (rostering then myBlueprint+) lead in ``_ENTITY_ORDER`` order; any enabled
    entity NOT in that spine (a non-standard partner key) is appended after (sorted for a stable
    order), labelled via ``ENTITY_LABELS`` with a raw-key fallback (total).
    """
    labels: list[str] = [ENTITY_LABELS.get(key, key) for key in _ENTITY_ORDER if key in enabled]
    extras = sorted(enabled - set(_ENTITY_ORDER))
    labels.extend(ENTITY_LABELS.get(key, key) for key in extras)
    return tuple(labels)


def _source_file_count(cfg, produced: set[str]) -> int:  # type: ignore[no-untyped-def]
    """Count DISTINCT source filenames across the produced entities (the same file often feeds several).

    ``produced`` is always a subset of ``cfg.mappings.keys()`` (the caller intersects), so every
    key resolves to a defined ``EntityConfig``.
    """
    filenames: set[str] = set()
    for name in produced:
        filenames.update(cfg.mappings[name].source_files.values())
    return len(filenames)


def list_configs(*, config_dir: Path | None = None) -> list[ConfigSummary]:
    """Summarize every discoverable district config, in ``available_configs`` order.

    One ``ConfigSummary`` per enumerated SIS id — some possibly degraded (a broken config is
    listed, never omitted or crashed on). ``config_dir`` is the test seam.
    """
    return [summarize_config(sis_type, config_dir=config_dir) for sis_type in available_configs(config_dir)]
