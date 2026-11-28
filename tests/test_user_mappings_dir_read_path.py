"""DSYNC-110 — reading the user mappings dir never creates it; writing it does.

The bug: ``paths.user_mappings_dir()`` mkdir-ed on EVERY call, including the config
loader's read-only search path. On a machine-wide install the nightly's service
principal holds only RX on the shared profile root (Modify is on ``runs/`` alone), so a
profile where no mapping was ever authored failed the nightly at once with
``[WinError 5] Access is denied: '...\\mappings'`` — 0 rows.

The fix separates the two paths: ``user_mappings_dir()`` is a pure path, and the ONE
writer-side helper ``ensure_user_mappings_dir()`` creates it. Every "not created"
assertion here has a positive twin that proves the folder CAN appear (no vacuous greens).

Profile isolation comes from the autouse ``isolated_user_profile`` fixture in
``tests/conftest.py``: ``paths.user_data_dir()`` is a per-test tmp dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.utils.paths as paths_module
from src.config.authoring import OverlaySpec, delete_overlay, overlay_path, write_overlay
from src.config.loader import available_configs, load_config, resolve_config_path

_BUNDLED_DISTRICT = "sd40myedbc"  # bundled, and `_base`s myedbc — exercises the inheritance search too


def _mappings_dir() -> Path:
    """The user mappings folder, spelled WITHOUT going through the function under test."""
    return paths_module.user_data_dir() / "mappings"


def _spec() -> OverlaySpec:
    return OverlaySpec(
        sd_number=93,
        district_name="SD93 - Read Path Test",
        district_domains=("sd93.bc.ca",),
        base="myedbc",
    )


@pytest.fixture
def mkdir_denied_on_mappings(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make ANY mkdir of the user mappings folder raise ``PermissionError``.

    The lab shape: the nightly's principal has RX on the profile root, so creating
    ``mappings/`` under it is ``[WinError 5] Access is denied``. The profile root itself
    is resolved (and created by the isolation fixture) BEFORE the patch goes on.
    """
    target = _mappings_dir()
    real_mkdir = Path.mkdir

    def _guarded_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == target:
            raise PermissionError(5, "Access is denied", str(self))
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _guarded_mkdir)
    return target


class TestReadersCreateNothing:
    def test_user_mappings_dir_is_a_pure_path(self):
        path = paths_module.user_mappings_dir()
        assert path == _mappings_dir()
        assert not path.exists()

    def test_loading_a_bundled_config_with_the_folder_absent_creates_nothing(self):
        config = load_config(_BUNDLED_DISTRICT)
        assert config.district_name
        assert not _mappings_dir().exists()

    def test_resolving_a_bundled_config_with_the_folder_absent_is_bundled_and_creates_nothing(self):
        resolved = resolve_config_path(_BUNDLED_DISTRICT)
        assert resolved is not None and resolved.origin == "bundled"
        assert not _mappings_dir().exists()

    def test_listing_configs_with_the_folder_absent_creates_nothing(self):
        assert _BUNDLED_DISTRICT in available_configs()
        assert not _mappings_dir().exists()

    def test_overlay_path_creates_nothing(self):
        assert overlay_path("sd93custom").parent == _mappings_dir()
        assert not _mappings_dir().exists()

    def test_deleting_with_the_folder_absent_is_a_clean_no_op(self):
        # ``resolve()`` on a not-yet-existing root is non-strict, so the containment
        # check still holds; nothing is there, so nothing is deleted or created.
        assert delete_overlay("sd93custom") is False
        assert not _mappings_dir().exists()


class TestTheNightlyPrincipalCannotMkdir:
    """The lab repro: mkdir of ``mappings/`` is denied, and the read path must not care."""

    def test_load_config_still_resolves_the_bundled_config(self, mkdir_denied_on_mappings):
        config = load_config(_BUNDLED_DISTRICT)
        assert config.district_name
        assert not mkdir_denied_on_mappings.exists()

    def test_resolve_config_path_still_resolves_the_bundled_config(self, mkdir_denied_on_mappings):
        resolved = resolve_config_path(_BUNDLED_DISTRICT)
        assert resolved is not None and resolved.origin == "bundled"

    def test_available_configs_still_lists_the_bundled_configs(self, mkdir_denied_on_mappings):
        assert _BUNDLED_DISTRICT in available_configs()

    def test_the_denial_is_real_for_a_writer(self, mkdir_denied_on_mappings):
        """Positive twin: the fixture really does deny the mkdir — the writer surfaces it loudly."""
        with pytest.raises(PermissionError):
            write_overlay(_spec(), overwrite=False)
        assert not mkdir_denied_on_mappings.exists()


class TestWritersCreateOnDemand:
    def test_ensure_user_mappings_dir_creates_the_folder(self):
        """Positive twin of ``test_user_mappings_dir_is_a_pure_path``."""
        assert not _mappings_dir().exists()
        path = paths_module.ensure_user_mappings_dir()
        assert path == paths_module.user_mappings_dir() == _mappings_dir()
        assert path.is_dir()

    def test_ensure_user_mappings_dir_is_idempotent(self):
        first = paths_module.ensure_user_mappings_dir()
        assert paths_module.ensure_user_mappings_dir() == first
        assert first.is_dir()

    def test_the_authoring_save_creates_the_folder_and_writes_the_overlay(self):
        """Positive twin of every reader test above: the WRITE path makes the folder."""
        assert not _mappings_dir().exists()
        path = write_overlay(_spec(), overwrite=False)
        assert path.parent == _mappings_dir()
        assert path.is_file()
        assert load_config("sd93custom").district_name == "SD93 - Read Path Test"
        resolved = resolve_config_path("sd93custom")
        assert resolved is not None and resolved.origin == "user"

    def test_a_refused_save_creates_no_folder(self):
        """Build -> validate -> write: a spec that fails its load-back leaves no folder behind.

        A secondary-only student scope over the INHERITED K-7 homeroom scope violates the
        ``homeroom <= class <= student`` chain, so the load-back refuses it.
        """
        bad = OverlaySpec(
            sd_number=93,
            district_name="SD93 - Read Path Test",
            district_domains=("sd93.bc.ca",),
            base="myedbc",
            student_rostering_grades=("08", "09"),
        )
        with pytest.raises(ValueError, match="homeroom_grades"):
            write_overlay(bad, overwrite=False)
        assert not _mappings_dir().exists()
