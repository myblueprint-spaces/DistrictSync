"""Identity primitives — reduce an email address to a comparable form, and to its domain.

Two pure functions and nothing else. They exist so that "what does this address reduce
to?" is decided ONCE, in one place, instead of being re-guessed at every call site that
wants to compare an admin's work email against a district's published domain.

**No salt, no hashing, no scheme.** Matching compares PUBLIC organisational facts — a
school district's staff email domain, which the district itself publishes on its own
website. There is no secret here to protect, so a digest would add no confidentiality
while making the shipped values impossible to test end-to-end. (The earlier hashed-
allowlist design was retired by the owner on 2026-07-28; plan 0038, flag 8.)

**The posture inversion vs ``validators.ALLOWED_SFTP_HOSTS`` — read this before editing.**
That allowlist and this module share a shape: normalize, then compare against a known
set. They have OPPOSITE failure modes, and confusing them is the one dangerous mistake
available here.

===========================  ==========================================================
``ALLOWED_SFTP_HOSTS``       this module
===========================  ==========================================================
a security boundary          a presentation filter (which rows a picker shows)
protects student PII in       decides list length; the admin can always see everything
transit                       via "Show all districts"
must fail **CLOSED** — an     must fail **OPEN** — an address we cannot reduce, or a
unrecognised host raises      district whose config is broken, yields NO match, and a
and no upload happens         no-match means the FULL unfiltered list
narrowing to zero is the      narrowing to zero would hide an admin's own district from
correct outcome               them; it is the failure this design makes unrepresentable
===========================  ==========================================================

Concretely: **these functions never raise and never narrow to zero.** They are total over
any string — including bytes an admin pasted from a mail client, a corrupted stored value,
and deliberately hostile input. Consumers fail open on top of that (`test_identity.py`
pins totality; the catalog layer in S5 pins the fail-open list rule).

Validation belongs at the BOUNDARY, not here: ``validators.validate_identity_email``
rejects a malformed typed address loudly before it is normalised or persisted. This
module is what runs afterwards, and on values already on disk — so it must cope with
anything, forever.
"""

from __future__ import annotations

import unicodedata

__all__ = ["extract_domain", "normalize_email"]

_AT = "@"


def normalize_email(raw: str) -> str:
    """Reduce an email address to its comparable form. TOTAL — never raises.

    The reduction, and every judgment call in it (each pinned by a row in
    ``tests/test_identity.py::_NORMALIZE_TABLE``):

    1. **Trim** surrounding whitespace — a value pasted from a mail client or a
       spreadsheet routinely carries a trailing space or newline.
    2. **Lowercase** the WHOLE address. RFC 5321 makes the local part technically
       case-sensitive, but no mail system in this product's world treats it that way, and
       an admin who types ``First.Last@…`` must match a district row written in lowercase.
       (The address the admin sees echoed back is the un-normalised one the boundary
       validator returned — see ``validators.validate_identity_email``.)
    3. **NFC-normalise.** The same accented name typed on macOS (decomposed) and on
       Windows (precomposed) is different bytes for an identical address; without this
       they would compare unequal. NFC runs AFTER lowercasing so the result is idempotent
       (lowercasing can itself decompose a character).
    4. **Plus-addressing is KEPT verbatim.** ``name+roster@district.ca`` is that person's
       address; stripping the tag would silently rewrite the value we store, echo on the
       Help surface, and would one day mail. Deliberately NOT a Gmail-style canonicaliser.
    5. **Exactly ONE trailing dot is stripped from the DOMAIN part** — the DNS
       fully-qualified root dot (``district.ca.`` and ``district.ca`` are the same
       domain). A second dot is malformed, is left alone, and simply never matches; the
       boundary validator is what rejects it loudly. With no ``@`` there is no domain
       part, so nothing is stripped.

    A value with more than one ``@`` is left structurally intact — deciding which ``@``
    "counts" is :func:`extract_domain`'s job (the final one wins), not normalisation's.
    """
    value = unicodedata.normalize("NFC", raw.strip().lower())
    local, at, domain = value.rpartition(_AT)
    if at and domain.endswith("."):
        return f"{local}{at}{domain[:-1]}"
    return value


def extract_domain(normalized: str) -> str:
    """Return the domain part of an already-normalised address. TOTAL — never raises.

    The part after the **FINAL** ``@``; the empty string when there is no ``@`` or when
    nothing follows it. Taking the final ``@`` is the only defensible reading of a
    malformed multi-``@`` value, and it is also the conservative one: the resulting string
    is compared for EXACT equality against a district's published domain, so a weird
    value simply matches nothing and the admin gets the full list.

    Takes an already-normalised value by contract — the sanctioned call is
    ``extract_domain(normalize_email(raw))``. It does not normalise on your behalf,
    because a function that silently normalised would hide a caller that forgot to.

    **Matching is EXACT, case-normalised string equality — never subdomain, suffix, or
    wildcard.** ``mail.sd48.bc.ca`` does NOT match ``sd48.bc.ca``. Suffix matching is the
    dangerous direction under fail-open (it over-matches, scoping an admin into a district
    that is not theirs), whereas exact matching under-matches into the FULL list, which is
    always safe. A district that needs a second domain or a subdomain gets it as its own
    row in ``district_domains``.
    """
    _local, at, domain = normalized.rpartition(_AT)
    return domain if at else ""
