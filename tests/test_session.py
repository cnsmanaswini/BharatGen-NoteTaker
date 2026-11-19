from app.meet.meeting_state import MeetingState
from app.session.session_manager import (EXIT_CRASHED, EXIT_DENIED, EXIT_FAILED,
                                         EXIT_OK, EXIT_REMOVED, SessionManager)


def test_exit_codes():
    s = SessionManager()
    assert s.exit_code() == EXIT_OK
    for state, code in [(MeetingState.FAILED, EXIT_FAILED), (MeetingState.DENIED, EXIT_DENIED),
                        (MeetingState.CRASHED, EXIT_CRASHED), (MeetingState.REMOVED, EXIT_REMOVED),
                        (MeetingState.ENDED, EXIT_OK)]:
        s.transition(state, "test")
        assert s.exit_code() == code


def test_transition_records_reason():
    s = SessionManager()
    s.transition(MeetingState.IN_MEETING, "joined")
    assert s.state == MeetingState.IN_MEETING and s.reason == "joined"