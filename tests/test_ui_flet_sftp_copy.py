"""Tests for src/ui_flet/sftp_copy.py — the pure SFTP Test-connection trust copy.

Slice 7 (D6): the Test-connection success message must name WHAT it verified —
which host, as which user, with which credential source — and must NEVER assert
the nightly sync can deliver for settings that aren't saved yet. That trust
decision is extracted into a COUNTED pure helper so the copy is unit-tested here,
not buried in coverage-omitted view glue.

The two pure helpers under test:
  - ``sftp_test_copy(provenance, unsaved_edits, host, username)`` — the success copy.
  - ``sftp_form_differs_from_saved(cfg, ...)`` — the unsaved-edits predicate.
"""

from __future__ import annotations

import pytest

from src.config.app_config import AppConfig
from src.ui_flet.sftp_copy import sftp_form_differs_from_saved, sftp_test_copy

_HOST = "sftp.ca.spacesedu.com"
_USER = "district_x"

# The over-claim this slice exists to kill — a success message must NEVER promise
# the nightly sync can deliver (that is a future outcome the test never verified).
_KILLED_OVERCLAIM = "the nightly sync can deliver"

# The fixed listing-denied note appended when auth worked but the account can't list the
# remote folder (upload-only accounts). Mirrors the tail of ``uploader.LISTING_DENIED_NOTE``.
_LISTING_NOTE = "This account can't list the remote folder — that's normal for upload-only delivery accounts."


class TestSftpTestCopy:
    """The success-copy truth table: provenance {stored, typed} x unsaved {False, True}."""

    def _copy(self, *, provenance: str, unsaved_edits: bool) -> tuple[str, str]:
        return sftp_test_copy(
            provenance=provenance,
            unsaved_edits=unsaved_edits,
            host=_HOST,
            username=_USER,
        )

    @pytest.mark.parametrize("provenance", ["stored", "typed"])
    @pytest.mark.parametrize("unsaved_edits", [False, True])
    def test_always_names_host_and_user(self, provenance, unsaved_edits):
        headline, detail = self._copy(provenance=provenance, unsaved_edits=unsaved_edits)
        assert headline == "SFTP connection succeeded"
        # Every success names WHAT it checked: which host, as which user.
        assert _HOST in detail
        assert _USER in detail

    @pytest.mark.parametrize("provenance", ["stored", "typed"])
    @pytest.mark.parametrize("unsaved_edits", [False, True])
    def test_never_promises_nightly_delivery(self, provenance, unsaved_edits):
        _headline, detail = self._copy(provenance=provenance, unsaved_edits=unsaved_edits)
        assert _KILLED_OVERCLAIM not in detail

    def test_stored_saved_names_credential_manager_no_save_prompt(self):
        # The strongest truthful state: stored credential + saved settings = exactly
        # what the nightly uses. Name the credential source; no "Save" prompt needed.
        _headline, detail = self._copy(provenance="stored", unsaved_edits=False)
        assert "saved in this computer's credential manager" in detail
        assert "you just entered" not in detail
        assert "These settings work" not in detail

    def test_typed_saved_prompts_save_to_keep_it(self):
        # A typed password isn't persisted until Save, so it must prompt "Save to keep it"
        # even when host/user/port/remote already match the saved config.
        _headline, detail = self._copy(provenance="typed", unsaved_edits=False)
        assert "you just entered" in detail
        assert "click Save to keep it" in detail
        assert "These settings work" not in detail

    def test_stored_unsaved_softens_with_settings_note(self):
        # Stored credential but settings differ from saved → never claim the nightly
        # delivers; soften to the present-tense "these settings work — Save to use them".
        _headline, detail = self._copy(provenance="stored", unsaved_edits=True)
        assert "saved in this computer's credential manager" in detail
        assert "These settings work — click Save to use them for the nightly sync." in detail

    def test_typed_unsaved_prompts_both_save_actions(self):
        _headline, detail = self._copy(provenance="typed", unsaved_edits=True)
        assert "you just entered" in detail
        assert "These settings work — click Save to use them for the nightly sync." in detail


class TestSftpTestCopyListingDenied:
    """``listing_denied=True`` appends a fixed note (auth worked; listing is denied on
    upload-only accounts). Default False must leave the copy byte-identical."""

    @pytest.mark.parametrize("provenance", ["stored", "typed"])
    def test_note_appended_for_both_provenances(self, provenance):
        _headline, detail = sftp_test_copy(
            provenance=provenance,
            unsaved_edits=False,
            host=_HOST,
            username=_USER,
            listing_denied=True,
        )
        assert _LISTING_NOTE in detail
        # It still never over-claims the nightly sync.
        assert _KILLED_OVERCLAIM not in detail

    def test_composes_after_unsaved_tail(self):
        # Both softeners present, and the listing note comes LAST (after the unsaved tail).
        _headline, detail = sftp_test_copy(
            provenance="stored",
            unsaved_edits=True,
            host=_HOST,
            username=_USER,
            listing_denied=True,
        )
        assert "These settings work — click Save to use them for the nightly sync." in detail
        assert _LISTING_NOTE in detail
        assert detail.index(_LISTING_NOTE) > detail.index("These settings work")

    @pytest.mark.parametrize("provenance", ["stored", "typed"])
    @pytest.mark.parametrize("unsaved_edits", [False, True])
    def test_default_false_is_byte_identical(self, provenance, unsaved_edits):
        # Regression: not passing listing_denied (default False) yields the exact prior copy.
        without = sftp_test_copy(provenance=provenance, unsaved_edits=unsaved_edits, host=_HOST, username=_USER)
        explicit_false = sftp_test_copy(
            provenance=provenance,
            unsaved_edits=unsaved_edits,
            host=_HOST,
            username=_USER,
            listing_denied=False,
        )
        assert without == explicit_false
        assert _LISTING_NOTE not in without[1]


class TestSftpFormDiffersFromSaved:
    """The unsaved-edits predicate: form values vs the persisted AppConfig SFTP fields."""

    def _saved_cfg(self) -> AppConfig:
        return AppConfig(
            sftp_enabled=True,
            sftp_host=_HOST,
            sftp_port=22,
            sftp_username=_USER,
            sftp_remote_path="/files",
        )

    def _differs(self, cfg: AppConfig, **overrides) -> bool:
        fields = {"host": _HOST, "username": _USER, "remote_path": "/files", "port": "22"}
        fields.update(overrides)
        return sftp_form_differs_from_saved(cfg, **fields)

    def test_form_matches_saved_is_not_unsaved(self):
        assert self._differs(self._saved_cfg()) is False

    def test_port_string_vs_int_normalizes_no_diff(self):
        # Form port arrives as a string ("22"); saved is int 22 — same value, not a diff.
        assert self._differs(self._saved_cfg(), port="22") is False

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("host", "sftp.app.spacesedu.com"),
            ("username", "district_y"),
            ("remote_path", "/other"),
            ("port", "2222"),
        ],
    )
    def test_any_field_differing_is_unsaved(self, field, value):
        assert self._differs(self._saved_cfg(), **{field: value}) is True

    def test_whitespace_only_edits_are_normalized(self):
        # A trailing space in the form doesn't count as a real edit.
        assert self._differs(self._saved_cfg(), username=f"{_USER} ") is False

    def test_empty_saved_config_makes_any_settings_unsaved(self):
        # First-time setup: nothing saved yet → the entered settings are unsaved.
        assert self._differs(AppConfig()) is True

    def test_unparseable_port_counts_as_unsaved(self):
        # Defensive: a non-numeric port can never equal the saved int → treat as a diff
        # (softer copy), never raise.
        assert self._differs(self._saved_cfg(), port="not-a-port") is True
