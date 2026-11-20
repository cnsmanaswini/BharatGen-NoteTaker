"""Meeting lifecycle states and the transitions the bot is allowed to make.

This is the *meeting* state machine only. Media capture health is tracked
separately (see app/capture) so a joined meeting can have failed audio or video
without the meeting state claiming otherwise.
"""
from enum import Enum


class MeetingState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    BROWSER_LAUNCHING = "browser_launching"
    AUTHENTICATING = "authenticating"
    NAVIGATING = "navigating"
    PREJOIN = "prejoin"
    JOIN_REQUESTED = "join_requested"
    WAITING_ADMISSION = "waiting_admission"
    IN_MEETING = "in_meeting"
    RECONNECTING = "reconnecting"
    LEAVING = "leaving"
    ENDED = "ended"
    COMPLETED = "completed"
    DENIED = "denied"
    REMOVED = "removed"
    FAILED = "failed"
    CRASHED = "crashed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({
    MeetingState.ENDED, MeetingState.COMPLETED, MeetingState.DENIED,
    MeetingState.REMOVED, MeetingState.FAILED, MeetingState.CRASHED,
    MeetingState.CANCELLED,
})

# Progressive edges. Abort edges are added to every live state below.
_EDGES: dict[MeetingState, set[MeetingState]] = {
    MeetingState.CREATED: {MeetingState.STARTING},
    MeetingState.STARTING: {MeetingState.BROWSER_LAUNCHING},
    MeetingState.BROWSER_LAUNCHING: {MeetingState.AUTHENTICATING},
    MeetingState.AUTHENTICATING: {MeetingState.NAVIGATING},
    MeetingState.NAVIGATING: {MeetingState.PREJOIN},
    MeetingState.PREJOIN: {
        MeetingState.JOIN_REQUESTED,
        MeetingState.WAITING_ADMISSION,
        MeetingState.IN_MEETING,   # "Join now" meetings skip the lobby
    },
    MeetingState.JOIN_REQUESTED: {
        MeetingState.WAITING_ADMISSION,
        MeetingState.IN_MEETING,
        MeetingState.DENIED,
    },
    MeetingState.WAITING_ADMISSION: {
        MeetingState.JOIN_REQUESTED,
        MeetingState.IN_MEETING,
        MeetingState.DENIED,
    },
    MeetingState.IN_MEETING: {
        MeetingState.RECONNECTING,
        MeetingState.LEAVING,
        MeetingState.ENDED,
        MeetingState.REMOVED,
    },
    MeetingState.RECONNECTING: {
        MeetingState.IN_MEETING,
        MeetingState.LEAVING,
        MeetingState.ENDED,
        MeetingState.REMOVED,
    },
    MeetingState.LEAVING: {MeetingState.ENDED, MeetingState.COMPLETED},
}

_ABORT_EDGES = {MeetingState.ENDED, MeetingState.FAILED, MeetingState.CRASHED,
                MeetingState.CANCELLED}

_ALLOWED: dict[MeetingState, frozenset[MeetingState]] = {
    state: frozenset(edges | _ABORT_EDGES)
    for state, edges in _EDGES.items()
}
for _terminal in TERMINAL_STATES:
    _ALLOWED.setdefault(_terminal, frozenset())


class InvalidTransition(ValueError):
    """Raised when the session is asked to make an impossible state change."""


def allowed_from(state: MeetingState) -> frozenset[MeetingState]:
    return _ALLOWED.get(state, frozenset())


def can_transition(current: MeetingState, new_state: MeetingState) -> bool:
    """True if `current -> new_state` is legal (or a no-op)."""
    return new_state == current or new_state in allowed_from(current)
