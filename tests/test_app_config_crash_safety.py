"""Crash-safety tests for ``src/config/app_config.py`` (slice W2-B, extended by FIX-1).

RED-FIRST, in two waves. The W2-B tests (classes 1–5 below) were written and confirmed
FAILING against the pre-slice implementation — a bare ``Path.write_text`` save (no
staging, no fsync, no atomic promote) plus a blanket ``except Exception`` load that
returned all-defaults — before a single line of that fix was written. The FIX-1 tests
(the last three classes) were likewise written and confirmed failing against the LANDED
W2-B code before its follow-up fix: 13 red, including the blocker reproduction below.

Four failure modes are pinned here:

1. **A torn write.** A crash / power loss / disk-full partway through the settings
   write must NOT leave a half-written ``config.json`` behind. The write stages to a
   sibling temp file, fsyncs it, and promotes it with a single ``os.replace``
   (an atomic same-filesystem overwrite — never ``shutil.move``, whose Windows
   copy2+unlink path tears *within* the file). A failure at any point leaves the
   previous settings byte-intact.
2. **"I lost your settings" read as "you're a new user".** An existing-but-unreadable
   ``config.json`` must be distinguishable from a genuinely absent one, so a working
   install can never be silently reset to first-run.
3. **The read's honesty thrown away by the next write (FIX-1).** W2-B taught ``load()``
   to report UNREADABLE, but nothing on the WRITE path consulted it — so the advisory
   window-geometry save on plain app exit replaced the admin's district / folders /
   delivery settings with defaults, quarantining nothing whenever the read failure had
   cleared by save time. The onboarding suppression survived only until the admin closed
   the window.
4. **A wrong-typed key accepted and persisted back (FIX-1).** ``config.json`` is
   hand-editable, untrusted input; a wrong type must be caught at the boundary rather
   than carried through the session and written back verbatim.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import src.config.app_config as app_config
from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet.home_status import derive_home_status
from src.ui_flet.nav import nav_model, needs_setup, prominent_initial_id
from src.ui_flet.verdict import Verdict

# A realistic torn write: a valid JSON *prefix* truncated mid-value — exactly what a
# non-atomic ``write_text`` leaves on disk when the process dies mid-flush.
TORN_PREFIX = '{\n  "input_dir": "/data/in",\n  "output_dir": "/da'


@pytest.fixture
def config_dir(isolated_user_profile: Path) -> tuple[Path, Path]:
    """The isolated app-data dir + the ``config.json`` path inside it."""
    return isolated_user_profile, isolated_user_profile / "config.json"


def _write_raw(cfg_dir: Path, cfg_file: Path, text: str) -> None:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(text, encoding="utf-8")


class TestAtomicSave:
    """The write is staged, fsynced, and promoted atomically — or it changes nothing."""

    def test_crash_between_staging_and_promote_leaves_previous_settings_intact(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The torn-write simulation: kill the save at the promote, lose nothing.

        A completed install saves its settings; the next save dies at the exact moment
        a power loss is most likely (the promote). The previous ``config.json`` must be
        byte-identical afterwards and must still load as a COMPLETED install — the
        nightly sync's district/folders/finish-line survive.
        """
        _cfg_dir, cfg_file = config_dir
        AppConfig(
            input_dir="/data/in",
            output_dir="/data/out",
            sis_type="sd48myedbc",
            setup_completed=True,
        ).save()
        before = cfg_file.read_bytes()

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("simulated power loss between staging and promote")

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "replace", boom)
            with pytest.raises(OSError):
                AppConfig(input_dir="/other", output_dir="/other", sis_type="sd74myedbc").save()

        assert cfg_file.read_bytes() == before
        recovered = AppConfig.load()
        assert recovered.sis_type == "sd48myedbc"
        assert recovered.input_dir == "/data/in"
        assert recovered.has_completed_setup() is True

    def test_a_failed_save_leaves_no_temp_litter(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A save that dies at the promote cleans up its own staging file."""
        cfg_dir, _cfg_file = config_dir
        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("simulated power loss between staging and promote")

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "replace", boom)
            with pytest.raises(OSError):
                AppConfig(input_dir="/other", output_dir="/other", sis_type="sd74myedbc").save()

        assert sorted(p.name for p in cfg_dir.iterdir()) == ["config.json"]

    def test_the_payload_is_fully_staged_before_the_promote(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At promote time the target still holds the OLD bytes and the source the WHOLE new payload.

        This is the atomicity proof: there is no instant at which ``config.json``
        contains a partial document.
        """
        _cfg_dir, cfg_file = config_dir
        AppConfig(input_dir="/old", output_dir="/old", sis_type="sd48myedbc").save()
        old_bytes = cfg_file.read_bytes()

        observed: dict[str, object] = {}
        real_replace = os.replace

        def spy(src: object, dst: object, **kwargs: object) -> None:
            observed["target_before_promote"] = Path(str(dst)).read_bytes()
            observed["staged"] = json.loads(Path(str(src)).read_text(encoding="utf-8"))
            real_replace(str(src), str(dst))

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "replace", spy)
            AppConfig(input_dir="/new", output_dir="/new", sis_type="sd74myedbc").save()

        assert observed["target_before_promote"] == old_bytes
        staged = observed["staged"]
        assert isinstance(staged, dict)
        assert staged["sis_type"] == "sd74myedbc"
        assert staged["input_dir"] == "/new"
        assert cfg_file.read_bytes() != old_bytes

    def test_the_payload_is_fsynced_before_the_promote(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Durability ordering: the bytes hit the disk BEFORE the name is swapped.

        Without the fsync, ``os.replace`` is atomic only with respect to the *name* —
        a power loss can still promote an empty/partial file whose data never left the
        page cache.
        """
        calls: list[str] = []
        real_fsync = os.fsync
        real_replace = os.replace

        def fsync_spy(fd: int) -> None:
            calls.append("fsync")
            real_fsync(fd)

        def replace_spy(src: object, dst: object, **kwargs: object) -> None:
            calls.append("replace")
            real_replace(str(src), str(dst))

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "fsync", fsync_spy)
            mp.setattr(app_config.os, "replace", replace_spy)
            AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()

        assert "fsync" in calls, "the staged payload was never fsynced"
        assert "replace" in calls, "the payload was not promoted with os.replace"
        assert calls.index("fsync") < calls.index("replace")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; no-op on Windows")
class TestPermissionsSurviveTheTempFile:
    """The staging file must not widen the 0o600 / 0o700 window."""

    def test_the_staged_file_is_owner_only_before_the_promote(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        modes: list[int] = []
        real_replace = os.replace

        def spy(src: object, dst: object, **kwargs: object) -> None:
            modes.append(stat.S_IMODE(os.stat(str(src)).st_mode))
            real_replace(str(src), str(dst))

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "replace", spy)
            AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()

        assert modes == [0o600], "the staging file was world/group readable before the promote"

    def test_the_promoted_file_and_its_directory_stay_owner_only(self, config_dir: tuple[Path, Path]) -> None:
        cfg_dir, cfg_file = config_dir
        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()

        assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700

    def test_permissions_are_re_narrowed_on_every_save(self, config_dir: tuple[Path, Path]) -> None:
        """A pre-existing world-readable config.json is narrowed by the next save."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, "{}")
        os.chmod(cfg_file, 0o644)

        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()

        assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600


class TestUnreadablePredecessorIsPreserved:
    """Bytes we are about to destroy are kept for hand-recovery — but only those."""

    def test_save_preserves_an_unreadable_predecessor(self, config_dir: tuple[Path, Path]) -> None:
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        AppConfig(input_dir="/new", output_dir="/new", sis_type="myedbc").save()

        preserved = sorted(cfg_dir.glob("config.corrupt-*.json"))
        assert len(preserved) == 1, "the unreadable predecessor was destroyed without a copy"
        assert preserved[0].read_text(encoding="utf-8") == TORN_PREFIX
        assert AppConfig.load().sis_type == "myedbc"

    def test_save_over_a_readable_config_leaves_no_quarantine_copy(self, config_dir: tuple[Path, Path]) -> None:
        """The happy path never litters — quarantine fires only on an unreadable predecessor."""
        cfg_dir, _cfg_file = config_dir
        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()
        AppConfig(input_dir="/in2", output_dir="/out2", sis_type="sd48myedbc").save()

        assert list(cfg_dir.glob("config.corrupt-*.json")) == []
        assert sorted(p.name for p in cfg_dir.iterdir()) == ["config.json"]

    def test_a_failed_preservation_never_blocks_the_repairing_save(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Archiving the BROKEN settings is best-effort; writing the GOOD ones is not.

        If the quarantine copy cannot be created (read-only volume, quota), the save
        that repairs the settings must still land — otherwise a single unwritable
        sibling file would permanently wedge the admin out of their own settings.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        real_open = os.open

        def refuse_quarantine(path: object, flags: int, *args: object, **kwargs: object) -> int:
            if "config.corrupt-" in str(path):
                raise OSError("simulated read-only volume")
            return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "open", refuse_quarantine)
            AppConfig(input_dir="/new", output_dir="/new", sis_type="myedbc").save()

        assert list(cfg_dir.glob("config.corrupt-*.json")) == []
        assert AppConfig.load().sis_type == "myedbc"

    def test_an_uninspectable_predecessor_never_blocks_the_save(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A predecessor we cannot even READ is left alone — and the save still lands."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        real_read_bytes = Path.read_bytes

        def denied(self: Path) -> bytes:
            if self == cfg_file:
                raise PermissionError("simulated locked settings file")
            return real_read_bytes(self)

        with monkeypatch.context() as mp:
            mp.setattr(Path, "read_bytes", denied)
            AppConfig(input_dir="/new", output_dir="/new", sis_type="myedbc").save()

        assert list(cfg_dir.glob("config.corrupt-*.json")) == []
        assert AppConfig.load().sis_type == "myedbc"


class TestLoadStateIsHonest:
    """ "We lost your settings" must never be indistinguishable from "you're a new user"."""

    def test_absent_file_reports_absent(self, config_dir: tuple[Path, Path]) -> None:
        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.ABSENT
        assert cfg.settings_unreadable() is False

    def test_readable_file_reports_loaded(self, config_dir: tuple[Path, Path]) -> None:
        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()
        assert AppConfig.load().load_state is ConfigLoadState.LOADED

    def test_torn_file_reports_unreadable_not_absent(self, config_dir: tuple[Path, Path]) -> None:
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE
        assert cfg.settings_unreadable() is True

    def test_non_object_json_reports_unreadable(self, config_dir: tuple[Path, Path]) -> None:
        """A syntactically valid JSON document that is not a settings OBJECT is unreadable."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, "[1, 2, 3]")

        assert AppConfig.load().load_state is ConfigLoadState.UNREADABLE

    def test_wrong_value_types_report_unreadable_instead_of_raising(self, config_dir: tuple[Path, Path]) -> None:
        """A nonsense value type that makes the config UNUSABLE is unreadable, never a crash.

        ``sis_type`` here is an object, so the ``_SIS_TYPE_RE`` match inside
        ``is_complete()`` would raise ``TypeError`` mid-load. The pre-slice code swallowed
        that into silent defaults; it must now be reported as UNREADABLE — and ``load()``
        must still not raise.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(
            cfg_dir,
            cfg_file,
            json.dumps({"input_dir": "/in", "output_dir": "/out", "sis_type": {"not": "a string"}}),
        )

        assert AppConfig.load().load_state is ConfigLoadState.UNREADABLE

    def test_an_unreadable_read_error_is_not_absent(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file we cannot READ (permissions, locked) is unreadable, never "no file"."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, json.dumps({"sis_type": "myedbc"}))

        real_read_bytes = Path.read_bytes

        def denied(self: Path) -> bytes:
            if self == cfg_file:
                raise PermissionError("simulated locked settings file")
            return real_read_bytes(self)

        with monkeypatch.context() as mp:
            mp.setattr(Path, "read_bytes", denied)
            cfg = AppConfig.load()

        assert cfg.load_state is ConfigLoadState.UNREADABLE

    def test_load_does_not_mutate_the_filesystem(self, config_dir: tuple[Path, Path]) -> None:
        """load() is a PURE read — repeated loads of a torn file report the SAME state.

        This pins the design choice against quarantine-inside-load: ``AppConfig.load()``
        is called on nearly every UI surface, so a load that moved the bad file aside
        would report UNREADABLE once and ABSENT ever after — dumping the admin back into
        onboarding one screen later, which is the exact failure this slice removes.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)
        before = cfg_file.read_bytes()

        first = AppConfig.load()
        second = AppConfig.load()

        assert first.load_state is ConfigLoadState.UNREADABLE
        assert second.load_state is ConfigLoadState.UNREADABLE
        assert cfg_file.read_bytes() == before
        assert sorted(p.name for p in cfg_dir.iterdir()) == ["config.json"]

    def test_load_state_is_never_persisted(self, config_dir: tuple[Path, Path]) -> None:
        _cfg_dir, cfg_file = config_dir
        AppConfig(input_dir="/in", output_dir="/out", sis_type="myedbc").save()

        assert "load_state" not in json.loads(cfg_file.read_text(encoding="utf-8"))

    def test_load_state_cannot_be_forged_from_disk(self, config_dir: tuple[Path, Path]) -> None:
        """Provenance is observed, never read from the file it describes."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, json.dumps({"sis_type": "myedbc", "load_state": "unreadable"}))

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED
        assert cfg.settings_unreadable() is False


class TestUnreadableSettingsAreNotANewInstall:
    """The product consequence: a Firefighter is never dumped back into onboarding."""

    def test_unreadable_settings_do_not_trigger_onboarding(self, config_dir: tuple[Path, Path]) -> None:
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        assert needs_setup(AppConfig.load()) is False

    def test_a_genuinely_absent_file_still_onboards(self, config_dir: tuple[Path, Path]) -> None:
        """The newcomer path is untouched — no config.json still means first-run."""
        assert needs_setup(AppConfig.load()) is True

    def test_unreadable_settings_do_not_fake_a_completed_setup(self, config_dir: tuple[Path, Path]) -> None:
        """We stop asserting "new user"; we do NOT start asserting "set up" either.

        ``has_completed_setup()`` stays a fact about what was READ. Unreadable settings
        mean the finish-line fact is unknown, so it stays False — the honesty split is
        carried by ``settings_unreadable()``, not by faking the finish line.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        cfg = AppConfig.load()
        assert cfg.has_completed_setup() is False
        assert cfg.setup_completed is False


# --------------------------------------------------------------------------- #
# The write-path half of the honesty split (W2-B follow-up, FIX-1).            #
# --------------------------------------------------------------------------- #
def _complete_install() -> AppConfig:
    """A fully-configured install: district, folders, delivery, finish line crossed."""
    return AppConfig(
        input_dir="/data/in",
        output_dir="/data/out",
        sis_type="sd74myedbc",
        sftp_enabled=True,
        sftp_host="sftp.spacesedu.com",
        sftp_username="sd74",
        setup_completed=True,
    )


def _load_through_one_read_blip(cfg_file: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """``AppConfig.load()`` while ONE read of ``config.json`` fails, then reads fine again.

    The cross-platform stand-in for a transient Windows sharing violation / AV lock /
    permissions blip — the ``except OSError`` branch of ``load()``. Mocking the read keeps
    this reproducible on the Ubuntu CI runner (no real OS locking involved).
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
    return cfg


class TestAnUnreadableLoadNeverSilentlyReplacesTheSettings:
    """A config we FAILED TO READ may not be overwritten with defaults nobody chose.

    ``load()`` returns DEFAULTS tagged ``UNREADABLE``. The landed W2-B slice taught the READ
    path to say so, but nothing on the WRITE path consulted the tag: ``save()`` wrote those
    invented defaults verbatim, and ``_preserve_unreadable_predecessor`` decided whether to
    quarantine by RE-READING the disk instead of trusting the ``load_state`` it exists to
    protect. So a file that was unreadable at load time but readable again at save time — a
    transient sharing violation, an AV lock, a permissions blip — was judged "readable,
    nothing to preserve" and atomically, durably replaced with defaults, with NO quarantine
    copy.

    The trigger needs no corruption and no crash: ``shell._persist_window_geometry`` saves the
    window bounds on PLAIN APP EXIT, so the onboarding suppression the read path bought
    survived only until the admin closed the window.
    """

    def test_a_transient_read_blip_then_the_exit_geometry_save_keeps_every_setting(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE reproduction: blip on load, plain app exit, settings intact.

        Mirrors ``shell._persist_window_geometry`` exactly — including its deliberate
        ``except Exception`` (the exit path must stay unblockable), so this asserts the
        OUTCOME the admin sees and not the mechanism that produces it.
        """
        _cfg_dir, cfg_file = config_dir
        _complete_install().save()
        good_bytes = cfg_file.read_bytes()

        cfg = _load_through_one_read_blip(cfg_file, monkeypatch)
        assert cfg.load_state is ConfigLoadState.UNREADABLE
        assert cfg.sis_type == ""

        # The shell's advisory geometry save on window close (shell.py `_persist_window_geometry`).
        cfg.window_width = 1280.0
        cfg.window_height = 800.0
        cfg.window_maximized = False
        with contextlib.suppress(Exception):
            cfg.save()

        assert cfg_file.read_bytes() == good_bytes, "the settings file was replaced by an advisory geometry save"
        restored = AppConfig.load()
        assert restored.load_state is ConfigLoadState.LOADED
        assert restored.sis_type == "sd74myedbc"
        assert restored.input_dir == "/data/in"
        assert restored.output_dir == "/data/out"
        assert restored.sftp_host == "sftp.spacesedu.com"
        assert restored.has_completed_setup() is True
        assert needs_setup(restored) is False, "closing the window dropped a configured admin into onboarding"

    def test_the_refused_save_is_loud_not_silent(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A settings write that did NOT happen must never look like one that did.

        Same contract the file already documents for a failed ``os.replace``: ``save()``
        raises rather than returning as if it had written. The ONE reachable caller of a
        settings-free save is the advisory geometry saver, which already swallows and
        keeps closing — so the refusal cannot block or crash the app exit — but the WARNING
        is emitted inside ``save()`` so the event reaches the support log regardless of what
        the caller does with the exception.
        """
        from src.config.app_config import SettingsOverwriteRefused

        _cfg_dir, cfg_file = config_dir
        _complete_install().save()

        cfg = _load_through_one_read_blip(cfg_file, monkeypatch)
        cfg.window_width = 1280.0

        with caplog.at_level("WARNING", logger=app_config.logger.name), pytest.raises(SettingsOverwriteRefused):
            cfg.save()

        assert caplog.records, "the refusal was silent — nothing reached the log"

    def test_a_deliberate_repair_still_lands_and_preserves_the_bytes_it_replaces(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The anti-wedge half: the Setup wizard's repair save is never blocked.

        The discriminating case for the blocker — the predecessor is perfectly READABLE at
        save time (the blip has passed), so a re-read says "nothing to preserve". The
        ``load_state`` in hand says otherwise: these are bytes this config never read, so
        they are quarantined before the replacement lands.
        """
        cfg_dir, cfg_file = config_dir
        _complete_install().save()
        original_bytes = cfg_file.read_bytes()

        cfg = _load_through_one_read_blip(cfg_file, monkeypatch)
        # What the wizard does on an install it could not read: the admin re-enters their
        # choices, and the finish line records completion.
        cfg.sis_type = "sd48myedbc"
        cfg.input_dir = "/repaired/in"
        cfg.output_dir = "/repaired/out"
        cfg.setup_completed = True
        cfg.save()

        repaired = AppConfig.load()
        assert repaired.sis_type == "sd48myedbc"
        assert repaired.input_dir == "/repaired/in"

        preserved = sorted(cfg_dir.glob("config.corrupt-*.json"))
        assert len(preserved) == 1, "settings we never read were replaced without a recoverable copy"
        assert preserved[0].read_bytes() == original_bytes

    def test_a_freshly_constructed_config_still_saves_normally(self, config_dir: tuple[Path, Path]) -> None:
        """Provenance, not shape: a never-loaded ``AppConfig()`` is not an unreadable one.

        A genuinely-new install constructs its config in memory and saves it — that path
        must be untouched by the refusal (no exception, no quarantine litter).
        """
        cfg_dir, cfg_file = config_dir
        fresh = AppConfig()
        assert fresh.load_state is ConfigLoadState.ABSENT

        fresh.window_width = 1024.0
        fresh.save()

        assert json.loads(cfg_file.read_text(encoding="utf-8"))["window_width"] == 1024.0
        assert list(cfg_dir.glob("config.corrupt-*.json")) == []

    def test_a_geometry_only_save_over_a_readable_blank_config_still_writes(
        self, config_dir: tuple[Path, Path]
    ) -> None:
        """The refusal keys on PROVENANCE, never on "the settings look empty".

        A present-and-readable ``config.json`` whose settings happen to still be defaults (a
        launched-but-never-configured install) must keep persisting its window bounds — those
        values WERE read, so nothing is being invented.
        """
        _cfg_dir, cfg_file = config_dir
        _write_raw(_cfg_dir, cfg_file, "{}")

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED
        cfg.window_left = 40.0
        cfg.save()

        assert json.loads(cfg_file.read_text(encoding="utf-8"))["window_left"] == 40.0

    def test_a_repaired_config_quarantines_ONCE_not_on_every_later_save(self, config_dir: tuple[Path, Path]) -> None:
        """A successful save re-tags the instance LOADED — it now holds what is on disk.

        The Setup wizard keeps ONE ``AppConfig`` across every step, saving at each. Without
        the provenance transition that instance stays UNREADABLE forever and quarantines
        its OWN freshly-written good bytes on every subsequent save — a growing pile of
        ``config.corrupt-*.json`` copies of a perfectly healthy file.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE

        cfg.sis_type = "myedbc"
        cfg.save()
        assert cfg.load_state is ConfigLoadState.LOADED

        for step in ("/in", "/out"):
            cfg.input_dir = step
            cfg.save()

        preserved = list(cfg_dir.glob("config.corrupt-*.json"))
        assert len(preserved) == 1, f"a repaired config kept re-quarantining itself: {[p.name for p in preserved]}"
        assert preserved[0].read_text(encoding="utf-8") == TORN_PREFIX

    def test_a_failed_write_does_not_advance_the_provenance(
        self, config_dir: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A write that did not happen must not claim the instance now matches the disk."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        cfg = AppConfig.load()
        cfg.sis_type = "myedbc"

        def boom(src: object, dst: object, **kwargs: object) -> None:
            raise OSError("simulated disk full")

        with monkeypatch.context() as mp:
            mp.setattr(app_config.os, "replace", boom)
            with pytest.raises(OSError):
                cfg.save()

        assert cfg.load_state is ConfigLoadState.UNREADABLE

    def test_a_still_torn_predecessor_is_preserved_by_the_repair_save(self, config_dir: tuple[Path, Path]) -> None:
        """The already-covered path stays covered: unreadable at load AND at save."""
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE
        cfg.sis_type = "myedbc"
        cfg.input_dir = "/in"
        cfg.output_dir = "/out"
        cfg.save()

        preserved = sorted(cfg_dir.glob("config.corrupt-*.json"))
        assert len(preserved) == 1
        assert preserved[0].read_text(encoding="utf-8") == TORN_PREFIX
        assert AppConfig.load().sis_type == "myedbc"


class TestWrongTypedKeysAreRejectedAtTheBoundary:
    """``config.json`` is untrusted input — a hand-edited wrong type is caught on the way IN.

    Without this the nonsense value is accepted, carried through the session, and PERSISTED
    BACK to disk verbatim by the next save — cementing the corruption instead of quarantining
    it. Validate at boundaries (CLAUDE.md): the settings file is one.
    """

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("sis_type", {}),  # the docstring's own example — an EMPTY dict is falsy, so it
            ("sis_type", {"not": "a string"}),  # slipped past the is_complete() guard entirely
            ("input_dir", 17),
            ("sftp_port", "22"),
            ("sftp_port", True),  # bool is an int subclass — must not pass an int field
            ("sftp_enabled", "yes"),
            ("schedule_registered", 1),
            ("window_width", "wide"),
            ("schedule_task_args", ["not", "a", "mapping"]),
        ],
    )
    def test_a_wrong_typed_known_key_makes_the_document_unreadable(
        self, config_dir: tuple[Path, Path], key: str, value: object
    ) -> None:
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, json.dumps({key: value}))

        assert AppConfig.load().load_state is ConfigLoadState.UNREADABLE

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("sis_type", "sd74myedbc"),
            ("sftp_port", 2222),
            ("sftp_enabled", False),
            ("window_width", 1280),  # a JSON int is a fine ``float | None``
            ("window_width", 1280.5),
            ("window_width", None),  # ``None`` = "never saved"
            ("schedule_task_args", {"sis": "myedbc"}),
            ("schedule_task_args", None),
        ],
    )
    def test_a_correctly_typed_key_still_loads(self, config_dir: tuple[Path, Path], key: str, value: object) -> None:
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, json.dumps({key: value}))

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED
        assert getattr(cfg, key) == value

    def test_an_unrecognised_annotation_form_is_never_a_reason_to_call_a_config_corrupt(self) -> None:
        """The matcher's total fallback: a type check must never invent corruption.

        Pinned directly because no CURRENT field reaches it — that is the point. If a
        future field carries an annotation form ``_value_fits`` doesn't model, the value
        must pass (the ``except (TypeError, ValueError)`` floor in ``_config_from_bytes``
        catches a genuinely unusable one) rather than tell a working install its settings
        are corrupt.
        """
        assert app_config._value_fits("anything", "a-form-this-matcher-does-not-model") is True

    def test_unknown_keys_are_still_ignored_not_rejected(self, config_dir: tuple[Path, Path]) -> None:
        """Forward-compatibility with a newer build's config is unchanged — and unpoliced.

        The type check applies ONLY to keys this build knows; an unknown key of any type is
        dropped, never a reason to call the document corrupt.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(
            cfg_dir,
            cfg_file,
            json.dumps({"sis_type": "myedbc", "a_future_setting": {"deeply": ["nested", 1]}}),
        )

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.LOADED
        assert cfg.sis_type == "myedbc"

    def test_a_rejected_document_is_preserved_by_the_repairing_save(self, config_dir: tuple[Path, Path]) -> None:
        """Rejection routes into the SAME quarantine path — the bytes stay recoverable."""
        cfg_dir, cfg_file = config_dir
        raw = json.dumps({"input_dir": "/data/in", "output_dir": "/data/out", "sftp_port": "22"})
        _write_raw(cfg_dir, cfg_file, raw)

        cfg = AppConfig.load()
        assert cfg.load_state is ConfigLoadState.UNREADABLE
        cfg.sis_type = "myedbc"
        cfg.input_dir = "/in"
        cfg.output_dir = "/out"
        cfg.save()

        preserved = sorted(cfg_dir.glob("config.corrupt-*.json"))
        assert len(preserved) == 1
        assert preserved[0].read_text(encoding="utf-8") == raw

    def test_a_wrong_typed_key_never_logs_its_value(
        self, config_dir: tuple[Path, Path], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Privacy: the settings file holds folder paths and a delivery username.

        The rejection diagnostic names the KEY and the TYPE it found — never the value.
        """
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, json.dumps({"input_dir": ["/districts/sd74/private/roster"]}))

        with caplog.at_level("DEBUG", logger=app_config.logger.name):
            AppConfig.load()

        assert "/districts/sd74/private/roster" not in caplog.text


class TestTheUnreadableStateMisleadsNeitherSurface:
    """``needs_setup`` False while ``has_completed_setup`` False — reachable ONLY here.

    Before W2-B the two were exact complements. The unreadable state deliberately splits
    them: we stop asserting "you are a new user" (a claim we know to be false) WITHOUT
    asserting "you are set up" (a claim we cannot verify). Both surfaces must stay honest in
    the gap.
    """

    @pytest.fixture
    def unreadable(self, config_dir: tuple[Path, Path]) -> AppConfig:
        cfg_dir, cfg_file = config_dir
        _write_raw(cfg_dir, cfg_file, TORN_PREFIX)
        cfg = AppConfig.load()
        assert needs_setup(cfg) is False
        assert cfg.has_completed_setup() is False
        return cfg

    def test_the_launch_lands_on_home_not_on_setup(self, unreadable: AppConfig) -> None:
        """The rail must not shove a configured admin at the wizard as if they were new."""
        assert prominent_initial_id(nav_model(unreadable)) == "home"

    def test_home_shows_the_dashboard_and_reports_the_real_run(self, unreadable: AppConfig) -> None:
        """Home reads the run store — a separate, intact artifact — not the lost settings.

        The verdict is whatever the store says; nothing about it is derived from the config
        we failed to read, so the admin sees the truth about their sync.
        """
        now = datetime(2026, 7, 21, 8, 0, 0)
        records = [
            {
                "timestamp": (now - timedelta(hours=5)).isoformat(timespec="seconds"),
                "status": "success",
                "sftp_attempted": True,
                "sftp_ok": True,
                "Students": 1200,
            }
        ]

        status = derive_home_status(records, unreadable, now=now, store_created_at="2026-01-01T00:00:00")

        assert status.verdict is Verdict.HEALTHY
        assert "no sync has run" not in status.headline.lower()
        assert "set up" not in status.detail.lower()

    def test_home_never_claims_the_admin_has_never_run_a_sync_when_the_store_is_established(
        self, unreadable: AppConfig
    ) -> None:
        """The empty-store copy must not read as "you're brand new" here.

        ``has_completed_setup()`` is False in this state, so the established-install
        discriminator falls to the store's own ``created_at`` — which is why Home must keep
        consulting it rather than the config flag alone.
        """
        status = derive_home_status([], unreadable, store_created_at="2026-01-01T00:00:00")

        assert status.headline == "Run history starts fresh here"
        assert status.verdict is Verdict.WARNING

    def test_home_degrades_calmly_when_the_run_store_is_unreadable_too(self, unreadable: AppConfig) -> None:
        """Both artifacts gone: Home says it cannot tell — it never guesses "all clear"."""
        status = derive_home_status(None, unreadable)

        assert status.verdict is Verdict.WARNING
        assert status.headline == "Sync status unavailable"

    def test_setup_offers_the_wizard_as_the_repair_door_and_that_repair_saves(
        self, unreadable: AppConfig, config_dir: tuple[Path, Path]
    ) -> None:
        """Setup's mode discriminator is ``has_completed_setup()`` — False, so: wizard.

        That is the honest answer (we cannot confirm a finish line we never read) AND the
        only door back to working settings. So the load-bearing assertion is not the mode —
        it is that the wizard's saves LAND: a refusal here would wedge the admin out of
        their own settings permanently.
        """
        cfg_dir, _cfg_file = config_dir
        assert unreadable.has_completed_setup() is False  # → screens/setup.py mounts the wizard

        unreadable.sis_type = "sd74myedbc"  # the wizard's District step
        unreadable.save()
        unreadable.input_dir = "/in"  # the Folders step
        unreadable.output_dir = "/out"
        unreadable.save()
        unreadable.setup_completed = True  # the finish line
        unreadable.save()

        graduated = AppConfig.load()
        assert graduated.has_completed_setup() is True
        assert needs_setup(graduated) is False
        assert graduated.sis_type == "sd74myedbc"
        # The FIRST repair save preserved the torn bytes; the later ones re-tagged LOADED and
        # had nothing left to preserve — exactly one copy, however many steps the admin takes.
        assert len(list(cfg_dir.glob("config.corrupt-*.json"))) == 1
