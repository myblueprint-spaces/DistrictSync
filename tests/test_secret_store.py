"""The delivery-secret store — the select rule, identity binding, and write atomicity.

Plan 0049 S-1a-ii. Two stores, never both: ``UserSecretStore`` (today's keyring, every
install in the field) and ``MachineSecretStore`` (a LocalMachine-DPAPI blob under the
shared profile). ``select_store()`` picks ONE from ``paths.is_machine_scope()``.

**How the machine store is reachable on every OS.** DPAPI is a Windows API, and CI's
headline leg is ubuntu, so the two syscall wrappers ``protect_machine_blob`` /
``unprotect_machine_blob`` are module-level SEAMS (the S-1a-i pattern: one line per
syscall, everything decided around it testable everywhere). ``fake_dpapi`` below swaps in
a length-prefixed stand-in that reproduces the ONE property the logic depends on — an
unprotect with the wrong entropy RAISES — so the identity binding, the verify-before-
promote write and the totality of ``has_secret`` are all exercised on Linux, macOS and
Windows alike. The stand-in does NOT encrypt; every assertion that depends on real
ciphertext (the password absent from the on-disk bytes) lives in the ``WINDOWS_ONLY``
round trip at the end, which drives the real API.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import keyring
import pytest

from src.config.app_config import AppConfig
from src.sftp import secret_store
from src.sftp.secret_store import (
    KEYRING_SERVICE,
    MachineSecretStore,
    SecretStoreError,
    UserSecretStore,
    select_store,
)
from src.utils import paths as paths_module

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows-only API")

HOST = "sftp.ca.spacesedu.com"
USER = "district_x"
# Distinctive enough that a substring search over a log/exception/repr is meaningful.
SECRET = "zz-delivery-secret-9f3a-NEVER-LOGGED"


# --------------------------------------------------------------------------- #
# Seams                                                                        #
# --------------------------------------------------------------------------- #
def _fake_seal(data: bytes, entropy: bytes) -> bytes:
    """Stand-in for ``CryptProtectData``: length-prefix the entropy, then the payload."""
    return len(entropy).to_bytes(4, "big") + entropy + data


def _fake_open(blob: bytes, entropy: bytes) -> bytes:
    """Stand-in for ``CryptUnprotectData``: a wrong entropy fails the UNPROTECT itself."""
    size = int.from_bytes(blob[:4], "big")
    if blob[4 : 4 + size] != entropy:
        # The shape the real API produces (see src/utils/dpapi.py) — an OSError that
        # names the call and the Windows error number and never echoes the payload.
        raise OSError("CryptUnprotectData failed (error 13).")
    return blob[4 + size :]


@pytest.fixture
def fake_dpapi(monkeypatch):
    """Swap the two DPAPI seams for the cross-platform stand-in described in the module docstring."""
    monkeypatch.setattr(secret_store, "protect_machine_blob", _fake_seal)
    monkeypatch.setattr(secret_store, "unprotect_machine_blob", _fake_open)


@pytest.fixture
def machine_scope(tmp_path, monkeypatch):
    """Put the process in MACHINE scope with the shared profile inside tmp.

    Drives the seams S-1a-i established — the HKLM switch, the machine dir and the trust
    predicate — so the REAL ``is_machine_scope()`` / ``_pinned()`` ladder resolves. Never
    the real ``C:\\ProgramData\\DistrictSync``.
    """
    shared = tmp_path / "ProgramData" / "DistrictSync"
    shared.mkdir(parents=True)
    monkeypatch.setattr(paths_module, "machine_data_dir", lambda: shared)
    monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: True)
    monkeypatch.setattr(paths_module, "_assert_machine_dir_trusted", lambda path: None)
    paths_module.reset_data_dir_pin()
    yield shared
    paths_module.reset_data_dir_pin()


@pytest.fixture
def keyring_spy(monkeypatch):
    """Record every keyring call (storage still goes to the suite-wide in-memory backend)."""
    calls: list[tuple[str, str, str]] = []
    real_set = keyring.set_password
    real_get = keyring.get_password

    def _set(service: str, username: str, password: str) -> None:
        calls.append(("set", service, username))
        real_set(service, username, password)

    def _get(service: str, username: str) -> str | None:
        calls.append(("get", service, username))
        return real_get(service, username)

    monkeypatch.setattr(keyring, "set_password", _set)
    monkeypatch.setattr(keyring, "get_password", _get)
    return calls


# --------------------------------------------------------------------------- #
# 1. The select rule — one store, never both, never a fallback chain           #
# --------------------------------------------------------------------------- #
class TestSelectRule:
    def test_machine_scope_selects_the_machine_store_and_never_touches_the_keyring(
        self, machine_scope, fake_dpapi, keyring_spy
    ):
        store = select_store()
        assert isinstance(store, MachineSecretStore)

        store.store_password(HOST, USER, SECRET)
        assert store.get_password(HOST, USER) == SECRET
        assert store.has_secret(HOST, USER) is True

        # The whole round trip happened without one keyring call.
        assert keyring_spy == []

    def test_per_user_selects_the_keyring_and_writes_nothing_under_the_machine_dir(
        self, tmp_path, monkeypatch, keyring_spy
    ):
        machine_dir = tmp_path / "ProgramData" / "DistrictSync"
        machine_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: machine_dir)
        # The autouse isolation fixture already forces the switch off; be explicit.
        monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: False)
        paths_module.reset_data_dir_pin()

        store = select_store()
        assert isinstance(store, UserSecretStore)

        store.store_password(HOST, USER, SECRET)
        assert store.get_password(HOST, USER) == SECRET
        assert store.has_secret(HOST, USER) is True

        assert [c[0] for c in keyring_spy] == ["set", "get", "get"]
        assert list(machine_dir.iterdir()) == []

    def test_legacy_profile_is_still_per_user(self, tmp_path, monkeypatch, keyring_spy):
        """A legacy ``~/.districtsync`` profile resolves through the SAME per-user branch.

        Scope is decided before the per-user ladder chooses platform-vs-legacy, so this
        case and the one above share a branch — asserted anyway, because "legacy installs
        keep the keyring" is a promise 20 installs depend on.
        """
        legacy = tmp_path / "legacy_home" / ".districtsync"
        legacy.mkdir(parents=True)
        machine_dir = tmp_path / "ProgramData" / "DistrictSync"
        machine_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: machine_dir)
        monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: False)
        monkeypatch.setattr(paths_module, "_user_scope_data_dir", lambda *, create: legacy)
        paths_module.reset_data_dir_pin()

        assert isinstance(select_store(), UserSecretStore)
        select_store().store_password(HOST, USER, SECRET)
        assert keyring_spy == [("set", KEYRING_SERVICE, USER)]
        assert list(machine_dir.iterdir()) == []

    def test_data_dir_override_is_always_the_keyring_even_with_the_switch_on(self, tmp_path, monkeypatch, keyring_spy):
        """The support/test seam is NEVER machine scope — guaranteed by the pin's ladder."""
        override = tmp_path / "override"
        override.mkdir()
        machine_dir = tmp_path / "ProgramData" / "DistrictSync"
        machine_dir.mkdir(parents=True)
        monkeypatch.setenv(paths_module._DATA_DIR_ENV_VAR, str(override))
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: machine_dir)
        monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: True)
        paths_module.reset_data_dir_pin()

        assert paths_module.is_machine_scope() is False
        assert isinstance(select_store(), UserSecretStore)

        select_store().store_password(HOST, USER, SECRET)
        assert keyring_spy == [("set", KEYRING_SERVICE, USER)]
        assert list(machine_dir.iterdir()) == []

    def test_the_user_store_ignores_host_because_the_shipped_key_is_username_only(self, keyring_spy):
        """Re-keying would invalidate 20 live installs' stored passwords — so ``host`` is inert."""
        store = UserSecretStore()
        store.store_password(HOST, USER, SECRET)

        assert store.get_password("sftp.us.spacesedu.com", USER) == SECRET
        assert keyring_spy == [
            ("set", KEYRING_SERVICE, USER),
            ("get", KEYRING_SERVICE, USER),
        ]


class TestTheUploaderRoutesThroughTheStore:
    """The select rule is worthless if the one caller keeps its own keyring call.

    Found by a falsification probe: replacing ``SFTPUploader.store_password``'s body with
    a direct ``keyring.set_password`` left every other test in this file GREEN. On a
    machine-scoped install that regression stores the password in the ADMIN's Credential
    Manager, where the nightly's service principal can never read it — the exact fault the
    slice exists to remove — so it is pinned here, by EFFECT rather than by call spying.
    """

    @staticmethod
    def _uploader():
        from src.sftp.uploader import SFTPUploader

        return SFTPUploader(HOST, 22, USER, "/upload")

    def test_machine_scope_writes_the_blob_and_never_the_keyring(self, machine_scope, fake_dpapi, keyring_spy):
        uploader = self._uploader()
        uploader.store_password(SECRET)

        assert (machine_scope / secret_store.SECRET_FILENAME).exists()
        assert uploader.get_stored_password() == SECRET
        assert keyring_spy == []

    def test_per_user_writes_the_keyring_and_never_the_machine_dir(self, tmp_path, monkeypatch, keyring_spy):
        machine_dir = tmp_path / "ProgramData" / "DistrictSync"
        machine_dir.mkdir(parents=True)
        monkeypatch.setattr(paths_module, "machine_data_dir", lambda: machine_dir)
        monkeypatch.setattr(paths_module, "_machine_switch_on", lambda: False)
        paths_module.reset_data_dir_pin()

        uploader = self._uploader()
        uploader.store_password(SECRET)

        assert keyring.get_password(KEYRING_SERVICE, USER) == SECRET
        assert uploader.get_stored_password() == SECRET
        assert list(machine_dir.iterdir()) == []

    def test_the_uploader_s_own_host_is_bound_into_the_machine_blob(self, machine_scope, fake_dpapi):
        """``self.host`` really reaches the entropy — not a hardcoded or empty host."""
        self._uploader().store_password(SECRET)

        with pytest.raises(OSError):
            MachineSecretStore().get_password("sftp.us.spacesedu.com", USER)
        assert MachineSecretStore().get_password(HOST, USER) == SECRET

    def test_an_unreadable_store_degrades_to_none_never_a_raise(self, machine_scope, fake_dpapi, monkeypatch, caplog):
        """The uploader's read path stays total — a delivery reports "no password", not a crash."""
        (machine_scope / secret_store.SECRET_FILENAME).write_bytes(b"not a DPAPI blob at all")
        with caplog.at_level("DEBUG"):
            assert self._uploader().get_stored_password() is None
        assert SECRET not in caplog.text


# --------------------------------------------------------------------------- #
# 2. Identity binding — in the ENTROPY, not a post-decrypt comparison          #
# --------------------------------------------------------------------------- #
class TestIdentityBinding:
    @pytest.mark.parametrize(
        ("other_host", "other_user"),
        [(HOST, "someone_else"), ("sftp.us.spacesedu.com", USER)],
        ids=["different-username", "different-host"],
    )
    def test_a_foreign_identity_fails_the_unprotect_not_the_payload_check(
        self, machine_scope, fake_dpapi, other_host, other_user
    ):
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)

        with pytest.raises(OSError) as excinfo:
            store.get_password(other_host, other_user)

        # The DISCRIMINATING assertion: the blob never opened, so no payload comparison
        # could have run. A SecretStoreError here would mean the password WAS
        # materialised and only then rejected.
        assert not isinstance(excinfo.value, SecretStoreError)
        assert "CryptUnprotectData" in str(excinfo.value)
        assert store.has_secret(other_host, other_user) is False

    def test_the_owning_identity_still_reads_it(self, machine_scope, fake_dpapi):
        """Positive twin — the refusals above are not a store that refuses everyone."""
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        assert store.get_password(HOST, USER) == SECRET

    def test_identity_is_exact_not_case_folded(self, machine_scope, fake_dpapi):
        """Matching the keyring's exact-key semantics — ``District_X`` is a different key."""
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        with pytest.raises(OSError):
            store.get_password(HOST, USER.upper())

    def test_surrounding_whitespace_is_stripped_on_both_halves(self, machine_scope, fake_dpapi):
        store = MachineSecretStore()
        store.store_password(f"  {HOST} ", f" {USER}  ", SECRET)
        assert store.get_password(HOST, USER) == SECRET

    def test_the_payload_check_still_catches_a_mismatched_body(self, machine_scope, fake_dpapi):
        """Belt-and-braces twin: a blob sealed with the RIGHT entropy but the wrong body.

        Unreachable through the store's own writer — which is the point of asserting it
        separately, so the post-decrypt comparison is not dead code nobody ever proved.
        """
        entropy = secret_store.identity_entropy(HOST, USER)
        forged = json.dumps({"host": HOST, "username": "someone_else", "password": SECRET})
        (machine_scope / secret_store.SECRET_FILENAME).write_bytes(
            secret_store.protect_machine_blob(forged.encode("utf-8"), entropy)
        )

        store = MachineSecretStore()
        with pytest.raises(SecretStoreError):
            store.get_password(HOST, USER)
        assert store.has_secret(HOST, USER) is False

    def test_the_entropy_is_namespaced_away_from_the_elevation_handshake(self):
        entropy = secret_store.identity_entropy(HOST, USER)
        assert entropy.startswith(b"DistrictSync/sftp-secret/v1|")
        assert b"DistrictSync/elevation/v1" not in entropy
        assert entropy == b"DistrictSync/sftp-secret/v1|" + HOST.encode() + b"|" + USER.encode()

    def test_the_machine_flags_are_local_machine_plus_ui_forbidden(self):
        assert secret_store.MACHINE_DPAPI_FLAGS == 0x5


# --------------------------------------------------------------------------- #
# 3. Verify BEFORE promote — a failed write never destroys a working secret    #
# --------------------------------------------------------------------------- #
class TestWriteAtomicity:
    def test_a_disagreeing_read_back_raises_and_leaves_the_live_blob_intact(
        self, machine_scope, fake_dpapi, monkeypatch
    ):
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        live = machine_scope / secret_store.SECRET_FILENAME
        committed = live.read_bytes()

        # Corrupt the read-back: every unprotect from here yields a DIFFERENT password.
        def _lying_open(blob: bytes, entropy: bytes) -> bytes:
            opened = json.loads(_fake_open(blob, entropy).decode("utf-8"))
            opened["password"] = "something-else-entirely"
            return json.dumps(opened).encode("utf-8")

        monkeypatch.setattr(secret_store, "unprotect_machine_blob", _lying_open)

        with pytest.raises(SecretStoreError):
            store.store_password(HOST, USER, "a-brand-new-password")

        assert live.read_bytes() == committed
        assert list(machine_scope.glob(f"{secret_store.SECRET_FILENAME}.*.tmp")) == []

    def test_the_previous_secret_still_reads_after_a_refused_write(self, machine_scope, fake_dpapi, monkeypatch):
        """Positive twin — "the bytes are unchanged" must also mean "it still works"."""
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)

        def _boom(blob: bytes, entropy: bytes) -> bytes:
            raise OSError("CryptUnprotectData failed (error 13).")

        monkeypatch.setattr(secret_store, "unprotect_machine_blob", _boom)
        with pytest.raises(OSError):
            store.store_password(HOST, USER, "a-brand-new-password")

        monkeypatch.setattr(secret_store, "unprotect_machine_blob", _fake_open)
        assert store.get_password(HOST, USER) == SECRET

    def test_a_failed_write_on_a_fresh_profile_leaves_no_file_at_all(self, machine_scope, fake_dpapi, monkeypatch):
        def _boom(blob: bytes, entropy: bytes) -> bytes:
            raise OSError("CryptUnprotectData failed (error 13).")

        monkeypatch.setattr(secret_store, "unprotect_machine_blob", _boom)
        with pytest.raises(OSError):
            MachineSecretStore().store_password(HOST, USER, SECRET)

        assert list(machine_scope.iterdir()) == []

    def test_a_stranded_tmp_is_swept_on_the_next_write(self, machine_scope, fake_dpapi):
        stranded = machine_scope / f"{secret_store.SECRET_FILENAME}.deadbeef.tmp"
        stranded.write_bytes(b"a sealed password nobody will ever promote")
        _age(stranded)

        MachineSecretStore().store_password(HOST, USER, SECRET)

        assert not stranded.exists()
        assert (machine_scope / secret_store.SECRET_FILENAME).exists()

    def test_the_sweep_leaves_another_writer_s_in_flight_tmp_alone(self, machine_scope, fake_dpapi):
        """A fresh tmp belongs to a write still in progress — deleting it would fail THAT writer."""
        in_flight = machine_scope / f"{secret_store.SECRET_FILENAME}.c0ffee.tmp"
        in_flight.write_bytes(b"in flight")

        MachineSecretStore().store_password(HOST, USER, SECRET)

        assert in_flight.exists()

    def test_the_sweep_leaves_the_live_blob_and_unrelated_files_alone(self, machine_scope, fake_dpapi):
        bystander = machine_scope / "config.json"
        bystander.write_text("{}", encoding="utf-8")
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        _age(machine_scope / secret_store.SECRET_FILENAME)
        _age(bystander)

        store.store_password(HOST, USER, "a-second-password")

        assert bystander.read_text(encoding="utf-8") == "{}"
        assert store.get_password(HOST, USER) == "a-second-password"

    def test_an_empty_password_is_refused_at_the_boundary(self, machine_scope, fake_dpapi):
        """Refused on the way IN, not discovered as a verify failure on the way out."""
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)

        with pytest.raises(SecretStoreError, match="empty"):
            store.store_password(HOST, USER, "")

        # And the refusal did not disturb what was already stored.
        assert store.get_password(HOST, USER) == SECRET
        assert list(machine_scope.glob(f"{secret_store.SECRET_FILENAME}.*.tmp")) == []

    def test_two_interleaved_writers_never_destroy_a_committed_secret(self, machine_scope, fake_dpapi, monkeypatch):
        """Writer A commits; writer B fails mid-flight while A's blob is live."""
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        live = machine_scope / secret_store.SECRET_FILENAME
        committed = live.read_bytes()

        # B's tmp is stranded (fresh, so A's sweep cannot touch it) and B then fails.
        b_tmp = machine_scope / f"{secret_store.SECRET_FILENAME}.b0b.tmp"
        b_tmp.write_bytes(b"writer B, still in flight")

        real_replace = os.replace

        def _replace_fails(src, dst, *args, **kwargs):
            raise PermissionError("the file is locked by another process")

        monkeypatch.setattr(secret_store.os, "replace", _replace_fails)
        with pytest.raises(PermissionError):
            store.store_password(HOST, USER, "writer-B-password")

        monkeypatch.setattr(secret_store.os, "replace", real_replace)
        assert live.read_bytes() == committed
        assert store.get_password(HOST, USER) == SECRET

    def test_each_write_uses_a_unique_tmp_name(self, machine_scope, fake_dpapi, monkeypatch):
        seen: list[str] = []
        real_replace = os.replace

        def _spy(src, dst, *args, **kwargs):
            seen.append(Path(src).name)
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(secret_store.os, "replace", _spy)
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        store.store_password(HOST, USER, SECRET)

        assert len(set(seen)) == 2
        assert all(name.startswith(f"{secret_store.SECRET_FILENAME}.") and name.endswith(".tmp") for name in seen)


# --------------------------------------------------------------------------- #
# 4. ``has_secret`` is TOTAL                                                   #
# --------------------------------------------------------------------------- #
class TestHasSecretIsTotal:
    def test_absent_file_is_false_and_silent(self, machine_scope, fake_dpapi, caplog):
        with caplog.at_level("WARNING"):
            assert MachineSecretStore().has_secret(HOST, USER) is False
        assert caplog.text == ""

    def test_a_true_answer_proves_the_false_ones_are_not_vacuous(self, machine_scope, fake_dpapi):
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        assert store.has_secret(HOST, USER) is True

    def test_an_unreadable_blob_warns_once_and_returns_false(self, machine_scope, fake_dpapi, caplog):
        (machine_scope / secret_store.SECRET_FILENAME).write_bytes(b"not a DPAPI blob at all")
        with caplog.at_level("WARNING"):
            assert MachineSecretStore().has_secret(HOST, USER) is False
        assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1

    def test_a_raising_filesystem_is_false_never_an_exception(self, machine_scope, fake_dpapi, monkeypatch, caplog):
        def _boom(self, *args, **kwargs):
            raise PermissionError("access is denied")

        monkeypatch.setattr(Path, "read_bytes", _boom)
        with caplog.at_level("WARNING"):
            assert MachineSecretStore().has_secret(HOST, USER) is False

    def test_the_user_store_is_total_too(self, monkeypatch, caplog):
        def _boom(service, username):
            raise RuntimeError("no keyring backend")

        monkeypatch.setattr(keyring, "get_password", _boom)
        with caplog.at_level("WARNING"):
            assert UserSecretStore().has_secret(HOST, USER) is False

    def test_the_user_store_answers_true_when_a_password_is_stored(self):
        UserSecretStore().store_password(HOST, USER, SECRET)
        assert UserSecretStore().has_secret(HOST, USER) is True

    def test_an_empty_stored_string_is_not_a_secret(self):
        keyring.set_password(KEYRING_SERVICE, USER, "")
        assert UserSecretStore().has_secret(HOST, USER) is False


# --------------------------------------------------------------------------- #
# 5. Non-leak — the value reaches no log, exception or repr                    #
# --------------------------------------------------------------------------- #
class TestNonLeak:
    def test_a_successful_store_and_read_log_nothing(self, machine_scope, fake_dpapi, caplog):
        with caplog.at_level("DEBUG"):
            store = MachineSecretStore()
            store.store_password(HOST, USER, SECRET)
            assert store.get_password(HOST, USER) == SECRET
            assert store.has_secret(HOST, USER) is True
        assert SECRET not in caplog.text

    def test_a_verify_failure_names_the_fault_never_the_value(self, machine_scope, fake_dpapi, monkeypatch, caplog):
        def _lying_open(blob: bytes, entropy: bytes) -> bytes:
            opened = json.loads(_fake_open(blob, entropy).decode("utf-8"))
            opened["password"] = "something-else-entirely"
            return json.dumps(opened).encode("utf-8")

        monkeypatch.setattr(secret_store, "unprotect_machine_blob", _lying_open)
        with caplog.at_level("DEBUG"), pytest.raises(SecretStoreError) as excinfo:
            MachineSecretStore().store_password(HOST, USER, SECRET)

        assert SECRET not in str(excinfo.value)
        assert SECRET not in repr(excinfo.value)
        assert SECRET not in caplog.text

    def test_the_sweep_logs_no_value(self, machine_scope, fake_dpapi, caplog):
        stranded = machine_scope / f"{secret_store.SECRET_FILENAME}.deadbeef.tmp"
        stranded.write_bytes(SECRET.encode("utf-8"))
        _age(stranded)
        with caplog.at_level("DEBUG"):
            MachineSecretStore().store_password(HOST, USER, SECRET)
        assert SECRET not in caplog.text

    def test_a_has_secret_failure_logs_no_value(self, machine_scope, fake_dpapi, caplog):
        (machine_scope / secret_store.SECRET_FILENAME).write_bytes(b"not a DPAPI blob at all")
        with caplog.at_level("DEBUG"):
            assert MachineSecretStore().has_secret(HOST, USER) is False
        assert SECRET not in caplog.text

    @pytest.mark.parametrize("factory", [UserSecretStore, MachineSecretStore], ids=["user", "machine"])
    def test_no_store_repr_can_carry_a_value(self, machine_scope, fake_dpapi, factory):
        store = factory()
        store.store_password(HOST, USER, SECRET)
        assert SECRET not in repr(store)
        assert SECRET not in str(store)

    def test_the_leak_check_would_actually_catch_one(self, caplog):
        """Falsification twin — prove the substring assertions above are not vacuous."""
        with caplog.at_level("DEBUG"):
            secret_store.logger.warning("a careless line carrying %s", SECRET)
        assert SECRET in caplog.text


# --------------------------------------------------------------------------- #
# 6. ``AppConfig.sftp_is_configured`` — one conjunct, machine scope only       #
# --------------------------------------------------------------------------- #
def _configured() -> AppConfig:
    cfg = AppConfig()
    cfg.sftp_enabled = True
    cfg.sftp_host = HOST
    cfg.sftp_username = USER
    cfg.sftp_remote_path = "/upload"
    return cfg


class TestSftpIsConfigured:
    def test_per_user_with_no_keyring_entry_is_still_true(self):
        """Today's behaviour, deliberately unchanged — 20 installs answer this way."""
        assert _configured().sftp_is_configured() is True

    def test_machine_scope_without_a_secret_is_false(self, machine_scope, fake_dpapi):
        assert _configured().sftp_is_configured() is False

    def test_machine_scope_with_a_secret_is_true(self, machine_scope, fake_dpapi):
        MachineSecretStore().store_password(HOST, USER, SECRET)
        assert _configured().sftp_is_configured() is True

    def test_a_raising_store_is_false_never_a_propagated_exception(self, machine_scope, monkeypatch, caplog):
        def _boom():
            raise RuntimeError("the store could not be selected")

        monkeypatch.setattr(secret_store, "select_store", _boom)
        with caplog.at_level("WARNING"):
            assert _configured().sftp_is_configured() is False

    def test_the_other_conjuncts_still_gate_first(self, machine_scope, fake_dpapi):
        MachineSecretStore().store_password(HOST, USER, SECRET)
        cfg = _configured()
        cfg.sftp_enabled = False
        assert cfg.sftp_is_configured() is False

        cfg = _configured()
        cfg.sftp_host = "sftp.example.invalid"
        assert cfg.sftp_is_configured() is False


# --------------------------------------------------------------------------- #
# 7. The real API — Windows only                                               #
# --------------------------------------------------------------------------- #
@WINDOWS_ONLY
class TestRealLocalMachineRoundTrip:
    def test_store_then_read_through_real_dpapi(self, machine_scope):
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        assert store.get_password(HOST, USER) == SECRET
        assert store.has_secret(HOST, USER) is True

    def test_the_blob_on_disk_carries_no_plaintext(self, machine_scope):
        """The one assertion the cross-platform stand-in cannot make: it is really sealed."""
        MachineSecretStore().store_password(HOST, USER, SECRET)
        blob = (machine_scope / secret_store.SECRET_FILENAME).read_bytes()
        assert SECRET.encode("utf-8") not in blob
        assert SECRET.encode("utf-16-le") not in blob

    def test_a_foreign_identity_cannot_open_a_real_blob(self, machine_scope):
        store = MachineSecretStore()
        store.store_password(HOST, USER, SECRET)
        with pytest.raises(OSError) as excinfo:
            store.get_password(HOST, "someone_else")
        assert not isinstance(excinfo.value, SecretStoreError)


def _age(path: Path, seconds: float = 3600.0) -> None:
    """Backdate a file so the store's stale-tmp sweep considers it abandoned."""
    stamp = path.stat().st_mtime - seconds
    os.utime(path, (stamp, stamp))
