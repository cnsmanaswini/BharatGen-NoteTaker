"""Presence and lifecycle monitoring inside a meeting."""
import re
import time
from typing import Callable

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from app.config.settings import Settings
from app.meet.meeting_state import MeetingState
from app.session.session_manager import SessionManager
from app.utils.logger import get_logger
from app.utils.snapshot import save_snapshot

log = get_logger(__name__)

LEAVE_CALL = re.compile(r"^leave call", re.I)
PEOPLE_BUTTON = re.compile(r"^people", re.I)
# Best-guess wording, NOT yet verified against real Meet screens. A snapshot is saved
# whenever one fires so the wording can be checked.
RECONNECTING_TEXT = re.compile(r"reconnecting|trying to reconnect|you're offline|connection (was )?lost", re.I)
REMOVED_TEXT = re.compile(r"removed from the (meeting|call)|you('ve| have) been removed", re.I)


def in_meeting(page: Page) -> bool:
    """True only when BOTH 'Leave call' and 'People' controls are visible.

    The waiting-room screen also shows 'Leave call', so that button alone is not
    proof the bot was admitted. The People button exists only inside the meeting.
    """
    try:
        leave = page.get_by_role("button", name=LEAVE_CALL)
        people = page.get_by_role("button", name=PEOPLE_BUTTON)
        return (leave.count() > 0 and leave.first.is_visible()
                and people.count() > 0 and people.first.is_visible())
    except Exception:
        return False


def parse_count(text: str | None) -> int | None:
    match = re.search(r"\d+", text or "")
    return int(match.group()) if match else None


def participant_count(page: Page) -> int | None:
    """Number shown on the People button, or None if it cannot be read."""
    try:
        button = page.get_by_role("button", name=PEOPLE_BUTTON).first
        return parse_count(button.inner_text(timeout=1000))
    except Exception:
        return None


def _text_visible(page: Page, pattern: re.Pattern) -> bool:
    try:
        loc = page.get_by_text(pattern)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def confirm_presence(page: Page, expected_name: str) -> str | None:
    """Open the People panel, read our own entry ('(You)'), then close the panel.

    Returns the displayed name, or None if it could not be read.
    """
    try:
        page.get_by_role("button", name=PEOPLE_BUTTON).first.click(timeout=10_000)
        me = page.get_by_role("listitem").filter(has_text="(You)").first
        me.wait_for(state="visible", timeout=10_000)
        shown = me.inner_text().split("(You)")[0].strip()
        if shown == expected_name:
            log.info("Presence confirmed in participant list as '%s'", shown)
        else:
            log.warning("Participant list shows '%s' but expected '%s'. "
                        "Set the Google account's name to '%s'.", shown, expected_name, expected_name)
        try:
            page.get_by_role("complementary", name="Side panel") \
                .get_by_role("button", name="Close").click(timeout=3_000)
        except PWTimeout:
            log.debug("Could not close People panel; leaving it open")
        return shown
    except Exception as exc:
        log.warning("Could not confirm presence in participant list: %s", exc)
        return None


def monitor_meeting(page: Page, session: SessionManager, settings: Settings,
                    is_alive: Callable[[], bool], poll_ms: int = 2000,
                    misses_allowed: int = 3) -> tuple[MeetingState, str]:
    """Block until the session ends. Returns (final_state, reason)."""
    misses = 0
    alone_since: float | None = None
    reconnect_since: float | None = None
    last_count: int | None = None
    try:
        while is_alive():
            if _text_visible(page, REMOVED_TEXT):
                save_snapshot(page, "removed")
                return MeetingState.REMOVED, "removed from the meeting by the host"

            if _text_visible(page, RECONNECTING_TEXT):
                now = time.monotonic()
                if reconnect_since is None:
                    reconnect_since = now
                    save_snapshot(page, "reconnecting")
                    session.transition(MeetingState.RECONNECTING, "connection problem detected")
                elif now - reconnect_since >= settings.reconnect_timeout_s:
                    return MeetingState.FAILED, f"could not reconnect within {settings.reconnect_timeout_s}s"
                page.wait_for_timeout(poll_ms)
                continue
            if reconnect_since is not None:
                reconnect_since = None
                session.transition(MeetingState.IN_MEETING, "connection restored")

            if in_meeting(page):
                misses = 0
                count = participant_count(page)
                if count != last_count:
                    log.info("Participant count: %s", count)
                    last_count = count
                if (count is not None and settings.alone_timeout_s > 0
                        and count <= settings.alone_participant_limit):
                    if alone_since is None:
                        alone_since = time.monotonic()
                        log.info("Only %d participant(s) left; leaving in %ds unless others join",
                                 count, settings.alone_timeout_s)
                    elif time.monotonic() - alone_since >= settings.alone_timeout_s:
                        return MeetingState.ENDED, f"alone in the meeting for {settings.alone_timeout_s}s"
                else:
                    alone_since = None
            else:
                misses += 1
                if misses >= misses_allowed:
                    return MeetingState.ENDED, "left the call (meeting ended)"
            page.wait_for_timeout(poll_ms)
    except PWError:
        pass  # window closed mid-poll
    return MeetingState.ENDED, "browser closed"