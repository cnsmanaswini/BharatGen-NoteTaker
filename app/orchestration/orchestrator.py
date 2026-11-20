"""Central registry and process supervisor for concurrent meeting sessions.

One `MeetingSession` = one worker process = one Chromium instance = one meeting.
The orchestrator never touches Playwright itself; it starts workers, follows
their status files, enforces limits, and stops individual workers on request.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from app.config.settings import Settings
from app.orchestration.process import (WorkerHandle, default_worker_factory,
                                       prepare_profile)
from app.orchestration.session import (MeetingSession, SessionStatus,
                                       WorkerConfig, atomic_write_json,
                                       is_valid_meeting_url, new_meeting_id,
                                       read_worker_state)
from app.session.session_manager import meeting_code
from app.utils.logger import get_logger
from app.utils.redact import redact_meeting_url

log = get_logger(__name__)

WorkerFactory = Callable[[MeetingSession, WorkerConfig, Settings], WorkerHandle]


class OrchestrationError(RuntimeError):
    """Base class for orchestration refusals."""


class ConcurrencyLimitError(OrchestrationError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"maximum concurrent meetings reached ({limit})")
        self.limit = limit


class DuplicateMeetingError(OrchestrationError):
    def __init__(self, meeting_id: str, meeting_url: str) -> None:
        super().__init__(f"meeting {redact_meeting_url(meeting_url)} is already active as {meeting_id}")
        self.meeting_id = meeting_id
        self.meeting_url = meeting_url


class MeetingOrchestrator:
    def __init__(self, settings: Settings, *, max_concurrent: int | None = None,
                 allow_duplicate: bool | None = None,
                 worker_factory: WorkerFactory | None = None,
                 monitor_interval_s: float | None = None,
                 startup_timeout_s: float | None = None,
                 shutdown_timeout_s: float | None = None,
                 now: Callable[[], datetime] = datetime.now,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.max_concurrent = int(max_concurrent if max_concurrent is not None
                                  else settings.max_concurrent_meetings)
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self.allow_duplicate = (settings.allow_duplicate_meetings
                                if allow_duplicate is None else allow_duplicate)
        self.startup_timeout_s = float(startup_timeout_s if startup_timeout_s is not None
                                       else settings.worker_startup_timeout_s)
        self.shutdown_timeout_s = float(shutdown_timeout_s if shutdown_timeout_s is not None
                                        else settings.worker_shutdown_timeout_s)
        self._factory = worker_factory or default_worker_factory
        self._now = now
        self._clock = clock
        self._sessions: dict[str, MeetingSession] = {}
        self._handles: dict[str, WorkerHandle] = {}
        self._started_mono: dict[str, float] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_interval = monitor_interval_s
        self._settings_overrides = {
            key: str(value) if isinstance(value, Path) else value
            for key, value in asdict(settings).items()
        }
        if monitor_interval_s is not None:
            self.start_monitor()

    # ---- lifecycle -----------------------------------------------------------
    def start_meeting(self, meeting_url: str, *, mode: str = "meet",
                      selftest_seconds: float = 3.0) -> MeetingSession:
        """Launch one worker for `meeting_url`.

        Raises ConcurrencyLimitError or DuplicateMeetingError when the request
        would violate a limit. A worker that fails to start is recorded as a
        FAILED session rather than raising, so other meetings are unaffected.

        `mode="browser_selftest"` is used by the real-browser integration test:
        the worker opens an about:blank Chromium instance and stays alive for
        `selftest_seconds` without joining any meeting.
        """
        if not is_valid_meeting_url(meeting_url):
            raise ValueError(f"not a valid Google Meet URL: {meeting_url}")
        with self._lock:
            self._refresh_locked()
            active = [s for s in self._sessions.values() if s.is_active]
            if not self.allow_duplicate:
                code = meeting_code(meeting_url)
                for session in active:
                    if meeting_code(session.meeting_url) == code:
                        raise DuplicateMeetingError(session.meeting_id, meeting_url)
            if len(active) >= self.max_concurrent:
                raise ConcurrencyLimitError(self.max_concurrent)

            meeting_id = new_meeting_id(meeting_url)
            created = self._now()
            run_dir = Path(self.settings.log_dir) / "sessions" / meeting_id
            log_file = run_dir / "session.log"
            session = MeetingSession(
                meeting_id=meeting_id,
                meeting_url=meeting_url,
                status=SessionStatus.STARTING,
                created_at=created,
                started_at=created,
                profile_dir=Path(self.settings.profiles_dir) / meeting_id,
                log_file=log_file,
                run_dir=run_dir,
            )
            config = WorkerConfig(
                meeting_id=meeting_id,
                meeting_url=meeting_url,
                profile_dir=str(session.profile_dir),
                log_file=str(log_file),
                state_file=str(run_dir / "state.json"),
                stop_file=str(run_dir / "stop.request"),
                events_file=str(run_dir / "events.jsonl"),
                mode=mode,
                selftest_seconds=selftest_seconds,
                settings_overrides=self._settings_overrides,
            )
            self._sessions[meeting_id] = session
            self._started_mono[meeting_id] = self._clock()
            log.info("Session %s starting for %s", meeting_id,
                     redact_meeting_url(meeting_url))
            try:
                prepare_profile(Path(self.settings.profile_source),
                                Path(session.profile_dir),
                                self.settings.profile_clone)
                run_dir.mkdir(parents=True, exist_ok=True)
                config.save(run_dir / "config.json")
                handle = self._factory(session, config, self.settings)
                self._handles[meeting_id] = handle
                session.worker_pid = handle.pid
                log.info("Session %s worker pid %s", meeting_id, handle.pid)
            except Exception as exc:
                log.exception("Worker for %s failed to start", meeting_id)
                session.status = SessionStatus.FAILED
                session.error = f"worker failed to start: {exc}"
                session.ended_at = self._now()
                self._close_handle(meeting_id)
            return replace(session)

    def stop_meeting(self, meeting_id: str) -> bool:
        """Stop one meeting; other sessions keep running. Returns False if unknown/finished."""
        with self._lock:
            session = self._sessions.get(meeting_id)
            if session is None or session.is_terminal:
                return False
            log.info("Stopping session %s", meeting_id)
            self._request_stop(session)
            session.status = SessionStatus.CANCELLED
            session.reason = "stopped by orchestrator"
            session.ended_at = self._now()
            handle = self._handles.get(meeting_id)
            if handle is not None:
                handle.stop(self.shutdown_timeout_s)
                session.exit_code = handle.poll()
            self._close_handle(meeting_id)
            return True

    def shutdown(self) -> None:
        """Stop every active session and the monitor thread."""
        self._stop_event.set()
        thread = self._monitor_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        with self._lock:
            for session in [s for s in self._sessions.values() if s.is_active]:
                self._request_stop(session)
                session.status = SessionStatus.CANCELLED
                session.reason = "orchestrator shut down"
                session.ended_at = self._now()
            handles = list(self._handles.items())
            self._handles.clear()
        for meeting_id, handle in handles:
            try:
                handle.stop(self.shutdown_timeout_s)
            except Exception as exc:
                log.warning("Error stopping worker %s: %s", meeting_id, exc)
        log.info("Orchestrator shut down")

    def close(self) -> None:
        self.shutdown()

    def __enter__(self) -> "MeetingOrchestrator":
        return self

    def __exit__(self, *_exc) -> None:
        self.shutdown()

    # ---- monitoring ----------------------------------------------------------
    def start_monitor(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        interval = self._monitor_interval if self._monitor_interval is not None else 0.5
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval,),
                                                name="meeting-orchestrator-monitor",
                                                daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self, interval: float) -> None:
        while not self._stop_event.wait(interval):
            try:
                self.refresh()
            except Exception:
                log.exception("Orchestrator monitor refresh failed")

    def refresh(self) -> None:
        """Reconcile registry with worker state files and process exit codes."""
        with self._lock:
            self._refresh_locked()

    def wait(self, timeout_s: float | None = None, poll_s: float = 0.1) -> bool:
        """Block until no session is active. Returns False on timeout."""
        deadline = None if timeout_s is None else self._clock() + timeout_s
        while True:
            self.refresh()
            # A session can report COMPLETED just before its process exits; keep
            # waiting so exit codes and browser cleanup are accounted for.
            with self._lock:
                drained = not self._handles
            if not self.active_sessions() and drained:
                return True
            if deadline is not None and self._clock() >= deadline:
                return False
            time.sleep(poll_s)

    def _refresh_locked(self) -> None:
        for meeting_id, session in list(self._sessions.items()):
            state = None
            if session.run_dir is not None:
                state = read_worker_state(Path(session.run_dir) / "state.json")
            if state:
                self._apply_worker_state(session, state)

            handle = self._handles.get(meeting_id)
            exit_code = handle.poll() if handle is not None else None
            if exit_code is not None:
                if not session.is_terminal:
                    session.status = SessionStatus.FAILED
                    session.error = (session.error
                                     or f"worker exited without a terminal status "
                                        f"(code {exit_code})")
                    session.ended_at = session.ended_at or self._now()
                    log.error("Session %s worker exited unexpectedly (code %s): %s",
                              meeting_id, exit_code, session.error)
                session.exit_code = exit_code
                session.ended_at = session.ended_at or self._now()
                self._close_handle(meeting_id)
                continue

            if session.is_terminal:
                continue
            started = self._started_mono.get(meeting_id)
            if (started is not None
                    and session.status in (SessionStatus.CREATED, SessionStatus.STARTING)
                    and self._clock() - started > self.startup_timeout_s):
                session.status = SessionStatus.TIMED_OUT
                session.error = (f"worker did not finish starting within "
                                 f"{self.startup_timeout_s:.0f}s")
                session.ended_at = self._now()
                log.error("Session %s timed out during startup", meeting_id)
                self._request_stop(session)
                self._close_handle(meeting_id, stop=True)

    def _apply_worker_state(self, session: MeetingSession, state: dict) -> None:
        if session.is_terminal:
            return
        try:
            status = SessionStatus(state.get("status", ""))
        except ValueError:
            log.warning("Ignoring unknown worker status %r for %s",
                        state.get("status"), session.meeting_id)
            return
        session.status = status
        session.reason = state.get("reason") or session.reason
        if status == SessionStatus.FAILED and state.get("error"):
            session.error = state["error"]
        if status in (SessionStatus.FAILED, SessionStatus.COMPLETED,
                      SessionStatus.CANCELLED, SessionStatus.TIMED_OUT):
            session.ended_at = session.ended_at or self._now()
            log.info("Session %s %s (%s)", session.meeting_id, status.value,
                     session.reason or session.error or "no reason")

    # ---- queries -------------------------------------------------------------
    def list_sessions(self, active_only: bool = False) -> list[MeetingSession]:
        with self._lock:
            self._refresh_locked()
            sessions = [replace(s) for s in self._sessions.values()
                        if not active_only or s.is_active]
        return sorted(sessions, key=lambda s: s.created_at)

    def active_sessions(self) -> list[MeetingSession]:
        return self.list_sessions(active_only=True)

    def get_session(self, meeting_id: str) -> MeetingSession | None:
        with self._lock:
            self._refresh_locked()
            session = self._sessions.get(meeting_id)
            return replace(session) if session else None

    def get_status(self, meeting_id: str) -> dict | None:
        session = self.get_session(meeting_id)
        return session.to_dict() if session else None

    def resolve_meeting_id(self, prefix: str) -> str:
        """Resolve a full id or unambiguous prefix (CLI convenience)."""
        with self._lock:
            matches = [mid for mid in self._sessions if mid.startswith(prefix)]
        if not matches:
            raise KeyError(f"no meeting matches {prefix!r}")
        if len(matches) > 1:
            raise ValueError(f"{prefix!r} matches {len(matches)} meetings; use a longer id")
        return matches[0]

    # ---- internals -----------------------------------------------------------
    def _request_stop(self, session: MeetingSession) -> None:
        if session.run_dir is None:
            return
        try:
            atomic_write_json(Path(session.run_dir) / "stop.request",
                              {"meeting_id": session.meeting_id,
                               "requested_at": self._now().isoformat()})
        except OSError as exc:
            log.warning("Could not write stop request for %s: %s",
                        session.meeting_id, exc)

    def _close_handle(self, meeting_id: str, stop: bool = False) -> None:
        handle = self._handles.pop(meeting_id, None)
        if handle is None:
            return
        if stop:
            try:
                handle.stop(self.shutdown_timeout_s)
            except Exception as exc:
                log.warning("Error stopping worker %s: %s", meeting_id, exc)
