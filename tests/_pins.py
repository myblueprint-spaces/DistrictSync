"""Pinned counts that several test files assert — ONE spelling each (plan 0053 S13a).

Importable (``from tests._pins import BUNDLED_CONFIG_COUNT``), deliberately NOT a
``conftest.py`` fixture: a pin is a plain constant a test compares against, and a module
constant cannot be shadowed by a fixture of the same name in a nearer conftest.

``BUNDLED_CONFIG_COUNT`` is how many mapping configs ship in ``config/mappings`` — what
``available_configs(bundle_mappings_dir())`` enumerates. It is PINNED rather than derived
so a deleted or unregistered config fails loudly instead of silently shrinking every
sweep that iterates the bundled set. Three copies OUTSIDE ``tests/`` cannot import it and
are tied back by ``tests/test_config_count_pin.py``: ``.github/workflows/ci.yml``'s
``EXPECTED_CONFIGS``, the Makefile's ``validate-config`` list, and CLAUDE.md's
"pinned at N" / "validates all N configs" sentences. No numeric config-count literal may
appear anywhere else under ``tests/`` (the same file scans for one).

Bumping it: add the config, bump this constant, then follow the red tests to the three
lockstep copies. An ``_ALLOWED`` look-alike in ``tests/test_config_count_pin.py`` whose number
leaves the count +/- 1 window after a bump fails ``test_every_allowlist_entry_is_live`` — re-key
or remove it.
"""

from __future__ import annotations

from typing import Final

#: The number of bundled mapping configs (``config/mappings/*_mapping.yaml``).
BUNDLED_CONFIG_COUNT: Final = 20
