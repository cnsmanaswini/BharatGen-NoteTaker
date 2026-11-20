"""Capture Coordinator: runs audio (and later video/text) subsystems independently.

One failing subsystem must not stop the others, and the coordinator reports each
subsystem's real state rather than a single optimistic "recording" flag.
"""
import time

from app.capture.subsystem import CaptureState, CaptureSubsystem, SubsystemStatus
from app.utils.logger import get_logger

log = get_logger(__name__)


class CaptureCoordinator:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.started_at: float | None = None
        self._subsystems: dict[str, CaptureSubsystem] = {}
        self._stopped = False

    def register(self, subsystem: CaptureSubsystem) -> None:
        if self._stopped:
            raise RuntimeError("cannot register a capture subsystem after stopping")
        self._subsystems[subsystem.name] = subsystem

    def start(self) -> bool:
        """Initialize and start every subsystem. Returns True if any is running."""
        if not self._subsystems:
            log.info("No capture subsystems enabled; the meeting will not be recorded")
            return False
        self.started_at = time.time()
        any_running = False
        for name, subsystem in list(self._subsystems.items()):
            try:
                if not subsystem.initialize():
                    subsystem.state = CaptureState.FAILED
                    subsystem.detail = "initialization failed"
                    log.error("Capture subsystem %s failed to initialize", name)
                    continue
                any_running = bool(subsystem.start()) or any_running
            except Exception as exc:
                subsystem.state = CaptureState.FAILED
                subsystem.detail = str(exc)
                log.error("Capture subsystem %s failed to start: %s "
                          "(other capture continues)", name, exc)
        self._log_report("started")
        return any_running

    def stop(self) -> None:
        """Stop every subsystem independently. Never raises."""
        if self._stopped:
            return
        self._stopped = True
        for name, subsystem in self._subsystems.items():
            try:
                subsystem.stop()
            except Exception as exc:
                log.error("Error stopping capture subsystem %s: %s", name, exc)
            try:
                subsystem.cleanup()
            except Exception as exc:
                log.error("Error cleaning up capture subsystem %s: %s", name, exc)
        self._log_report("stopped")

    def status(self) -> dict[str, SubsystemStatus]:
        return {name: subsystem.get_status()
                for name, subsystem in self._subsystems.items()}

    def all_healthy(self) -> bool:
        """True only while every registered subsystem is actually RUNNING."""
        statuses = self.status()
        return bool(statuses) and all(s.state == CaptureState.RUNNING
                                      for s in statuses.values())

    def report(self) -> dict:
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "subsystems": {
                name: {"state": s.state.value, "detail": s.detail, "stats": s.stats}
                for name, s in self.status().items()
            },
        }

    def _log_report(self, verb: str) -> None:
        statuses = self.status()
        if not statuses:
            log.info("Capture %s: nothing was enabled", verb)
            return
        parts = [f"{name}={s.state.value}" + (f" ({s.detail})" if s.detail else "")
                 for name, s in statuses.items()]
        log.info("Capture %s: %s", verb, "; ".join(parts))
        if verb == "started" and not self.all_healthy():
            log.warning("Capture is incomplete: only %s; the meeting record may be missing data",
                        ", ".join(statuses))
