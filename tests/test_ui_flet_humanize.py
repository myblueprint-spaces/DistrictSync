"""Unit tests for the pure ``humanize.friendly_district_name`` helper (DS-2, COUNTED).

Pins the TOTAL contract of the sole IA-2 humanization helper: a trust surface must
never show a raw config id when a friendly ``district_name`` exists, and must never
crash / go blank on a bad, empty, or unknown config. The synthetic-``config_dir``
fixtures (empty ``district_name`` + malformed YAML) exercise branches no bundled
config can reach — the ``iff non-empty`` clause and the ``except → raw id`` path.

Uses the ``config_dir`` seam (passed straight through to ``loader.load_config``,
whose own ``config_dir`` arg overrides the ``~/.districtsync`` search dirs), so these
are hermetic — no home dependency, no real-config coupling for the fixture branches.
"""

from __future__ import annotations

from datetime import date as _date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.ui_flet.humanize import (
    AnomalyVariant,
    friendly_anomaly_detail,
    friendly_date_short,
    friendly_district_name,
    friendly_sftp_reason,
    friendly_timestamp,
    pluralize,
)


class TestFriendlyDateShort:
    """The seasonal-resume date copy (B): plain "Aug 11", PII-free, no year, un-padded day."""

    def test_abbreviated_month_and_unpadded_day(self) -> None:
        assert friendly_date_short(_date(2026, 8, 11)) == "Aug 11"

    def test_single_digit_day_has_no_leading_zero(self) -> None:
        assert friendly_date_short(_date(2026, 7, 6)) == "Jul 6"

    def test_no_year_is_rendered(self) -> None:
        # The seasonal window recurs every year — a year would misinform, so it is never shown.
        assert "2026" not in friendly_date_short(_date(2026, 1, 1))

    def test_leap_day_is_total(self) -> None:
        assert friendly_date_short(_date(2028, 2, 29)) == "Feb 29"


# The real bundled configs — used only for the "known district" and "unknown id"
# cases (their district_name is asserted structurally, not by hardcoded string).
BUNDLED_MAPPINGS = Path(__file__).resolve().parent.parent / "config" / "mappings"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# A minimal standalone (no `_base`) valid MappingConfig — version + sis + one entity.
_MINIMAL_VALID = """\
version: "1.0"
sis: MyEducationBC
district_name: "{name}"
mappings:
  Students:
    source_files:
      student_demographic: StudentDemographicInformation.txt
    field_map: {{}}
"""


class TestFriendlyDistrictName:
    def test_known_district_returns_friendly_name_not_id(self) -> None:
        # A real bundled config carries a non-empty district_name; the helper must
        # return the human name, never the raw id. Asserted structurally (robust to
        # config-copy edits) — the product invariant is "not the id, and non-empty".
        result = friendly_district_name("sd40myedbc", config_dir=BUNDLED_MAPPINGS)
        assert result and result != "sd40myedbc"

    def test_empty_district_name_falls_back_to_raw_id(self, tmp_path: Path) -> None:
        # REQUIRED synthetic fixture: no bundled config has an empty district_name,
        # so the `iff non-empty` clause is unreachable without this.
        _write(tmp_path / "faux_mapping.yaml", _MINIMAL_VALID.format(name=""))
        assert friendly_district_name("faux", config_dir=tmp_path) == "faux"

    def test_whitespace_district_name_falls_back_to_raw_id(self, tmp_path: Path) -> None:
        # A whitespace-only district_name strips to empty → raw id (same clause).
        _write(tmp_path / "faux_mapping.yaml", _MINIMAL_VALID.format(name="   "))
        assert friendly_district_name("faux", config_dir=tmp_path) == "faux"

    def test_broken_yaml_falls_back_to_raw_id(self, tmp_path: Path) -> None:
        # REQUIRED synthetic fixture: malformed YAML → the `except` path is genuinely
        # hit (falls back to the raw id, never raises, never blanks).
        _write(tmp_path / "broken_mapping.yaml", "district_name: [unterminated\n  : : :")
        assert friendly_district_name("broken", config_dir=tmp_path) == "broken"

    def test_unknown_id_returns_raw_id(self, tmp_path: Path) -> None:
        # No mapping file for the id → FileNotFoundError caught → input unchanged.
        assert friendly_district_name("no_such_district", config_dir=tmp_path) == "no_such_district"

    def test_empty_string_returns_empty(self) -> None:
        assert friendly_district_name("") == ""

    def test_whitespace_input_returns_empty(self) -> None:
        assert friendly_district_name("   ") == ""

    def test_known_district_never_surfaces_the_raw_id(self) -> None:
        # Product invariant across every bundled config: when a friendly name exists
        # (all 9 bundled configs carry one), the helper never returns the raw id.
        for sis_id in ("sd40myedbc", "sd48myedbc", "sd51myedbc", "sd60myedbc", "sd74myedbc"):
            result = friendly_district_name(sis_id, config_dir=BUNDLED_MAPPINGS)
            assert result and result != sis_id


class TestFriendlyTimestamp:
    _NOW = datetime(2026, 7, 4, 12, 0, 0)

    def _iso(self, **delta: float) -> str:
        return (self._NOW - timedelta(**delta)).isoformat(timespec="seconds")

    def test_just_now(self) -> None:
        assert friendly_timestamp(self._iso(seconds=20), now=self._NOW) == "just now"

    def test_minutes_ago_plural(self) -> None:
        assert friendly_timestamp(self._iso(minutes=15), now=self._NOW) == "15 minutes ago"

    def test_a_minute_ago_singular(self) -> None:
        assert friendly_timestamp(self._iso(minutes=1), now=self._NOW) == "a minute ago"

    def test_hours_ago_plural(self) -> None:
        assert friendly_timestamp(self._iso(hours=5), now=self._NOW) == "5 hours ago"

    def test_an_hour_ago_singular(self) -> None:
        assert friendly_timestamp(self._iso(hours=1), now=self._NOW) == "an hour ago"

    def test_yesterday_has_a_plain_time(self) -> None:
        result = friendly_timestamp(self._iso(hours=30), now=self._NOW)
        assert result.startswith("yesterday at ")
        # A plain clock time, never the raw ISO.
        assert "T" not in result

    def test_days_ago(self) -> None:
        assert friendly_timestamp(self._iso(days=3), now=self._NOW) == "3 days ago"

    def test_weeks_ago(self) -> None:
        assert friendly_timestamp(self._iso(days=15), now=self._NOW) == "2 weeks ago"

    def test_output_is_never_the_raw_iso(self) -> None:
        raw = self._iso(hours=5)
        assert friendly_timestamp(raw, now=self._NOW) != raw

    def test_empty_input_falls_back_to_recently(self) -> None:
        assert friendly_timestamp("", now=self._NOW) == "recently"

    def test_unparseable_input_falls_back_to_recently_never_raises(self) -> None:
        assert friendly_timestamp("not-a-timestamp", now=self._NOW) == "recently"
        assert friendly_timestamp("2026-13-99", now=self._NOW) == "recently"

    def test_future_timestamp_is_just_now_not_negative(self) -> None:
        future = (self._NOW + timedelta(hours=3)).isoformat(timespec="seconds")
        assert friendly_timestamp(future, now=self._NOW) == "just now"

    def test_default_now_does_not_raise(self) -> None:
        # No `now` seam → uses datetime.now(); must not raise and must not echo raw ISO.
        recent = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds")
        result = friendly_timestamp(recent)
        assert result and "T" not in result

    def test_naive_aware_mismatch_falls_back_to_recently(self) -> None:
        # An aware `now` vs a naive parsed ISO → TypeError on subtraction → total ("recently").
        aware_now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
        naive_iso = self._iso(hours=5)  # naive
        assert friendly_timestamp(naive_iso, now=aware_now) == "recently"


class TestPluralize:
    """The shared "1 warning / N warnings" plural — TOTAL, 1 is singular, everything else plural."""

    def test_singular_at_one(self) -> None:
        assert pluralize("warning", 1) == "warning"

    def test_plural_at_zero(self) -> None:
        assert pluralize("warning", 0) == "warnings"

    def test_plural_at_many(self) -> None:
        assert pluralize("warning", 4) == "warnings"

    def test_plural_at_negative_is_total(self) -> None:
        # TOTAL — a negative count never raises; it reads as plural (matches the prior copies).
        assert pluralize("warning", -1) == "warnings"

    def test_works_for_any_word(self) -> None:
        assert pluralize("file", 1) == "file"
        assert pluralize("file", 3) == "files"


class TestFriendlyAnomalyDetail:
    """Each surface's anomaly detail reproduced BYTE-FOR-BYTE from the pre-consolidation copies."""

    def test_home_singular(self) -> None:
        assert friendly_anomaly_detail(1, variant=AnomalyVariant.HOME) == "One roster file was smaller than usual."

    def test_home_plural(self) -> None:
        assert friendly_anomaly_detail(2, variant=AnomalyVariant.HOME) == "2 roster files were smaller than usual."

    def test_history_singular(self) -> None:
        assert (
            friendly_anomaly_detail(1, variant=AnomalyVariant.HISTORY)
            == "One roster file was smaller than usual in the most recent run."
        )

    def test_history_plural(self) -> None:
        assert (
            friendly_anomaly_detail(3, variant=AnomalyVariant.HISTORY)
            == "3 roster files were smaller than usual in the most recent run."
        )

    def test_convert_singular_has_review_cta_and_it_pronoun(self) -> None:
        assert (
            friendly_anomaly_detail(1, variant=AnomalyVariant.CONVERT)
            == "One roster file has far fewer rows than last time. Review it before delivering."
        )

    def test_convert_plural_swaps_pronoun_to_them(self) -> None:
        assert (
            friendly_anomaly_detail(2, variant=AnomalyVariant.CONVERT)
            == "2 roster files have far fewer rows than last time. Review them before delivering."
        )

    def test_never_surfaces_the_raw_anomaly_prefix(self) -> None:
        # No variant / count ever leaks the raw ``ANOMALY:``-prefixed string.
        for variant in AnomalyVariant:
            for count in (0, 1, 5):
                assert "ANOMALY:" not in friendly_anomaly_detail(count, variant=variant)


class TestFriendlySftpReason:
    """A bounded, category-mapped SFTP-failure reason — NEVER the raw core string."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # auth — real paramiko AuthenticationException text
            ("Authentication failed.", "The username or password wasn't accepted."),
            ("Bad password for user", "The username or password wasn't accepted."),
            ("auth type not supported", "The username or password wasn't accepted."),
            # host / DNS resolution
            (
                "[Errno 11001] getaddrinfo failed",
                "Couldn't find that host — double-check the SFTP host.",
            ),
            (
                "Name or service not known",
                "Couldn't find that host — double-check the SFTP host.",
            ),
            (
                "nodename nor servname provided",
                "Couldn't find that host — double-check the SFTP host.",
            ),
            # reachability
            ("timed out", "Couldn't reach the server — check the host and your network."),
            (
                "[Errno 111] Connection refused",
                "Couldn't reach the server — check the host and your network.",
            ),
            (
                "[Errno 113] No route to host (network is unreachable)",
                "Couldn't reach the server — check the host and your network.",
            ),
            # remote path
            (
                "[Errno 2] No such file or directory",
                "Connected, but the remote folder wasn't accessible — check the remote path.",
            ),
            (
                "Permission denied",
                "Connected, but the remote folder wasn't accessible — check the remote path.",
            ),
        ],
    )
    def test_each_category_maps_to_its_fixed_reason(self, raw: str, expected: str) -> None:
        assert friendly_sftp_reason(raw) == expected

    def test_case_insensitive_match(self) -> None:
        assert friendly_sftp_reason("AUTHENTICATION FAILED") == "The username or password wasn't accepted."

    def test_unknown_falls_through_to_the_catch_all(self) -> None:
        result = friendly_sftp_reason("something totally unexpected happened at 0xDEADBEEF")
        assert result == (
            "Couldn't connect to SpacesEDU. Check the host, username, password, and remote path, then try again."
        )

    def test_empty_input_is_the_catch_all(self) -> None:
        assert friendly_sftp_reason("") == (
            "Couldn't connect to SpacesEDU. Check the host, username, password, and remote path, then try again."
        )

    def test_never_returns_the_raw_string(self) -> None:
        # A raw paramiko/socket string carrying a host / path / socket detail must NEVER echo.
        raw = "SSHException: Error reading SSH protocol banner from sftp.secret-host.internal:/root/roster"
        result = friendly_sftp_reason(raw)
        assert raw not in result
        assert "secret-host" not in result
        assert "/root/roster" not in result


class TestFriendlySftpReasonHostKey:
    """W1-A: a host-key / server-identity failure gets its OWN category, ahead of the
    generic fallback — which used to invite an admin to retype credentials at a possibly
    impostor server (the raw reject message matches none of the other rules' words)."""

    MISMATCH_REASON = (
        "The server didn't match SpacesEDU's known identity, so nothing was sent. "
        "Contact SpacesEDU support — don't retry or re-enter your password."
    )
    UNVERIFIED_REASON = (
        "DistrictSync couldn't verify the server's identity, so nothing was sent. "
        "Contact SpacesEDU support — don't retry or re-enter your password."
    )

    def test_the_real_uploader_mismatch_message_maps_to_the_identity_category(self) -> None:
        """The cross-module pin: the EXACT string ``test_connection`` returns for a pinned-key
        mismatch (not a hand-written approximation) must hit the identity rule."""
        from src.sftp.uploader import _host_key_mismatch_message

        raw = _host_key_mismatch_message("sftp.ca.spacesedu.com")
        assert friendly_sftp_reason(raw) == self.MISMATCH_REASON

    def test_the_real_uploader_unpinned_message_maps_to_the_unverified_category(self) -> None:
        from src.sftp.uploader import _host_key_unpinned_message

        raw = _host_key_unpinned_message("sftp.ca.spacesedu.com")
        assert friendly_sftp_reason(raw) == self.UNVERIFIED_REASON

    def test_the_real_uploader_port_message_maps_to_the_unverified_category(self) -> None:
        from src.sftp.uploader import _host_key_port_unpinned_message

        raw = _host_key_port_unpinned_message("[sftp.ca.spacesedu.com]:2222", "sftp.ca.spacesedu.com")
        assert friendly_sftp_reason(raw) == self.UNVERIFIED_REASON

    def test_raw_paramiko_bad_host_key_text_maps_to_the_identity_category(self) -> None:
        """paramiko's own ``BadHostKeyException`` text carries the host AND both key blobs —
        it must never reach an admin card, and it is an identity failure, not a generic one."""
        raw = (
            "Host key for server 'sftp.ca.spacesedu.com' does not match: got "
            "'AAAAC3NzaC1lZDI1NTE5AAAAIPxBtjBnb69WmfeFndeQQtzHLMu', expected "
            "'AAAAC3NzaC1lZDI1NTE5AAAAII+Z9Hmt2exPFjiWplMl4AyFcKf0litdDwfbwVWLwz9K'"
        )
        result = friendly_sftp_reason(raw)
        assert result == self.MISMATCH_REASON
        assert "AAAAC3Nza" not in result

    @pytest.mark.parametrize("reason_attr", ["MISMATCH_REASON", "UNVERIFIED_REASON"])
    def test_identity_copy_never_invites_a_retry_or_a_credential_re_entry(self, reason_attr: str) -> None:
        reason = getattr(self, reason_attr)
        assert "try again" not in reason.lower()
        assert "check the host" not in reason.lower()
        assert "don't retry or re-enter your password" in reason

    def test_identity_copy_leaks_no_host_path_or_fingerprint(self) -> None:
        from src.sftp.uploader import _host_key_mismatch_message, _host_key_unpinned_message

        for raw in (
            _host_key_mismatch_message("sftp.ca.spacesedu.com"),
            _host_key_unpinned_message("sftp.ca.spacesedu.com"),
        ):
            result = friendly_sftp_reason(raw)
            assert "spacesedu.com" not in result
            assert "known_hosts" not in result
            assert "ssh-keyscan" not in result
            assert raw not in result
