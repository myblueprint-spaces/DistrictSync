"""The registered-schedule record (plan 0055 M3: moved from ``src/ui_flet/setup_flow.py``).

Pure, no UI import. What a registration writes into ``AppConfig`` and how it is read back: ``TaskArgs`` (the
task-baked arguments), ``RegisteredSchedule`` / :func:`registered_schedule` (the durable "what the live task carries"
record), and the ONE writer and clearer of the five ``schedule_*`` facets, :func:`write_registered_record` /
:func:`clear_registered_record`, shared by the Setup view and the headless ``--schedule-register`` /
``--schedule-remove``. ``src/ui_flet/setup_flow.py`` re-exports the moved names.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from src.scheduler.task_com import PrincipalKind, principal_kind_from_record

if TYPE_CHECKING:
    from src.config.app_config import AppConfig


@dataclass(frozen=True)
class TaskArgs:
    """The exact fields baked into the registered scheduled-task action (D8 reconcile).

    A change to ANY of these means the live task's action is stale and must be re-registered
    so tonight's run uses the new settings. The SFTP host/user/remote/port are deliberately
    NOT here — the pipeline reads those from the config at run time, so they don't change the
    task's command line and never need a re-register.
    """

    input_dir: str
    output_dir: str
    sis_type: str
    sftp_enabled: bool
    run_time: str

    @staticmethod
    def of(*, input_dir: str, output_dir: str, sis_type: str, sftp_enabled: bool, run_time: str) -> TaskArgs:
        """Build normalized ``TaskArgs`` (strings stripped) so cosmetic whitespace isn't a change."""
        return TaskArgs(
            input_dir=(input_dir or "").strip(),
            output_dir=(output_dir or "").strip(),
            sis_type=(sis_type or "").strip(),
            sftp_enabled=bool(sftp_enabled),
            run_time=(run_time or "").strip(),
        )


def task_args_changed(saved: TaskArgs, pending: TaskArgs) -> bool:
    """Whether any task-baked field changed → the live schedule needs re-registration (pure).

    Compares the five fields (input_dir, output_dir, sis_type, sftp flag, run_time). The
    Settings-mode Save uses this to drive re-registration ONLY when a change would otherwise
    leave the nightly task pointing at stale arguments.
    """
    return saved != pending


# The exact key set a persisted task-args record must carry (mirrors ``TaskArgs``' fields).
_TASK_ARGS_STR_FIELDS: tuple[str, ...] = ("input_dir", "output_dir", "sis_type", "run_time")
_TASK_ARGS_FIELDS: tuple[str, ...] = (*_TASK_ARGS_STR_FIELDS, "sftp_enabled")


def task_args_to_persisted(args: TaskArgs) -> dict[str, object]:
    """Serialize just-registered ``TaskArgs`` for the durable AppConfig record (0034 S3-d).

    Written at every CONFIRMED successful register so the Settings reconcile can compare a
    pending Save against what the live task ACTUALLY carries — surviving app restarts and
    Mapping district switches (a mount-time snapshot forgets both).
    """
    return asdict(args)


def task_args_from_persisted(raw: object) -> TaskArgs | None:
    """Rebuild the last-REGISTERED ``TaskArgs`` from its persisted form (total; 0034 S3-d).

    ``None`` means **"no usable record" — the honest UNKNOWN**, not a licence to substitute a
    guess (W3-C). DEFENSIVE rather than fail-loud by design: the record lives in the user-profile
    ``config.json`` (hand-editable, and absent on installs that registered before the field
    existed in v3.7.0), so a missing/garbled record must degrade, never crash Settings. Values are
    normalized through ``TaskArgs.of`` so a persisted record and a live snapshot compare on equal
    footing.
    """
    if not isinstance(raw, dict):
        return None
    if not all(field in raw for field in _TASK_ARGS_FIELDS):
        return None
    if not all(isinstance(raw[field], str) for field in _TASK_ARGS_STR_FIELDS):
        return None
    if not isinstance(raw["sftp_enabled"], bool):
        return None
    return TaskArgs.of(
        input_dir=raw["input_dir"],
        output_dir=raw["output_dir"],
        sis_type=raw["sis_type"],
        sftp_enabled=raw["sftp_enabled"],
        run_time=raw["run_time"],
    )


@dataclass(frozen=True)
class RegisteredSchedule:
    """What the app durably KNOWS about the live scheduled task — ``None`` means "we can't tell".

    The five facets are written together by every confirmed register and cleared together by every
    confirmed unregister, so the record is **atomic**: either the facts are evidenced or none of
    them is. That is why an absent ``args`` also makes ``unattended`` unknown — the ``False``
    default of ``AppConfig.schedule_unattended`` is a dataclass default, not an observation.

    Attributes:
        args: the task-baked args the live task actually carries, or ``None`` when there is no
            usable record (an install that registered before the record shipped in v3.7.0, or a
            hand-edited ``config.json``).
        unattended: whether the live task runs while signed out, or ``None`` when unknown. Always
            ``False`` where the platform has no stored-password logon at all (cron): there is no
            logon type an unproven re-register could downgrade, so "unknown" would buy nothing.
        run_as_user: the PRINCIPAL the live task was registered to (plan 0046 B). ``None`` exactly
            when ``args`` is ``None`` — the facets are written and cleared together, so an absent
            record makes all three unknown. ``""`` (the signed-in account) is not a guess: no build
            before this one could register anything else (``screens/setup.py`` passed
            ``run_as_user=None`` unconditionally), so a pre-0046 record's ``""`` is EVIDENCED. A
            hand-edited ``config.json`` can still lie; every consequence of being wrong falls back
            to refusing a switch, never to performing one.
        run_as_kind: the principal KIND the live task was registered with (plan 0049 S-4), or
            ``None`` exactly when ``args`` is ``None`` — the fourth facet of the same atomic
            record, so an absent record makes all four unknown. It is never ``None`` beside a
            usable record: ``task_com.principal_kind_from_record`` resolves a blank stored value
            from the recorded USER, and both of its answers are evidenced by what earlier builds
            could register. Recorded rather than derived because a managed service account is
            unattended WITHOUT a password, so ``unattended`` no longer implies which credential
            Windows wants — and the ``$`` on the name is a spelling, not a security kind.
    """

    args: TaskArgs | None
    unattended: bool | None
    run_as_user: str | None
    run_as_kind: PrincipalKind | None
    wake_to_run: bool | None = None
    """Whether the live task wakes the computer to run (DSYNC-37) — the fifth facet.

    ``None`` exactly when ``args`` is ``None`` (the facets move together). Beside a usable record
    it is never ``None``: a blank stored value resolves to ``False``, which is EVIDENCED — no build
    before DSYNC-37 set ``Settings.WakeToRun`` and the COM default is False.
    """


#: The wake-to-run answer for a NEW registration (DSYNC-37): on. Waking a sleeping computer for
#: the nightly is the point of an unattended task. It never applies to a task that already exists
#: — that one is described by its record (see :func:`wake_to_run_initial`).
WAKE_TO_RUN_DEFAULT = True


def wake_to_run_initial(*, schedule_registered: bool, record: RegisteredSchedule) -> bool:
    """The tick box's opening value (pure, TOTAL; DSYNC-37).

    With a live task the box must describe THAT task — otherwise a Settings Save on an install
    whose task predates DSYNC-37 would read "on" against a task that does not wake, and quietly
    re-register it (with a UAC prompt on the unattended path) the admin never asked for. So a
    registered schedule reads its record (``False`` for a pre-DSYNC-37 record, see
    :class:`RegisteredSchedule`), and an UNPROVEN registration (no usable record) reads ``False``
    for the same evidenced reason. Only an install with no task gets the new-registration default.
    """
    if schedule_registered:
        return bool(record.wake_to_run)
    return WAKE_TO_RUN_DEFAULT


def registered_schedule(
    *,
    raw_task_args: object,
    unattended_flag: bool,
    raw_run_as_user: object,
    raw_run_as_kind: object,
    raw_wake_to_run: object,
    supports_unattended: bool = True,
) -> RegisteredSchedule:
    """Resolve the durable registered-schedule record from its persisted parts (pure, TOTAL).

    The SINGLE place ``AppConfig.schedule_task_args`` + ``schedule_unattended`` are turned into
    reconcile facts, so no caller can re-derive "what the task carries" from the *current* config
    (the unsound baseline W3-C removes: a surface that mutated config before Settings mounted —
    a Mapping district switch — makes any current-config baseline equal the pending args, so the
    reconcile reads "unchanged" and silently skips the re-register it just promised).

    ``supports_unattended`` gates only the INFERENCE, never a recorded fact: a persisted
    ``unattended_flag`` is evidence and is honored on any platform (a ``config.json`` can travel),
    while the *absence* of evidence is treated as unknown only where an unattended logon type
    exists to lose.

    ``raw_run_as_kind`` (plan 0049 S-4) is REQUIRED and undefaulted, for the same reason
    ``raw_run_as_user`` is: a defaulted facet is one a new call site can quietly stop supplying,
    and this one selects which credential story the admin is told about. Its blank/absent value
    is resolved by ``task_com.principal_kind_from_record`` — the ONE spelling of that evidenced
    rule, shared with the elevated prune.

    ``raw_wake_to_run`` (DSYNC-37) is REQUIRED and undefaulted for the same reason. Only a real
    ``bool`` is a recorded answer; anything else beside a usable record resolves to ``False`` —
    the evidenced state of every task registered before the field existed.
    """
    args = task_args_from_persisted(raw_task_args)
    if args is None:
        return RegisteredSchedule(
            args=None,
            unattended=None if supports_unattended else False,
            run_as_user=None,
            run_as_kind=None,
            wake_to_run=None,
        )
    principal = raw_run_as_user.strip() if isinstance(raw_run_as_user, str) else ""
    return RegisteredSchedule(
        args=args,
        unattended=bool(unattended_flag),
        run_as_user=principal,
        run_as_kind=principal_kind_from_record(raw_run_as_kind, user=principal),
        wake_to_run=raw_wake_to_run if isinstance(raw_wake_to_run, bool) else False,
    )


def write_registered_record(
    cfg: AppConfig,
    *,
    run_time: str,
    args: TaskArgs,
    account: str,
    kind: PrincipalKind,
    wake_to_run: bool,
) -> None:
    """The five-facet register record, in ONE save (the Setup view's and the headless command's only writer).

    ``account`` is ``""`` for the signed-in account. ``schedule_unattended`` is keyed on the KIND (0049 S-4): a managed
    service account runs unattended and carries no password. The password itself is never here.
    """
    from src.config.app_config import registration_stamp

    cfg.schedule_time = run_time
    cfg.schedule_registered = True
    cfg.schedule_registered_at = registration_stamp()
    cfg.schedule_unattended = kind is not PrincipalKind.INTERACTIVE_TOKEN
    cfg.schedule_task_args = task_args_to_persisted(args)
    cfg.schedule_run_as_user = account or ""
    cfg.schedule_run_as_kind = kind.value
    cfg.schedule_wake_to_run = wake_to_run
    cfg.save()


def clear_registered_record(cfg: AppConfig) -> None:
    """No task exists any more: every "what was registered" facet goes together, in ONE save. ``schedule_time`` stays."""
    cfg.schedule_registered = False
    cfg.schedule_registered_at = ""
    cfg.schedule_unattended = False
    cfg.schedule_task_args = None
    cfg.schedule_run_as_user = ""
    cfg.schedule_run_as_kind = ""
    cfg.schedule_wake_to_run = None
    cfg.save()
