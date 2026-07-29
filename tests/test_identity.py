"""Identity primitives — the pinned normalize / extract-domain edge tables.

``src/utils/identity.py`` is the ONE place "what does this email address reduce to?"
is decided. Every judgment call it makes (case, whitespace, Unicode form,
plus-addressing, a trailing DNS root dot, more than one ``@``, no ``@`` at all) is
pinned here ONCE so a later edit cannot quietly widen or narrow matching.

The posture is the inverse of ``validators.ALLOWED_SFTP_HOSTS``: same
normalize-then-compare shape, OPPOSITE failure mode. A host that fails to normalize
must fail CLOSED (no connection); an identity that fails to normalize must fail OPEN
(the admin still gets in, unfiltered). So these functions may never raise — pinned by
``test_normalize_is_total_over_hostile_input``.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.utils.identity import extract_domain, normalize_email

# Unicode fixtures. These two literals hold GENUINELY different byte sequences (NFD vs
# NFC) — an editor or formatter that "tidies" this file into one form would make the NFC
# test vacuous, so the test asserts they differ BEFORE asserting they normalise together.
_E_ACUTE_DECOMPOSED = "josé@sd48.bc.ca"  # e + COMBINING ACUTE ACCENT (NFD form)
_E_ACUTE_PRECOMPOSED = "josé@sd48.bc.ca"  # LATIN SMALL LETTER E WITH ACUTE (NFC form)

# --------------------------------------------------------------------------- #
# normalize_email — the decided-and-pinned reductions                          #
# --------------------------------------------------------------------------- #
# (raw, expected, why) — every row is a DECISION, not an observation.
_NORMALIZE_TABLE: tuple[tuple[str, str, str], ...] = (
    ("admin@sd48.bc.ca", "admin@sd48.bc.ca", "already normal - unchanged"),
    ("Admin@SD48.BC.CA", "admin@sd48.bc.ca", "case-insensitive: whole address lowercased"),
    ("  admin@sd48.bc.ca  ", "admin@sd48.bc.ca", "surrounding whitespace trimmed"),
    ("\tadmin@sd48.bc.ca\r\n", "admin@sd48.bc.ca", "tabs/newlines are whitespace too"),
    # DECISION: plus-addressing is KEPT verbatim. A district contact who uses
    # `name+roster@district.ca` IS that address; stripping the tag would silently
    # rewrite the address we store and echo back on the Help surface.
    ("admin+roster@sd48.bc.ca", "admin+roster@sd48.bc.ca", "plus-addressing kept, never stripped"),
    ("Admin+Roster@SD48.BC.CA", "admin+roster@sd48.bc.ca", "plus tag lowercased with the rest"),
    # DECISION: exactly ONE trailing dot is stripped from the DOMAIN part - the DNS
    # fully-qualified root dot. Anything beyond one is malformed and left alone (the
    # boundary validator rejects it; matching then simply never fires).
    ("admin@sd48.bc.ca.", "admin@sd48.bc.ca", "a single trailing root dot is stripped"),
    ("admin@sd48.bc.ca..", "admin@sd48.bc.ca.", "only ONE trailing dot is stripped"),
    ("admin@sd48.bc.ca...", "admin@sd48.bc.ca..", "still only one"),
    # DECISION: no `@` means there is no domain part, so no dot stripping happens.
    ("admin.", "admin.", "no @ so no domain part so the trailing dot is left alone"),
    ("ADMIN", "admin", "no @ still lowercases/trims (total, never raises)"),
    # DECISION: more than one `@` is left structurally intact - `extract_domain`
    # takes the part after the FINAL one; deciding which `@` "counts" is not
    # normalisation's job.
    ("A@B@SD48.BC.CA", "a@b@sd48.bc.ca", "multiple @ left intact, lowercased"),
    ("a@b@sd48.bc.ca.", "a@b@sd48.bc.ca", "root dot still stripped after the final @"),
    ("", "", "empty stays empty"),
    ("     ", "", "whitespace-only reduces to empty"),
    ("@sd48.bc.ca", "@sd48.bc.ca", "empty local part is not normalisation's problem"),
    ("admin@", "admin@", "empty domain part is not normalisation's problem"),
)


@pytest.mark.parametrize(("raw", "expected", "why"), _NORMALIZE_TABLE, ids=[row[2] for row in _NORMALIZE_TABLE])
def test_normalize_email_table(raw: str, expected: str, why: str) -> None:
    assert normalize_email(raw) == expected, why


def test_normalize_email_nfc_composes_decomposed_accents() -> None:
    """NFC: ``e`` + COMBINING ACUTE and precomposed ``e-acute`` reduce to the same value.

    Two admins typing the same name on a Mac and on Windows can produce different byte
    sequences for an identical address; without NFC they would compare unequal.
    """
    assert _E_ACUTE_DECOMPOSED != _E_ACUTE_PRECOMPOSED  # the inputs really do differ
    assert normalize_email(_E_ACUTE_DECOMPOSED) == _E_ACUTE_PRECOMPOSED
    assert normalize_email(_E_ACUTE_DECOMPOSED) == normalize_email(_E_ACUTE_PRECOMPOSED)


# Every table row EXCEPT the deliberately-malformed multi-dot ones, which the boundary
# validator rejects and which therefore can never reach storage. See
# ``test_multi_dot_is_deliberately_not_idempotent`` for why that non-idempotence is the
# SAFE choice rather than an oversight.
_STORABLE_RAWS = tuple(row[0] for row in _NORMALIZE_TABLE if not row[0].endswith(".."))


@pytest.mark.parametrize("raw", _STORABLE_RAWS)
def test_normalize_email_is_idempotent(raw: str) -> None:
    """``normalize(normalize(x)) == normalize(x)`` for every value that can be STORED.

    Load-bearing: a persisted ``identity_email`` is re-normalised on every read (each
    screen re-loads ``AppConfig``), so a non-idempotent reduction would let the same saved
    value match on one paint and not on the next.
    """
    once = normalize_email(raw)
    assert normalize_email(once) == once


@pytest.mark.parametrize("malformed", ["admin@sd48.bc.ca..", "admin@sd48.bc.ca..."])
def test_multi_dot_is_deliberately_not_idempotent(malformed: str) -> None:
    """A DECLARED, deliberate exception — and the safe direction.

    Stripping every trailing dot would make ``normalize`` fully idempotent, at the cost of
    silently repairing a malformed value into one that MATCHES a real district. Over-
    normalising in the matching direction is the dangerous error; under-normalising just
    means no match, which means the full unfiltered list. So exactly one dot is stripped,
    the residue never matches anything, and the boundary validator refuses the value long
    before it could be stored — pinned by the companion assertion below.
    """
    once = normalize_email(malformed)
    assert normalize_email(once) != once  # the declared non-idempotence, asserted not assumed

    from src.utils.validators import validate_identity_email

    with pytest.raises(ValueError):
        validate_identity_email(malformed)  # ...so it can never reach storage


@pytest.mark.parametrize(
    "hostile",
    [
        "\x00\x01\x02",
        "a" * 5000 + "@x.ca",
        "‮" + "admin@sd48.bc.ca",  # RIGHT-TO-LEFT OVERRIDE
        "@@@@",
        "�￿",
        "admin@sd48.bc.ca\x7f",
        "\U0001f600@sd48.bc.ca",
    ],
)
def test_normalize_is_total_over_hostile_input(hostile: str) -> None:
    """TOTAL - the posture inversion vs ``ALLOWED_SFTP_HOSTS``: this must never raise.

    An identity filter that raises fails CLOSED, which is the one thing this layer is
    forbidden to do (the admin would be locked out of a product that has no accounts).
    """
    assert isinstance(normalize_email(hostile), str)


# --------------------------------------------------------------------------- #
# extract_domain — the part after the FINAL @                                  #
# --------------------------------------------------------------------------- #
_DOMAIN_TABLE: tuple[tuple[str, str, str], ...] = (
    ("admin@sd48.bc.ca", "sd48.bc.ca", "the ordinary case"),
    ("a@b@c.ca", "c.ca", "the FINAL @ wins, never the first"),
    ("nope", "", "no @ means empty string, never a raise"),
    ("", "", "empty means empty"),
    ("admin@", "", "empty domain part means empty"),
    ("@x.ca", "x.ca", "empty local part still yields the domain"),
    ("admin@sd48.bc.ca.", "sd48.bc.ca.", "operates on the value it is GIVEN - normalize first"),
)


@pytest.mark.parametrize(("value", "expected", "why"), _DOMAIN_TABLE, ids=[row[2] for row in _DOMAIN_TABLE])
def test_extract_domain_table(value: str, expected: str, why: str) -> None:
    assert extract_domain(value) == expected, why


@pytest.mark.parametrize("hostile", ["\x00", "@" * 100, "‮@x.ca"])
def test_extract_domain_is_total(hostile: str) -> None:
    """TOTAL for the same fail-open reason as ``normalize_email``."""
    assert isinstance(extract_domain(hostile), str)


def test_extract_domain_composes_with_normalize() -> None:
    """The sanctioned call shape: ``extract_domain(normalize_email(raw))``."""
    assert extract_domain(normalize_email("  Admin@SD48.BC.CA.  ")) == "sd48.bc.ca"


def test_a_subdomain_is_a_different_domain() -> None:
    """Exact-equality semantics at the primitive layer (plan 0038, R3 item 3a).

    ``mail.sd48.bc.ca`` is NOT ``sd48.bc.ca``. Matching compares these strings for
    equality - never suffix/wildcard - so an unmatched subdomain simply falls back to
    the full unfiltered list (the safe direction under fail-open). A district needing a
    second domain gets its own list row.
    """
    assert extract_domain(normalize_email("admin@mail.sd48.bc.ca")) == "mail.sd48.bc.ca"
    assert extract_domain(normalize_email("admin@mail.sd48.bc.ca")) != "sd48.bc.ca"


def test_no_hashing_apparatus_in_the_module() -> None:
    """The retired hash apparatus (plan 0038 flag 8) must not creep back in.

    Matching compares PUBLIC organisational domains; there is no secret to protect, so a
    salt or a digest would be security theatre that also makes the shipped values
    untestable. Pinned STRUCTURALLY (an AST import/name walk, not a text grep) so the
    module docstring is free to *say* "no salt, no hashing" without tripping its own pin.
    """
    import src.utils.identity as identity_module

    tree = ast.parse(inspect.getsource(identity_module))
    banned = {"hashlib", "hmac", "secrets", "base64"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in banned, f"{alias.name} imported by src/utils/identity.py"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in banned, f"{node.module} imported by src/utils/identity.py"
        elif isinstance(node, ast.Name):
            assert node.id not in banned, f"{node.id} referenced in src/utils/identity.py"
