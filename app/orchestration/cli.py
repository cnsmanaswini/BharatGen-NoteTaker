"""Concurrent orchestration CLI.

    python -m app.orchestration https://meet.google.com/aaa-bbbb-ccc https://meet.google.com/ddd-eeee-fff

Each URL becomes one worker process with its own Chromium, profile and logs.
While running, commands on stdin: `list`, `stop <id-prefix>`, `stop-all`, `quit`.
"""
from __future__ import annotations

import argparse
import sys
import time

from app.config.settings import Settings
from app.orchestration.orchestrator import (ConcurrencyLimitError,
                                            DuplicateMeetingError,
                                            MeetingOrchestrator)
from app.orchestration.session import SessionStatus, is_valid_meeting_url
from app.session.session_manager import EXIT_OK, EXIT_USAGE
from app.utils.logger import get_logger, setup_logging
from app.utils.redact import redact_meeting_url

log = get_logger("orchestration")

HELP_TEXT = "commands: list | stop <id-prefix> | stop-all | quit"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.orchestration",
        description="Run one bot worker per Google Meet URL, concurrently")
    parser.add_argument("meet_urls", nargs="+", metavar="MEET_URL",
                        help="One or more Google Meet URLs")
    parser.add_argument("--max-concurrent", type=int, default=None,
                        help="Override MAX_CONCURRENT_MEETINGS for this run")
    parser.add_argument("--allow-duplicates", action="store_true", default=None,
                        help="Allow two workers on the same meeting code")
    return parser.parse_args(argv)


def _print_sessions(orchestrator: MeetingOrchestrator) -> None:
    sessions = orchestrator.list_sessions()
    if not sessions:
        print("No sessions.")
        return
    print(f"{'meeting_id':<40} {'status':<22} {'pid':>8}  meeting")
    for s in sessions:
        print(f"{s.meeting_id:<40} {s.status.value:<22} {str(s.worker_pid or '-'):>8}  "
              f"{redact_meeting_url(s.meeting_url)}")
        if s.error:
            print(f"  error: {s.error}")


def _start_all(orchestrator: MeetingOrchestrator, urls: list[str]) -> None:
    for url in urls:
        try:
            session = orchestrator.start_meeting(url)
        except DuplicateMeetingError as exc:
            log.error("Skipping %s: duplicate of active session %s",
                      redact_meeting_url(url), exc.meeting_id)
            continue
        except ConcurrencyLimitError as exc:
            log.error("Skipping %s: %s", redact_meeting_url(url), exc)
            continue
        if session.status is SessionStatus.FAILED:
            log.error("Session %s failed to start: %s", session.meeting_id, session.error)
        else:
            log.info("Started %s (pid %s) for %s", session.meeting_id,
                     session.worker_pid, redact_meeting_url(url))
    _print_sessions(orchestrator)


def _interactive_loop(orchestrator: MeetingOrchestrator) -> None:
    while orchestrator.active_sessions():
        try:
            line = input("orchestrator> ").strip()
        except EOFError:
            break
        if not line:
            continue
        command, _, argument = line.partition(" ")
        command = command.lower()
        if command in ("quit", "exit", "q"):
            break
        if command in ("list", "ls", "status"):
            _print_sessions(orchestrator)
        elif command == "stop":
            if not argument:
                print("usage: stop <meeting-id-or-prefix>")
                continue
            try:
                meeting_id = orchestrator.resolve_meeting_id(argument)
            except (KeyError, ValueError) as exc:
                print(exc)
                continue
            if orchestrator.stop_meeting(meeting_id):
                print(f"Stopped {meeting_id}")
            else:
                print(f"{meeting_id} is not active")
        elif command in ("stop-all", "stopall"):
            for session in orchestrator.active_sessions():
                orchestrator.stop_meeting(session.meeting_id)
            break
        elif command in ("help", "?"):
            print(HELP_TEXT)
        else:
            print(f"unknown command {command!r}; {HELP_TEXT}")


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    setup_logging(settings.log_level, settings.log_dir)

    invalid = [url for url in args.meet_urls if not is_valid_meeting_url(url)]
    if invalid:
        for url in invalid:
            log.error("Not a valid Google Meet URL: %s", url)
        return EXIT_USAGE

    orchestrator = MeetingOrchestrator(
        settings,
        max_concurrent=args.max_concurrent,
        allow_duplicate=args.allow_duplicates,
        monitor_interval_s=0.5,
    )
    try:
        _start_all(orchestrator, args.meet_urls)
        if sys.stdin.isatty():
            print(HELP_TEXT)
            _interactive_loop(orchestrator)
        else:
            orchestrator.wait()
    except KeyboardInterrupt:
        log.info("Interrupted; stopping all sessions")
        orchestrator.shutdown()
    finally:
        orchestrator.shutdown()
        _print_sessions(orchestrator)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
