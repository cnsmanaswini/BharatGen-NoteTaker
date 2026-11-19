"""Entry point: python -m app.main [MEET_URL]"""
import argparse
import re
import sys

from playwright.sync_api import Error as PWError

from app.browser.auth_manager import AuthManager
from app.browser.chromium_manager import ChromiumManager
from app.config.settings import Settings
from app.meet.join_manager import join_meeting
from app.meet.meet_navigator import dismiss_media_prompt, open_meeting
from app.meet.meeting_state import MeetingState
from app.meet.participant_monitor import confirm_presence, monitor_meeting
from app.session.cleanup_manager import CleanupManager
from app.session.session_manager import EXIT_USAGE, SessionManager
from app.utils.logger import get_logger, setup_logging
from app.utils.snapshot import save_snapshot

MEET_HOME = "https://meet.google.com/"
MEET_URL_RE = re.compile(r"^https://meet\.google\.com/[a-z0-9\-]+(\?.*)?$", re.I)

log = get_logger("main")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BharatGen Notetaker Meet bot")
    p.add_argument("meet_url", nargs="?", default=None,
                   help="Google Meet URL (omit to just open Meet home)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.log_dir)

    if args.meet_url and not MEET_URL_RE.match(args.meet_url):
        log.error("Not a valid Google Meet URL: %s", args.meet_url)
        return EXIT_USAGE

    session = SessionManager()
    browser = ChromiumManager(settings)
    cleanup = CleanupManager(browser)
    try:
        page = browser.start()
        if not AuthManager(browser, settings.auth_wait_timeout_s).ensure_authenticated():
            session.transition(MeetingState.FAILED, "sign-in did not complete")
            return session.exit_code()

        if not args.meet_url:
            page.goto(MEET_HOME, wait_until="domcontentloaded")
            log.info("Meet home opened. Press Ctrl+C or close the window to exit.")
            while browser.is_alive():
                page.wait_for_timeout(1000)
            return 0

        open_meeting(page, args.meet_url)
        dismiss_media_prompt(page)
        session.transition(MeetingState.PREJOIN, "meeting page opened")

        state = join_meeting(page, settings, browser.is_alive)
        session.transition(state, "join finished")
        if state != MeetingState.IN_MEETING:
            return session.exit_code()

        save_snapshot(page, "in_meeting")
        confirm_presence(page, settings.bot_display_name)
        log.info("Bot is in the meeting. Press Ctrl+C to leave.")

        end_state, reason = monitor_meeting(page, session, settings, browser.is_alive)
        if browser.crashed:
            end_state, reason = MeetingState.CRASHED, "browser page crashed"
        elif end_state != MeetingState.ENDED or reason != "browser closed":
            if browser.is_alive():
                save_snapshot(page, "session_end")  # shows what Meet displayed at the end
        session.transition(end_state, reason)
        return session.exit_code()
    except KeyboardInterrupt:
        session.transition(MeetingState.ENDED, "stopped by user (Ctrl+C)")
        return session.exit_code()
    except PWError as exc:
        if browser.crashed:
            session.transition(MeetingState.CRASHED, "browser page crashed")
        elif not browser.is_alive():
            session.transition(MeetingState.ENDED, "browser closed")
        else:
            log.exception("Browser automation error")
            session.transition(MeetingState.FAILED, str(exc).splitlines()[0])
        return session.exit_code()
    except Exception as exc:
        log.exception("Unexpected failure")
        session.transition(MeetingState.FAILED, str(exc))
        return session.exit_code()
    finally:
        cleanup.run()
        log.info("Final state: %s%s", session.state.value,
                 f" ({session.reason})" if session.reason else "")


if __name__ == "__main__":
    sys.exit(main())