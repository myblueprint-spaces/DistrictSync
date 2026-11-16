"""Where DistrictSync's update feed is, and which signing keys it trusts (plan 0054 S4, DSYNC-63).

Both are EMPTY until the owner's Azure steps are done: the feed host name is not chosen yet, and the
manifest signing key is not created. With either empty, ``production_target()`` returns ``None`` and
the update check opens no connection at all (reason ``not_configured``). So merging this slice changes
nothing for a district, and filling these two values in is the single act that switches checking on.

The host comes from ``src.utils.validators.ALLOWED_UPDATE_HOSTS`` (one place, in the style of
``ALLOWED_SFTP_HOSTS``) so a host can never be reached that the validator does not name. There is NO
environment variable, setting or command-line option that points the release build at another host or
another key: a test passes its own ``FeedTarget`` to the check function, and production call sites never do.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from src.utils.validators import ALLOWED_UPDATE_HOSTS

#: Trusted manifest public keys, ``key_id`` -> uncompressed SEC1 P-256 point (65 bytes). Two slots
#: (current, next) so a rotation is a release and not a redesign (plan 0054 R12). Empty until the key exists.
TRUSTED_KEYS: Final[Mapping[str, bytes]] = {}

#: Path of the signed manifest on the feed host. The layout is fixed by the plan (section 5): a flat
#: container with ``manifest.json`` at its root, uploaded last.
MANIFEST_PATH: Final = "/manifest.json"
CHANNEL: Final = "stable"


@dataclass(frozen=True)
class FeedTarget:
    """Everything a check needs to know about the feed. Frozen: nothing edits it after construction."""

    host: str
    trusted_keys: Mapping[str, bytes]
    manifest_path: str = MANIFEST_PATH
    channel: str = CHANNEL
    #: A test may reach a loopback http feed. Production never sets it.
    allow_loopback_http: bool = False


def production_target() -> FeedTarget | None:
    """The configured feed, or ``None`` while the host or the key is not set (no connection is made)."""
    if len(ALLOWED_UPDATE_HOSTS) != 1 or not TRUSTED_KEYS:
        return None
    (host,) = ALLOWED_UPDATE_HOSTS
    return FeedTarget(host=host, trusted_keys=TRUSTED_KEYS)
