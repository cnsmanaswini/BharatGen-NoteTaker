"""Tracks session state and maps the outcome to a process exit code."""
from app.meet.meeting_state import MeetingState
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


class SessionManager:
    def __init__(self) -> None:
        self.state = MeetingState.NAVIGATING
        self.reason = ""

    def transition(self, new_state: MeetingState, reason: str = "") -> None:
        if new_state != self.state or reason:
            log.info("Session state: %s -> %s%s", self.state.value, new_state.value,
                     f" ({reason})" if reason else "")
        self.state = new_state
        self.reason = reason

    def exit_code(self) -> int:
        return _EXIT_CODES.get(self.state, EXIT_OK)