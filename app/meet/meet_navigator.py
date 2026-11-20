"""Opening the meeting and clearing the pre-join obstacles."""
import re

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PWTimeout

from app.utils.logger import get_logger
from app.utils.redact import redact_meeting_url

log = get_logger(__name__)

CONTINUE_WITHOUT_MEDIA = re.compile(r"continue without microphone and camera", re.I)
DEVICE_OFF_BUTTONS = ("Turn off microphone", "Turn off camera")


def open_meeting(page: Page, url: str) -> None:
    log.info("Opening meeting %s", redact_meeting_url(url))
    page.goto(url, wait_until="domcontentloaded")


def dismiss_media_prompt(page: Page, timeout_ms: int = 10_000) -> bool:
    """Click 'Continue without microphone and camera' if Meet shows that dialog."""
    button = page.get_by_role("button", name=CONTINUE_WITHOUT_MEDIA)
    try:
        button.click(timeout=timeout_ms)
        log.info("Dismissed media permission dialog (continuing without mic/camera)")
        return True
    except PWTimeout:
        log.info("Media permission dialog did not appear")
        return False


def ensure_devices_off(page: Page) -> None:
    """If Meet offers 'Turn off ...' buttons, the device is on: switch it off."""
    for name in DEVICE_OFF_BUTTONS:
        button = page.get_by_role("button", name=name)
        if button.count() and button.first.is_visible():
            button.first.click()
            log.info("Clicked '%s'", name)