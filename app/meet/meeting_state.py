"""Meeting lifecycle states."""
from enum import Enum


class MeetingState(str, Enum):
    NAVIGATING = "navigating"
    PREJOIN = "prejoin"
    WAITING_ADMISSION = "waiting_admission"
    IN_MEETING = "in_meeting"
    RECONNECTING = "reconnecting"
    DENIED = "denied"
    REMOVED = "removed"
    ENDED = "ended"
    FAILED = "failed"
    CRASHED = "crashed"