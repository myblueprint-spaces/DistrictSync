"""BC school district staff email domains and names — vendored owner-supplied prefill data.

Plan 0044 slice 1 (domains) and its 2026-09-03 owner round (names). Pure data-as-code:
no I/O, no flet/pandas imports, nothing that needs to reach PyInstaller's ``add-data``
list — the two tables below ARE the artifact.

**Provenance, DOMAINS (owner, 2026-08-27):** a CSV of ``District Number,Email,Schools``
pulled from a public BC document, grouped by district number, with each district's
domain(s) taken from its schools' contact email addresses. School counts were dropped
on vendoring — they carry no signal this module needs.

**Provenance, NAMES (owner, 2026-09-03):** a second, cleaner sheet
(``District Number,District Name,Contact Email``) covering the SAME 60 district numbers.
Its domain column was checked against the table below and disagreed NOWHERE, but it is
single-domain, so it would have LOST SD63's ``sides.ca``/``sd63.bc.ca``: only the names
were taken from it. It closes the gap where name prefill covered only districts we
already ship a mapping for.

**Quality: PLACEHOLDER, not a source of truth.** These tables exist to PREFILL the
self-service config creator's district-domain and district-name fields so an admin has
something to start from — each is corrected (added to, replaced, or cleared) in the
creator form, never treated as authoritative. Two shipped-config cross-checks anchor
the domains against data this repo already trusts: SD60 -> ``prn.bc.ca`` and
SD48 -> ``sd48.bc.ca`` both match the domains those districts' bundled configs declare
in ``district_domains`` (see ``tests/test_config_district_domains.py``).

**The effective domain answer is :func:`domains_for`, not the literal.** Since
2026-09-03 a district with its own custom domain still matches ``sd<N>.bc.ca`` (owner),
and that rule lives in the function so the literal can stay a faithful record of its
source. Read the function before reasoning about what a district matches.

**SD70's second domain was dropped 2026-09-03 (owner):** ``kackaamin.org`` is an
artifact of the same grouping method described below, not an SD70 staff domain. The
owner confirmed the real district domain is ``sd70.bc.ca``, and the cleaner 2026-09-03
sheet does not carry ``kackaamin.org`` at all.

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
# order and is preserved VERBATIM — this literal is the SHEET, not the effective
# answer. The conventional ``sd<N>.bc.ca`` form the owner also wants matched is
# added by :func:`domains_for`, so this table stays a faithful record of its source
# and a future vendored row inherits the rule without anyone remembering to.
# (SD63 is now the only multi-domain row here.)
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
    # kackaamin.org dropped 2026-09-03 — OWNER: an artifact, not an SD70 staff domain
    # (same grouping-artifact shape as SD78 below; absent from the cleaner 2026-09-03 sheet).
    70: ("sd70.bc.ca",),
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

# District number -> the district's BARE name, exactly as the owner's sheet spells it
# (2026-09-03). BARE means no ``SD<N> - `` prefix and no ``School District`` suffix: the
# prefix is put back by ``ui_flet.humanize.friendly_district_name`` and the creator's
# name field wants the name alone. The suffix is deliberately NOT appended (owner,
# 2026-09-03) — SD92 "Nisga'a" and SD93 "Conseil scolaire francophone" are not that
# shape, so a blanket suffix would be wrong for them and merely redundant elsewhere.
#
# Same 60 keys as ``DOMAINS_BY_SD``, from the same owner sheet, and the same
# PLACEHOLDER quality: a prefill the admin corrects, never a source of truth.
NAMES_BY_SD: Mapping[int, str] = {
    5: "Southeast Kootenay",
    6: "Rocky Mountain",
    8: "Kootenay Lake",
    10: "Arrow Lakes",
    19: "Revelstoke",
    20: "Kootenay-Columbia",
    22: "Vernon",
    23: "Central Okanagan",
    27: "Cariboo-Chilcotin",
    28: "Quesnel",
    33: "Chilliwack",
    34: "Abbotsford",
    35: "Langley",
    36: "Surrey",
    37: "Delta",
    38: "Richmond",
    39: "Vancouver",
    40: "New Westminster",
    41: "Burnaby",
    42: "Maple Ridge-Pitt Meadows",
    43: "Coquitlam",
    44: "North Vancouver",
    45: "West Vancouver",
    46: "Sunshine Coast",
    47: "qathet",
    48: "Sea to Sky",
    49: "Central Coast",
    50: "Haida Gwaii",
    51: "Boundary",
    52: "Prince Rupert",
    53: "Okanagan Similkameen",
    54: "Bulkley Valley",
    57: "Prince George",
    58: "Nicola-Similkameen",
    59: "Peace River South",
    60: "Peace River North",
    61: "Greater Victoria",
    62: "Sooke",
    63: "Saanich",
    64: "Gulf Islands",
    67: "Okanagan Skaha",
    68: "Nanaimo-Ladysmith",
    69: "Qualicum",
    70: "Pacific Rim",
    71: "Comox Valley",
    72: "Campbell River",
    73: "Kamloops-Thompson",
    74: "Gold Trail",
    75: "Mission",
    78: "Fraser-Cascade",
    79: "Cowichan Valley",
    81: "Fort Nelson",
    82: "Coast Mountains",
    # SD83's name is Secwepemc: K + U+0313 COMBINING COMMA ABOVE, then e-acute,
    # u-acute and U+2019 RIGHT SINGLE QUOTATION MARK. Do not "tidy" the punctuation.
    83: "K̓wsaltktnéws ne Secwepemcúl’ecw",
    84: "Vancouver Island West",
    85: "Vancouver Island North",
    87: "Stikine",
    91: "Nechako Lakes",
    92: "Nisga'a",
    93: "Conseil scolaire francophone",
}


def domains_for(sd_number: int) -> tuple[str, ...]:
    """Known public staff domain(s) for ``sd_number``, or ``()`` if unknown.

    TOTAL: never raises, for any ``int`` (including 0, negative, or a number the
    source CSV never covered). The empty tuple is a legitimate, common answer — most
    BC district numbers are not in the source table — and callers (the creator
    form's prefill) treat it as "nothing to prefill", not an error.

    **A district with its own custom domain STILL matches ``sd<N>.bc.ca``** (owner,
    2026-09-03). Seventeen vendored rows carry only a custom domain — Surrey, Delta,
    Abbotsford, Vancouver, Burnaby, West Vancouver, Peace River North, Mission,
    Coast Mountains, Nisga'a, the CSF and others — and several more carry a subdomain
    of the conventional form (SD58's ``365.sd58.bc.ca``, SD84's ``viw.sd84.bc.ca``),
    which under this project's EXACT-equality matching claims nothing for the plain
    form. So the conventional domain is APPENDED here when the sheet omits it, rather
    than hand-added to the literal above: the rule then has one home, the table stays
    a faithful record of its source, and a row vendored next year gets it for free.

    Appended LAST, never prepended: the sheet's own domain is the one the owner
    verified, so it keeps the lead in the creator's prefill ordering.

    This is deliberately NOT :func:`presumptive_domain`'s job. That one answers for a
    district the sheet does not cover at all, where the conventional form is a GUESS
    offered alone; here it accompanies domains we were actually given.
    """
    known = DOMAINS_BY_SD.get(sd_number, ())
    if not known:
        return ()
    conventional = f"sd{sd_number}.bc.ca"
    return known if conventional in known else (*known, conventional)


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


def name_for(sd_number: int) -> str:
    """The district's bare name for ``sd_number``, or ``""`` if unknown. TOTAL.

    Never raises, for any ``int`` — the same posture as :func:`domains_for`, and for
    the same reason: this feeds a PREFILL, and a prefill that raises is worse than one
    that stays quiet. ``""`` is the ordinary answer for the many BC district numbers
    the owner's sheet does not cover.

    Deliberately no fabricated fallback. :func:`presumptive_domain` can guess a domain
    because ``sd<N>.bc.ca`` is a real naming CONVENTION; there is no convention that
    would let us guess a district's NAME, and inventing "District 34" would put a
    string in the picker that looks like a name somebody chose. The creator's name
    field simply opens empty — the rule
    :func:`src.ui_flet.config_editor.district_name_seed` already applies to the names
    of configs we SHIP, which take precedence over this table.
    """
    return NAMES_BY_SD.get(sd_number, "")
