"""Tests for src/utils/validators.py — input validation for security-sensitive ops."""

import hashlib

import pytest

from src.utils.validators import (
    ALLOWED_SFTP_HOSTS,
    IDENTITY_EMAIL_MAX_LEN,
    is_config_digest,
    quote_for_shell,
    validate_identity_email,
    validate_run_time,
    validate_sftp_host,
    validate_sis_type,
    validate_task_name,
)


class TestValidateSisType:
    def test_valid_alphanumeric(self):
        assert validate_sis_type("myedbc") == "myedbc"

    def test_valid_with_underscore(self):
        assert validate_sis_type("sd40_myedbc") == "sd40_myedbc"

    def test_valid_with_digits(self):
        assert validate_sis_type("sd48myedbc") == "sd48myedbc"

    def test_strips_whitespace(self):
        assert validate_sis_type("  myedbc  ") == "myedbc"

    def test_rejects_special_chars(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("my;edbc")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("my edbc")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("../etc/passwd")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid SIS type"):
            validate_sis_type("")


class TestIsConfigDigest:
    """The one shape a stored "this config was tested" fact may take (plan 0044 S3).

    TOTAL: a malformed stored value must read as ABSENT (ask for another test run), never
    raise — ``config.json`` is hand-editable, and a crash on the way in would take the
    admin's district, folders and delivery settings down with it. Lowercase-only because
    the digest is compared as a plain string at both ends; accepting a mixed-case twin
    would make "tested" depend on which end wrote it.
    """

    VALID = "a" * 64
    REAL = hashlib.sha256(b"a resolved config").hexdigest()

    @pytest.mark.parametrize("value", [VALID, REAL, "0" * 64, "0123456789abcdef" * 4])
    def test_accepts_64_lowercase_hex(self, value: str):
        assert is_config_digest(value) is True

    @pytest.mark.parametrize(
        ("value", "why"),
        [
            ("a" * 63, "63 characters — one short"),
            ("a" * 65, "65 characters — one long"),
            ("A" * 64, "uppercase hex"),
            (REAL.upper(), "a real digest, upper-cased"),
            ("g" * 64, "not hex at all"),
            ("", "blank"),
            ("  " + "a" * 64, "leading whitespace is not trimmed — this is a predicate, not a validator"),
            ("a" * 64 + "\n", "a trailing newline"),
            (None, "None — the shape a missing entry has"),
            (0, "an int"),
            (b"a" * 64, "bytes, not str"),
            (["a" * 64], "a list holding one"),
            ({"digest": "a" * 64}, "a nested mapping — the hand-edited shape"),
        ],
    )
    def test_rejects_everything_else_without_raising(self, value: object, why: str):
        assert is_config_digest(value) is False, why


class TestValidateTaskName:
    def test_valid_simple(self):
        assert validate_task_name("DistrictSync_Daily") == "DistrictSync_Daily"

    def test_valid_with_spaces_and_hyphens(self):
        assert validate_task_name("DistrictSync Daily-Run") == "DistrictSync Daily-Run"

    def test_strips_whitespace(self):
        assert validate_task_name("  MyTask  ") == "MyTask"

    def test_rejects_semicolons(self):
        with pytest.raises(ValueError, match="Invalid task name"):
            validate_task_name("task;rm -rf /")

    def test_rejects_slashes(self):
        with pytest.raises(ValueError, match="Invalid task name"):
            validate_task_name("task/name")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid task name"):
            validate_task_name("")


class TestValidateRunTime:
    def test_valid_time(self):
        assert validate_run_time("03:00") == ("03", "00")

    def test_valid_midnight(self):
        assert validate_run_time("00:00") == ("00", "00")

    def test_valid_end_of_day(self):
        assert validate_run_time("23:59") == ("23", "59")

    def test_strips_whitespace(self):
        assert validate_run_time("  14:30  ") == ("14", "30")

    def test_rejects_bad_format(self):
        with pytest.raises(ValueError, match="Expected HH:MM"):
            validate_run_time("3:00")

    def test_rejects_hour_out_of_range(self):
        with pytest.raises(ValueError, match="Hour must be"):
            validate_run_time("25:00")

    def test_rejects_minute_out_of_range(self):
        with pytest.raises(ValueError, match="Minute must be"):
            validate_run_time("12:60")

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError, match="Expected HH:MM"):
            validate_run_time("ab:cd")


class TestValidateSftpHost:
    def test_valid_hosts(self):
        for host in ALLOWED_SFTP_HOSTS:
            assert validate_sftp_host(host) == host

    def test_case_insensitive(self):
        assert validate_sftp_host("SFTP.CA.SPACESEDU.COM") == "sftp.ca.spacesedu.com"

    def test_strips_whitespace(self):
        assert validate_sftp_host("  sftp.ca.spacesedu.com  ") == "sftp.ca.spacesedu.com"

    def test_rejects_unknown_host(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_sftp_host("evil.example.com")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="not allowed"):
            validate_sftp_host("")


class TestQuoteForShell:
    def test_simple_value(self):
        result = quote_for_shell("myedbc")
        assert "myedbc" in result

    def test_value_with_spaces(self):
        result = quote_for_shell("path with spaces")
        # shlex.quote wraps in single quotes
        assert "'" in result or '"' in result

    def test_value_with_special_chars(self):
        result = quote_for_shell("value;rm -rf /")
        # Must be quoted/escaped safely
        assert result != "value;rm -rf /"


class TestValidateIdentityEmail:
    """The BOUNDARY validator for the admin's typed work email (plan 0038 S3).

    Runs BEFORE ``identity.normalize_email`` / persistence, and is the ONLY place the
    typed value is rejected. Deliberately different from its siblings in two ways, both
    load-bearing:

    * it returns the value with CASE and Unicode form untouched (only surrounding
      whitespace trimmed) — ``normalize_email`` owns reduction, so the address the admin
      sees echoed back on Help is the one they typed;
    * its error messages NEVER quote the value — sibling validators echo the offending
      input (``Invalid SIS type 'x'``), which for an email address would put personal
      data into any log line a caller writes.
    """

    # ---- accepted -------------------------------------------------------- #
    @pytest.mark.parametrize(
        ("raw", "expected", "why"),
        [
            ("admin@sd48.bc.ca", "admin@sd48.bc.ca", "the ordinary case"),
            ("First.Last@SD48.BC.CA", "First.Last@SD48.BC.CA", "case preserved, NOT normalised"),
            ("  admin@sd48.bc.ca  ", "admin@sd48.bc.ca", "surrounding whitespace trimmed"),
            ("admin+roster@sd48.bc.ca", "admin+roster@sd48.bc.ca", "plus-addressing is a real address"),
            ("a_b-c%d@x-y.co", "a_b-c%d@x-y.co", "the full permitted local-part charset"),
            ("a@b.co", "a@b.co", "shortest plausible address"),
            ("admin@sd48.bc.ca.", "admin@sd48.bc.ca.", "a trailing root dot is accepted; normalize strips it"),
        ],
    )
    def test_accepts(self, raw, expected, why):
        assert validate_identity_email(raw) == expected, why

    def test_accepts_exactly_the_max_length(self):
        local = "a" * (IDENTITY_EMAIL_MAX_LEN - len("@sd48.bc.ca"))
        value = f"{local}@sd48.bc.ca"
        assert len(value) == IDENTITY_EMAIL_MAX_LEN
        assert validate_identity_email(value) == value

    # ---- rejected -------------------------------------------------------- #
    @pytest.mark.parametrize(
        ("raw", "why"),
        [
            ("", "empty"),
            ("     ", "whitespace only"),
            ("noatsign.ca", "no @ at all"),
            ("a@b@c.ca", "more than one @ — ambiguous, and normalize would keep both"),
            ("@sd48.bc.ca", "empty local part"),
            ("admin@", "empty domain part"),
            ("admin@localhost", "domain with no dot"),
            ("admin@sd48", "domain with no dot"),
            ("admin@-sd48.bc.ca", "domain label may not start with a hyphen"),
            ("admin@sd48.bc.c", "single-character TLD"),
            ("admin@sd48.bc.4a", "non-alphabetic TLD"),
            ("ad min@sd48.bc.ca", "internal whitespace"),
            ("ad\tmin@sd48.bc.ca", "internal tab"),
            ("ad\nmin@sd48.bc.ca", "internal newline"),
            ("ad\rmin@sd48.bc.ca", "internal carriage return"),
            ("admin@sd48.bc.ca\x00", "NUL byte"),
            ("admin@sd48.bc.ca\x07", "control character"),
            ("admin@sd48​.bc.ca", "zero-width space"),
            ("admin‮@sd48.bc.ca", "RTL override"),
            ("josé@sd48.bc.ca", "non-ASCII local part — deliberate narrowing, see the docstring"),
            ("admin@sd48.bc.cä", "non-ASCII domain"),
            ("admin<script>@sd48.bc.ca", "angle brackets"),
            ("admin;drop@sd48.bc.ca", "semicolon"),
            ('"admin"@sd48.bc.ca', "quoted local part (RFC-legal, deliberately refused)"),
        ],
    )
    def test_rejects(self, raw, why):
        with pytest.raises(ValueError):
            validate_identity_email(raw)

    def test_rejects_one_character_over_the_max_length(self):
        local = "a" * (IDENTITY_EMAIL_MAX_LEN - len("@sd48.bc.ca") + 1)
        with pytest.raises(ValueError):
            validate_identity_email(f"{local}@sd48.bc.ca")

    # ---- the PII pin ----------------------------------------------------- #
    @pytest.mark.parametrize(
        "raw",
        [
            "distinctivelocalpart.ca",
            "distinctivelocalpart@second@third.ca",
            "@distinctivedomain.bc.ca",
            "distinctivelocalpart@localhost",
            "distinctive localpart@sd48.bc.ca",
            "distinctivelocalpärt@sd48.bc.ca",
            "distinctivelocalpart" * 20 + "@sd48.bc.ca",
            "  distinctivelocalpart@nodot  ",
        ],
    )
    def test_error_message_never_echoes_the_address(self, raw):
        """A rejection message must be actionable WITHOUT quoting the value.

        The identity email is personal data. Every sibling validator echoes its input into
        the message (``Invalid SIS type 'x'``); a caller that logs ``str(exc)`` would
        therefore leak the address. This validator's messages carry the RULE and a canned
        example, never the value.

        The local part is the identifying half and is checked strictly. The DOMAIN half is
        deliberately not substring-checked: a district's email domain is a public
        organisational fact, and the canned example (``name@yourdistrict.bc.ca``) shares
        real substrings with any ``*.bc.ca`` address by construction — a check there would
        fail for a coincidence rather than a leak.
        """
        with pytest.raises(ValueError) as excinfo:
            validate_identity_email(raw)
        message = str(excinfo.value)

        stripped = raw.strip()
        assert stripped not in message, "the whole typed address reached the message"
        assert "distinctivelocalpart" not in message, "the local part reached the message"
        assert message  # ...but it still says something

    def test_the_leak_pin_would_actually_catch_a_leak(self):
        """Falsification twin: prove the assertion above is not vacuous.

        A message built the sibling way — echoing the value — must trip exactly the
        assertions the pin makes, otherwise "no leak found" would only mean "no leak
        looked for".
        """
        leaky = f"Invalid email address {'distinctivelocalpart@sd48.bc.ca'!r}."

        assert "distinctivelocalpart@sd48.bc.ca" in leaky
        assert "distinctivelocalpart" in leaky

    def test_error_messages_are_actionable(self):
        """Each rejection names what to fix (the fail-loud bar), in plain language."""
        with pytest.raises(ValueError, match="@"):
            validate_identity_email("noatsign.ca")
        with pytest.raises(ValueError, match="(?i)email"):
            validate_identity_email("")
