"""Setup Wizard — guided 5-step configuration for DistrictSync.

Steps:
    1. File paths (input GDE directory, output CSV directory)
    2. District config selection
    3. Schedule time
    4. SFTP configuration
    5. Summary and activation
"""

import sys
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Ensure src/ is importable when running directly via streamlit
# ---------------------------------------------------------------------------
_root = Path(__file__).parent.parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from src.config.app_config import AppConfig  # noqa: E402
from src.config.loader import available_configs, load_config  # noqa: E402
from src.etl.pipeline import extract_required_files  # noqa: E402
from src.ui.brand import header, inject_brand_css, sidebar_exit_control, step_progress  # noqa: E402
from src.ui.lifecycle import request_exit  # noqa: E402
from src.utils.validators import ALLOWED_SFTP_HOSTS  # noqa: E402

st.set_page_config(page_title="Setup Wizard — DistrictSync", page_icon="⚙️", layout="wide")
inject_brand_css()
header("Setup Wizard", "Configure DistrictSync for automated daily processing")
sidebar_exit_control()

# ---------------------------------------------------------------------------
# Session state: current step (1–5) and working config
# ---------------------------------------------------------------------------

if "wizard_step" not in st.session_state:
    st.session_state.wizard_step = 1

if "wizard_cfg" not in st.session_state:
    st.session_state.wizard_cfg = AppConfig.load()

cfg: AppConfig = st.session_state.wizard_cfg

# Default to management view when config is already complete
if "wizard_view" not in st.session_state:
    st.session_state.wizard_view = "manage" if cfg.is_complete() else "wizard"


def _go(step: int) -> None:
    st.session_state.wizard_step = step


def _browse_into(state_key: str) -> None:
    """Open the native folder picker and write the chosen path into session.

    Runs as a button ``on_click`` callback (before the text widgets are
    re-instantiated), so writing the widget-keyed session value is allowed.
    A ``None`` return means the user cancelled or no GUI is available — in that
    case we leave the existing value untouched and let them type/paste instead.
    """
    from src.ui.folder_picker import pick_directory

    current = st.session_state.get(state_key, "")
    initial = current if current and Path(current).is_dir() else None
    picked = pick_directory(initialdir=initial)
    if picked:
        st.session_state[state_key] = picked


# ---------------------------------------------------------------------------
# Helper — schedule registration
# ---------------------------------------------------------------------------


def _classify_schedule_error(msg: str, elevated: bool) -> str:
    """Map a (now clean) registration error into a calm, actionable message.

    Keyed off the canonical substrings ``register_task`` publishes
    (``"PowerShell not found"`` / ``"ScheduledTasks module not available"`` /
    Windows' own ``"Access is denied"``) plus whether the process is elevated
    (:func:`src.scheduler.windows.is_elevated`). ``msg`` is already de-CLIXML'd
    by ``register_task`` — it is a plain one-liner, safe to show verbatim in the
    ``else`` branch.

    Pure function (no Streamlit) so it is unit-testable headless — the UI calls
    it and renders the result via ``st.error``.
    """
    access_denied = "Access is denied" in msg or "access denied" in msg.lower()

    if "PowerShell not found" in msg:
        return (
            "Windows PowerShell wasn't found on this machine, so the schedule can't be created. "
            "PowerShell ships with Windows 8 / Server 2012 and newer — if it's missing, this "
            "server is unsupported for automated scheduling. You can still run conversions manually."
        )
    if "ScheduledTasks module not available" in msg:
        return (
            "This Windows version is too old to schedule tasks this way (it needs Windows 8 / "
            "Server 2012 or newer). You can still run conversions manually from the Convert page."
        )
    if access_denied and not elevated:
        return (
            "Permission denied — right-click the application and choose **Run as administrator**, "
            "then try again. (Creating an unattended task needs administrator rights.)"
        )
    if access_denied and elevated:
        return (
            "Registration was denied even though you're running as administrator. The account "
            "likely can't be used for an unattended task: make sure you entered your **Windows "
            "account password** (not your Windows Hello PIN — for a Microsoft Account, your "
            "microsoft.com password), and that the account is allowed to "
            "**'Log on as a batch job'**."
        )
    return f"Failed to register schedule: {msg}"


def _register_schedule(
    cfg: AppConfig,
    *,
    run_as_user: str | None = None,
    run_as_password: str | None = None,
) -> bool:
    """Register the OS schedule and update cfg.schedule_registered.

    Args:
        cfg: Current application config (schedule fields must be set before calling).
        run_as_user: Windows account the task should run as. Defaults to the
            current interactive user when ``run_as_password`` is supplied.
        run_as_password: Windows account password. When truthy the task is
            registered to run whether or not the user is logged on. When
            omitted/blank the task is registered with default (logged-on-only)
            scope, and a warning is displayed.

    Returns:
        True if the schedule was successfully registered, False otherwise.
    """
    import sys as _sys

    exe_path = Path(_sys.executable)  # The running Python / frozen exe

    if _sys.platform == "win32":
        from src.scheduler.windows import register_task

        ok, msg = register_task(
            task_name=cfg.schedule_task_name,
            exe_path=exe_path,
            sis_type=cfg.sis_type,
            input_dir=Path(cfg.input_dir),
            output_dir=Path(cfg.output_dir),
            run_time=cfg.schedule_time,
            sftp=cfg.sftp_enabled,
            run_as_user=run_as_user,
            run_as_password=run_as_password or None,
        )
    else:
        from src.scheduler.linux import register_cron

        ok, msg = register_cron(
            exe_path=exe_path,
            sis_type=cfg.sis_type,
            input_dir=Path(cfg.input_dir),
            output_dir=Path(cfg.output_dir),
            run_time=cfg.schedule_time,
            sftp=cfg.sftp_enabled,
        )

    if ok:
        cfg.schedule_registered = True
        cfg.save()
        if not run_as_password and _sys.platform == "win32":
            st.warning(
                "No Windows password provided — the task will only run while you are logged in to this server. "
                "For unattended operation across reboots, re-run setup with the password."
            )
    else:
        # Classify on the (clean) message + elevation so an already-elevated
        # admin isn't sent in circles by a blanket "run as administrator".
        if _sys.platform == "win32":
            from src.scheduler.windows import is_elevated

            elevated = is_elevated()
        else:
            elevated = False
        st.error(_classify_schedule_error(msg, elevated))

    return ok


# ═══════════════════════════════════════════════════════════════════════════
# MANAGEMENT VIEW — shown when config is complete (unless user chose wizard)
# ═══════════════════════════════════════════════════════════════════════════

if st.session_state.wizard_view == "manage":
    import datetime

    # --- Quick summary bar ---
    st.subheader("Current Configuration")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("District", cfg.sis_type)
    with col2:
        st.metric("Schedule", cfg.schedule_time if cfg.schedule_registered else "Not active")
    with col3:
        st.metric("SFTP", "Enabled" if cfg.sftp_enabled else "Disabled")
    with col4:
        st.metric("Input Dir", Path(cfg.input_dir).name if cfg.input_dir else "—")

    st.divider()

    # --- Schedule Management ---
    st.subheader("Schedule")

    if cfg.schedule_registered:
        # Query live task status on Windows
        task_info = None
        if sys.platform == "win32":
            from src.scheduler.windows import query_task

            task_info = query_task(cfg.schedule_task_name)

        if task_info and task_info.get("exists"):
            info_cols = st.columns(3)
            with info_cols[0]:
                st.markdown(f"**Task name:** `{cfg.schedule_task_name}`")
            with info_cols[1]:
                st.markdown(f"**Runs daily at:** `{cfg.schedule_time}`")
            with info_cols[2]:
                status = task_info.get("status", "Unknown")
                next_run = task_info.get("next_run_time", "—")
                st.markdown(f"**Status:** {status}  \n**Next run:** {next_run}")

            last_result = task_info.get("last_result", "")
            last_run = task_info.get("last_run_time", "—")
            if last_run and last_run != "—":
                st.caption(f"Last run: {last_run} | Result: {last_result}")
        else:
            st.info(f"Schedule registered — runs daily at **{cfg.schedule_time}**.")

        # Edit schedule time
        with st.expander("Edit schedule time"):
            try:
                h, m = cfg.schedule_time.split(":")
                current_time = datetime.time(int(h), int(m))
            except Exception:
                current_time = datetime.time(3, 0)
            new_time = st.time_input("New daily run time (24-hour)", value=current_time, key="manage_schedule_time")
            if st.button("Update Schedule", type="primary"):
                cfg.schedule_time = new_time.strftime("%H:%M")
                cfg.save()
                _register_schedule(cfg)
                st.rerun()

        # Disable schedule
        if st.button("Disable Schedule", type="secondary"):
            if sys.platform == "win32":
                from src.scheduler.windows import delete_task

                ok, msg = delete_task(cfg.schedule_task_name)
            else:
                from src.scheduler.linux import delete_cron

                ok, msg = delete_cron()
            if ok:
                cfg.schedule_registered = False
                cfg.save()
                st.success("Schedule disabled.")
                st.rerun()
            else:
                st.error(f"Failed to disable schedule: {msg}")
    else:
        st.warning("No active schedule.")
        with st.expander("Set up a schedule"):
            new_time = st.time_input(
                "Daily run time (24-hour)", value=datetime.time(3, 0), key="manage_new_schedule_time"
            )
            if st.button("Enable Schedule", type="primary"):
                cfg.schedule_time = new_time.strftime("%H:%M")
                cfg.save()
                _register_schedule(cfg)
                st.rerun()

    st.divider()

    # --- SFTP Management ---
    st.subheader("SFTP Upload")

    if cfg.sftp_enabled:
        sftp_cols = st.columns(3)
        with sftp_cols[0]:
            st.markdown(f"**Host:** `{cfg.sftp_host}:{cfg.sftp_port}`")
        with sftp_cols[1]:
            st.markdown(f"**Username:** `{cfg.sftp_username}`")
        with sftp_cols[2]:
            st.markdown(f"**Remote path:** `{cfg.sftp_remote_path}`")

        with st.expander("Edit SFTP settings"):
            allowed_hosts_str = ", ".join(sorted(ALLOWED_SFTP_HOSTS))
            st.caption(f"Allowed SFTP hosts: {allowed_hosts_str}")

            ecol1, ecol2 = st.columns(2)
            with ecol1:
                edit_host = st.selectbox(
                    "SFTP Host",
                    options=sorted(ALLOWED_SFTP_HOSTS),
                    index=(
                        sorted(ALLOWED_SFTP_HOSTS).index(cfg.sftp_host) if cfg.sftp_host in ALLOWED_SFTP_HOSTS else 0
                    ),
                    key="manage_sftp_host",
                )
                edit_username = st.text_input("Username", value=cfg.sftp_username, key="manage_sftp_user")
                edit_remote_path = st.text_input("Remote Path", value=cfg.sftp_remote_path, key="manage_sftp_path")
            with ecol2:
                edit_port = st.number_input(
                    "Port", value=cfg.sftp_port or 22, min_value=1, max_value=65535, key="manage_sftp_port"
                )
                edit_password = st.text_input(
                    "Password", type="password", placeholder="Leave blank to keep existing", key="manage_sftp_pass"
                )

            bcol1, bcol2 = st.columns(2)
            with bcol1:
                if st.button("Test Connection", key="manage_sftp_test"):
                    from src.sftp.uploader import SFTPUploader

                    try:
                        uploader = SFTPUploader(edit_host, int(edit_port), edit_username, edit_remote_path)
                        if edit_password:
                            uploader.store_password(edit_password)
                        with st.spinner("Connecting..."):
                            ok, msg = uploader.test_connection()
                        if ok:
                            st.success(f"Connection successful: {msg}")
                        else:
                            st.error(f"Connection failed: {msg}")
                    except ValueError as e:
                        st.error(str(e))
            with bcol2:
                if st.button("Save SFTP Settings", type="primary", key="manage_sftp_save"):
                    cfg.sftp_host = edit_host
                    cfg.sftp_port = int(edit_port)
                    cfg.sftp_username = edit_username
                    cfg.sftp_remote_path = edit_remote_path
                    if edit_password:
                        from src.sftp.uploader import SFTPUploader

                        try:
                            SFTPUploader(edit_host, int(edit_port), edit_username, edit_remote_path).store_password(
                                edit_password
                            )
                        except Exception as e:
                            st.error(f"Could not store password: {e}")
                            st.stop()
                    cfg.save()
                    st.success("SFTP settings saved.")
                    st.rerun()

        if st.button("Disable SFTP", type="secondary"):
            cfg.sftp_enabled = False
            cfg.save()
            st.success("SFTP disabled.")
            st.rerun()
    else:
        st.info("SFTP upload is disabled.")
        with st.expander("Enable SFTP"):
            allowed_hosts_str = ", ".join(sorted(ALLOWED_SFTP_HOSTS))
            st.caption(f"Allowed SFTP hosts: {allowed_hosts_str}")

            ecol1, ecol2 = st.columns(2)
            with ecol1:
                new_host = st.selectbox("SFTP Host", options=sorted(ALLOWED_SFTP_HOSTS), key="manage_sftp_new_host")
                new_username = st.text_input("Username", key="manage_sftp_new_user")
                new_remote_path = st.text_input("Remote Path", value="/files", key="manage_sftp_new_path")
            with ecol2:
                new_port = st.number_input("Port", value=22, min_value=1, max_value=65535, key="manage_sftp_new_port")
                new_password = st.text_input("Password", type="password", key="manage_sftp_new_pass")

            if st.button("Enable SFTP", type="primary", key="manage_sftp_enable"):
                errors = []
                if not new_username:
                    errors.append("Username is required.")
                if not new_password:
                    errors.append("Password is required for first-time SFTP setup.")
                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    cfg.sftp_enabled = True
                    cfg.sftp_host = new_host
                    cfg.sftp_port = int(new_port)
                    cfg.sftp_username = new_username
                    cfg.sftp_remote_path = new_remote_path
                    from src.sftp.uploader import SFTPUploader

                    try:
                        SFTPUploader(new_host, int(new_port), new_username, new_remote_path).store_password(
                            new_password
                        )
                    except Exception as e:
                        st.error(f"Could not store password: {e}")
                        st.stop()
                    cfg.save()
                    st.success("SFTP enabled.")
                    st.rerun()

    st.divider()

    # --- Re-run wizard button ---
    if st.button("Re-run Setup Wizard"):
        st.session_state.wizard_view = "wizard"
        st.session_state.wizard_step = 1
        st.rerun()

    st.stop()  # Don't fall through to wizard steps below

# ═══════════════════════════════════════════════════════════════════════════
# WIZARD VIEW — linear 5-step setup flow
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# Progress bar + step labels
# ---------------------------------------------------------------------------

STEPS = ["File Paths", "District", "Schedule", "SFTP", "Activate"]
step_progress(st.session_state.wizard_step, total=len(STEPS))

step_cols = st.columns(len(STEPS))
for i, (col, label) in enumerate(zip(step_cols, STEPS), start=1):
    current = st.session_state.wizard_step
    if i < current:
        col.markdown(f"<span style='color:#16A34A;font-size:0.8rem'>✓ {i}. {label}</span>", unsafe_allow_html=True)
    elif i == current:
        col.markdown(
            f"<span style='color:#1D5BB5;font-size:0.8rem;font-weight:700'>● {i}. {label}</span>",
            unsafe_allow_html=True,
        )
    else:
        col.markdown(f"<span style='color:#94A3B8;font-size:0.8rem'>{i}. {label}</span>", unsafe_allow_html=True)

st.divider()


# ---------------------------------------------------------------------------
# Step 1 — File paths
# ---------------------------------------------------------------------------

if st.session_state.wizard_step == 1:
    st.subheader("Step 1 — File Paths")
    st.markdown(
        "Enter the directories that DistrictSync will read source files from and write CSVs to.\n\n"
        "Both paths must already exist on this machine."
    )

    st.session_state.setdefault("wizard_input_dir", cfg.input_dir or "")
    st.session_state.setdefault("wizard_output_dir", cfg.output_dir or "")

    st.markdown("**GDE Input Directory**")
    in_field, in_browse = st.columns([5, 1])
    with in_field:
        input_dir = st.text_input(
            "GDE Input Directory",
            key="wizard_input_dir",
            placeholder=r"C:\DistrictSync\input",
            help="Directory where MyEducation BC places the GDE .txt files",
            label_visibility="collapsed",
        )
    with in_browse:
        st.button(
            "📁 Browse",
            on_click=_browse_into,
            args=("wizard_input_dir",),
            use_container_width=True,
            key="browse_input_dir",
        )

    st.markdown("**CSV Output Directory**")
    out_field, out_browse = st.columns([5, 1])
    with out_field:
        output_dir = st.text_input(
            "CSV Output Directory",
            key="wizard_output_dir",
            placeholder=r"C:\DistrictSync\output",
            help="Directory where the generated CSV files will be written",
            label_visibility="collapsed",
        )
    with out_browse:
        st.button(
            "📁 Browse",
            on_click=_browse_into,
            args=("wizard_output_dir",),
            use_container_width=True,
            key="browse_output_dir",
        )

    st.caption("Tip: use 📁 Browse to pick a folder, or type / paste a path directly.")

    if st.button("Validate & Continue →", type="primary"):
        errors = []
        if not input_dir:
            errors.append("Input directory is required.")
        elif not Path(input_dir).is_dir():
            errors.append(f"Input directory does not exist: `{input_dir}`")

        if not output_dir:
            errors.append("Output directory is required.")
        else:
            out_path = Path(output_dir)
            if not out_path.exists():
                try:
                    out_path.mkdir(parents=True)
                    st.info(f"Created output directory: `{output_dir}`")
                except Exception as e:
                    errors.append(f"Cannot create output directory: {e}")

        if errors:
            for e in errors:
                st.error(e)
        else:
            cfg.input_dir = input_dir
            cfg.output_dir = output_dir
            cfg.save()  # Persist per-step to survive browser closure

            # Check for expected GDE files
            if cfg.sis_type:
                try:
                    config_obj = load_config(cfg.sis_type)
                    expected = extract_required_files(config_obj)
                    present = [f for f in expected if (Path(input_dir) / f).exists()]
                    missing = [f for f in expected if f not in [Path(p).name for p in present]]
                    missing_check = [f for f in expected if not (Path(input_dir) / f).exists()]
                    if missing_check:
                        st.warning(f"Expected GDE files not found in input directory: {', '.join(missing_check)}")
                # Optional file-existence check; a failure here isn't fatal.
                except Exception:  # nosec B110
                    pass

            _go(2)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 2 — District config
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 2:
    st.subheader("Step 2 — District Configuration")
    st.markdown(
        "Select the mapping configuration that matches your school district. "
        "Contact support@myBlueprint.ca if you are unsure which to choose."
    )
    st.page_link("pages/04_Mapping_Editor.py", label="Need a new district mapping? Open the Mapping Editor", icon="🗺️")

    # List district configs from user dir + bundled defaults (user wins on name collision)
    available = available_configs()

    # Read district_name from each config; fall back to the config key
    friendly_names: dict[str, str] = {}
    for key in available:
        try:
            loaded_cfg = load_config(key)
            friendly_names[key] = loaded_cfg.district_name or key
        except Exception:
            friendly_names[key] = key

    options = [(friendly_names.get(k, k), k) for k in available]
    labels = [o[0] for o in options]
    values = [o[1] for o in options]

    current_idx = values.index(cfg.sis_type) if cfg.sis_type in values else 0
    selected_label = st.selectbox("District", labels, index=current_idx)
    selected = values[labels.index(selected_label)]

    # Show what the config contains
    try:
        loaded = load_config(selected)
        gc = loaded.global_config
        st.success(f"Config loaded — SIS: `{loaded.sis}` | Version: `{loaded.version}`")
        if gc.homeroom_grades:
            st.info(f"Homeroom grades: {', '.join(gc.homeroom_grades)}")
        # Surface the high school course grade floor when this config produces
        # the course files (CourseInfo / StudentCourses). Edit it in the
        # Mapping Editor's "Classes and Courses" step.
        if {"CourseInfo", "StudentCourses"} & set(gc.enabled_entities):
            st.info(f"High school course grade: Grade {gc.course_start_grade} and up")
    except Exception as e:
        st.error(f"Could not load config `{selected}`: {e}")

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(1)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            cfg.sis_type = selected
            cfg.save()
            _go(3)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 3 — Schedule
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 3:
    st.subheader("Step 3 — Schedule")
    st.markdown(
        "Optionally set up a daily automated schedule. "
        "You can skip this and use the Convert page for ad-hoc runs instead."
    )

    import datetime

    enable_schedule = st.toggle("Enable daily schedule", value=cfg.schedule_time != "")

    if enable_schedule:
        st.markdown(
            "Choose a time when the GDE files have been generated by your SIS "
            "(usually overnight) and the server is not busy."
        )

        current_time = datetime.time(3, 0)
        if cfg.schedule_time:
            try:
                h, m = cfg.schedule_time.split(":")
                current_time = datetime.time(int(h), int(m))
            # Malformed time string falls back to the 03:00 default initialized above.
            except Exception:  # nosec B110
                pass

        run_time = st.time_input("Daily run time (24-hour)", value=current_time)
        st.info(f"The tool will run every day at **{run_time.strftime('%H:%M')}** local server time.")

        if sys.platform == "win32":
            from src.scheduler.windows import current_run_as_user

            _run_as = current_run_as_user()
            st.caption(f"The task will run as: **{_run_as}**")
            schedule_password = st.text_input(
                "Windows account password",
                type="password",
                key="wizard_schedule_password",
                help=(
                    "Lets the scheduled task run after a server reboot with no one logged in. "
                    "Used once to register the task with Windows Task Scheduler — "
                    "DistrictSync does not store it."
                ),
            )
            # Persist for Step 5's register call (password not stored in cfg)
            st.session_state["_wizard_run_as_user"] = _run_as
            st.session_state["_wizard_run_as_password"] = schedule_password or None
        else:
            st.session_state["_wizard_run_as_user"] = None
            st.session_state["_wizard_run_as_password"] = None

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(2)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            if enable_schedule:
                cfg.schedule_time = run_time.strftime("%H:%M")
            else:
                cfg.schedule_time = ""
                # Clear any stored run-as creds when schedule is disabled
                st.session_state.pop("_wizard_run_as_user", None)
                st.session_state.pop("_wizard_run_as_password", None)
            cfg.save()
            _go(4)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 4 — SFTP
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 4:
    st.subheader("Step 4 — SFTP Upload")
    st.markdown(
        "Configure SFTP to automatically upload the generated CSVs to SpacesEDU "
        "after each successful run. Credentials are stored securely in your "
        "operating system's credential manager."
    )

    enable_sftp = st.toggle("Enable SFTP upload", value=cfg.sftp_enabled)

    if enable_sftp:
        allowed_hosts_str = ", ".join(sorted(ALLOWED_SFTP_HOSTS))
        st.caption(f"Allowed SFTP hosts: {allowed_hosts_str}")

        col1, col2 = st.columns(2)
        with col1:
            sftp_host = st.selectbox(
                "SFTP Host",
                options=sorted(ALLOWED_SFTP_HOSTS),
                index=sorted(ALLOWED_SFTP_HOSTS).index(cfg.sftp_host) if cfg.sftp_host in ALLOWED_SFTP_HOSTS else 0,
            )
            sftp_username = st.text_input("Username", value=cfg.sftp_username)
            sftp_remote_path = st.text_input("Remote Path", value=cfg.sftp_remote_path or "/files")
        with col2:
            sftp_port = st.number_input("Port", value=cfg.sftp_port or 22, min_value=1, max_value=65535)
            sftp_password = st.text_input("Password", type="password", placeholder="Leave blank to keep existing")

        if st.button("Test Connection"):
            if not sftp_host or not sftp_username:
                st.error("Host and username are required to test the connection.")
            else:
                from src.sftp.uploader import SFTPUploader

                try:
                    uploader = SFTPUploader(sftp_host, int(sftp_port), sftp_username, sftp_remote_path)
                    if sftp_password:
                        uploader.store_password(sftp_password)
                    with st.spinner("Connecting..."):
                        ok, msg = uploader.test_connection()
                    if ok:
                        st.success(f"Connection successful: {msg}")
                    else:
                        st.error(f"Connection failed: {msg}")
                except ValueError as e:
                    st.error(str(e))
    else:
        sftp_host = cfg.sftp_host
        sftp_port = cfg.sftp_port
        sftp_username = cfg.sftp_username
        sftp_remote_path = cfg.sftp_remote_path
        sftp_password = None

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(3)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            if enable_sftp:
                # Validate required fields
                errors = []
                if not sftp_host:
                    errors.append("SFTP Host is required.")
                if not sftp_username:
                    errors.append("Username is required.")
                if not cfg.sftp_host and not sftp_password:
                    # First-time setup requires password
                    errors.append("Password is required for first-time SFTP setup.")

                if errors:
                    for e in errors:
                        st.error(e)
                else:
                    cfg.sftp_enabled = True
                    cfg.sftp_host = sftp_host
                    cfg.sftp_port = int(sftp_port)
                    cfg.sftp_username = sftp_username
                    cfg.sftp_remote_path = sftp_remote_path
                    if sftp_password:
                        try:
                            from src.scheduler.windows import current_run_as_user as _cur_user
                            from src.sftp.uploader import SFTPUploader

                            _uploader = SFTPUploader(sftp_host, int(sftp_port), sftp_username, sftp_remote_path)
                            _uploader.store_password(sftp_password)
                            # Keyring round-trip: verify the credential is readable by
                            # this account (the scheduled task runs as the same account).
                            _read_back = _uploader.get_stored_password()
                            try:
                                _account = _cur_user()
                            except Exception:
                                _account = "this account"
                            if _read_back:
                                st.success(f"Verified: SFTP credentials are stored and readable by **{_account}**.")
                            else:
                                st.error(
                                    "Could not read back the stored SFTP credential on this account — "
                                    "SFTP uploads may fail. Re-enter and try again."
                                )
                                st.stop()
                            st.caption(
                                "The scheduled task runs as this same Windows account, "
                                "which is why its credential store must hold these credentials."
                            )
                        except Exception as e:
                            st.error(
                                f"Could not store password: {e}\n\n"
                                "The OS credential manager may not be available. "
                                "Try running the application as administrator."
                            )
                            st.stop()
                    cfg.save()
                    _go(5)
                    st.rerun()
            else:
                cfg.sftp_enabled = False
                cfg.save()
                _go(5)
                st.rerun()

# ---------------------------------------------------------------------------
# Step 5 — Summary and activation
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 5:
    st.subheader("Step 5 — Review & Save")
    st.markdown("Review your configuration below, then click **Save** to apply.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**File paths**")
        st.code(f"Input:  {cfg.input_dir}\nOutput: {cfg.output_dir}")
        st.markdown("**District config**")
        st.code(cfg.sis_type)
        st.markdown("**Daily schedule**")
        st.code(f"Every day at {cfg.schedule_time}" if cfg.schedule_time else "Disabled")
    with col2:
        st.markdown("**SFTP**")
        if cfg.sftp_enabled:
            st.code(
                f"Host:   {cfg.sftp_host}:{cfg.sftp_port}\nUser:   {cfg.sftp_username}\nPath:   {cfg.sftp_remote_path}"
            )
        else:
            st.code("Disabled")

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("← Back"):
            _go(4)
            st.rerun()
    with col2:
        save_label = "Save & Activate Schedule" if cfg.schedule_time else "Save Configuration"
        if st.button(save_label, type="primary"):
            cfg.save()
            if cfg.schedule_time:
                _run_as_user = st.session_state.get("_wizard_run_as_user")
                _run_as_password = st.session_state.get("_wizard_run_as_password")
                registered = _register_schedule(
                    cfg,
                    run_as_user=_run_as_user,
                    run_as_password=_run_as_password,
                )
                if not registered:
                    # Error already shown by _register_schedule — stay on this step
                    st.stop()
                # Clean up transient password from session state (not persisted)
                st.session_state.pop("_wizard_run_as_password", None)
            else:
                cfg.schedule_registered = False
                cfg.save()
            st.session_state.wizard_view = "manage"
            st.rerun()

    st.divider()
    if cfg.schedule_registered:
        if sys.platform == "win32":
            from src.scheduler.windows import current_run_as_user as _cur_user

            try:
                _task_user = _cur_user()
            except Exception:
                _task_user = "the configured Windows account"
        else:
            _task_user = None

        if _task_user:
            st.success(
                f"Schedule registered. The task will run **as {_task_user}, whether or not you're logged in**, "
                f"daily at {cfg.schedule_time}."
            )
        else:
            st.success(f"Schedule registered — runs daily at {cfg.schedule_time}.")

        st.markdown(
            f"- Make sure fresh GDE files land in `{cfg.input_dir}` before **{cfg.schedule_time}** "
            "each day, or the run will re-deliver the previous output.\n"
            "- If uploads ever stop, check **Run History** — failed SFTP deliveries are logged there "
            "and the scheduled task will report a non-zero result."
        )

        # Setup is done — the daily run is the scheduled CLI, not this UI, so the
        # window can be closed. Offer a one-click shutdown (safe: the wizard never
        # calls save_all, so no write can be in flight here).
        st.success("Setup complete — you can close this window.")
        if st.button("Finish & Close", type="primary", key="wizard_finish_close"):
            request_exit()

    # Dry-run test — always available when config is complete
    if cfg.is_complete():
        st.markdown("**Test your configuration** — runs the full pipeline without writing any files.")
        if st.button("Run Test (Dry Run)"):
            try:
                import contextlib
                import io as _io

                from src.etl.pipeline import run_pipeline

                output_buf = _io.StringIO()
                with contextlib.redirect_stdout(output_buf):
                    run_pipeline(
                        cfg.sis_type,
                        cfg.input_dir,
                        cfg.output_dir,
                        dry_run=True,
                        quality=True,
                    )
                result_text = output_buf.getvalue()
                if result_text:
                    st.code(result_text, language="text")
                st.success("Dry run completed successfully.")
            except Exception as e:
                st.error(f"Dry run failed: {e}")
