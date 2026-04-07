"""Setup Wizard — guided 5-step configuration for GDE2Acsv.

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
from src.config.loader import load_config  # noqa: E402
from src.ui.brand import header, inject_brand_css, step_progress  # noqa: E402

st.set_page_config(page_title="Setup Wizard — GDE2Acsv", page_icon="⚙️", layout="wide")
inject_brand_css()
header("Setup Wizard", "Configure GDE2Acsv for automated daily processing")

# ---------------------------------------------------------------------------
# Session state: current step (1–5) and working config
# ---------------------------------------------------------------------------

if "wizard_step" not in st.session_state:
    st.session_state.wizard_step = 1

if "wizard_cfg" not in st.session_state:
    st.session_state.wizard_cfg = AppConfig.load()

cfg: AppConfig = st.session_state.wizard_cfg


def _go(step: int) -> None:
    st.session_state.wizard_step = step


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
        col.markdown(f"<span style='color:#1D5BB5;font-size:0.8rem;font-weight:700'>● {i}. {label}</span>", unsafe_allow_html=True)
    else:
        col.markdown(f"<span style='color:#94A3B8;font-size:0.8rem'>{i}. {label}</span>", unsafe_allow_html=True)

st.divider()


# ---------------------------------------------------------------------------
# Helper — schedule registration (defined before the if/elif chain)
# ---------------------------------------------------------------------------

def _register_schedule(cfg: AppConfig) -> None:
    """Register the OS schedule and update cfg.schedule_registered."""
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
        st.success(msg)
    else:
        st.error(msg)


# ---------------------------------------------------------------------------
# Step 1 — File paths
# ---------------------------------------------------------------------------

if st.session_state.wizard_step == 1:
    st.subheader("Step 1 — File Paths")
    st.markdown(
        "Enter the directories that GDE2Acsv will read source files from and write CSVs to.\n\n"
        "Both paths must already exist on this machine."
    )

    input_dir = st.text_input(
        "GDE Input Directory",
        value=cfg.input_dir or "",
        placeholder=r"C:\GDE2Acsv\input",
        help="Directory where MyEducation BC places the GDE .txt files",
    )
    output_dir = st.text_input(
        "CSV Output Directory",
        value=cfg.output_dir or "",
        placeholder=r"C:\GDE2Acsv\output",
        help="Directory where the generated CSV files will be written",
    )

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
            _go(2)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 2 — District config
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 2:
    st.subheader("Step 2 — District Configuration")
    st.markdown(
        "Select the mapping configuration that matches your school district. "
        "Contact SpacesEDU support if you are unsure which to choose."
    )

    mapping_dir = Path("config/mappings")
    available = sorted(
        p.stem.replace("_mapping", "")
        for p in mapping_dir.glob("*_mapping.yaml")
    )

    friendly_names = {
        "myedbc": "MyEducation BC (default)",
        "sd48myedbc": "SD48 – Sea to Sky School District",
        "sd51myedbc": "SD51 – Boundary School District",
        "sd74myedbc": "SD74 – Gold Trail School District",
    }

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
            _go(3)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 3 — Schedule
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 3:
    st.subheader("Step 3 — Schedule")
    st.markdown(
        "GDE2Acsv will run automatically at this time every day. "
        "Choose a time when the GDE files have been generated by your SIS "
        "(usually overnight) and the server is not busy."
    )

    import datetime
    current_time = datetime.time(3, 0)
    if cfg.schedule_time:
        try:
            h, m = cfg.schedule_time.split(":")
            current_time = datetime.time(int(h), int(m))
        except Exception:
            pass

    run_time = st.time_input("Daily run time (24-hour)", value=current_time)

    st.info(
        f"The tool will run every day at **{run_time.strftime('%H:%M')}** local server time.\n\n"
        f"On Windows, this creates a Windows Task Scheduler entry.\n"
        f"On Linux/macOS, this adds a crontab entry."
    )

    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("← Back"):
            _go(2)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary"):
            cfg.schedule_time = run_time.strftime("%H:%M")
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
        "operating system's credential manager (Windows Credential Manager / "
        "macOS Keychain)."
    )

    enable_sftp = st.toggle("Enable SFTP upload", value=cfg.sftp_enabled)

    if enable_sftp:
        col1, col2 = st.columns(2)
        with col1:
            sftp_host = st.text_input("SFTP Host", value=cfg.sftp_host,
                                       placeholder="sftp.spacesEDU.com")
            sftp_username = st.text_input("Username", value=cfg.sftp_username)
            sftp_remote_path = st.text_input("Remote Path", value=cfg.sftp_remote_path or "/upload")
        with col2:
            sftp_port = st.number_input("Port", value=cfg.sftp_port or 22, min_value=1, max_value=65535)
            sftp_password = st.text_input("Password", type="password",
                                           placeholder="Leave blank to keep existing")

        if st.button("Test Connection"):
            if not sftp_host or not sftp_username:
                st.error("Host and username are required to test the connection.")
            else:
                from src.sftp.uploader import SFTPUploader
                uploader = SFTPUploader(sftp_host, int(sftp_port), sftp_username, sftp_remote_path)
                if sftp_password:
                    uploader.store_password(sftp_password)
                with st.spinner("Connecting..."):
                    ok, msg = uploader.test_connection()
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")
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
            cfg.sftp_enabled = enable_sftp
            if enable_sftp:
                cfg.sftp_host = sftp_host
                cfg.sftp_port = int(sftp_port)
                cfg.sftp_username = sftp_username
                cfg.sftp_remote_path = sftp_remote_path
                if sftp_password:
                    try:
                        from src.sftp.uploader import SFTPUploader
                        SFTPUploader(
                            sftp_host, int(sftp_port), sftp_username, sftp_remote_path
                        ).store_password(sftp_password)
                    except Exception as e:
                        st.warning(f"Could not store password: {e}")
            _go(5)
            st.rerun()

# ---------------------------------------------------------------------------
# Step 5 — Summary and activation
# ---------------------------------------------------------------------------

elif st.session_state.wizard_step == 5:
    st.subheader("Step 5 — Review & Activate")
    st.markdown("Review your configuration below, then click **Save & Activate Schedule** to apply.")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**File paths**")
        st.code(f"Input:  {cfg.input_dir}\nOutput: {cfg.output_dir}")
        st.markdown("**District config**")
        st.code(cfg.sis_type)
        st.markdown("**Daily schedule**")
        st.code(f"Every day at {cfg.schedule_time}")
    with col2:
        st.markdown("**SFTP**")
        if cfg.sftp_enabled:
            st.code(
                f"Host:   {cfg.sftp_host}:{cfg.sftp_port}\n"
                f"User:   {cfg.sftp_username}\n"
                f"Path:   {cfg.sftp_remote_path}"
            )
        else:
            st.code("Disabled")

    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("← Back"):
            _go(4)
            st.rerun()
    with col2:
        if st.button("💾 Save & Activate Schedule", type="primary"):
            cfg.save()
            _register_schedule(cfg)

    st.divider()
    if cfg.schedule_registered:
        st.success(
            f"Schedule is active — runs daily at {cfg.schedule_time}. "
            "You can close this window; the tool will run automatically."
        )


