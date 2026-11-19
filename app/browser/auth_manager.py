"""Detects Google sign-in state via session cookies and waits for manual login."""
import time

from app.browser.chromium_manager import ChromiumManager
from app.utils.logger import get_logger

log = get_logger(__name__)

SESSION_COOKIES = {"SID", "SAPISID", "__Secure-1PSID", "__Secure-3PSID"}
SIGNIN_URL = "https://accounts.google.com/"


class AuthManager:
    def __init__(self, browser: ChromiumManager, timeout_s: int):
        self.browser = browser
        self.timeout_s = timeout_s

    def is_authenticated(self) -> bool:
        cookies = self.browser.context.cookies("https://accounts.google.com")
        return any(c["name"] in SESSION_COOKIES for c in cookies)

    def ensure_authenticated(self) -> bool:
        """Return True once signed in. Prompts for manual login if needed."""
        page = self.browser.page
        page.goto(SIGNIN_URL, wait_until="domcontentloaded")
        if self.is_authenticated():
            log.info("Existing Google session found in persistent profile")
            return True

        log.warning("Not signed in. Please sign in manually in the browser window "
                    "(waiting up to %ss)...", self.timeout_s)
        deadline = time.monotonic() + self.timeout_s
        while time.monotonic() < deadline:
            if not self.browser.is_alive():
                log.error("Browser closed before sign-in completed")
                return False
            if self.is_authenticated():
                log.info("Sign-in detected; session saved to persistent profile")
                return True
            page.wait_for_timeout(2000)
        log.error("Timed out waiting for manual sign-in")
        return False
