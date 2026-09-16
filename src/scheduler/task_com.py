"""Task Scheduler COM engine — the in-process replacement for PowerShell transport (plan 0041).

Windows-private: every ``win32com``/``pythoncom`` import in this module is LAZY (inside a
function, behind the caller's ``sys.platform`` guard) because ``src/scheduler/__init__.py``
imports ``windows`` — and therefore this module — at module level **on every OS**, including
the Linux and macOS CI legs. A top-level pywin32 import here would break every non-Windows
import chain in the repo. Pinned by a ``sys.modules`` assert that runs on Linux CI.

**Why COM at all** (ROADMAP ``[AV / DISTRIBUTION]``, fix 3): ``powershell.exe
-EncodedCommand <base64>`` creating scheduled tasks is the textbook malware-persistence
signature, explicitly weighted by Defender ASR and Bitdefender ATC — which blocked this
product live on 2026-08-04. The Task Scheduler COM API is the same service the PowerShell
cmdlets drive, minus the child process, the base64, and the console flash. Slice 1a moves
the READ and DELETE paths (the highest-frequency spawns — the read fires on nearly every
nav click); registration follows in Slice 1b.

**Dispatch discipline:** dynamic ``win32com.client.Dispatch`` ONLY. ``EnsureDispatch`` /
``gencache`` / ``makepy`` are banned (absence-pinned in the tests): they write generated
code into a cache directory and import it at runtime — the exact frozen-exe cache failure
that disqualified ``comtypes``, and itself an AV-shaped behaviour (runtime code-gen +
import) in a plan whose whole purpose is removing AV-shaped behaviours.

**Nothing COM-shaped escapes this module.** Callers receive plain Python: a
:class:`TaskFacts` (str/int/None fields) or a :class:`TaskComError` (scode + canonical
message). This is a hard boundary for three live-probed reasons (2026-08-05):

* **The wrapped-HRESULT trap:** ``pythoncom.com_error.hresult`` for a missing task is
  ``0x80020009`` (``DISP_E_EXCEPTION`` — the generic IDispatch wrapper); the REAL status
  lives in ``excepinfo[5]`` (observed ``-2147024894`` == ``0x80070002``). Classification
  keyed on the outer ``hresult`` would make "definitively absent" unreachable and every
  missing task read as UNKNOWN — the D4 tri-state's one forbidden failure mode.
* **Exception tracebacks pin COM objects past teardown:** a raising ``com_error`` carries
  frames whose locals reference the service/folder/task interfaces; releasing those at GC
  time — after ``CoUninitialize`` — is the "Win32 exception occurred releasing IUnknown"
  failure observed live. Hence: the raise sites convert to :class:`TaskComError` ``from
  None`` and every frame's COM locals are re-bound to ``None`` in ``finally`` blocks
  before the apartment closes.
* **pywin32 mislabels local wall-clock as UTC:** a 03:00 *local* trigger reads back as
  ``03:00+00:00``. The bogus tzinfo is stripped — datetimes are emitted as NAIVE local
  ISO strings, matching the naive-local run-record timestamps the contradiction check
  compares against (an offset-bearing string would skew that comparison by the timezone
  delta, and a mixed aware/naive comparison raises).

**Threading:** COM apartments are per-thread. :func:`bounded` runs the COM work on a fresh
daemon worker that does its OWN ``CoInitialize``/``CoUninitialize``; the caller only ever
joins with a timeout. A hung Task Scheduler RPC therefore leaks one logged daemon thread
instead of wedging the UI thread or leaving a killed-subprocess zombie — the recorded row-8
trade in plan 0041.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from src.scheduler.messages import carries_foreign_marker

logger = logging.getLogger(__name__)

# The Task Scheduler root folder — tasks are registered at "\" by bare name (contract
# row 12 in plan 0041; read-back and delete stop finding the task if this ever drifts).
ROOT_FOLDER = "\\"

# --- HRESULTs this module classifies (unsigned) -------------------------------------
# The definitive "task does not exist" status: ERROR_FILE_NOT_FOUND as an HRESULT.
# The ONLY status allowed to produce found=False / an idempotent-success delete.
HR_NOT_FOUND = 0x80070002
HR_ACCESS_DENIED = 0x80070005
# ERROR_NO_SUCH_LOGON_SESSION as an HRESULT. Windows returns it when a credential-storing
# registration cannot obtain a logon session — Microsoft documents the "Network access: Do not
# allow storage of passwords and credentials for network authentication" hardening policy as a
# cause elsewhere. The INFERENCE belongs in the UI classifier's hedged copy, never here — it
# lives in `setup_errors.classify_schedule_error`'s branch for this canonical, which names the
# setting and says Microsoft DOCUMENTS it, never that it is what happened.
HR_NO_SUCH_LOGON_SESSION = 0x80070520
HR_LOGON_FAILURE = 0x8007052E  # ERROR_LOGON_FAILURE — bad user/password at registration
# ERROR_NONE_MAPPED as an HRESULT: the run-as account NAME is not a principal Windows can
# resolve. MEASURED 2026-09-16 on this exact COM path (plan 0046, Investigations M1) with a
# deliberately bogus account name and a junk password: it is DISTINCT from a wrong password
# (0x8007052E), which is why it earns its own canonical and its own classifier branch — dropping
# a name typo into the credential branch loops the admin on the password. The same probe recorded
# `excepinfo[2] == "(21,8):UserId:"` — a COM FIELD LOCATOR, not prose, which is a second argument
# for mapping it rather than letting the guarded description pass through.
HR_NONE_MAPPED = 0x80070534
HR_ACCOUNT_INFO_NOT_SET = 0x8004130F  # SCHED_E_ACCOUNT_INFORMATION_NOT_SET

# SCHED_S_TASK_HAS_NOT_RUN — LastTaskResult of a task that has never fired.
RESULT_HAS_NOT_RUN = 267011

# Canonical, secret-free, locale-independent text per HRESULT — DESCRIPTIVE of the status
# Windows returned, never a cause. `setup_errors.classify_schedule_error` keys on these by
# EXACT equality and IMPORTS them (plan 0047 A2) — any edit here must be mirrored there,
# because a re-worded canonical silently moves a branch to the unclassified fallback. The
# cause copy is the classifier's, hedged to its evidence; this module states the STATUS only.
#
# RULE (pinned; docs/claugentic-INVARIANTS.md): no string this module can RETURN — a table
# value, Windows' own description, or `str(exc)` — may carry a marker another consumer owns
# (`messages.ABSENT_TASK_MARKERS` / `ACCESS_DENIED_MARKERS` / `SECRET_SENTINEL_PREFIX`) unless
# it IS that code's canonical; and every `MSG_`/`_MSG_`-NAMED binding in `task_com` / `windows` /
# `elevated_apply` has exactly ONE name (the injectivity sweep's reach). An UNNAMED return — a
# guarded description plus its code, or the coded generic — is outside that sweep by
# construction, and is model-upheld rather than pinned.
#
# Two owned contracts: "Access is denied." carries the substring the adapter's elevated-retry predicate
# matches (src/scheduler/__init__.py), and HR_NOT_FOUND's text carries "cannot find" so
# `schedule_status.interpret_unregister` stays idempotent-success-shaped on an already-absent
# task. Windows' OWN text for 0x80070520 reads "…does not exist…" — which is exactly why that
# code's canonical restates the status in other words, and why the description pass-through
# below is guarded. Mapping is BY HRESULT, never by Windows' locale-dependent FormatMessage
# text — which retires the implicit en-only assumption the PS path carried.
MSG_ACCESS_DENIED = "Access is denied."
MSG_NOT_FOUND = "The system cannot find the file specified."
MSG_LOGON_FAILURE = "The user name or password is incorrect."
# Deliberately free of every marker another consumer owns ("cannot find" / "does not exist" /
# "no such" / "access…denied" / the secret sentinel) — a marker in a message that does not own it
# turns a failure into `interpret_unregister`'s success-shaped "No schedule was registered".
MSG_ACCOUNT_NOT_RECOGNIZED = "Windows did not recognize the account name given for the task."
MSG_ACCOUNT_INFO_NOT_SET = "Windows has no saved account information for the task."
MSG_NO_LOGON_SESSION = "Windows reported that the logon session for the task registration was unavailable."
MSG_OPERATION_FAILED = "The schedule operation failed."

_HRESULT_CANONICAL: dict[int, str] = {
    HR_ACCESS_DENIED: MSG_ACCESS_DENIED,
    HR_NO_SUCH_LOGON_SESSION: MSG_NO_LOGON_SESSION,
    HR_LOGON_FAILURE: MSG_LOGON_FAILURE,
    HR_NONE_MAPPED: MSG_ACCOUNT_NOT_RECOGNIZED,
    HR_ACCOUNT_INFO_NOT_SET: MSG_ACCOUNT_INFO_NOT_SET,
    HR_NOT_FOUND: MSG_NOT_FOUND,
}
# The inverse — the ONE way a consumer recovers a code from a canonical message. Well-defined
# only while the table is injective, which the injectivity sweep pins.
_MESSAGE_TO_HRESULT: dict[str, int] = {v: k for k, v in _HRESULT_CANONICAL.items()}


def format_hresult(scode: int | None) -> str:
    """``0x%08X`` (masked — a signed int arrives from ``excepinfo``) or ``"n/a"``; total."""
    if scode is None:
        return "n/a"
    return f"0x{scode & 0xFFFFFFFF:08X}"


def hresult_for(message: str) -> int | None:
    """The HRESULT a canonical message stands for; ``None`` for anything else."""
    return _MESSAGE_TO_HRESULT.get(message)


# The pywin32-missing canonical (contract row 10) — the COM analogue of the retired
# "PowerShell not found". A frozen build that failed to bundle pywin32 must degrade to a
# readable, classifiable message, never an ImportError escaping a never-raises contract.
MSG_COM_UNAVAILABLE = "The Windows scheduling interface is not available."

# Bound for the read-back probe — SAME value the retired subprocess path used, because the
# probe fires on nav clicks and its callers were sized around it.
READ_TIMEOUT_S = 10.0

# Bound for a delete — the retired schtasks call had NO bound at all (the ROADMAP
# "Robustness" gap); generous because a delete is a click-driven action, not a nav probe.
DELETE_TIMEOUT_S = 30.0

# Bound for a registration — the retired PS subprocess had NO bound (Setup could hang with
# both buttons greyed); matches the elevation wait so the two register paths age together.
REGISTER_TIMEOUT_S = 120.0

# --- Registration constants (Slice 1b) ----------------------------------------------
# Task Scheduler 2.0 enumeration values, spelled as named constants so the S4U ban and the
# logon-type matrix are ASSERTABLE facts rather than magic numbers. TASK_LOGON_S4U (2) is
# deliberately NOT defined: S4U runs logged-off with no network token, which silently
# breaks the nightly SFTP egress — the exact regression class DECISIONS 2026-06-25 records.
TASK_ACTION_EXEC = 0
TASK_TRIGGER_DAILY = 2
TASK_CREATE_OR_UPDATE = 6
TASK_LOGON_PASSWORD = 1
TASK_LOGON_INTERACTIVE_TOKEN = 3
TASK_RUNLEVEL_LUA = 0
TASK_RUNLEVEL_HIGHEST = 1
TASK_INSTANCES_IGNORE_NEW = 2

# The FIXED past StartBoundary date — parity with the retired XML/PS registrations
# (DECISIONS 2026-06-15): a deterministic boundary that, beside StartWhenAvailable=False,
# can never interact with catch-up semantics. A today-dated boundary at an already-past
# time would lean on StartWhenAvailable to not fire immediately — reasoned, not defaulted.
TRIGGER_BOUNDARY_DATE = "2024-01-01"


@dataclass(frozen=True, repr=False)
class RegisterParams:
    """Everything one task registration needs — including, on the unattended path, the PASSWORD.

    ``repr=False`` is load-bearing, not style: a default dataclass repr would hand the
    password to any log/f-string/assert that formats the params object. The pin lives in
    ``tests/test_scheduler_runas.py``. ``run_time`` is the validated ``"HH:mm"`` string;
    ``password=None`` selects the interactive-token (logged-on-only) path.
    """

    task_name: str
    exe: str
    arguments: str
    working_dir: str
    run_time: str
    user: str
    password: str | None
    run_highest: bool


def apply_definition(service: Any, folder: Any, params: RegisterParams) -> None:
    """Build + register one task definition against LIVE or FAKE COM objects.

    The testable core (plan 0041 rows 1–2, 11–12): tests drive it with MagicMocks and
    assert the exact settings/principal/trigger/action values; the apartment entry point
    below drives it with the real service. Keeping it separate is what lets the contract
    rows be pinned as COM-object asserts instead of retired script-text asserts.

    Every value set here is explicit because **COM defaults differ on all five settings**
    (row 1): a naive registration would get PT72H, battery-disallowed, parallel instances —
    silently breaking laptop/UPS districts and the no-catch-up guarantee.
    """
    definition = service.NewTask(0)

    settings = definition.Settings
    settings.Enabled = True
    settings.StartWhenAvailable = False  # the no-catch-up-run guarantee (2026-06-15)
    settings.MultipleInstances = TASK_INSTANCES_IGNORE_NEW
    settings.ExecutionTimeLimit = "PT2H"
    settings.DisallowStartIfOnBatteries = False  # battery operation ENABLED (COM default disallows)
    settings.StopIfGoingOnBatteries = False

    trigger = definition.Triggers.Create(TASK_TRIGGER_DAILY)
    # Invariant ISO-8601, composed by US — never a locale-formatted string (row 4). The
    # validated "HH:mm" slots straight in; seconds fixed at 00.
    trigger.StartBoundary = f"{TRIGGER_BOUNDARY_DATE}T{params.run_time}:00"
    trigger.DaysInterval = 1

    action = definition.Actions.Create(TASK_ACTION_EXEC)
    action.Path = params.exe
    action.Arguments = params.arguments
    action.WorkingDirectory = params.working_dir

    if params.password is not None:
        # Unattended: explicit TASK_LOGON_PASSWORD — never parameter-set inference, never
        # S4U (row 2; the 2026-06-25 regression class). run_highest is honoured HERE only.
        definition.Principal.RunLevel = TASK_RUNLEVEL_HIGHEST if params.run_highest else TASK_RUNLEVEL_LUA
        folder.RegisterTaskDefinition(
            params.task_name,
            definition,
            TASK_CREATE_OR_UPDATE,
            params.user,
            params.password,
            TASK_LOGON_PASSWORD,
        )
    else:
        # Logged-on-only: interactive token, ALWAYS Limited — run_highest ignored (row 2).
        definition.Principal.RunLevel = TASK_RUNLEVEL_LUA
        folder.RegisterTaskDefinition(
            params.task_name,
            definition,
            TASK_CREATE_OR_UPDATE,
            params.user,
            None,
            TASK_LOGON_INTERACTIVE_TOKEN,
        )


def register_task_definition(params: RegisterParams) -> None:
    """Create-or-replace one task at the root folder (row 12: works over a PS-registered task).

    Raises :class:`TaskComError` (plain data) or ``ImportError`` (pywin32-less host). The
    same COM-lifetime discipline as :func:`read_task` — and the SAME function serves the
    direct path AND the elevated child (``src/scheduler/elevated_apply.py``), which is the
    single-source property the retired PS ``_register_body`` sharing existed to protect,
    now structural rather than textual.
    """
    import pythoncom  # noqa: PLC0415 - lazy: Windows-only

    error: TaskComError | None = None
    with _apartment() as service:
        folder = None
        try:
            folder = service.GetFolder(ROOT_FOLDER)
            apply_definition(service, folder, params)
        except pythoncom.com_error as exc:
            error = _as_task_com_error(exc)  # raised OUTSIDE the handler — see read_task
        finally:
            folder = service = None  # noqa: F841 - releases THIS frame's COM refs pre-teardown
    if error is not None:
        raise error


class TaskComError(Exception):
    """A Task Scheduler failure as PLAIN data — the only error shape that leaves this module.

    ``scode`` is the real unsigned HRESULT (unwrapped from ``excepinfo`` — see the module
    docstring); ``message`` is the canonical, secret-free, classifier-ready text. Raised
    ``from None`` at the boundary so no COM-laden traceback survives the apartment.
    """

    def __init__(self, scode: int | None, message: str) -> None:
        super().__init__(message)
        self.scode = scode
        self.message = message


class BoundedTimeout(Exception):
    """The worker did not finish inside the bound — the OUTCOME is unknown, not failed."""


@dataclass(frozen=True)
class TaskFacts:
    """Plain-Python facts about one registered task — everything ``ScheduleReadback`` needs.

    Datetimes are already NAIVE-LOCAL ISO strings (or ``None`` under the never-run rule);
    ``last_result`` is already an unsigned int. No COM type crosses this boundary.
    """

    next_run: str | None
    last_run: str | None
    last_result: int | None
    action_path: str | None


def com_error_scode(exc: BaseException) -> int | None:
    """The REAL unsigned HRESULT out of a ``pythoncom.com_error`` — never the wrapper.

    IDispatch wraps the underlying failure in ``DISP_E_EXCEPTION`` and parks the real
    status in ``excepinfo[5]`` (probed live — see the module docstring). Falls back to the
    outer ``hresult`` when there is no excepinfo scode; ``None`` for a non-com_error.
    """
    hresult = getattr(exc, "hresult", None)
    if hresult is None:
        return None
    excepinfo = getattr(exc, "excepinfo", None)
    scode = None
    if excepinfo is not None and len(excepinfo) >= 6:
        scode = excepinfo[5]
    value = scode if scode else hresult
    try:
        return int(value) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None


def _canonical_message(scode: int | None, exc: BaseException) -> str:
    """A readable, secret-free one-liner for a COM failure (contract rows 9–10).

    Known HRESULTs map to the canonical strings the classifier/adapter key on; an unmapped
    one surfaces the ``excepinfo`` description (Windows' own explanation of the specific
    failure) **with its code appended**, or, failing that, the hex status alone. On the
    ``scode is None`` branch below this returns ``str(exc)`` — which, for a ``com_error``, IS
    its tuple repr; that branch is marker-guarded (see below) but NOT shape-guarded, so it can
    still surface a raw tuple repr when one carries no marker. No path here formats argv, an
    environment value, or the password into a message — the register path's password never
    appears in Task Scheduler error descriptions — but "Windows' own description never echoes
    a credential" is an assumption this module does not itself enforce.

    **The boundary guard (plan 0047; INVARIANTS).** The two escapes this function does not
    author — Windows' own description and ``str(exc)`` — are checked against
    :func:`messages.carries_foreign_marker` and DROPPED for the coded generic when they carry
    a marker another consumer owns. Measured live 2026-09-16: Windows' text for
    ``0x80070520`` is "A specified logon session does not exist…", which
    ``interpret_unregister`` was reading as the success-shaped "No schedule was registered"
    over a task that was still live. The two legitimate owners return from the table ABOVE
    the guard, so they keep the markers they own — which is why there is no ``owned=`` knob.
    """
    if scode is None:
        text = str(exc).strip()
        return text if text and not carries_foreign_marker(text) else MSG_OPERATION_FAILED
    if scode in _HRESULT_CANONICAL:  # the two marker OWNERS return here, before the guard
        return _HRESULT_CANONICAL[scode]
    excepinfo = getattr(exc, "excepinfo", None)
    description = ""
    if excepinfo is not None and len(excepinfo) >= 3 and excepinfo[2]:
        description = str(excepinfo[2]).strip()
    if description and not carries_foreign_marker(description):
        return f"{description} ({format_hresult(scode)})"
    return f"The schedule operation failed ({format_hresult(scode)})."


def _as_task_com_error(exc: BaseException) -> TaskComError:
    """Convert ANY apartment-side failure to the plain boundary error (data only)."""
    scode = com_error_scode(exc)
    return TaskComError(scode, _canonical_message(scode, exc))


@contextmanager
def _apartment() -> Iterator[Any]:
    """A per-thread COM apartment holding one connected ``Schedule.Service``.

    Imports are lazy so this module stays importable on every OS. The ``service`` local is
    re-bound to ``None`` in ``finally`` so this generator frame — which survives in any
    propagating traceback — holds no interface pointer past ``CoUninitialize`` (the
    "releasing IUnknown" failure; module docstring).
    """
    import pythoncom  # noqa: PLC0415 - lazy: Windows-only, see module docstring
    import win32com.client  # noqa: PLC0415 - lazy: Windows-only, see module docstring

    pythoncom.CoInitialize()
    service = None
    try:
        service = win32com.client.Dispatch("Schedule.Service")
        service.Connect()
        yield service
    finally:
        service = None
        pythoncom.CoUninitialize()


def _iso_or_none(value: Any) -> str | None:
    """A COM task datetime → NAIVE-LOCAL ISO string, or ``None`` under the sentinel rule.

    Two live-probed facts (2026-08-05) shape this:

    * Task Scheduler's "never" sentinel through the raw COM task object is the
      **1999-11-30** null date — NOT the 1899-12-30 epoch the CIM cmdlets show (the PS
      script's ``Year -gt 1900`` guard was calibrated to the latter and would MISS the
      real value). The guard is therefore ``year < 2000``: no legitimate DistrictSync
      task timestamp can predate the product, and both observed epochs fall under it.
      Without this, a never-run task's epoch "last run" makes the fired-but-no-record
      contradiction false-alarm on every fresh install (contract row 6).
    * pywin32 stamps the returned datetimes ``+00:00`` while their VALUE is local
      wall-clock (a 03:00 local trigger reads back as ``03:00+00:00``) — the tzinfo is a
      lie and is stripped, keeping comparisons against the naive-local run-record
      timestamps well-defined.
    """
    if value is None:
        return None
    year = getattr(value, "year", None)
    if year is None or year < 2000:
        return None
    try:
        naive = value.replace(tzinfo=None)
        return naive.isoformat()
    except (TypeError, ValueError):
        return None


def _unsigned_or_none(value: Any) -> int | None:
    """``LastTaskResult`` → unsigned int (PS/schtasks parity: 267011, not -0x…), or None."""
    if not isinstance(value, (int, float)):
        return None
    return int(value) & 0xFFFFFFFF


def read_task(task_name: str) -> TaskFacts:
    """Read one task's facts from the live Task Scheduler.

    Raises :class:`TaskComError` (plain data — the caller classifies ``scode``) or, on a
    pywin32-less host, ``ImportError``. Every COM local is re-bound to ``None`` before the
    apartment closes, on success and raise paths alike (module docstring).
    """
    import pythoncom  # noqa: PLC0415 - lazy: Windows-only

    error: TaskComError | None = None
    with _apartment() as service:
        folder = task = actions = None
        try:
            folder = service.GetFolder(ROOT_FOLDER)
            task = folder.GetTask(task_name)
            action_path: str | None = None
            actions = task.Definition.Actions
            if actions.Count >= 1:
                # 1-indexed COM collection; the FIRST action's Execute is the read-back
                # contract (`action_path`) Home's moved-exe detection keys on (row 11).
                action_path = str(actions.Item(1).Path or "").strip() or None
            return TaskFacts(
                next_run=_iso_or_none(task.NextRunTime),
                last_run=_iso_or_none(task.LastRunTime),
                last_result=_unsigned_or_none(task.LastTaskResult),
                action_path=action_path,
            )
        except pythoncom.com_error as exc:
            # Extract plain data and let the com_error DIE HERE (Python clears the `exc`
            # binding at except-exit). The raise happens OUTSIDE this handler — `raise …
            # from None` inside it would still chain the com_error as __context__, whose
            # traceback pins win32com dispatch frames (and their COM locals) past
            # CoUninitialize (verified live: one "releasing IUnknown" per raise-path
            # apartment until this shape).
            error = _as_task_com_error(exc)
        finally:
            # Release every COM ref THIS frame holds — including the `as service` binding,
            # which otherwise outlives __exit__'s CoUninitialize by one frame-teardown.
            folder = task = actions = service = None  # noqa: F841
    if error is None:  # pragma: no cover - unreachable: every with-body path returns or sets error
        error = TaskComError(None, MSG_OPERATION_FAILED)
    raise error


def delete_task_by_name(task_name: str) -> None:
    """Delete one task at the root folder.

    Raises :class:`TaskComError` on any failure — including ``scode == HR_NOT_FOUND`` for
    an already-absent task, which the caller maps to its idempotency contract.
    """
    import pythoncom  # noqa: PLC0415 - lazy: Windows-only

    error: TaskComError | None = None
    with _apartment() as service:
        folder = None
        try:
            folder = service.GetFolder(ROOT_FOLDER)
            folder.DeleteTask(task_name, 0)
        except pythoncom.com_error as exc:
            error = _as_task_com_error(exc)  # raised OUTSIDE the handler — see read_task
        finally:
            folder = service = None  # noqa: F841 - releases THIS frame's COM refs pre-teardown (see read_task)
    if error is not None:
        raise error


def bounded(fn: Callable[[], Any], *, timeout_s: float, label: str) -> Any:
    """Run ``fn`` on a fresh daemon worker; return its result or raise within the bound.

    COM apartments are per-thread, so ``fn`` must do its own apartment setup (both public
    entry points above do, via ``_apartment``). On timeout the worker cannot be cancelled —
    it may still complete its COM call later; that is deliberate and logged, and the caller
    must resolve the AMBIGUITY honestly (a timed-out read is UNKNOWN, never "absent"; a
    timed-out delete resolves through read-back, never a bare "failed").
    """
    result: dict[str, Any] = {}

    def _work() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - transported to the caller's thread
            result["error"] = exc

    worker = threading.Thread(target=_work, name=f"task-com-{label}", daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        # The leaked-daemon-thread trade, named in plan 0041 row 8: strictly bounded
        # caller, one WARN per occurrence, never a wedged UI and never a false verdict.
        logger.warning("Task Scheduler %s did not answer within %.0fs; treating as unknown.", label, timeout_s)
        raise BoundedTimeout(label)
    if "error" in result:
        raise result["error"]
    return result.get("value")
