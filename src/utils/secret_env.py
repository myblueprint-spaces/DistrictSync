"""The environment variables that can carry a password, named ONCE (DSYNC-116 review M5).

``main`` reads them (``--sftp-configure``, ``--schedule-register``, ``--migrate --apply``); a child process this app
starts must never inherit them, so a child's environment is built with :func:`scrubbed_environ`.
"""

from __future__ import annotations

import os

SFTP_PASSWORD_ENV_VAR = "DISTRICTSYNC_SFTP_PASSWORD"  # nosec B105 - the NAME of the variable, not a secret
TASK_PASSWORD_ENV_VAR = "DISTRICTSYNC_TASK_PASSWORD"  # nosec B105 - the NAME of the variable, not a secret
SECRET_ENV_VARS: tuple[str, ...] = (SFTP_PASSWORD_ENV_VAR, TASK_PASSWORD_ENV_VAR)


def scrubbed_environ() -> dict[str, str]:
    """This process's environment without any password variable: what a child is given."""
    return {key: value for key, value in os.environ.items() if key.upper() not in SECRET_ENV_VARS}
