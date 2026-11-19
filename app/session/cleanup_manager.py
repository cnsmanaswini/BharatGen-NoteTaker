"""One place that always releases everything, whatever ended the session."""
from app.browser.chromium_manager import ChromiumManager
from app.meet.join_manager import leave_meeting
from app.utils.logger import get_logger

log = get_logger(__name__)


class CleanupManager:
    def __init__(self, browser: ChromiumManager) -> None:
        self.browser = browser
        self._done = False

    def run(self) -> None:
        if self._done:
            return
        self._done = True
        page = self.browser.page
        if page is not None and not self.browser.crashed:
            leave_meeting(page)  # best effort; never raises
        self.browser.close()