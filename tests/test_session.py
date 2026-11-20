from datetime import datetime

import pytest

from app.meet.meeting_state import InvalidTransition, MeetingState
from app.session.session_manager import (EXIT_CRASHED, EXIT_DENIED, EXIT_FAILED,
                                         EXIT_OK, EXIT_REMOVED, SessionManager,
                                         make_session_id, meeting_code)

STEPS = [MeetingState.STARTING, MeetingState.BROWSER_LAUNCHING,
         MeetingState.AUTHENTICATING, MeetingState.NAVIGATING, MeetingState.PREJOIN]


def _reach(*states):
    s = SessionManager()
    for state in STEPS + list(states):
        s.transition(state, "test setup")
    return s


def test_exit_codes():
    assert SessionManager().exit_code() == EXIT_OK
    assert _reach(MeetingState.FAILED).exit_code() == EXIT_FAILED
    assert _reach(MeetingState.JOIN_REQUESTED, MeetingState.WAITING_ADMISSION,
                  MeetingState.DENIED).exit_code() == EXIT_DENIED
    assert _reach(MeetingState.IN_MEETING, MeetingState.CRASHED).exit_code() == EXIT_CRASHED
    assert _reach(MeetingState.IN_MEETING, MeetingState.REMOVED).exit_code() == EXIT_REMOVED
    assert _reach(MeetingState.IN_MEETING, MeetingState.LEAVING,
                  MeetingState.ENDED).exit_code() == EXIT_OK


def test_transition_records_reason():
    s = SessionManager()
    assert s.transition(MeetingState.STARTING, "launching") is True
    assert s.state == MeetingState.STARTING and s.reason == "launching"
    assert s.transition(MeetingState.STARTING, "still launching") is False
    assert s.reason == "still launching"


def test_invalid_transition_is_rejected():
    s = SessionManager()
    with pytest.raises(InvalidTransition):
        s.transition(MeetingState.IN_MEETING, "skipped everything")
    assert s.state == MeetingState.CREATED
    assert s.safe_transition(MeetingState.IN_MEETING) is False
    assert s.state == MeetingState.CREATED


def test_session_id_and_meeting_code():
    assert meeting_code("https://meet.google.com/abc-defg-hij?authuser=0") == "abc-defg-hij"
    stamp = datetime(2026, 11, 19, 21, 30, 5)
    assert make_session_id("https://meet.google.com/abc-defg-hij", stamp) \
        == "abc-defg-hij_20261119_213005"
