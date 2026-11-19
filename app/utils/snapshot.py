"""Failure diagnostics: screenshot + accessibility tree."""
from datetime import datetime
from pathlib import Path

from app.utils.logger import get_logger

log = get_logger(__name__)


def save_snapshot(page, label: str, directory: Path = Path("screenshots")) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f"{datetime.now():%Y%m%d_%H%M%S}_{label}"
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
    except Exception as exc:
        log.warning("Screenshot failed: %s", exc)
    try:
        Path(f"{base}.aria.txt").write_text(page.locator("body").aria_snapshot(), encoding="utf-8")
    except Exception as exc:
        log.warning("Accessibility snapshot failed: %s", exc)
    log.info("Diagnostic snapshot saved: %s.*", base)