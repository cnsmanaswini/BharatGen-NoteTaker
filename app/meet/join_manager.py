"""Clicking Join now / Ask to join and waiting for entry."""
import re
import time
from typing import Callable

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from app.config.settings import Settings
from app.meet.meet_navigator import ensure_devices_off
from app.meet.meeting_state import MeetingState
from app.meet.participant_monitor import LEAVE_CALL, PEOPLE_BUTTON, in_meeting
from app.utils.logger import get_logger
from app.utils.snapshot import save_snapshot

log = get_logger(__name__)

JOIN_BUTTON = re.compile(r"^(ask to join|join now)\b", re.I)
# Best-guess wording for a rejected request; verify against a real denial.
DENIED_TEXT = re.compile(r"denied your request|can't join this (video )?call|removed you", re.I)


def join_meeting(page: Page, settings: Settings, is_alive: Callable[[], bool]) -> MeetingState:
    join_button = page.get_by_role("button", name=JOIN_BUTTON).first
    try:
        join_button.wait_for(state="visible", timeout=settings.default_timeout_ms)
    except PWTimeout:
        log.error("No 'Join now' / 'Ask to join' button found")
        save_snapshot(page, "no_join_button")
        return MeetingState.FAILED

    label = join_button.inner_text().strip()
    log.info("Pre-join screen ready; button: '%s'", label)
    ensure_devices_off(page)
    join_button.click()

    if "ask" in label.lower():
        log.info("Admission requested; waiting for the host (up to %ss)", settings.admission_timeout_s)
        return _wait_for_entry(page, settings.admission_timeout_s, is_alive)
    return _wait_for_entry(page, settings.default_timeout_ms // 1000, is_alive)


def _wait_for_entry(page: Page, timeout_s: int, is_alive: Callable[[], bool]) -> MeetingState:
    """Entered = 'Leave call' AND 'People' visible. Waiting room never has People."""
    denied = page.get_by_text(DENIED_TEXT)
    target = page.get_by_role("button", name=PEOPLE_BUTTON).or_(denied)
    start = last_log = time.monotonic()
    while time.monotonic() - start < timeout_s:
        if not is_alive():
            log.error("Browser closed while waiting for admission")
            return MeetingState.ENDED
        try:
            target.first.wait_for(state="visible", timeout=2000)
        except PWTimeout:
            pass
        except PWError:
            log.error("Browser closed while waiting for admission")
            return MeetingState.ENDED
        else:
            if in_meeting(page):
                log.info("Admitted: entered the meeting")
                return MeetingState.IN_MEETING
            if denied.count() and denied.first.is_visible():
                log.error("Join request was denied")
                save_snapshot(page, "denied")
                return MeetingState.DENIED
            page.wait_for_timeout(500)
        if time.monotonic() - last_log >= 30:
            log.info("Still waiting for admission (%ds)...", int(time.monotonic() - start))
            last_log = time.monotonic()
    log.error("Timed out waiting for admission")
    save_snapshot(page, "admission_timeout")
    return MeetingState.FAILED


def leave_meeting(page: Page) -> None:
    """Best-effort: click Leave call so the bot doesn't linger as a ghost participant."""
    try:
        if in_meeting(page):
            page.get_by_role("button", name=LEAVE_CALL).first.click(timeout=3000)
            log.info("Left the call")
    except Exception as exc:
        log.debug("Could not click Leave call: %s", exc)