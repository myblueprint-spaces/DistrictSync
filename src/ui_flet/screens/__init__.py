"""Flet UI surfaces (screens) for DistrictSync.

Each module here builds one real navigation surface (replacing a ``shell.py``
placeholder). The trust-critical decision logic for a surface lives in a COUNTED
pure helper (e.g. ``filepicker.setup_state``); the ``build_*`` view functions are
coverage-omitted Flet glue.
"""
