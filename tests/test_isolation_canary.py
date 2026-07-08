"""Isolation canary — the tripwire for the D3 test-isolation fix.

Exercises the exact write paths that historically seeded ~365 pytest fixture
records into the real ``~/.districtsync/etl_tool.log`` (and could have clobbered
``config.json``) — ``AppConfig.save()`` and ``get_logger()`` — under the autouse
``isolated_user_profile`` fixture, then asserts the real user profile is
byte-untouched (mtime/existence unchanged vs the pristine baseline captured at
conftest import) AND that the writes landed in the isolated tmp profile instead.

HONEST SCOPE: this proves the seams are *redirected* — the guarantee holds only
while writes route through the patched ``paths.user_data_dir`` seam. It is a
tripwire against regressions (a new module that reaches ``Path.home()`` directly,
or an AppConfig that stops resolving through the seam), NOT a mechanical
impossibility. Slice 4b extends this canary with a store write.
"""

from __future__ import annotations

from pathlib import Path

from src.config.app_config import AppConfig
from src.utils import paths
from src.utils.logger import get_logger


def test_isolation_canary_leaves_real_profile_untouched(
    isolated_user_profile: Path,
    real_profile_baseline: dict[str, tuple[Path, int | None]],
) -> None:
    # Exercise the historically-polluting write paths under the isolation fixture.
    AppConfig(input_dir="/x", output_dir="/y", sis_type="myedbc").save()
    log = get_logger("canary_probe")
    log.info("canary probe line — must NOT reach the real etl_tool.log")
    # Slice 4b adds a store write here: write_run_record(...) via user_history_db().

    # The writes landed in the ISOLATED tmp profile...
    assert (isolated_user_profile / "config.json").exists()
    assert paths.user_log_file() == isolated_user_profile / "etl_tool.log"
    assert (isolated_user_profile / "etl_tool.log").exists()

    # ...and the REAL profile is byte-untouched (existence + mtime vs the pristine
    # baseline captured at conftest import, before any seam was patched).
    for _name, (real_path, baseline_mtime) in real_profile_baseline.items():
        current = real_path.stat().st_mtime_ns if real_path.exists() else None
        assert current == baseline_mtime, (
            f"ISOLATION BREACH: {real_path} changed during the test run "
            f"(baseline mtime={baseline_mtime}, now={current}). A write path bypassed "
            f"the paths.user_data_dir seam."
        )
