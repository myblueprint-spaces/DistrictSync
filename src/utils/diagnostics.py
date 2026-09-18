"""``DistrictSync --diagnose`` — the read-only support report (plan 0049 D7 / S-1b-ii.3).

The evidence instrument for a machine-scoped install: what profile this computer resolved,
which scope, what the HKLM switch says, what the shared folder's owner/DACL look like, which
delivery-secret store is selected and whether it opens for the configured account, what the
Task Scheduler reads back, and the last run in the ledger.

**It is NOT "PII-free", and the copy must not claim it is** (this corrects D7's own
over-claim). It names Windows account names, the resolved profile path — which embeds the
signed-in username — and the SFTP username. What it does NOT print, in any state, is a
secret: not the delivery password, not the task password, not a DPAPI blob. The header says
exactly that: *carries no passwords — it does name Windows accounts and folders, so treat it
like a log.*

**Every profile-resolving line is guarded on its own.** This command exists for the state
where ``user_data_dir()`` REFUSES, and on that install ``read_run_records()`` →
``user_history_db()`` → ``user_data_dir()`` re-raises the refusal — so one shared try block
would print a header and nothing else. A line that cannot be resolved says so, names the
bounded reason where there is one, and the next line still prints.

It deliberately reads two of ``paths``' private seams (``_override_data_dir``,
``_read_dir_security``). A diagnostic's job is to report the ladder's OWN inputs, and
promoting a seam to public API so a REPORT can print it would grow a surface every other
consumer could then build on. Contrast ``paths.machine_switch_on()``, which is public
because :mod:`src.scheduler.provision_session` makes a DECISION on it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import IO

from src.utils import paths
from src.utils.accounts import process_account
from src.utils.version import app_version

# The grep anchor, in the bracketed form ``main._report_machine_scope_refusal`` established:
# a district's support mail is searched for one thing, and this is it.
REPORT_ANCHOR = "[districtsync-diagnose]"

# The honesty line. NOT "no personal information" — see the module docstring.
PRIVACY_NOTE = "This report carries no passwords. It does name Windows accounts and folders, so treat it like a log."

# ``_sftp_show``'s aligned-label block, one width for the whole report.
_LABEL_WIDTH = 22

# The two display values the elevated commit writes beside the switch. Spelled here because
# ``provisioning._commit_machine_switch`` is the writer and there is no shared constant yet;
# a reader that invented different names would silently print "not set" forever, so the
# parity is pinned by a test instead.
_PROVISIONED_AT = "ProvisionedAt"
_PROVISIONED_BY = "ProvisionedBy"
#: Report order, which is NOT the order :func:`machine_scope_provenance` returns them in. The
#: two are spelled once, above, and every consumer picks BY NAME: reading this tuple
#: positionally is what silently swapped the pair once already, and the swap was invisible
#: because the degraded copy it produced is also the correct answer on every other install.
_HKLM_DISPLAY_VALUES = (_PROVISIONED_AT, _PROVISIONED_BY)


def _row(label: str, value: object) -> str:
    """One aligned ``  label:      value`` row — ``_sftp_show``'s block, single-sourced.

    The pad is ``max(..., 1)`` rather than a bare ``ljust``: a label longer than the column
    would otherwise butt straight against its value (``recorded run-as:CORP\\svc``), which is
    unreadable in exactly the report an admin is asked to paste into a support mail.
    """
    stem = f"{label}:"
    return f"  {stem}{' ' * max(_LABEL_WIDTH - len(stem), 1)}{value}"


def _guarded(label: str, resolve: Callable[[], object]) -> str:
    """One aligned row whose value is resolved behind its OWN guard.

    Broad by design, and not a swallow: this command's whole job is to run where everything
    else refuses, so an unresolvable line REPORTS its failure and the report continues. A
    machine-scope refusal renders its bounded reason (the fact the reader came for);
    anything else renders its exception class, which is bounded too — never a message that
    could carry something this report has promised not to print.
    """
    try:
        return _row(label, resolve())
    except paths.MachineScopeRefused as exc:
        return _row(label, f"unavailable — shared profile refused: {exc.reason.value}")
    except Exception as exc:  # noqa: BLE001 - see the docstring: reported, never swallowed
        return _row(label, f"unavailable ({type(exc).__name__})")


def _yes_no(value: object) -> str:
    return "yes" if value else "no"


def read_hklm_values() -> dict[str, object]:  # pragma: no cover - Windows-only registry read
    """Read every value under ``HKLM\\SOFTWARE\\DistrictSync``. The raw syscall seam.

    Isolated exactly like :func:`src.utils.paths._read_machine_switch_value`, so the report
    that renders it is testable on every OS. Opened with the EXPORTED
    :data:`src.utils.paths.MACHINE_SCOPE_KEY_ACCESS` — a read in the redirected 32-bit view
    would answer about ``WOW6432Node\\DistrictSync``, i.e. a different key entirely, which
    is precisely the class of mistake a diagnostic exists to expose rather than reproduce.

    Raises ``FileNotFoundError`` when the key is absent (the normal state on every install
    in the field today) and ``OSError`` on a real read failure.

    The ``sys.platform`` guard is what lets a type-checker running on Linux skip this body:
    ``winreg``'s typeshed stubs mark every attribute Windows-only.
    """
    if sys.platform != "win32":
        raise FileNotFoundError("the machine-scope key is Windows-only")

    import winreg

    values: dict[str, object] = {}
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, paths.MACHINE_SCOPE_KEY_PATH, 0, paths.MACHINE_SCOPE_KEY_ACCESS
    ) as key:
        for name in (paths.MACHINE_SCOPE_VALUE_NAME, *_HKLM_DISPLAY_VALUES):
            try:
                values[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                continue
    return values


def machine_scope_provenance() -> tuple[str, str]:
    """``(ProvisionedBy, ProvisionedAt)`` for display, ``("", "")`` when either is unavailable.

    The UI's seam onto the two display values, routed through :func:`read_hklm_values` rather
    than a second ``winreg`` call so the value NAMES are spelled exactly once in this process
    (:data:`_HKLM_DISPLAY_VALUES`). A reader that invented its own spelling would render "set up
    by  on " forever and no test would notice.

    TOTAL against ANY exception, and that breadth is the point rather than laziness: this is
    advisory copy resolved at MOUNT on Home and on Settings, so a raise here does not degrade a
    sentence — it drops both surfaces to their ``ErrorCard`` and takes the verdict with it. The
    conservative answer is ``("", "")``, which lands ``home_status.machine_scope_line`` on a form
    that CLAIMS no provenance.

    ``OSError`` alone was not enough, measured: ``read_hklm_values`` guards on ``sys.platform``
    and then does ``import winreg``, so a caller that patches the platform on a non-Windows host
    raises ``ModuleNotFoundError`` — an ``ImportError``, outside ``OSError`` entirely. That is not
    a hypothetical: it reddened CI's Linux leg through three Settings tests and two Home ones,
    while the Windows leg stayed green.
    """
    try:
        values = read_hklm_values()
    except Exception:  # noqa: BLE001 - see the docstring: a mount may never fall over display copy
        return ("", "")

    def _text(name: str) -> str:
        value = values.get(name)
        return value.strip() if isinstance(value, str) else ""

    return (_text(_PROVISIONED_BY), _text(_PROVISIONED_AT))


def _hklm_lines() -> list[str]:
    lines = [f"Shared-settings switch (HKLM\\{paths.MACHINE_SCOPE_KEY_PATH}):"]
    try:
        values = read_hklm_values()
    except FileNotFoundError:
        return [*lines, _row("key", "not present (this install is per-account)")]
    except OSError as exc:
        return [*lines, _row("key", f"unreadable ({type(exc).__name__})")]
    for name in (paths.MACHINE_SCOPE_VALUE_NAME, *_HKLM_DISPLAY_VALUES):
        lines.append(_row(name, values.get(name, "not set")))
    return lines


def _shared_folder_lines() -> list[str]:
    """Owner / reparse / DACL summary for the shared folder, through the app's OWN predicate.

    An admin does not need the control word; they need to know whether DistrictSync would
    accept the folder, and if not, why. So the summary is the trust predicate's verdict plus
    its bounded reason — the same answer the app acts on, never a second opinion.
    """
    lines = ["Shared folder:"]
    try:
        root = paths.machine_data_dir()
    except paths.MachineScopeRefused as exc:
        return [*lines, _row("path", f"unavailable — {exc.reason.value}")]
    except Exception as exc:  # noqa: BLE001 - a diagnostic never dies on a line
        return [*lines, _row("path", f"unavailable ({type(exc).__name__})")]

    exists = root.is_dir()
    lines.append(_row("path", root))
    lines.append(_row("exists", _yes_no(exists)))
    try:
        paths.assert_machine_dir_trusted(root)
    except paths.MachineScopeRefused as exc:
        lines.append(_row("trusted", f"no — {exc.reason.value}"))
    except Exception as exc:  # noqa: BLE001
        lines.append(_row("trusted", f"unknown ({type(exc).__name__})"))
    else:
        lines.append(_row("trusted", "yes (owner, inheritance and permissions all check out)"))
    if exists:
        # Only asked of a folder that is there: on a per-account install this path does not
        # exist at all, and an "unavailable (OSError)" row for a folder we just reported as
        # absent is noise in a report an admin has to read.
        lines.append(_guarded("owner SID", lambda: paths._read_dir_security(root)[0]))
    lines.append(_guarded("run store would be", lambda: paths.history_db_in(root, machine_scope=True)))
    return lines


def _profile_lines() -> list[str]:
    return [
        "Profile:",
        _guarded("data dir", paths.user_data_dir),
        _guarded("scope", lambda: "shared (this computer)" if paths.is_machine_scope() else "this account only"),
        _guarded("this account", process_account),
        _guarded("DISTRICTSYNC_DATA_DIR", lambda: paths._override_data_dir() or "not set"),
        _guarded("log file", paths.user_log_file),
        _guarded("run store", paths.user_history_db),
        _guarded("handshake dir", paths.handshake_dir),
    ]


def _delivery_lines() -> list[str]:
    """Which secret store is selected, and whether it opens for the CONFIGURED account.

    ``has_secret`` is the identity-match boolean: both stores bind the secret to
    ``{host, username}``, so "a secret exists" and "the secret belongs to the delivery
    account configured right now" are different questions — and only the second one means
    the nightly will deliver.
    """
    lines = ["Delivery (SFTP):"]
    try:
        from src.config.app_config import AppConfig
        from src.sftp.secret_store import MachineSecretStore, select_store

        cfg = AppConfig.load()
        store = select_store()
    except Exception as exc:  # noqa: BLE001 - a diagnostic never dies on a line
        return [*lines, _row("status", f"unavailable ({type(exc).__name__})")]

    machine = isinstance(store, MachineSecretStore)
    lines.append(_row("store", "shared file (sftp_secret.bin)" if machine else "this account's Windows keyring"))
    lines.append(_row("configured", _yes_no(cfg.sftp_enabled)))
    lines.append(_row("host", cfg.sftp_host or "not set"))
    lines.append(_row("username", cfg.sftp_username or "not set"))
    if machine:
        lines.append(_guarded("secret file", lambda: _yes_no(store.secret_path().is_file())))  # type: ignore[attr-defined]
    if cfg.sftp_host and cfg.sftp_username:
        lines.append(_guarded("identity match", lambda: _yes_no(store.has_secret(cfg.sftp_host, cfg.sftp_username))))
    else:
        # NOT asked with a blank identity. ``UserSecretStore`` deliberately ignores the host
        # and the Windows backend is loose about a blank username, so the question has no
        # meaningful answer here — and "yes" against unconfigured delivery is a worse answer
        # than none. (Noted in ROADMAP: the predicate itself should refuse a blank identity.)
        lines.append(_row("identity match", "n/a (delivery is not configured)"))
    return lines


def _schedule_lines() -> list[str]:
    """The Task Scheduler read-back, plus the principal DistrictSync has on record.

    The RECORDED principal, explicitly labelled as such: ``ScheduleReadback`` does not carry
    the task's own ``UserId`` yet (that is plan 0049 S-3), and a report that printed the
    record under a "runs as" heading would be asserting something it never read.
    """
    lines = ["Nightly schedule:"]
    try:
        from src.config.app_config import AppConfig
        from src.scheduler.windows import read_schedule

        cfg = AppConfig.load()
        readback = read_schedule(cfg.schedule_task_name)
    except Exception as exc:  # noqa: BLE001 - a diagnostic never dies on a line
        return [*lines, _row("status", f"unavailable ({type(exc).__name__})")]

    found = {True: "live", False: "not scheduled", None: "could not be read"}[readback.found]
    lines.append(_row("task name", cfg.schedule_task_name))
    lines.append(_row("read-back", found))
    lines.append(_row("next run", readback.next_run or "-"))
    lines.append(_row("last run", readback.last_run or "-"))
    lines.append(_row("last result", "-" if readback.last_result is None else hex(readback.last_result)))
    lines.append(_row("recorded run-as", cfg.schedule_run_as_user or "(the signed-in account)"))
    lines.append(_row("recorded", f"registered = {_yes_no(cfg.schedule_registered)}"))
    return lines


def _last_run_lines() -> list[str]:
    lines = ["Last run recorded:"]
    try:
        from src.history.store import read_run_records

        records = read_run_records(limit=1)
    except Exception as exc:  # noqa: BLE001 - a diagnostic never dies on a line
        return [*lines, _row("status", f"unavailable ({type(exc).__name__})")]

    if records is None:
        return [*lines, _row("status", "the run store could not be read")]
    if not records:
        return [*lines, _row("status", "no runs recorded yet")]
    record = records[0]
    for label, key in (
        ("when", "created_at"),
        ("status", "status"),
        ("source", "source"),
        ("ran as", "run_as"),
        ("district", "sis_type"),
        ("problem", "error_category"),
    ):
        lines.append(_row(label, record.get(key) or "-"))
    return lines


def diagnose_lines() -> list[str]:
    """The whole report, as lines. Pure enough to assert on; never raises."""
    return [
        f"DistrictSync diagnose {REPORT_ANCHOR}",
        PRIVACY_NOTE,
        "",
        "Version:",
        _guarded("version", app_version),
        _row("platform", sys.platform),
        _row("packaged exe", _yes_no(getattr(sys, "frozen", False))),
        "",
        *_profile_lines(),
        "",
        *_hklm_lines(),
        "",
        *_shared_folder_lines(),
        "",
        *_delivery_lines(),
        "",
        *_schedule_lines(),
        "",
        *_last_run_lines(),
    ]


def run_diagnose(out: IO[str] | None = None) -> int:
    """Print the report and return the documented exit code **0**.

    Always 0, in every state: this command reports a broken install, it does not fail with
    one. A support instruction that says "run this and send me the output" must not have the
    admin also interpreting an exit code.
    """
    stream = out if out is not None else sys.stdout
    for line in diagnose_lines():
        print(line, file=stream)
    return 0
