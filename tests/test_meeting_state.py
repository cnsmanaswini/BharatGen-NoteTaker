import pytest

from app.meet.meeting_state import (TERMINAL_STATES, InvalidTransition, MeetingState,
                                    allowed_from, can_transition)

M = MeetingState


def test_full_happy_path():
    path = [M.CREATED, M.STARTING, M.BROWSER_LAUNCHING, M.AUTHENTICATING, M.NAVIGATING,
            M.PREJOIN, M.JOIN_REQUESTED, M.WAITING_ADMISSION, M.IN_MEETING,
            M.RECONNECTING, M.IN_MEETING, M.LEAVING, M.ENDED]
    assert all(can_transition(a, b) for a, b in zip(path, path[1:]))


def test_lobby_denial_and_direct_join_paths():
    assert can_transition(M.WAITING_ADMISSION, M.DENIED)
    assert can_transition(M.PREJOIN, M.IN_MEETING)      # "Join now" meetings
    assert can_transition(M.JOIN_REQUESTED, M.DENIED)


def test_terminal_states_have_no_outgoing_edges():
    for terminal in TERMINAL_STATES:
        assert allowed_from(terminal) == frozenset()
        assert not can_transition(terminal, M.IN_MEETING)


def test_impossible_transitions():
    assert not can_transition(M.CREATED, M.IN_MEETING)
    assert not can_transition(M.IN_MEETING, M.AUTHENTICATING)
    assert not can_transition(M.PREJOIN, M.REMOVED)
    assert not can_transition(M.ENDED, M.FAILED)


def test_abort_edges_available_while_live():
    for state in (M.CREATED, M.AUTHENTICATING, M.PREJOIN, M.IN_MEETING):
        assert can_transition(state, M.FAILED)
        assert can_transition(state, M.CANCELLED)


def test_same_state_is_a_noop():
    assert can_transition(M.IN_MEETING, M.IN_MEETING)


def test_session_manager_rejects_illegal_jump():
    from app.session.session_manager import SessionManager
    s = SessionManager()
    with pytest.raises(InvalidTransition):
        s.transition(M.IN_MEETING)
    assert s.state == M.CREATED
