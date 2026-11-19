"""Owns the Playwright instance and persistent browser context."""
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from app.browser.browser_config import build_launch_kwargs
from app.config.settings import Settings
from app.utils.logger import get_logger

log = get_logger(__name__)


class ChromiumManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.crashed = False

    def start(self) -> Page:
        self.settings.user_data_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs = build_launch_kwargs(self.settings)
        log.info("Launching browser (channel=%r, profile=%s)",
                 self.settings.browser_channel or "bundled-chromium",
                 self.settings.user_data_dir)
        self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        self.context.set_default_timeout(self.settings.default_timeout_ms)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.on("crash", self._on_crash)
        return self.page

    def _on_crash(self, _page) -> None:
        self.crashed = True
        log.error("Browser page crashed")

    def is_alive(self) -> bool:
        """False once the window is closed or the page has crashed."""
        if self.crashed:
            return False
        try:
            return bool(self.context) and any(not p.is_closed() for p in self.context.pages)
        except Exception:
            return False

    def close(self) -> None:
        for name, action in (("context", lambda: self.context and self.context.close()),
                             ("playwright", lambda: self._pw and self._pw.stop())):
            try:
                action()
            except Exception as exc:  # already closed is fine
                log.debug("Ignoring error closing %s: %s", name, exc)
        self.context = self.page = self._pw = None
        log.info("Browser resources released")