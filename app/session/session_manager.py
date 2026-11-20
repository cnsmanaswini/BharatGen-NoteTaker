"""Tracks session state, enforces the lifecycle graph and maps to exit codes."""
from datetime import datetime

from app.meet.meeting_state import InvalidTransition, MeetingState, can_transition
from app.utils.logger import get_logger

log = get_logger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_DENIED = 3
EXIT_CRASHED = 4
EXIT_REMOVED = 5

_EXIT_CODES = {
    MeetingState.FAILED: EXIT_FAILED,
    MeetingState.DENIED: EXIT_DENIED,
    MeetingState.CRASHED: EXIT_CRASHED,
    MeetingState.REMOVED: EXIT_REMOVED,
}


def meeting_code(url: str) -> str:
    """'.../abc-defg-hij?authuser=0' -> 'abc-defg-hij'."""
    return url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def make_session_id(url: str, now: datetime | None = None) -> str:
    """Stable per-run identifier used for capture output files."""
    return f"{meeting_code(url)}_{(now or datetime.now()):%Y%m%d_%H%M%S}"


class SessionManager:
    def __init__(self) -> None:
        self.state = MeetingState.CREATED
        self.reason = ""

    def transition(self, new_state: MeetingState, reason: str = "") -> bool:
        """Move to `new_state`. Raises InvalidTransition for impossible moves.

        Returns True when the state actually changed.
        """
        if new_state == self.state:
            if reason and reason != self.reason:
                self.reason = reason
                log.info("Session state: %s (%s)", self.state.value, reason)
            return False
        if not can_transition(self.state, new_state):
            raise InvalidTransition(
                f"cannot go from {self.state.value} to {new_state.value}"
                + (f" ({reason})" if reason else ""))
        log.info("Session state: %s -> %s%s", self.state.value, new_state.value,
                 f" ({reason})" if reason else "")
        self.state = new_state
        self.reason = reason
        return True

    def safe_transition(self, new_state: MeetingState, reason: str = "") -> bool:
        """Best-effort transition for error paths; never raises."""
        try:
            return self.transition(new_state, reason)
        except InvalidTransition as exc:
            log.warning("Ignoring invalid state transition: %s", exc)
            return False

    def exit_code(self) -> int:
        return _EXIT_CODES.get(self.state, EXIT_OK)
