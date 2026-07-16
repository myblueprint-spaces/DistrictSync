"""Tests for src/ui_flet/about.py — the Help About/support derivations (0032 T1 #9).

Pins the release-notes URL (drift guard, mirroring ``test_ui_flet_help``'s exact-case
constants), the plain-language version line, and the PII-free prefilled support mailto
(subject = version + district DISPLAY name only — never a path, never a body).
"""

from __future__ import annotations

from src.ui_flet.about import (
    RELEASE_NOTES_URL,
    support_mailto,
    support_subject,
    version_display,
)

_EMAIL = "support@myBlueprint.ca"


class TestReleaseNotesUrl:
    def test_release_notes_url_is_the_canonical_repo_releases_page(self):
        # Exact == (like the Help Centre URL guard): the canonical repo is
        # myblueprint-spaces/DistrictSync (README download links + git origin).
        assert RELEASE_NOTES_URL == "https://github.com/myblueprint-spaces/DistrictSync/releases"


class TestVersionDisplay:
    def test_release_version_reads_as_version_line(self):
        assert version_display("3.4.0") == "Version 3.4.0"

    def test_dev_fallback_reads_as_a_development_build(self):
        # utils.version.app_version() returns "dev" on an unbuilt source checkout —
        # "Version dev" would be cryptic on the About block.
        assert version_display("dev") == "Development build (not a release)"

    def test_blank_version_reads_as_a_development_build(self):
        assert version_display("") == "Development build (not a release)"
        assert version_display("   ") == "Development build (not a release)"

    def test_version_is_stripped(self):
        assert version_display("  3.4.0  ") == "Version 3.4.0"


class TestSupportSubject:
    def test_subject_carries_version_and_district_display_name(self):
        subject = support_subject("3.4.0", "Gold Trail")
        assert subject == "DistrictSync 3.4.0 support request — Gold Trail"

    def test_no_district_omits_the_district_clause(self):
        assert support_subject("3.4.0", "") == "DistrictSync 3.4.0 support request"
        assert support_subject("3.4.0", "   ") == "DistrictSync 3.4.0 support request"

    def test_blank_version_falls_back_to_dev(self):
        assert support_subject("", "Gold Trail").startswith("DistrictSync dev support request")


class TestSupportMailto:
    def test_mailto_targets_the_support_address_with_an_encoded_subject(self):
        url = support_mailto(_EMAIL, "3.4.0", "Gold Trail")
        assert url.startswith(f"mailto:{_EMAIL}?subject=")
        assert " " not in url  # RFC 6068: the subject is %-encoded
        assert "DistrictSync%203.4.0%20support%20request" in url
        assert "Gold%20Trail" in url

    def test_mailto_has_a_subject_only_no_body(self):
        # PII-free by construction: no body parameter exists to carry data into.
        url = support_mailto(_EMAIL, "3.4.0", "Gold Trail")
        assert "body=" not in url

    def test_mailto_carries_no_filesystem_path(self):
        # The subject is built ONLY from the version + display name arguments — a
        # path can appear only if a caller passes one, which the Help view never does.
        url = support_mailto(_EMAIL, "3.4.0", "Boundary")
        assert "%2F" not in url.split("?", 1)[1]  # no encoded "/" in the subject
        assert "%5C" not in url  # no encoded "\"
