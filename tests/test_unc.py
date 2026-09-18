"""Tests for src/utils/unc.py — mapped-drive resolution and the folder-reach heuristic.

Plan 0049 S-2b.1. Two halves, tested on every OS:

* the PURE rule (:func:`~src.utils.unc.reach_unconfirmed`) — a truth table, including both
  directions the heuristic is knowingly wrong in, which is WHY it warns instead of gating;
* the TOTALITY of the ctypes seam — every failure shape must land on ``""`` so the caller
  simply does not offer a replacement. The Linux failure is reproduced ON WINDOWS by
  removing ``ctypes.WinDLL`` (an ``AttributeError``, outside ``OSError`` — the exact shape
  that reddened CI's Linux leg on this plan, PR #136).

Nothing here touches a real network share or a real drive mapping.
"""

from __future__ import annotations

import ctypes

import pytest

from src.utils import unc


class TestReachUnconfirmed:
    """The pure rule. ``True`` = "we cannot CONFIRM another account could reach this"."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            # Under a user profile -> we cannot confirm.
            ("C:\\Users\\ted\\Documents\\GDE", True),
            ("C:\\Users\\ted", True),
            ("C:\\Users", True),  # the container itself is nobody else's to read
            ("D:\\Users\\ted\\Exports", True),  # any drive, not just C:
            ("c:/users/ted/exports", True),  # case + forward slashes
            # The exemption: Public is readable by every account on the computer BY DESIGN.
            ("C:\\Users\\Public", False),
            ("C:\\Users\\Public\\DistrictSync", False),
            ("c:\\users\\public\\districtsync", False),
            # An ordinary local folder: nothing against it (not "we checked it").
            ("D:\\Exports", False),
            ("C:\\DistrictSync\\input", False),
            # A UNC path passes CLEANLY — the acknowledged false negative.
            ("\\\\server\\share\\Exports", False),
            ("\\\\server\\share\\Users\\ted", False),  # "server" must not read as a drive
            # Nothing chosen: the folders gate owns that sentence, not this one.
            ("", False),
            ("   ", False),
        ],
    )
    def test_truth_table(self, path: str, expected: bool) -> None:
        assert unc.reach_unconfirmed(path, mapped=False) is expected

    @pytest.mark.parametrize("path", ["Z:\\Exports", "C:\\Users\\Public\\ok", "\\\\server\\share"])
    def test_a_mapped_drive_always_closes_it(self, path: str) -> None:
        """A drive letter is per-logon-session, so the scheduled task's session has no such
        path at all — the one arm of this rule that is not really a heuristic."""
        assert unc.reach_unconfirmed(path, mapped=True) is True

    def test_a_blank_path_is_never_warned_about_even_when_mapped(self) -> None:
        assert unc.reach_unconfirmed("", mapped=True) is False

    def test_mapped_is_required_and_keyword_only(self) -> None:
        # No permissive default on the half of the rule that needs a syscall (CLAUDE.md).
        with pytest.raises(TypeError):
            unc.reach_unconfirmed("C:\\Exports")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            unc.reach_unconfirmed("C:\\Exports", True)  # type: ignore[misc]

    def test_none_does_not_raise(self) -> None:
        assert unc.reach_unconfirmed(None, mapped=False) is False  # type: ignore[arg-type]


class TestUncTarget:
    """The seam's totality. Every failure shape -> ``""``; the caller offers nothing."""

    def test_it_returns_what_windows_answered(self, monkeypatch) -> None:
        """The POSITIVE twin for every "-> empty" assertion below: the path works at all."""
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: "  \\\\server\\share\\Exports  ")
        assert unc.unc_target("Z:\\Exports") == "\\\\server\\share\\Exports"

    def test_a_blank_path_never_reaches_the_syscall(self, monkeypatch) -> None:
        calls: list[str] = []
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: calls.append(path) or "x")
        assert unc.unc_target("   ") == ""
        assert calls == []

    def test_a_non_mapped_drive_is_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: "")
        assert unc.unc_target("C:\\Exports") == ""

    @pytest.mark.parametrize(
        "boom",
        [
            OSError("mpr.dll is not there"),
            ValueError("a ctypes signature surprise"),
            TypeError("another one"),
            RuntimeError("something else entirely"),
        ],
    )
    def test_every_failure_shape_fails_open(self, monkeypatch, boom: Exception) -> None:
        def _raise(path: str) -> str:
            raise boom

        monkeypatch.setattr(unc, "_read_universal_name", _raise)
        assert unc.unc_target("Z:\\Exports") == ""

    def test_the_linux_shape_reproduced_on_windows(self, monkeypatch) -> None:
        """``ctypes.WinDLL`` does not exist off Windows — an ``AttributeError``, NOT an
        ``OSError``. Removing the attribute drives the REAL seam down the exact path CI's
        ubuntu leg takes, which patching ``sys.platform`` alone would not (that reads the
        real API and passes for the wrong reason). Restoring the narrow ``except OSError``
        in ``unc_target`` turns this red — the non-vacuousness proof for the broad clause.
        """
        monkeypatch.delattr(ctypes, "WinDLL", raising=False)
        assert unc.unc_target("Z:\\Exports") == ""

    def test_it_never_raises_for_any_input(self, monkeypatch) -> None:
        monkeypatch.delattr(ctypes, "WinDLL", raising=False)
        for probe in ("", "   ", "Z:", "Z:\\", "\\\\server\\share", "not a path at all"):
            assert unc.unc_target(probe) == ""


class TestDescribeFolderReach:
    """The ONE call the confirm makes: one syscall, then the pure rule."""

    def test_a_mapped_drive_is_unconfirmed_and_carries_its_replacement(self, monkeypatch) -> None:
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: "\\\\server\\share\\Exports")
        reach = unc.describe_folder_reach("Z:\\Exports")
        assert reach == unc.FolderReach(
            path="Z:\\Exports", unconfirmed=True, unc_replacement="\\\\server\\share\\Exports"
        )

    def test_a_public_folder_is_confirmed_and_offers_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: "")
        reach = unc.describe_folder_reach("C:\\Users\\Public\\DistrictSync")
        assert reach.unconfirmed is False
        assert reach.unc_replacement == ""

    def test_a_profile_folder_is_unconfirmed_with_nothing_to_offer(self, monkeypatch) -> None:
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: "")
        reach = unc.describe_folder_reach("C:\\Users\\ted\\GDE")
        assert reach.unconfirmed is True
        assert reach.unc_replacement == ""

    def test_a_failed_resolution_degrades_to_the_pure_rule(self, monkeypatch) -> None:
        """Fail-open: no replacement is offered, and the profile test still answers."""

        def _raise(path: str) -> str:
            raise OSError("mpr said no")

        monkeypatch.setattr(unc, "_read_universal_name", _raise)
        assert unc.describe_folder_reach("C:\\Users\\ted\\GDE") == unc.FolderReach(
            path="C:\\Users\\ted\\GDE", unconfirmed=True, unc_replacement=""
        )
        assert unc.describe_folder_reach("D:\\Exports").unconfirmed is False

    def test_a_replacement_is_only_ever_one_windows_gave_us(self, monkeypatch) -> None:
        """The button that offers "use this instead" renders on a non-empty string, so an
        invented replacement would be a path the admin is told to trust."""
        monkeypatch.setattr(unc, "_read_universal_name", lambda path: "   ")
        assert unc.describe_folder_reach("Z:\\Exports").unc_replacement == ""
