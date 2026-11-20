"""One worker process = exactly one meeting.

The single-meeting flow that used to live in `app.main` lives here so the same
joining, admission and monitoring logic serves both `python -m app.main <url>`
and each orchestrated worker process.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import fields, replace
from pathlib import Path

from playwright.sync_api import Error as PWError

from app.browser.auth_manager import AuthManager
from app.browser.chromium_manager import ChromiumManager
from app.capture import CaptureCoordinator
from app.capture.audio import AudioSubsystem
from app.config.settings import Settings
from app.meet.join_manager import join_meeting
from app.meet.meet_navigator import dismiss_media_prompt, open_meeting
from app.meet.meeting_state import MeetingState
from app.meet.participant_monitor import confirm_presence, monitor_meeting
from app.orchestration.session import (SessionReporter, SessionStatus,
                                       WorkerConfig, status_for)
from app.session.cleanup_manager import CleanupManager
from app.session.session_manager import EXIT_OK, SessionManager
from app.utils.logger import get_logger, setup_logging
from app.utils.redact import redact_meeting_url
from app.utils.snapshot import save_snapshot, set_snapshot_namespace

MEET_HOME = "https://meet.google.com/"

log = get_logger("worker")


def _terminate_cancelled(session: SessionManager, reason: str) -> bool:
    """Move a live session to CANCELLED. Returns True if it is now cancelled."""
    if session.state in (MeetingState.CANCELLED, MeetingState.ENDED,
                         MeetingState.COMPLETED, MeetingState.FAILED,
                         MeetingState.CRASHED):
        return session.state == MeetingState.CANCELLED
    return session.safe_transition(MeetingState.CANCELLED, reason)


def _run_browser_selftest(config: WorkerConfig, settings: Settings,
                          reporter: SessionReporter) -> int:
    """Launch a real isolated browser, stay alive briefly, then clean up.

    Used by the integration test to prove concurrent Chromium isolation without
    needing a Google account or a live meeting.
    """
    browser = ChromiumManager(settings)
    reporter.update(SessionStatus.STARTING, "selftest starting")
    try:
        page = browser.start()
        reporter.update(SessionStatus.BROWSER_STARTED,
                        f"browser started (pid {os.getpid()})")
        page.goto("about:blank")
        deadline = time.monotonic() + max(0.0, config.selftest_seconds)
        while time.monotonic() < deadline:
            if not browser.is_alive() or config.stop_requested():
                break
            page.wait_for_timeout(100)
        if config.stop_requested():
            reporter.update(SessionStatus.CANCELLED, "stop requested during selftest")
            return EXIT_OK
        if not browser.is_alive():
            reporter.update(SessionStatus.FAILED, error="browser closed during selftest")
            return 1
        reporter.update(SessionStatus.COMPLETED, "selftest complete")
        return EXIT_OK
    except Exception as exc:
        log.exception("Browser selftest failed")
        reporter.update(SessionStatus.FAILED, error=str(exc))
        return 1
    finally:
        browser.close()


def run_session(config: WorkerConfig, settings: Settings,
                reporter: SessionReporter | None = None) -> int:
    """Run one meeting (or a browser selftest) to completion. Returns exit code."""
    reporter = reporter or SessionReporter(config)
    set_snapshot_namespace(config.meeting_id)
    if config.mode == "browser_selftest":
        return _run_browser_selftest(config, settings, reporter)

    session = SessionManager()
    browser = ChromiumManager(settings)
    cleanup = CleanupManager(browser)
    coordinator: CaptureCoordinator | None = None

    def advance(state: MeetingState, reason: str = "") -> None:
        session.transition(state, reason)
        reporter.update(status_for(state), reason)

    def stopped() -> bool:
        return config.stop_requested()

    def is_alive() -> bool:
        return browser.is_alive() and not stopped()

    try:
        advance(MeetingState.STARTING, "worker started")
        advance(MeetingState.BROWSER_LAUNCHING, "launching browser")
        page = browser.start()
        reporter.update(SessionStatus.BROWSER_STARTED,
                        f"browser started (pid {os.getpid()})")

        advance(MeetingState.AUTHENTICATING, "checking Google session")
        if not AuthManager(browser, settings.auth_wait_timeout_s).ensure_authenticated():
            advance(MeetingState.FAILED, "sign-in did not complete")
            reporter.update(SessionStatus.FAILED, error="sign-in did not complete")
            return session.exit_code()

        advance(MeetingState.NAVIGATING, "opening meeting")
        if not config.meeting_url:
            page.goto(MEET_HOME, wait_until="domcontentloaded")
            log.info("Meet home opened. Press Ctrl+C or close the window to exit.")
            while is_alive():
                page.wait_for_timeout(1000)
            return EXIT_OK

        open_meeting(page, config.meeting_url)
        dismiss_media_prompt(page)
        advance(MeetingState.PREJOIN, "meeting page opened")

        state = join_meeting(page, settings, is_alive, on_state=advance)
        if stopped():
            _terminate_cancelled(session, "stopped while joining")
            reporter.update(SessionStatus.CANCELLED, "stopped while joining")
            return session.exit_code()
        session.transition(state, "join finished")
        join_error = None if state in (MeetingState.IN_MEETING, MeetingState.ENDED) \
            else f"join ended in {state.value}"
        reporter.update(status_for(state), "join finished", error=join_error)
        if state != MeetingState.IN_MEETING:
            return session.exit_code()

        save_snapshot(page, "in_meeting")
        confirm_presence(page, settings.bot_display_name)
        log.info("Bot is in the meeting; monitoring.")
        reporter.update(SessionStatus.MONITORING, "watching the meeting")

        if settings.audio_enabled:
            # A session-unique id keeps WAV/index/FFmpeg output apart. Note that
            # multiple loopback captures still record the same system output.
            coordinator = CaptureCoordinator(config.meeting_id)
            coordinator.register(AudioSubsystem(settings, coordinator.session_id))
            if not coordinator.start():
                log.error("Audio capture did not start; continuing in the meeting without audio")
        else:
            log.info("Audio capture is disabled (AUDIO_ENABLED=false)")

        end_state, reason = monitor_meeting(page, session, settings, is_alive)
        if stopped():
            end_state, reason = MeetingState.CANCELLED, "stopped by orchestrator"
        elif browser.crashed:
            end_state, reason = MeetingState.CRASHED, "browser page crashed"
        elif end_state != MeetingState.ENDED or reason != "browser closed":
            if browser.is_alive():
                save_snapshot(page, "session_end")  # shows what Meet displayed at the end
        if end_state in (MeetingState.ENDED, MeetingState.COMPLETED):
            session.transition(MeetingState.LEAVING, reason)
        session.transition(end_state, reason)
        reporter.update(status_for(end_state), reason,
                        error=None if status_for(end_state) != SessionStatus.FAILED else reason)
        return session.exit_code()
    except KeyboardInterrupt:
        session.safe_transition(MeetingState.CANCELLED, "stopped by user (Ctrl+C)")
        reporter.update(SessionStatus.CANCELLED, "stopped by user (Ctrl+C)")
        return session.exit_code()
    except PWError as exc:
        if stopped():
            session.safe_transition(MeetingState.CANCELLED, "stopped by orchestrator")
            reporter.update(SessionStatus.CANCELLED, "stopped by orchestrator")
        elif browser.crashed:
            session.safe_transition(MeetingState.CRASHED, "browser page crashed")
            reporter.update(SessionStatus.FAILED, error="browser page crashed")
        elif not browser.is_alive():
            session.safe_transition(MeetingState.ENDED, "browser closed")
            reporter.update(SessionStatus.COMPLETED, "browser closed")
        else:
            log.exception("Browser automation error")
            detail = str(exc).splitlines()[0]
            session.safe_transition(MeetingState.FAILED, detail)
            reporter.update(SessionStatus.FAILED, error=detail)
        return session.exit_code()
    except Exception as exc:
        log.exception("Unexpected failure")
        session.safe_transition(MeetingState.FAILED, str(exc))
        reporter.update(SessionStatus.FAILED, error=str(exc))
        return session.exit_code()
    finally:
        if coordinator is not None:
            coordinator.stop()  # never raises; logs each subsystem's final state
        cleanup.run()
        log.info("Final state: %s%s", session.state.value,
                 f" ({session.reason})" if session.reason else "")


_PATH_SETTINGS = {"audio_output_dir", "profiles_dir", "profile_source"}


def settings_for_worker(config: WorkerConfig) -> Settings:
    """Settings for one worker: parent overrides plus its own profile and log.

    `user_data_dir` and `log_dir` are always per-session, so they cannot be
    inherited from the parent.
    """
    settings = Settings.from_env()
    valid = {f.name for f in fields(Settings)}
    overrides = {}
    for key, value in config.settings_overrides.items():
        if key not in valid or key in ("user_data_dir", "log_dir"):
            continue
        overrides[key] = Path(value) if key in _PATH_SETTINGS and value else value
    settings = replace(settings, **overrides)
    return replace(settings,
                   user_data_dir=Path(config.profile_dir),
                   log_dir=Path(config.log_file).parent)


def worker_entry(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one meeting worker process")
    parser.add_argument("--config", required=True, help="Path to the session config JSON")
    args = parser.parse_args(argv)

    config = WorkerConfig.load(Path(args.config))
    settings = settings_for_worker(config)
    setup_logging(settings.log_level, session_id=config.meeting_id,
                  log_file=Path(config.log_file))
    log.info("Worker started for %s (pid %s, profile %s)",
             redact_meeting_url(config.meeting_url), os.getpid(), config.profile_dir)

    reporter = SessionReporter(config)
    try:
        exit_code = run_session(config, settings, reporter)
    except BaseException as exc:  # noqa: BLE001 - always report before exiting
        log.exception("Worker crashed")
        reporter.update(SessionStatus.FAILED, error=str(exc))
        return 1
    log.info("Worker exiting with code %s (final status %s)",
             exit_code, reporter.status.value)
    return exit_code


if __name__ == "__main__":
    sys.exit(worker_entry())
