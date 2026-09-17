"""Tests for ``src/utils/dpapi.py`` — the shared Win32 DPAPI primitive.

Two layers, deliberately:

1. **A flags SPY (every OS).** ``dpapi_call`` takes ``flags`` as a REQUIRED keyword
   because the protection scope is the one thing a caller may choose, and a defaulted
   scope is the permissive safety default ``CLAUDE.md`` bans. A round trip alone passes
   with the WRONG flags — CurrentUser and LocalMachine both round-trip for the process
   that sealed the blob — so the only assertion that can prove the scope is the exact
   ``dwFlags`` word reaching the API. The spy drives ``ctypes`` through monkeypatched
   seams, so it runs on Linux CI as well as Windows.
2. **Real round trips (Windows only).** The existing CurrentUser pair lives in
   ``tests/test_scheduler_elevation.py``; this file adds the LocalMachine one that
   ``src/sftp/secret_store.py`` (S-1a-ii) will depend on, plus the entropy binding.

``elevation._dpapi`` keeps its name and signature and delegates here with
``flags=CRYPTPROTECT_UI_FORBIDDEN`` — NOT ``0``. Without that flag DPAPI may raise a
modal prompt inside the ``SW_HIDE`` elevated child or the non-interactive nightly,
turning a clean ``OSError`` into a bounded-wait hang. That is the reason the spy asserts
elevation's flag word literally.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from src.scheduler import elevation
from src.utils import dpapi

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows-only API")


# ---------------------------------------------------------------------------
# ctypes seams — a fake crypt32/kernel32 that records the flags word.
# ---------------------------------------------------------------------------


class _FakeFunc:
    """Stands in for ``crypt32.CryptProtectData`` — records ``dwFlags`` (argument 6)."""

    def __init__(self, spy: _WinDllSpy, name: str) -> None:
        self._spy = spy
        self._name = name
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> bool:
        if self._name.startswith("Crypt"):  # LocalFree rides the same fake library
            self._spy.calls.append((self._name, args[5].value))  # type: ignore[attr-defined]
            return self._spy.succeed
        return True


class _FakeLib:
    def __init__(self, spy: _WinDllSpy) -> None:
        self._spy = spy

    def __getattr__(self, name: str) -> _FakeFunc:
        return _FakeFunc(self._spy, name)


class _WinDllSpy:
    """Stands in for ``ctypes.WinDLL`` (which does not exist off Windows)."""

    def __init__(self, succeed: bool = True) -> None:
        self.calls: list[tuple[str, int]] = []
        self.succeed = succeed

    def __call__(self, name: str, use_last_error: bool = False) -> _FakeLib:
        return _FakeLib(self)

    @property
    def flags(self) -> list[int]:
        return [flags for _name, flags in self.calls]


@pytest.fixture
def dpapi_spy(monkeypatch) -> _WinDllSpy:
    spy = _WinDllSpy()
    monkeypatch.setattr(ctypes, "WinDLL", spy, raising=False)
    monkeypatch.setattr(ctypes, "string_at", lambda ptr, size: b"unsealed-bytes", raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 13, raising=False)
    return spy


class TestFlagsAreExactAndRequired:
    def test_flags_keyword_is_required_and_undefaulted(self):
        with pytest.raises(TypeError):
            dpapi.dpapi_call("CryptProtectData", b"x", b"e")  # type: ignore[call-arg]

    def test_the_exact_flags_word_reaches_the_api(self, dpapi_spy):
        out = dpapi.dpapi_call("CryptProtectData", b"payload", b"entropy", flags=0x5)
        assert out == b"unsealed-bytes"
        assert dpapi_spy.calls == [("CryptProtectData", 0x5)]

    def test_the_function_name_selects_the_api(self, dpapi_spy):
        dpapi.dpapi_call("CryptUnprotectData", b"payload", b"entropy", flags=0x1)
        assert dpapi_spy.calls == [("CryptUnprotectData", 0x1)]

    def test_constants_are_the_documented_words(self):
        assert dpapi.CRYPTPROTECT_UI_FORBIDDEN == 0x1
        assert dpapi.CRYPTPROTECT_LOCAL_MACHINE == 0x4

    def test_elevation_seals_with_ui_forbidden_never_zero(self, dpapi_spy):
        # The load-bearing one: elevation's delegation must not quietly become flags=0.
        elevation.protect_blob(b"a-secret")
        assert dpapi_spy.flags == [0x1]
        assert dpapi_spy.flags == [dpapi.CRYPTPROTECT_UI_FORBIDDEN]

    def test_elevation_unseals_with_ui_forbidden(self, dpapi_spy):
        elevation.unprotect_blob(b"a-sealed-blob")
        assert dpapi_spy.flags == [dpapi.CRYPTPROTECT_UI_FORBIDDEN]

    def test_elevation_never_asks_for_local_machine_scope(self, dpapi_spy):
        # The 2026-06-25 / D5 decision: the handshake blob stays CurrentUser-bound.
        elevation.protect_blob(b"a-secret")
        assert all(not (flags & dpapi.CRYPTPROTECT_LOCAL_MACHINE) for flags in dpapi_spy.flags)

    def test_api_failure_raises_oserror_naming_the_call_and_no_payload(self, monkeypatch):
        spy = _WinDllSpy(succeed=False)
        monkeypatch.setattr(ctypes, "WinDLL", spy, raising=False)
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 13, raising=False)
        with pytest.raises(OSError) as excinfo:
            dpapi.dpapi_call("CryptProtectData", b"hunter2-plaintext", b"entropy", flags=0x1)
        message = str(excinfo.value)
        assert "CryptProtectData" in message
        assert "13" in message
        assert "hunter2-plaintext" not in message  # never echo what was being sealed


class TestRealRoundTrips:
    @WINDOWS_ONLY
    def test_local_machine_round_trip(self):
        # The scope S-1a-ii's machine secret store uses: any account on THIS computer can
        # unseal it, which is the whole point (a gMSA cannot seed its own keyring).
        flags = dpapi.CRYPTPROTECT_LOCAL_MACHINE | dpapi.CRYPTPROTECT_UI_FORBIDDEN
        secret = b'{"password":"s3cr3t"}'
        entropy = b"DistrictSync/test/v1"
        blob = dpapi.dpapi_call("CryptProtectData", secret, entropy, flags=flags)
        assert blob != secret
        assert b"s3cr3t" not in blob
        assert dpapi.dpapi_call("CryptUnprotectData", blob, entropy, flags=flags) == secret

    @WINDOWS_ONLY
    def test_local_machine_blob_is_entropy_bound(self):
        flags = dpapi.CRYPTPROTECT_LOCAL_MACHINE | dpapi.CRYPTPROTECT_UI_FORBIDDEN
        blob = dpapi.dpapi_call("CryptProtectData", b"payload", b"entropy-a", flags=flags)
        with pytest.raises(OSError):
            dpapi.dpapi_call("CryptUnprotectData", blob, b"entropy-b", flags=flags)

    @WINDOWS_ONLY
    def test_current_user_round_trip_still_works_through_the_shared_helper(self):
        blob = dpapi.dpapi_call("CryptProtectData", b"payload", b"entropy", flags=0x1)
        assert dpapi.dpapi_call("CryptUnprotectData", blob, b"entropy", flags=0x1) == b"payload"
