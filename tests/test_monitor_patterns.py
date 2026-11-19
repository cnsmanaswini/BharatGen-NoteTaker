from app.meet.participant_monitor import (PEOPLE_BUTTON, REMOVED_TEXT, RECONNECTING_TEXT,
                                          parse_count)


def test_parse_count():
    assert parse_count("2") == 2
    assert parse_count("People\n4") == 4
    assert parse_count("") is None
    assert parse_count(None) is None


def test_patterns():
    assert PEOPLE_BUTTON.match("People")
    assert REMOVED_TEXT.search("You've been removed from the meeting")
    assert RECONNECTING_TEXT.search("Reconnecting...")
    assert not REMOVED_TEXT.search("Welcome to the meeting")