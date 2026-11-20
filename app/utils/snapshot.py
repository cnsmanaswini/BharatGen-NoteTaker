"""Failure diagnostics: screenshot + accessibility tree."""
from datetime import datetime
from pathlib import Path

from app.utils.logger import get_logger

log = get_logger(__name__)

# Set once per worker process (see set_snapshot_namespace) so concurrent
# sessions never overwrite each other's diagnostics.
_namespace: str | None = None


def set_snapshot_namespace(namespace: str | None) -> None:
    """Route snapshots into screenshots/<namespace>/ for this process."""
    global _namespace
    _namespace = namespace


def save_snapshot(page, label: str, directory: Path | None = None) -> None:
    base_dir = Path(directory) if directory is not None else Path("screenshots")
    if directory is None and _namespace:
        base_dir = base_dir / _namespace
    base_dir.mkdir(parents=True, exist_ok=True)
    base = base_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{label}"
    try:
        page.screenshot(path=f"{base}.png", full_page=True)
    except Exception as exc:
        log.warning("Screenshot failed: %s", exc)
    try:
        Path(f"{base}.aria.txt").write_text(page.locator("body").aria_snapshot(), encoding="utf-8")
    except Exception as exc:
        log.warning("Accessibility snapshot failed: %s", exc)
    log.info("Diagnostic snapshot saved: %s.*", base)