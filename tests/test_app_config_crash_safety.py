"""Crash-safety tests for ``src/config/app_config.py`` (slice W2-B).

RED-FIRST: every test in this module was written and confirmed FAILING against the
pre-slice implementation — a bare ``Path.write_text`` save (no staging, no fsync, no
atomic promote) plus a blanket ``except Exception`` load that returned all-defaults —
before a single line of the fix was written.

Two failure modes are pinned here:

1. **A torn write.** A crash / power loss / disk-full partway through the settings
   write must NOT leave a half-written ``config.json`` behind. The write stages to a
   sibling temp file, fsyncs it, and promotes it with a single ``os.replace``
   (an atomic same-filesystem overwrite — never ``shutil.move``, whose Windows
   copy2+unlink path tears *within* the file). A failure at any point leaves the
   previous settings byte-intact.
2. **"I lost your settings" read as "you're a new user".** An existing-but-unreadable
   ``config.json`` must be distinguishable from a genuinely absent one, so a working
   install can never be silently reset to first-run.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

import src.config.app_config as app_config
from src.config.app_config import AppConfig, ConfigLoadState
from src.ui_flet.nav import needs_setup

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
