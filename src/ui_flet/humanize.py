"""Humanization helpers for the Flet UI — turn machine ids into the words an admin speaks.

PURE + COUNTED (no flet import): trust-surface copy must never show a raw config id
(``sd48myedbc``) to a non-technical admin. IA-2 needs only ``friendly_district_name``;
IA-9 grows the broader sweep (timestamps, no raw paths/filenames/stack traces). Kept
minimal by design (YAGNI) — this is one total function, not a framework.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def friendly_district_name(sis_type: str, *, config_dir: Path | None = None) -> str:
    """Map a SIS id to its human ``district_name``; TOTAL — never raises, never crashes a view.

    - empty/whitespace ``sis_type`` → ``""`` (no district chosen → generic hero copy).
    - else load the config and return its ``district_name`` stripped IFF non-empty,
      otherwise fall back to the raw ``sis_type``.
    - any load failure (FileNotFoundError / ValueError / unexpected) → fall back to the
      raw ``sis_type`` (an admin sees ``sd48myedbc`` at worst, never a blank or a crash),
      logged at ``warning`` (mirrors ``screens/setup._district_options``).

    ``config_dir`` is a test seam passed straight through to ``loader.load_config``
    (whose own ``config_dir`` arg overrides the ``~/.districtsync`` search dirs), so
    this is unit-testable against a fixture mappings dir without a home dependency.
    """
    sis = sis_type.strip()
    if not sis:
        return ""
    try:
        from src.config.loader import load_config

        cfg = load_config(sis, config_dir)
        name = (cfg.district_name or "").strip()
        return name if name else sis
    except Exception as exc:  # noqa: BLE001 - total: any load failure falls back to the raw id
        logger.warning("friendly_district_name(%r) fell back to the raw id: %s", sis, exc)
        return sis
