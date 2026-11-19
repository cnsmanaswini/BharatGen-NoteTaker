from app.meet.join_manager import DENIED_TEXT, JOIN_BUTTON
from app.meet.participant_monitor import LEAVE_CALL


def test_join_button_pattern():
    assert JOIN_BUTTON.match("Ask to join")
    assert JOIN_BUTTON.match("Join now")
    assert not JOIN_BUTTON.match("Join and use a phone for audio")


def test_leave_call_pattern():
    assert LEAVE_CALL.match("Leave call")
    assert not LEAVE_CALL.match("Call controls")


def test_denied_pattern():
    assert DENIED_TEXT.search("Someone in the meeting denied your request to join")