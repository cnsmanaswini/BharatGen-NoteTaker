"""Real-process, real-Chromium concurrency checks.

These do not join a Google Meet: each worker runs in `browser_selftest` mode,
which launches its own Chromium, opens about:blank, stays alive briefly and
shuts down. That is enough to verify process isolation, per-session profiles,
unique worker PIDs and independent cancellation without needing an account.

Skipped when Google Chrome (the repo's recommended BROWSER_CHANNEL=chrome) is
not installed.
"""
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.orchestration.orchestrator import MeetingOrchestrator
from app.orchestration.session import SessionStatus

URL_A = "https://meet.google.com/aaa-bbbb-ccc"
URL_B = "https://meet.google.com/ddd-eeee-fff"


def _find_chrome() -> Path | None:
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if root:
            candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if candidate.exists():
                return candidate
    for command in ("google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(command)
        if found:
            return Path(found)
    mac = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return mac if mac.exists() else None


CHROME = _find_chrome()
pytestmark = pytest.mark.skipif(
    CHROME is None, reason="Google Chrome is not installed (BROWSER_CHANNEL=chrome)")


@pytest.fixture
def settings(tmp_path) -> Settings:
    seed = tmp_path / "seed"
    seed.mkdir()
    return replace(
        Settings.from_env(),
        browser_channel="chrome",  # same channel the repo uses by default
        headless=True,
        log_dir=tmp_path / "logs",
        profiles_dir=tmp_path / "profiles",
        profile_source=seed,
        profile_clone=False,
        audio_enabled=False,
        worker_startup_timeout_s=120,
        worker_shutdown_timeout_s=20,
        max_concurrent_meetings=4,
    )


def _wait_for(orch: MeetingOrchestrator, meeting_id: str,
              statuses: set[SessionStatus], timeout_s: float = 90):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        orch.refresh()
        session = orch.get_session(meeting_id)
        if session and session.status in statuses:
            return session
        time.sleep(0.25)
    session = orch.get_session(meeting_id)
    raise AssertionError(f"timed out waiting for {statuses}; last={session}")


def test_two_real_chromium_workers_run_concurrently(settings):
    orch = MeetingOrchestrator(settings, monitor_interval_s=None)
    try:
        a = orch.start_meeting(URL_A, mode="browser_selftest", selftest_seconds=3)
        b = orch.start_meeting(URL_B, mode="browser_selftest", selftest_seconds=3)

        assert a.worker_pid and b.worker_pid and a.worker_pid != b.worker_pid
        assert a.profile_dir != b.profile_dir

        assert orch.wait(timeout_s=120) is True

        final_a = orch.get_session(a.meeting_id)
        final_b = orch.get_session(b.meeting_id)
        assert final_a.status is SessionStatus.COMPLETED, final_a.error
        assert final_b.status is SessionStatus.COMPLETED, final_b.error
        assert final_a.exit_code == 0 and final_b.exit_code == 0
        assert final_a.profile_dir.is_dir() and final_b.profile_dir.is_dir()

        for session in (final_a, final_b):
            log_text = Path(session.log_file).read_text(encoding="utf-8")
            assert session.meeting_id in log_text
            assert "browser started" in log_text.lower()
    finally:
        orch.shutdown()


def test_stopping_one_real_worker_leaves_the_other_running(settings):
    orch = MeetingOrchestrator(settings, monitor_interval_s=None)
    try:
        a = orch.start_meeting(URL_A, mode="browser_selftest", selftest_seconds=30)
        b = orch.start_meeting(URL_B, mode="browser_selftest", selftest_seconds=30)
        _wait_for(orch, a.meeting_id, {SessionStatus.BROWSER_STARTED,
                                       SessionStatus.COMPLETED})
        _wait_for(orch, b.meeting_id, {SessionStatus.BROWSER_STARTED,
                                       SessionStatus.COMPLETED})

        assert orch.stop_meeting(a.meeting_id) is True
        stopped = orch.get_session(a.meeting_id)
        assert stopped.status is SessionStatus.CANCELLED
        assert stopped.is_terminal

        # B must still be a live process after A's whole tree was stopped.
        still_running = orch.get_session(b.meeting_id)
        assert still_running.is_active
        assert still_running.status is SessionStatus.BROWSER_STARTED

        assert orch.stop_meeting(b.meeting_id) is True
        assert orch.wait(timeout_s=60) is True
        assert orch.active_sessions() == []
        assert orch.get_session(b.meeting_id).status is SessionStatus.CANCELLED
    finally:
        orch.shutdown()
