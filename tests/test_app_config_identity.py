"""The `identity_*` settings fields, `_ADVISORY_FIELD_PREFIXES`, and `identity_save`.

Identity is ADVISORY metadata — who looks after this sync. It scopes a picker and echoes
on Help; it changes nothing about which district converts, from where, to where, or when.
Three consequences are pinned here, and each is a real hazard rather than a formality:

* **it must never look like a setting.** An identity-only save on a profile we FAILED TO
  READ must be REFUSED, exactly as the shell's advisory geometry save already is —
  otherwise typing an email at the launch page would trade an admin's real folders /
  district / delivery settings for invented blanks.
* **it must never complete setup.** If it leaked into the finish-line, an admin who
  answered a question would land on a dashboard for a sync that was never configured.
* **it must never trap anyone.** A failed identity save is logged and reported, never
  raised — the caller persists best-effort and enters the app regardless.

The `_ADVISORY_FIELD_PREFIXES` truth table below is the load-bearing one: it asserts the
NEW behaviour, the UNCHANGED geometry behaviour, and the near-miss (`sync_window_*` is
NOT advisory) in one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.config.app_config import AppConfig, ConfigLoadState, SettingsOverwriteRefused
from src.ui_flet.identity_gate import needs_identity

IDENTITY_FIELDS = ("identity_email", "identity_prompt_dismissed", "identity_sd_number")


@pytest.fixture
def config_file(isolated_user_profile: Path) -> Path:
    return isolated_user_profile / "config.json"


def _real_settings() -> dict[str, object]:
    """A configured install's settings — the ones an errant save would destroy."""
    return {
        "input_dir": "/district/in",
        "output_dir": "/district/out",
        "sis_type": "sd48myedbc",
        "schedule_registered": True,
        "setup_completed": True,
    }


# --------------------------------------------------------------------------- #
# Defaults + round-trip                                                        #
# --------------------------------------------------------------------------- #
def test_defaults_are_empty_so_a_fresh_install_is_asked():
    cfg = AppConfig()
    assert cfg.identity_email == ""
    assert cfg.identity_prompt_dismissed is False
    assert cfg.identity_sd_number == ""


def test_all_three_fields_round_trip_through_disk(config_file: Path):
    cfg = AppConfig(**_real_settings())
    cfg.identity_email = "First.Last@sd48.bc.ca"
    cfg.identity_prompt_dismissed = True
    cfg.identity_sd_number = "48"
    cfg.save()

    reloaded = AppConfig.load()
    assert reloaded.identity_email == "First.Last@sd48.bc.ca"  # stored AS TYPED
    assert reloaded.identity_prompt_dismissed is True
    assert reloaded.identity_sd_number == "48"
    assert set(IDENTITY_FIELDS) <= set(json.loads(config_file.read_text(encoding="utf-8")))


def test_a_v38x_config_without_the_keys_loads_unchanged(config_file: Path):
    """Journey 4, config layer: a LITERAL v3.8.x settings file upgrades in place.

    Written as the literal text a shipped v3.8.x install has on disk (not an
    ``asdict(AppConfig())`` round-trip, which would silently gain any field this build
    added and so could never catch the regression). It must load LOADED — not UNREADABLE,
    not ABSENT — stay `has_completed_setup()`, and NOT be asked for an identity at launch:
    a working install is never stopped at a front door in front of its own sync.
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        """{
  "input_dir": "C:\\\\DistrictSync\\\\input",
  "output_dir": "C:\\\\DistrictSync\\\\output",
  "sis_type": "sd48myedbc",
  "schedule_time": "03:00",
  "schedule_task_name": "DistrictSync_Daily",
  "schedule_registered": true,
  "schedule_unattended": true,
  "schedule_task_args": {"sis": "sd48myedbc", "sftp": true, "run_time": "03:00"},
  "sync_window_enabled": false,
  "sync_window_start": "",
  "sync_window_end": "",
  "setup_completed": true,
  "sftp_enabled": true,
  "sftp_host": "sftp.ca.spacesedu.com",
  "sftp_port": 22,
  "sftp_username": "district_x",
  "sftp_remote_path": "/files",
  "window_width": 1200.0,
  "window_height": 860.0,
  "window_left": 100.0,
  "window_top": 60.0,
  "window_maximized": false
}
""",
        encoding="utf-8",
    )

    cfg = AppConfig.load()

    assert cfg.load_state is ConfigLoadState.LOADED
    assert cfg.has_completed_setup() is True
    assert cfg.sis_type == "sd48myedbc"
    assert cfg.identity_email == ""
    assert needs_identity(cfg) is False  # gets the dismissible Home card, not a gate


def test_an_unknown_identity_like_key_survives_forward_compat(config_file: Path):
    """A key from a NEWER build is ignored, not rejected — and the file still loads."""
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        json.dumps({**_real_settings(), "identity_email": "a@b.ca", "identity_from_a_newer_build": "x"}),
        encoding="utf-8",
    )
    cfg = AppConfig.load()

    assert cfg.load_state is ConfigLoadState.LOADED
    assert cfg.identity_email == "a@b.ca"
    assert not hasattr(cfg, "identity_from_a_newer_build")


# --------------------------------------------------------------------------- #
# _ADVISORY_FIELD_PREFIXES — the truth table                                   #
# --------------------------------------------------------------------------- #
class TestAdvisoryFieldPrefixes:
    """What counts as "a setting the admin chose" when the profile is UNREADABLE.

    Each row is asserted through the PUBLIC behaviour (``save()`` refusing or not) rather
    than by reading the private predicate, so the test survives a refactor of the
    mechanism and still pins the outcome.
    """

    @staticmethod
    def _unreadable(**overrides: object) -> AppConfig:
        return AppConfig(load_state=ConfigLoadState.UNREADABLE, **overrides)  # type: ignore[arg-type]

    def test_geometry_only_save_is_still_refused(self):
        """UNCHANGED behaviour — the original member of the advisory family."""
        with pytest.raises(SettingsOverwriteRefused):
            self._unreadable(window_width=1400.0, window_maximized=True).save()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("identity_email", "admin@sd48.bc.ca"),
            ("identity_prompt_dismissed", True),
            ("identity_sd_number", "48"),
        ],
    )
    def test_identity_only_save_is_refused(self, field, value):
        """NEW: identity joins the advisory family, so the EXISTING guard catches it.

        Without this, the launch page's persist would write a document made entirely of
        invented defaults over settings we merely failed to read.
        """
        with pytest.raises(SettingsOverwriteRefused):
            self._unreadable(**{field: value}).save()

    def test_identity_and_geometry_together_are_still_refused(self):
        with pytest.raises(SettingsOverwriteRefused):
            self._unreadable(identity_email="admin@sd48.bc.ca", window_width=1400.0).save()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("sis_type", "sd48myedbc"),
            ("input_dir", "/district/in"),
            ("sftp_enabled", True),
            ("schedule_registered", True),
            # The deliberate NEAR-MISS: `sync_window_*` is named so it does NOT start
            # with the `window_` geometry prefix, because it IS an admin choice.
            ("sync_window_enabled", True),
            ("sync_window_start", "08-11"),
        ],
    )
    def test_a_real_chosen_setting_still_permits_the_save(self, field, value, isolated_user_profile):
        cfg = self._unreadable(**{field: value})
        cfg.save()  # must not raise
        assert cfg.load_state is ConfigLoadState.LOADED

    def test_a_real_setting_alongside_identity_still_permits_the_save(self, isolated_user_profile):
        """Identity does not POISON a save; it just cannot unlock one on its own."""
        cfg = self._unreadable(sis_type="sd48myedbc", identity_email="admin@sd48.bc.ca")
        cfg.save()
        assert AppConfig.load().identity_email == "admin@sd48.bc.ca"


# --------------------------------------------------------------------------- #
# identity_save — the one choke point                                          #
# --------------------------------------------------------------------------- #
class TestIdentitySave:
    def test_applies_the_updates_and_persists(self, config_file: Path):
        cfg = AppConfig(**_real_settings())

        assert cfg.identity_save(identity_email="admin@sd48.bc.ca", identity_sd_number="48") is True

        reloaded = AppConfig.load()
        assert reloaded.identity_email == "admin@sd48.bc.ca"
        assert reloaded.identity_sd_number == "48"
        # What a write must leave behind: a document the NEXT load can still read. A
        # mis-typed value would round-trip into JSON, fail `_value_fits` on the way back
        # in, and drop the whole install to defaults — so the round-trip is only proven
        # once the provenance is asserted too.
        assert reloaded.load_state is ConfigLoadState.LOADED

    # ---- value validation (the corruption route) ------------------------- #
    @pytest.mark.parametrize(
        ("updates", "why"),
        [
            ({"identity_email": None}, "None serialises to JSON null; the next load reads UNREADABLE"),
            ({"identity_email": True}, "a bool where a str is declared"),
            ({"identity_email": 42}, "an int where a str is declared"),
            ({"identity_sd_number": 42}, "SD numbers are strings — '08' is not 8"),
            ({"identity_prompt_dismissed": "yes"}, "a truthy string is not a bool"),
            ({"identity_prompt_dismissed": 1}, "1 is not a bool (bool is an int subclass; the reverse is not)"),
        ],
    )
    def test_a_wrong_typed_value_raises_before_anything_is_written(self, updates, why, config_file: Path):
        """A mis-typed value would make the WHOLE settings document unreadable.

        `config.json` is re-read through `_value_fits`, so a `null` or a `42` written here
        does not merely store a bad identity — it fails the document on the next load and
        drops the admin's district, folders and delivery settings to defaults. That is a
        settings-loss bug reached through an ADVISORY field, so it is refused loudly at
        the choke point rather than coerced.
        """
        cfg = AppConfig(**_real_settings())

        with pytest.raises(TypeError, match="identity_save"):
            cfg.identity_save(**updates)

        assert not config_file.exists(), f"a rejected save wrote to disk: {why}"

    def test_the_rejected_value_is_not_applied_in_memory_either(self):
        cfg = AppConfig(**_real_settings(), identity_email="admin@sd48.bc.ca")

        with pytest.raises(TypeError):
            cfg.identity_save(identity_email=None)

        assert cfg.identity_email == "admin@sd48.bc.ca"

    def test_a_bad_key_late_in_the_call_leaves_no_partial_mutation(self):
        """Validation runs to completion BEFORE any setattr.

        Applying as we validate would let a multi-field call mutate the good fields and
        then raise, leaving the instance in a state neither the caller nor the disk agrees
        with — and a caller that catches the error would go on to save it.
        """
        cfg = AppConfig(**_real_settings())

        with pytest.raises(AttributeError):
            cfg.identity_save(identity_email="admin@sd48.bc.ca", sis_type="pwned")
        assert cfg.identity_email == ""
        assert cfg.sis_type == "sd48myedbc"

        with pytest.raises(TypeError):
            cfg.identity_save(identity_email="admin@sd48.bc.ca", identity_prompt_dismissed="yes")
        assert cfg.identity_email == ""
        assert cfg.identity_prompt_dismissed is False

    def test_the_method_name_itself_cannot_be_shadowed(self):
        """`hasattr` answers True for every METHOD — membership is the only safe guard.

        Under a `hasattr` check `identity_save(identity_save="x")` returned True, persisted
        nothing, and bound a string over the bound method — permanently disabling the choke
        point on that instance while reporting success.
        """
        cfg = AppConfig(**_real_settings())

        with pytest.raises(AttributeError, match="only writes identity_"):
            cfg.identity_save(identity_save="pwned")

        assert callable(cfg.identity_save)

    def test_the_writable_set_is_derived_from_the_dataclass(self):
        """Not a hand-list: a new `identity_*` field is writable the moment it is declared."""
        from dataclasses import fields as dataclass_fields

        from src.config.app_config import _IDENTITY_FIELD_NAMES

        assert {f.name for f in dataclass_fields(AppConfig) if f.name.startswith("identity_")} == _IDENTITY_FIELD_NAMES
        assert set(IDENTITY_FIELDS) == _IDENTITY_FIELD_NAMES

    def test_returns_false_and_writes_nothing_when_settings_are_unreadable(self, config_file: Path, caplog):
        """The guard lives on the WRITE, re-checked on THIS instance at save time.

        The gate predicate was evaluated at launch and can be stale; the provenance of
        the instance about to be written cannot.
        """
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text('{"input_dir": "/real/in", "output_di', encoding="utf-8")  # torn
        planted = config_file.read_bytes()

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE

        with caplog.at_level(logging.WARNING):
            assert cfg.identity_save(identity_email="admin@sd48.bc.ca") is False

        assert config_file.read_bytes() == planted, "the unreadable settings file was touched"
        assert not list(config_file.parent.glob("config.corrupt-*.json")), "nothing should have been quarantined"
        assert any("who looks after this sync" in r.message for r in caplog.records)

    def test_the_positive_twin_proves_the_write_path_works_at_all(self, config_file: Path):
        """Pairs the absence-assertion above: same instance shape, READABLE profile.

        Without this twin, "the file was not touched" would also pass if
        ``identity_save`` never wrote anything under any circumstances.
        """
        cfg = AppConfig(**_real_settings())
        assert cfg.identity_save(identity_email="admin@sd48.bc.ca") is True
        assert "admin@sd48.bc.ca" in config_file.read_text(encoding="utf-8")

    def test_swallows_an_oserror_and_reports_failure(self, monkeypatch, caplog):
        """A disk-full / permission-denied save must not trap the admin at the page."""

        def boom(self):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(AppConfig, "save", boom)
        cfg = AppConfig(**_real_settings())

        with caplog.at_level(logging.WARNING):
            assert cfg.identity_save(identity_email="admin@sd48.bc.ca") is False

        assert any("Could not save who looks after this sync" in r.message for r in caplog.records)
        # The in-memory value is still applied — the caller may carry on this session.
        assert cfg.identity_email == "admin@sd48.bc.ca"

    def test_swallows_a_refusal_raised_by_save_itself(self, monkeypatch):
        """Defence in depth: if ``save()`` widens its refusal rule, we still don't raise."""

        def refuse(self):
            raise SettingsOverwriteRefused("nope")

        monkeypatch.setattr(AppConfig, "save", refuse)
        assert AppConfig(**_real_settings()).identity_save(identity_email="admin@sd48.bc.ca") is False

    def test_never_raises_out_of_the_two_handled_failure_modes(self, monkeypatch):
        """The contract the launch page and the Home cards both rely on."""
        for exc in (OSError("disk full"), SettingsOverwriteRefused("refused")):

            def raiser(self, _exc=exc):
                raise _exc

            monkeypatch.setattr(AppConfig, "save", raiser)
            assert AppConfig(**_real_settings()).identity_save(identity_email="a@b.ca") is False

    @pytest.mark.parametrize("field", ["sis_type", "input_dir", "output_dir", "sftp_host", "setup_completed"])
    def test_refuses_a_non_identity_field_loudly(self, field):
        """It can NEVER become a back door for rewriting the configured district.

        "Resolution never rewrites `sis_type`" is a product rule; enforcing it
        structurally at the single write point beats trusting every future call site.
        """
        cfg = AppConfig(**_real_settings())
        with pytest.raises(AttributeError, match="only writes identity_"):
            cfg.identity_save(**{field: "x"})
        assert cfg.sis_type == "sd48myedbc"

    def test_refuses_an_identity_prefixed_field_that_does_not_exist(self):
        """A typo is a programming error, not a silently-ignored no-op that looks saved."""
        with pytest.raises(AttributeError, match="only writes identity_"):
            AppConfig(**_real_settings()).identity_save(identity_emial="a@b.ca")

    def test_clearing_writes_the_empty_values(self, config_file: Path):
        """Blank CLEARS — the Settings affordance, pinned at the choke point."""
        cfg = AppConfig(**_real_settings())
        cfg.identity_save(identity_email="admin@sd48.bc.ca", identity_sd_number="48", identity_prompt_dismissed=True)

        assert cfg.identity_save(identity_email="", identity_sd_number="", identity_prompt_dismissed=False) is True

        reloaded = AppConfig.load()
        assert (reloaded.identity_email, reloaded.identity_sd_number) == ("", "")
        assert reloaded.identity_prompt_dismissed is False


# --------------------------------------------------------------------------- #
# Identity is not a setup step                                                 #
# --------------------------------------------------------------------------- #
def test_identity_alone_never_completes_setup_across_disk(config_file: Path):
    """Round-tripped through disk, so the `load()`-time back-compat bake is included."""
    cfg = AppConfig()
    cfg.identity_email = "admin@sd48.bc.ca"
    cfg.identity_sd_number = "48"
    cfg.identity_prompt_dismissed = True
    cfg.save()

    reloaded = AppConfig.load()
    assert reloaded.has_completed_setup() is False
    assert reloaded.is_complete() is False
    assert reloaded.setup_completed is False
