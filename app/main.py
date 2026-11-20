"""Entry point: python -m app.main [MEET_URL]

Single-meeting CLI. The concurrent orchestrator is `python -m app.orchestration`.
"""
import argparse
import sys

from app.config.settings import Settings
from app.orchestration.session import MEETING_URL_RE, WorkerConfig
from app.orchestration.worker import run_session
from app.session.session_manager import EXIT_USAGE, make_session_id
from app.utils.logger import get_logger, setup_logging

MEET_HOME = "https://meet.google.com/"
MEET_URL_RE = MEETING_URL_RE

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

    session_key = make_session_id(args.meet_url) if args.meet_url else "meet-home"
    run_dir = settings.log_dir / "sessions" / session_key
    # Single-meeting mode keeps using the authenticated browser_profile directly;
    # per-session profile clones are only needed when browsers run concurrently.
    config = WorkerConfig(
        meeting_id=session_key,
        meeting_url=args.meet_url,
        profile_dir=str(settings.user_data_dir),
        log_file=str(settings.log_dir / "meetbot.log"),
        state_file=str(run_dir / "state.json"),
        stop_file=str(run_dir / "stop.request"),
        events_file=str(run_dir / "events.jsonl"),
    )
    return run_session(config, settings)


if __name__ == "__main__":
    sys.exit(main())
