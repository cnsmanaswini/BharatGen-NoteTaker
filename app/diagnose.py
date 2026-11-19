"""Diagnostic capture of the real Google Meet UI.

Usage:  python -m app.diagnose https://meet.google.com/abc-defg-hij

Opens the meeting, captures the pre-join screen, then lets YOU drive the page
(type name, click Ask to join, get admitted, ...). After each stage, return to
the terminal, type a short label and press Enter to capture that state.
Output goes to diagnostics/<timestamp>/ (git-ignored).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from app.browser.auth_manager import AuthManager
from app.browser.chromium_manager import ChromiumManager
from app.config.settings import Settings
from app.main import MEET_URL_RE
from app.utils.logger import get_logger, setup_logging

log = get_logger("diagnose")

# Collects interactive elements with their accessible attributes (read-only).
_COLLECT_JS = """
() => {
  const sel = 'button, input, textarea, select, a[href], [role], [aria-label]';
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    out.push({
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role'),
      ariaLabel: el.getAttribute('aria-label'),
      ariaPressed: el.getAttribute('aria-pressed'),
      ariaChecked: el.getAttribute('aria-checked'),
      dataIsMuted: el.getAttribute('data-is-muted'),
      type: el.getAttribute('type'),
      placeholder: el.getAttribute('placeholder'),
      value: ('value' in el) ? String(el.value).slice(0, 80) : null,
      text: (el.innerText || '').trim().slice(0, 80),
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      visible: r.width > 0 && r.height > 0,
    });
  }
  return out;
}
"""


def capture(page, out_dir: Path, index: int, label: str) -> None:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label) or "state"
    prefix = out_dir / f"{index:02d}_{safe}"
    log.info("Capturing '%s' (url=%s)", label, page.url)

    page.screenshot(path=f"{prefix}.png", full_page=True)

    try:
        aria = page.locator("body").aria_snapshot()
    except Exception as exc:
        aria = f"aria_snapshot failed: {exc}"
    Path(f"{prefix}.aria.txt").write_text(aria, encoding="utf-8")

    try:
        elements = page.evaluate(_COLLECT_JS)
    except Exception as exc:
        elements = [{"error": str(exc)}]
    meta = {"label": label, "url": page.url, "title": page.title(),
            "time": datetime.now().isoformat(), "elements": elements}
    Path(f"{prefix}.elements.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2 or not MEET_URL_RE.match(sys.argv[1]):
        print("Usage: python -m app.diagnose https://meet.google.com/abc-defg-hij")
        return 2
    url = sys.argv[1]

    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.log_dir)
    out_dir = Path("diagnostics") / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    browser = ChromiumManager(settings)
    try:
        page = browser.start()
        if not AuthManager(browser, settings.auth_wait_timeout_s).ensure_authenticated():
            return 1
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle")
        capture(page, out_dir, 1, "prejoin")

        index = 2
        print("\nDrive the page yourself in the browser. After each stage, type a label")
        print("here (e.g. name_typed, asked_to_join, admitted, participants_open) + Enter.")
        print("Blank line or Ctrl+C to finish.\n")
        while browser.is_alive():
            label = input("label> ").strip()
            if not label:
                break
            capture(page, out_dir, index, label)
            index += 1
        log.info("Diagnostics saved to %s", out_dir.resolve())
        return 0
    except (KeyboardInterrupt, EOFError):
        log.info("Stopped; diagnostics in %s", out_dir.resolve())
        return 0
    except Exception:
        log.exception("Diagnostic run failed")
        return 1
    finally:
        browser.close()


if __name__ == "__main__":
    sys.exit(main())