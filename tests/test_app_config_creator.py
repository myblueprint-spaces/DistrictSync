"""The `creator_*` settings family, the shared write-discipline helper, and activation.

Plan 0044 S3 adds the third ADVISORY field family (`creator_pending_sis` +
`creator_verified`) and, with it, extracts the write discipline `identity_save` used to
own into `AppConfig._guarded_field_write`. Three claims are pinned here, each a real
hazard rather than a formality:

* **the family must never look like a setting.** A creator-only save on a profile we
  FAILED TO READ must be REFUSED — the token is resume convenience and the tested-fact is
  keyed on a digest of the RESOLVED config, so losing either can only force another test
  run, never unlock one. Writing them over settings we merely failed to read would trade
  an admin's real folders / district / delivery settings for invented blanks.
* **`creator_save` must never become a `sis_type` back door.** Activation is a separate,
  deliberately named and validated method; a creator flow that could pin the district
  through the advisory choke point would be exactly the hole that refusal exists to close.
* **`activate_creator_config` is ONE save and is NOT refused under UNREADABLE
  provenance** — it carries a chosen setting, so it takes the same posture as the wizard's
  District step, quarantining the predecessor bytes it displaces.

The equivalence proof for the extraction is `tests/test_app_config_identity.py`, which is
unchanged: identity's raise shapes, messages, returns, rollback and log text all still
come out byte-identical through the shared helper.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

from src.config import app_config as app_config_module
from src.config.app_config import (
    _ACTIVATION_FIELD_NAMES,
    _ADVISORY_FIELD_PREFIXES,
    _CREATOR_FIELD_NAMES,
    AppConfig,
    ConfigLoadState,
    SettingsOverwriteRefused,
)
from src.config.authoring import OverlaySpec, write_overlay

CREATOR_FIELDS = ("creator_pending_sis", "creator_verified")

#: A well-formed resolved-config digest (64 lowercase hex) — the only shape
#: ``validators.is_config_digest`` accepts, so the only shape activation stores.
DIGEST = hashlib.sha256(b"a resolved sd93custom config").hexdigest()
OTHER_DIGEST = hashlib.sha256(b"a different resolved config").hexdigest()


@pytest.fixture
def config_file(isolated_user_profile: Path) -> Path:
    return isolated_user_profile / "config.json"


def _real_settings() -> dict[str, object]:
    """A configured install's settings — the ones an errant save would destroy."""
    return {
        "input_dir": "/district/in",
        "output_dir": "/district/out",
        "sis_type": "sd48myedbc",
        "sftp_enabled": True,
        "sftp_host": "sftp.spacesedu.com",
        "sftp_username": "sd48",
        "schedule_registered": True,
        "setup_completed": True,
    }


def _write_sd93_overlay() -> str:
    """Author a REAL self-service overlay in the isolated profile. → its config id."""
    write_overlay(
        OverlaySpec(
            sd_number=93,
            district_name="SD93 - Creator Test",
            district_domains=("sd93.bc.ca",),
            base="myedbc",
        ),
        overwrite=False,
    )
    return "sd93custom"


def _load_through_one_read_blip(cfg_file: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """``AppConfig.load()`` while ONE read of ``config.json`` fails, then reads fine again.

    The same reproduction ``tests/test_app_config_crash_safety.py`` uses: the
    cross-platform stand-in for a transient sharing violation / AV lock / permissions
    blip, which is the ONLY way to get an UNREADABLE-provenance instance whose
    predecessor bytes are still perfectly recoverable.
    """
    failed: list[bool] = []
    real_read_bytes = Path.read_bytes

    def blip(self: Path) -> bytes:
        if self == cfg_file and not failed:
            failed.append(True)
            raise PermissionError("simulated transient sharing violation")
        return real_read_bytes(self)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "read_bytes", blip)
        cfg = AppConfig.load()
    assert failed, "the blip never fired — the reproduction is not exercising the OSError branch"
    assert cfg.load_state is ConfigLoadState.UNREADABLE
    return cfg


# --------------------------------------------------------------------------- #
# Registration — the prefix contract AND the comment that IS the contract      #
# --------------------------------------------------------------------------- #
class TestTheAdvisoryRegistration:
    def test_all_three_advisory_prefixes_are_registered(self):
        assert _ADVISORY_FIELD_PREFIXES == ("window_", "identity_", "creator_")

    def test_the_module_comment_names_the_creator_family_and_says_why(self):
        """The comment block IS the contract (a literal copied out of `src/` needs a tie-back).

        A prefix silently added to the tuple with no rationale beside it is how the next
        reader learns the wrong lesson — that the list is a grab-bag rather than "persisted,
        but NOT a setting that makes the sync work". So the registration and its argument
        are asserted together.
        """
        source = Path(app_config_module.__file__).read_text(encoding="utf-8")
        advisory_comment = source.split("_IDENTITY_FIELD_PREFIX = ")[0].rsplit("# ADVISORY field families", 1)[-1]
        assert "``creator_``" in advisory_comment
        assert "never unlock one" in advisory_comment, "the tested-fact's fail-safe direction is unargued"
        assert "activate_creator_config" in advisory_comment, "the deliberate NON-advisory exception is unnamed"

    def test_a_creator_only_save_is_refused_under_unreadable_provenance(self):
        """The registration's whole point, asserted through PUBLIC behaviour (`save()`)."""
        for field_name, value in (("creator_pending_sis", "sd93custom"), ("creator_verified", {"sd93custom": DIGEST})):
            cfg = AppConfig(load_state=ConfigLoadState.UNREADABLE, **{field_name: value})  # type: ignore[arg-type]
            with pytest.raises(SettingsOverwriteRefused):
                cfg.save()

    def test_a_real_chosen_setting_alongside_creator_state_still_permits_the_save(self, isolated_user_profile: Path):
        """The positive twin: creator state does not POISON a save, it just cannot unlock one."""
        cfg = AppConfig(
            load_state=ConfigLoadState.UNREADABLE,
            sis_type="sd93custom",
            creator_pending_sis="sd93custom",
        )
        cfg.save()
        assert AppConfig.load().creator_pending_sis == "sd93custom"

    def test_the_writable_set_is_derived_from_the_dataclass(self):
        assert {f.name for f in dataclass_fields(AppConfig) if f.name.startswith("creator_")} == _CREATOR_FIELD_NAMES
        assert set(CREATOR_FIELDS) == _CREATOR_FIELD_NAMES

    def test_the_activation_allowlist_is_exactly_the_creator_family_plus_sis_type(self):
        assert {"sis_type", "creator_pending_sis", "creator_verified"} == _ACTIVATION_FIELD_NAMES


# --------------------------------------------------------------------------- #
# Defaults + round-trip                                                        #
# --------------------------------------------------------------------------- #
class TestDefaultsAndRoundTrip:
    def test_defaults_are_empty_and_the_map_is_per_instance(self):
        first, second = AppConfig(), AppConfig()
        assert first.creator_pending_sis == ""
        assert first.creator_verified == {}
        first.creator_verified["sd93custom"] = DIGEST
        assert second.creator_verified == {}, "default_factory=dict is what keeps the map per-instance"

    def test_both_fields_round_trip_through_disk(self, config_file: Path):
        cfg = AppConfig(**_real_settings())
        cfg.creator_pending_sis = "sd93custom"
        cfg.creator_verified = {"sd93custom": DIGEST}
        cfg.save()

        reloaded = AppConfig.load()
        assert reloaded.load_state is ConfigLoadState.LOADED
        assert reloaded.creator_pending_sis == "sd93custom"
        assert reloaded.creator_verified == {"sd93custom": DIGEST}
        assert set(CREATOR_FIELDS) <= set(json.loads(config_file.read_text(encoding="utf-8")))

    def test_a_settings_file_without_the_keys_loads_unchanged(self, config_file: Path):
        """Additive with safe defaults: a v3.14.x install upgrades in place."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(_real_settings()), encoding="utf-8")

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED
        assert cfg.sis_type == "sd48myedbc"
        assert (cfg.creator_pending_sis, cfg.creator_verified) == ("", {})

    def test_creator_state_alone_never_completes_setup(self, config_file: Path):
        cfg = AppConfig(creator_pending_sis="sd93custom", creator_verified={"sd93custom": DIGEST})
        cfg.save()

        reloaded = AppConfig.load()
        assert reloaded.has_completed_setup() is False
        assert reloaded.is_complete() is False


# --------------------------------------------------------------------------- #
# creator_save — the advisory choke point                                      #
# --------------------------------------------------------------------------- #
class TestCreatorSave:
    def test_applies_the_updates_and_persists(self, config_file: Path):
        cfg = AppConfig(**_real_settings())

        assert cfg.creator_save(creator_pending_sis="sd93custom") is True

        reloaded = AppConfig.load()
        assert reloaded.creator_pending_sis == "sd93custom"
        # A write must leave behind a document the NEXT load can still read — a mis-typed
        # value would round-trip into JSON and fail `_value_fits` on the way back in.
        assert reloaded.load_state is ConfigLoadState.LOADED

    def test_returns_false_and_writes_nothing_when_settings_are_unreadable(self, config_file: Path, caplog):
        """The guard lives on the WRITE, re-checked on THIS instance at save time."""
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text('{"input_dir": "/real/in", "output_di', encoding="utf-8")  # torn
        planted = config_file.read_bytes()

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE

        with caplog.at_level(logging.WARNING):
            assert cfg.creator_save(creator_pending_sis="sd93custom") is False

        assert config_file.read_bytes() == planted, "the unreadable settings file was touched"
        assert not list(config_file.parent.glob("config.corrupt-*.json")), "nothing should have been quarantined"
        assert cfg.creator_pending_sis == "", "a refused write must leave the shared instance untouched"
        assert any("your district setup progress" in r.message for r in caplog.records)

    def test_the_positive_twin_proves_the_write_path_works_at_all(self, config_file: Path):
        """Pairs the absence-assertion above: same call, READABLE profile."""
        cfg = AppConfig(**_real_settings())
        assert cfg.creator_save(creator_pending_sis="sd93custom") is True
        assert "sd93custom" in config_file.read_text(encoding="utf-8")

    @pytest.mark.parametrize("field_name", ["sis_type", "input_dir", "output_dir", "setup_completed", "identity_email"])
    def test_refuses_a_non_creator_field_loudly(self, field_name: str, config_file: Path):
        """It can NEVER become the back door for pinning the configured district.

        `sis_type` is the row that matters: activation is a separate, validated method
        precisely so the advisory choke point cannot write it.
        """
        cfg = AppConfig(**_real_settings())
        with pytest.raises(AttributeError, match="only writes creator_"):
            cfg.creator_save(**{field_name: "x"})
        assert cfg.sis_type == "sd48myedbc"
        assert not config_file.exists()

    def test_the_method_name_itself_cannot_be_shadowed(self):
        """`hasattr` answers True for every METHOD — membership is the only safe guard."""
        cfg = AppConfig(**_real_settings())
        with pytest.raises(AttributeError, match="only writes creator_"):
            cfg.creator_save(creator_save="pwned")
        assert callable(cfg.creator_save)

    def test_refuses_a_creator_prefixed_field_that_does_not_exist(self):
        with pytest.raises(AttributeError, match="only writes creator_"):
            AppConfig(**_real_settings()).creator_save(creator_pendign_sis="sd93custom")

    @pytest.mark.parametrize(
        ("updates", "why"),
        [
            ({"creator_pending_sis": None}, "None serialises to JSON null; the next load reads UNREADABLE"),
            ({"creator_pending_sis": 93}, "an int where a str is declared"),
            ({"creator_pending_sis": True}, "a bool where a str is declared"),
            ({"creator_verified": "sd93custom"}, "a str where a dict is declared"),
            ({"creator_verified": None}, "None would blank the map AND fail the next load"),
            ({"creator_verified": [["sd93custom", DIGEST]]}, "a list of pairs is not a mapping"),
        ],
    )
    def test_a_wrong_typed_value_raises_before_anything_is_written(self, updates, why, config_file: Path):
        """A mis-typed value makes the WHOLE settings document unreadable on the next load.

        That is a settings-LOSS bug reached through an advisory field, so it is refused
        loudly at the choke point rather than coerced.
        """
        cfg = AppConfig(**_real_settings())

        with pytest.raises(TypeError, match="creator_save"):
            cfg.creator_save(**updates)

        assert not config_file.exists(), f"a rejected save wrote to disk: {why}"
        assert (cfg.creator_pending_sis, cfg.creator_verified) == ("", {})

    def test_a_bad_key_late_in_the_call_leaves_no_partial_mutation(self, config_file: Path):
        """Validation runs to completion BEFORE any setattr."""
        cfg = AppConfig(**_real_settings())

        with pytest.raises(AttributeError):
            cfg.creator_save(creator_pending_sis="sd93custom", sis_type="pwned")
        assert cfg.creator_pending_sis == ""
        assert cfg.sis_type == "sd48myedbc"

        with pytest.raises(TypeError):
            cfg.creator_save(creator_pending_sis="sd93custom", creator_verified=None)
        assert cfg.creator_pending_sis == ""
        assert not config_file.exists()

    def test_swallows_an_oserror_and_rolls_the_instance_back(self, monkeypatch, caplog):
        """A disk-full / permission-denied save must not trap the admin mid-setup."""

        def boom(self):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(AppConfig, "save", boom)
        original_map = {"sd93custom": DIGEST}
        cfg = AppConfig(**_real_settings(), creator_pending_sis="sd11custom", creator_verified=original_map)

        with caplog.at_level(logging.WARNING):
            assert cfg.creator_save(creator_pending_sis="sd93custom", creator_verified={"sd99custom": DIGEST}) is False

        assert any("Could not save your district setup progress" in r.message for r in caplog.records)
        assert cfg.creator_pending_sis == "sd11custom"
        # The rolled-back map is the ORIGINAL OBJECT, not an equal copy: this instance is
        # SHARED across every Settings section, and the prune builds a NEW dict, so a
        # failed write must hand back exactly what the caller held.
        assert cfg.creator_verified is original_map

    def test_swallows_a_refusal_raised_by_save_itself(self, monkeypatch):
        """Defence in depth: if `save()` widens its refusal rule, we still don't raise."""

        def refuse(self):
            raise SettingsOverwriteRefused("nope")

        monkeypatch.setattr(AppConfig, "save", refuse)
        cfg = AppConfig(**_real_settings())

        assert cfg.creator_save(creator_pending_sis="sd93custom") is False
        assert cfg.creator_pending_sis == "", "a refused write must leave the shared instance untouched"


# --------------------------------------------------------------------------- #
# The prune — the map is bounded by the configs that actually exist            #
# --------------------------------------------------------------------------- #
class TestTheVerifiedMapIsPruned:
    def test_drops_a_vanished_id_and_keeps_a_live_one(self, config_file: Path):
        live = _write_sd93_overlay()
        cfg = AppConfig(**_real_settings())

        assert cfg.creator_save(creator_verified={live: DIGEST, "sd99custom": OTHER_DIGEST}) is True

        assert cfg.creator_verified == {live: DIGEST}
        assert AppConfig.load().creator_verified == {live: DIGEST}

    def test_drops_an_id_that_now_resolves_to_a_BUNDLED_config(self, config_file: Path):
        """Origin is the test, not mere resolvability.

        A tested-fact about a file the admin never tested must not survive because a
        SHIPPED config happens to answer to the same id.
        """
        cfg = AppConfig(**_real_settings())
        assert cfg.creator_save(creator_verified={"myedbc": DIGEST}) is True
        assert cfg.creator_verified == {}

    def test_pruning_happens_even_when_the_call_does_not_touch_the_map(self, config_file: Path):
        cfg = AppConfig(**_real_settings(), creator_verified={"sd99custom": DIGEST})

        assert cfg.creator_save(creator_pending_sis="sd93custom") is True

        assert cfg.creator_verified == {}
        assert AppConfig.load().creator_verified == {}

    def test_a_raising_resolver_prunes_nothing_and_still_writes(self, config_file: Path, monkeypatch, caplog):
        """TOTAL: a tidy-up may never block the write it is only tidying."""

        def boom(sis_type, **kwargs):
            raise RuntimeError("the mappings dir exploded")

        monkeypatch.setattr("src.config.loader.resolve_config_path", boom)
        cfg = AppConfig(**_real_settings())

        with caplog.at_level(logging.DEBUG, logger=app_config_module.logger.name):
            assert cfg.creator_save(creator_verified={"sd99custom": DIGEST}) is True

        assert cfg.creator_verified == {"sd99custom": DIGEST}
        assert AppConfig.load().creator_verified == {"sd99custom": DIGEST}
        assert any("none were pruned" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- #
# activate_creator_config — the ONE gated `sis_type` write                     #
# --------------------------------------------------------------------------- #
class TestActivateCreatorConfig:
    def test_writes_all_three_fields_in_exactly_one_save(self, config_file: Path, monkeypatch):
        """TWO saves would leave the district active while the resume token still stands —
        and the resumed flow's Discard would then delete a LIVE config."""
        real_save = AppConfig.save
        calls: list[int] = []

        def counting_save(self):
            calls.append(1)
            real_save(self)

        monkeypatch.setattr(AppConfig, "save", counting_save)
        live = _write_sd93_overlay()
        cfg = AppConfig(**_real_settings(), creator_pending_sis=live)

        assert cfg.activate_creator_config(sis_type=live, digest=DIGEST) is True

        assert len(calls) == 1, "activation must be ONE atomic save, never one per field"
        reloaded = AppConfig.load()
        assert reloaded.sis_type == live
        assert reloaded.creator_pending_sis == ""
        assert reloaded.creator_verified == {live: DIGEST}

    def test_records_the_digest_on_a_PRUNED_copy(self, config_file: Path):
        live = _write_sd93_overlay()
        cfg = AppConfig(**_real_settings(), creator_verified={"sd99custom": OTHER_DIGEST})

        assert cfg.activate_creator_config(sis_type=live, digest=DIGEST) is True

        assert cfg.creator_verified == {live: DIGEST}

    @pytest.mark.parametrize("bad_sis", ["", "sd93 custom", "../etc/passwd", "sd93custom;rm -rf /"])
    def test_an_invalid_sis_type_raises_before_anything_is_applied(self, bad_sis: str, config_file: Path):
        cfg = AppConfig(**_real_settings())
        with pytest.raises(ValueError, match="Invalid SIS type"):
            cfg.activate_creator_config(sis_type=bad_sis, digest=DIGEST)
        assert cfg.sis_type == "sd48myedbc"
        assert not config_file.exists()

    @pytest.mark.parametrize(
        ("bad_digest", "why"),
        [
            ("", "blank"),
            (DIGEST[:-1], "63 characters"),
            (DIGEST + "a", "65 characters"),
            (DIGEST.upper(), "uppercase — the stored value is compared as-is"),
            ("z" * 64, "not hex"),
        ],
    )
    def test_a_malformed_digest_raises_before_anything_is_applied(self, bad_digest, why, config_file: Path):
        """A digest that reads as ABSENT on the way back out would silently ask for
        another test run forever, so it is refused at the boundary instead of stored."""
        cfg = AppConfig(**_real_settings(), creator_pending_sis="sd93custom")
        with pytest.raises(ValueError, match="64 lowercase hex"):
            cfg.activate_creator_config(sis_type="sd93custom", digest=bad_digest)
        assert cfg.sis_type == "sd48myedbc", why
        assert cfg.creator_pending_sis == "sd93custom"
        assert not config_file.exists()

    def test_it_cannot_write_anything_outside_its_own_allowlist(self):
        """The guard is real, not decorative: the same helper refuses a stray key here too."""
        cfg = AppConfig(**_real_settings())
        with pytest.raises(AttributeError, match="activate_creator_config"):
            cfg._guarded_field_write(
                {"input_dir": "/pwned"},
                allowed=_ACTIVATION_FIELD_NAMES,
                refuse_when_unreadable=False,
                subject="the district you set up",
                writer="activate_creator_config",
            )
        assert cfg.input_dir == "/district/in"

    def test_is_NOT_refused_under_unreadable_provenance_and_quarantines_the_predecessor(
        self, config_file: Path, monkeypatch
    ):
        """The deliberate asymmetry with `creator_save`, and the honest cost of it.

        `sis_type` off its default is a CHOSEN setting, so this takes the same posture as
        the wizard's District step: the write lands, and the bytes it displaces — bytes
        this config never read — are preserved as a `config.corrupt-*.json` copy from
        which the admin's delivery settings are recoverable by eye. Nothing is silently
        blanked without a copy.
        """
        AppConfig(**_real_settings()).save()
        original_bytes = config_file.read_bytes()
        live = _write_sd93_overlay()

        cfg = _load_through_one_read_blip(config_file, monkeypatch)
        assert cfg.activate_creator_config(sis_type=live, digest=DIGEST) is True

        reloaded = AppConfig.load()
        assert reloaded.sis_type == live
        assert reloaded.creator_verified == {live: DIGEST}

        preserved = sorted(config_file.parent.glob("config.corrupt-*.json"))
        assert len(preserved) == 1, "settings we never read were replaced without a recoverable copy"
        assert preserved[0].read_bytes() == original_bytes
        # The ROADMAP-acknowledged residual, asserted as a RECOVERY rather than a repair:
        # the delivery host is gone from config.json but readable in the copy.
        assert reloaded.sftp_host == ""
        assert "sftp.spacesedu.com" in json.loads(preserved[0].read_text(encoding="utf-8"))["sftp_host"]

    def test_the_advisory_twin_is_still_refused_on_the_same_profile(self, config_file: Path, monkeypatch):
        """The other half of the asymmetry — without it, "not refused" proves nothing."""
        AppConfig(**_real_settings()).save()
        cfg = _load_through_one_read_blip(config_file, monkeypatch)

        assert cfg.creator_save(creator_pending_sis="sd93custom") is False
        assert not list(config_file.parent.glob("config.corrupt-*.json"))

    def test_activating_from_the_MAPPING_HOST_quarantines_the_predecessor(self, config_file: Path, monkeypatch):
        """Plan 0044 S6 §6.5 — the ROADMAP 2026-07-21 entry's acceptance shape, from the NEW
        writer at that entry's surface (a).

        Mapping's hosted creator panel is a second place ``activate_creator_config`` is
        called, so the UNREADABLE branch of ``save()`` now has one more entry point. Driven
        through the REAL ``build_mapping`` mount to prove the host routes through THAT method
        (deliberately non-advisory) rather than a bare ``sis_type`` write, which would drop
        both the validation and the quarantine.

        The folders are set on the loaded instance deliberately: an UNREADABLE load answers
        with DEFAULTS (that is what "we could not read it" means), and the panel's test
        conversion needs a folder to be reachable at all. It is an in-memory state, not a
        claim about disk — and it is precisely the residual the ROADMAP entry names.
        """
        from unittest.mock import MagicMock

        from src.etl.pipeline import PipelineResult
        from src.ui_flet import mapping_catalog
        from src.ui_flet.screens import creator as creator_screen
        from src.ui_flet.screens import mapping as mapping_screen
        from tests.test_ui_flet_activation_gate import _button, _texts

        AppConfig(**_real_settings(), creator_pending_sis="sd93custom").save()
        original_bytes = config_file.read_bytes()
        live = _write_sd93_overlay()
        mapping_catalog.reset_catalog_cache()

        cfg = _load_through_one_read_blip(config_file, monkeypatch)
        cfg.input_dir = "/district/in"  # see the docstring: in-memory, so the panel is reachable
        cfg.output_dir = str(config_file.parent / "out")
        cfg.creator_pending_sis = live
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda _cls: cfg))
        monkeypatch.setattr(
            creator_screen,
            "creator_gate_job",
            lambda *_a, **_kw: PipelineResult(entity_counts={"Students": 5}),
        )
        page = MagicMock()
        page.run_thread = lambda fn: fn()
        page.run_task = lambda coro, *args: __import__("asyncio").run(coro(*args))

        tree = mapping_screen.build_mapping(page, app_config=cfg, on_navigate=None)
        _button(tree, mapping_screen.MAPPING_RESUME_LABEL).on_click(None)
        _button(tree, creator_screen.GATE_RUN_LABEL).on_click(None)
        assert creator_screen.GATE_PASSED_HEADLINE in _texts(tree), "the stubbed test never passed"
        _button(tree, creator_screen.GATE_CONFIRM_LABEL).on_click(None)

        # (a) the write LANDED — and never reported success for something it did not write.
        assert cfg.sis_type == live
        assert cfg.creator_verified.get(live), "the tested fact was not recorded"
        assert creator_screen.CREATOR_ACTIVATE_FAILED_NOTE not in _texts(tree)
        # ``AppConfig.load`` is pinned for this mount, so the on-disk check reads the JSON.
        on_disk = json.loads(config_file.read_text(encoding="utf-8"))
        assert on_disk["sis_type"] == live

        # (b) the bytes this config never read are RECOVERABLE, not silently replaced.
        preserved = sorted(config_file.parent.glob("config.corrupt-*.json"))
        assert len(preserved) == 1, "settings we never read were replaced without a recoverable copy"
        assert preserved[0].read_bytes() == original_bytes
        assert "sftp.spacesedu.com" in json.loads(preserved[0].read_text(encoding="utf-8"))["sftp_host"]

    def test_the_twin_a_READABLE_profile_activates_from_that_host_with_no_quarantine(
        self, config_file: Path, monkeypatch
    ):
        """Without this, the quarantine above could be a copy the write path always takes."""
        from unittest.mock import MagicMock

        from src.etl.pipeline import PipelineResult
        from src.ui_flet import mapping_catalog
        from src.ui_flet.screens import creator as creator_screen
        from src.ui_flet.screens import mapping as mapping_screen
        from tests.test_ui_flet_activation_gate import _button, _texts

        live = _write_sd93_overlay()
        mapping_catalog.reset_catalog_cache()
        settings = _real_settings() | {
            "creator_pending_sis": live,
            "output_dir": str(config_file.parent / "out"),
        }
        AppConfig(**settings).save()  # type: ignore[arg-type]
        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED
        monkeypatch.setattr(AppConfig, "load", classmethod(lambda _cls: cfg))
        monkeypatch.setattr(
            creator_screen,
            "creator_gate_job",
            lambda *_a, **_kw: PipelineResult(entity_counts={"Students": 5}),
        )
        page = MagicMock()
        page.run_thread = lambda fn: fn()
        page.run_task = lambda coro, *args: __import__("asyncio").run(coro(*args))

        tree = mapping_screen.build_mapping(page, app_config=cfg, on_navigate=None)
        _button(tree, mapping_screen.MAPPING_RESUME_LABEL).on_click(None)
        _button(tree, creator_screen.GATE_RUN_LABEL).on_click(None)
        _button(tree, creator_screen.GATE_CONFIRM_LABEL).on_click(None)

        assert cfg.sis_type == live
        assert _texts(tree), "the tree rendered nothing — the assertions above are vacuous"
        assert not list(config_file.parent.glob("config.corrupt-*.json"))
        assert json.loads(config_file.read_text(encoding="utf-8"))["sftp_host"] == "sftp.spacesedu.com", (
            "a readable profile lost the delivery settings it could read"
        )

    def test_returns_false_and_rolls_back_when_the_save_fails(self, monkeypatch):
        def boom(self):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(AppConfig, "save", boom)
        cfg = AppConfig(**_real_settings(), creator_pending_sis="sd93custom")

        assert cfg.activate_creator_config(sis_type="sd93custom", digest=DIGEST) is False

        assert cfg.sis_type == "sd48myedbc"
        assert cfg.creator_pending_sis == "sd93custom", "a failed activation must leave the resume token standing"
        assert cfg.creator_verified == {}
