"""About/support derivations for the Help surface — pure copy, COUNTED.

0032 Tier-1 #9: the Help screen's About block (version line, release-notes link,
prefilled support email). The STRINGS are decided here, unit-tested; the
coverage-omitted ``screens/help.py`` only renders them.

Privacy: the support subject carries the app VERSION and the district's DISPLAY
name only — never a filesystem path, never roster data, never an email body.
The version is app metadata and the district display name is the same friendly
label every surface already shows (``humanize.friendly_district_name``).

No ``flet`` import — testable without a display.
"""

from __future__ import annotations

from urllib.parse import quote

# The public releases page for the shipped exe — the canonical repo (README download
# links + git origin both point at ``myblueprint-spaces/DistrictSync``).
RELEASE_NOTES_URL = "https://github.com/myblueprint-spaces/DistrictSync/releases"


def version_display(version: str) -> str:
    """The plain-language version line: ``"Version 3.4.0"``.

    An unbuilt source checkout (``utils.version.app_version()`` → ``"dev"``) reads
    honestly as a development build instead of the cryptic ``"Version dev"``.
    """
    cleaned = (version or "").strip()
    if not cleaned or cleaned == "dev":
        return "Development build (not a release)"
    return f"Version {cleaned}"


def support_subject(version: str, district_display: str) -> str:
    """The PII-free support email subject: app + version + district DISPLAY name only.

    ``district_display`` is the friendly label (may be blank when no district is
    chosen — the subject simply omits it). No paths, no usernames, no data.
    """
    subject = f"DistrictSync {(version or '').strip() or 'dev'} support request"
    district = (district_display or "").strip()
    if district:
        subject = f"{subject} — {district}"
    return subject


def support_mailto(email: str, version: str, district_display: str) -> str:
    """A prefilled ``mailto:`` URL — subject only (no body), RFC 6068 %-encoded."""
    return f"mailto:{email}?subject={quote(support_subject(version, district_display))}"
