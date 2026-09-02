"""BC school district staff email domains — vendored owner-supplied prefill data.

Plan 0044 slice 1. Pure data-as-code: no I/O, no flet/pandas imports, nothing that
needs to reach PyInstaller's ``add-data`` list — the table below IS the artifact.

**Provenance (owner, 2026-08-27):** a CSV of ``District Number,Email,Schools`` pulled
from a public BC document, grouped by district number, with each district's domain(s)
taken from its schools' contact email addresses. School counts were dropped on
vendoring — they carry no signal this module needs.

**Quality: PLACEHOLDER, not a source of truth.** This table exists to PREFILL the
self-service config creator's district-domain field so an admin has something to
start from — it is corrected (added to, replaced, or cleared) in the creator form,
never treated as authoritative. Two shipped-config cross-checks anchor it against
data this repo already trusts: SD60 -> ``prn.bc.ca`` and SD48 -> ``sd48.bc.ca`` both
match the domains those districts' bundled configs already declare in
``district_domains`` (see ``tests/test_config_district_domains.py``).

**SD78's second domain is dropped on purpose.** The source CSV's SD78 row carries two
domains, ``sd78.bc.ca`` and ``sd48.bc.ca``. The second is almost certainly a grouping
artifact of the "group by district number, pull domains from school contact emails"
method (a school administratively grouped with SD48 for that CSV's purposes, not an
SD78 domain) — SD48 already owns ``sd48.bc.ca`` as its own row below, and no BC
district legitimately shares its staff domain with an unrelated district. SD78 is
vendored here as ``("sd78.bc.ca",)`` only.

**These are PUBLIC district staff domains, not personal addresses** — the same
category as the ``district_domains`` values already shipped in
``config/mappings/*.yaml`` (see ``src/config/models.py::is_valid_district_domain``).
``scripts/check_no_emails.py`` scans for plaintext personal email addresses; a bare
domain with no local part is not that, so this module is not a gap in that gate.

**Future option, not built here:** if this table needs to be corrected or extended
without a new release, the natural evolution is a served registry (fetched at
runtime, with this module's contents as the offline fallback) rather than editing
vendored source. Not needed yet — YAGNI — recorded so a future slice doesn't
rediscover the question.
"""

from __future__ import annotations

from collections.abc import Mapping

# District number -> tuple of known public staff email domain(s), lowercase, no
# leading/trailing whitespace, no ``@``. Order within a tuple is the source CSV's
# order and is preserved verbatim (SD63 and SD70 are the two multi-domain rows).
#
# 60 keys. Reproduced from the plan's owner-supplied table (2026-08-27); every
# absent SD number is simply not covered by the source CSV.
DOMAINS_BY_SD: Mapping[int, tuple[str, ...]] = {
    5: ("sd5.bc.ca",),
    6: ("sd6.bc.ca",),
    8: ("sd8.bc.ca",),
    10: ("sd10.bc.ca",),
    19: ("sd19.bc.ca",),
    20: ("sd20.bc.ca",),
    22: ("sd22.bc.ca",),
    23: ("sd23.bc.ca",),
    27: ("sd27.bc.ca",),
    28: ("sd28.bc.ca",),
    33: ("sd33.bc.ca",),
    34: ("abbyschools.ca",),
    35: ("sd35.bc.ca",),
    36: ("surreyschools.ca",),
    37: ("deltaschools.ca",),
    38: ("sd38.bc.ca",),
    39: ("vsb.bc.ca",),
    40: ("sd40.bc.ca",),
    41: ("burnabyschools.ca",),
    42: ("sd42.ca",),
    43: ("sd43.bc.ca",),
    44: ("sd44.ca",),
    45: ("wvschools.ca",),
    46: ("sd46.bc.ca",),
    47: ("sd47.bc.ca",),
    48: ("sd48.bc.ca",),
    49: ("sd49.ca",),
    50: ("sd50.bc.ca",),
    51: ("sd51.bc.ca",),
    52: ("sd52.bc.ca",),
    53: ("sd53.bc.ca",),
    54: ("sd54.bc.ca",),
    57: ("sd57.bc.ca",),
    58: ("365.sd58.bc.ca",),
    59: ("sd59.bc.ca",),
    60: ("prn.bc.ca",),
    61: ("sd61.bc.ca",),
    62: ("sd62.bc.ca",),
    63: ("saanichschools.ca", "sides.ca", "sd63.bc.ca"),
    64: ("sd64.org",),
    67: ("sd67.bc.ca",),
    68: ("sd68.bc.ca",),
    69: ("sd69.bc.ca",),
    70: ("sd70.bc.ca", "kackaamin.org"),
    71: ("sd71.bc.ca",),
    72: ("sd72.bc.ca",),
    73: ("sd73.bc.ca",),
    74: ("sd74.bc.ca",),
    75: ("mpsd.ca",),
    78: ("sd78.bc.ca",),  # sd48.bc.ca dropped — grouping artifact, see module docstring
    79: ("sd79.bc.ca",),
    81: ("sd81.bc.ca",),
    82: ("cmsd.bc.ca",),
    83: ("sd83.bc.ca",),
    84: ("viw.sd84.bc.ca",),
    85: ("sd85.bc.ca",),
    87: ("sd87.bc.ca",),
    91: ("sd91.bc.ca",),
    92: ("nisgaa.bc.ca",),
    93: ("csf.bc.ca",),
}


def domains_for(sd_number: int) -> tuple[str, ...]:
    """Known public staff domain(s) for ``sd_number``, or ``()`` if unknown.

    TOTAL: never raises, for any ``int`` (including 0, negative, or a number the
    source CSV never covered). The empty tuple is a legitimate, common answer — most
    BC district numbers are not in the source table — and callers (the creator
    form's prefill) treat it as "nothing to prefill", not an error.
    """
    return DOMAINS_BY_SD.get(sd_number, ())


def presumptive_domain(sd_number: int) -> str:
    """The conventional ``sd<N>.bc.ca`` guess for a district not in ``DOMAINS_BY_SD``.

    A fallback prefill, not a claim of fact — most BC districts follow this naming
    convention, but several vendored rows above prove plenty do not (Surrey, Delta,
    Abbotsford, ...). The admin corrects it in the creator form like every other
    vendored value.

    Raises ``ValueError`` for a non-positive ``sd_number`` — the one raising path in
    this module, and deliberately so: a bad district number here is a programming
    error (a caller passed something that was never validated as a real SD number),
    not untrusted external data, so failing loudly beats fabricating a nonsense
    domain like ``sd-1.bc.ca`` or ``sd0.bc.ca``.
    """
    if not isinstance(sd_number, int) or isinstance(sd_number, bool) or sd_number <= 0:
        raise ValueError(f"sd_number must be a positive int, got {sd_number!r}")
    return f"sd{sd_number}.bc.ca"
