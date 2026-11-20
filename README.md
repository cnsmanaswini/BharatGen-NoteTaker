# BharatGen Meet Bot

Joins a Google Meet as a participant, stays connected, and captures what the bot
is legitimately able to receive. Scope is bot orchestration, meeting lifecycle
and media capture only - not meeting intelligence.

## Setup (Windows 11 / PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium     # only needed if BROWSER_CHANNEL is empty
Copy-Item .env.example .env
# Audio capture (optional):
pip install -r requirements-audio.txt
winget install Gyan.FFmpeg
```

## Run
```powershell
python -m app.main                                   # opens meet.google.com
python -m app.main https://meet.google.com/abc-defg-hij
```
First run: sign in manually in the browser window with the dedicated account.
The session is stored in `browser_profile/` (git-ignored). Second run should
skip login. Exit with Ctrl+C or by closing the window.

## Concurrent meetings
```powershell
python -m app.orchestration https://meet.google.com/abc-defg-hij https://meet.google.com/xyz-abcd-efg
python -m app.orchestration --max-concurrent 5 URL1 URL2 URL3
```
Each URL gets one worker process (`python -m app.orchestration.worker`), one
Chromium instance, one browser profile and its own lifecycle. The orchestrator
prints a status table and accepts commands while sessions run:
```
list                  show every session and its state
stop <id-prefix>      stop one meeting; the others keep running
stop-all              stop every meeting and exit
quit                  stop everything and exit
```
`python -m app.main <url>` still runs a single meeting in the foreground and
uses the same joining/monitoring code as each worker.

### How it works
- `app/orchestration/orchestrator.py` owns the session registry, concurrency
  limit (`MAX_CONCURRENT_MEETINGS`, default 3), duplicate-meeting rejection,
  startup timeouts, crash detection, per-session stop and graceful shutdown.
- `app/orchestration/worker.py` contains the single-meeting flow (authentication,
  navigation, join/lobby, monitoring, cleanup). It is the only place Playwright
  runs, so a worker cannot affect another worker's browser.
- Every session gets `profiles/<meeting_id>/` (cloned from `PROFILE_SOURCE`,
  normally the authenticated `browser_profile/`), `logs/sessions/<meeting_id>/`
  (`session.log`, `worker.output.log`, `state.json`, `events.jsonl`) and
  `screenshots/<meeting_id>/`. A stop request is a `stop.request` file inside
  the session directory; if the worker does not exit in time its process tree
  is killed with `taskkill /T` on Windows.
- Session status: created -> starting -> browser_started -> joining ->
  waiting_for_admission -> joined -> monitoring -> completed, plus failed,
  cancelled and timed_out. Status is written atomically to `state.json` so the
  orchestrator can follow a worker without sharing memory.
- A failed session never stops the others: startup errors, crashes and stop
  requests are contained to that session's process tree.

### Known concurrency limitations
- Google/Meet, not this architecture, decides how many meetings one Google
  account may join at once. Meet may block or reject the same account joining
  several meetings simultaneously; verify with the real account.
- Cloned profiles inherit the signed-in session but are independent copies.
  Google may invalidate a session when it sees the same cookies from several
  browsers; re-authenticate the source profile if that happens.
- Admission still requires a human host to admit each bot. Lobby wait is
  bounded by `ADMISSION_TIMEOUT_S` (default 900s).
- Each session is a full Chrome instance: roughly 300-600 MB RAM and a browser
  window, so the practical limit is machine capacity (`MAX_CONCURRENT_MEETINGS`
  is a guard, not a guarantee).
- Audio capture uses Windows WASAPI loopback of the system output device. Two
  concurrent sessions capture the same system audio, so concurrent audio capture
  is not per-meeting; audio session IDs and FFmpeg processes are still isolated.
- `python -m app.orchestration` is local-process orchestration only (no
  cross-machine scheduling); moving workers to other hosts would require a
  transport layer, but each worker already communicates only via its config and
  state files.

### Manual test: two real meetings concurrently
1. In `browser_profile/`, sign in to the Google account the bot should use (run
   `python -m app.main` once and complete sign-in).
2. Start two separate Google Meet meetings from two other accounts and keep the
   host windows open; copy both meeting URLs.
3. Run `python -m app.orchestration <URL-A> <URL-B>` and wait until both sessions
   show `waiting_for_admission`.
4. Admit both bots from their respective host windows. Each session should move
   to `joined`, then `monitoring`.
5. Run `list`. Verify two different meeting IDs, worker PIDs and profile
   directories, and that each has its own log file under `logs/sessions/`.
6. Verify independence: the two bots appear in their own meetings and each
   browser window can be moved/closed separately; check both `session.log` files
   stay independent.
7. Run `stop <A-id-prefix>` (or close bot A's window) and verify A becomes
   `cancelled` while B remains `joined`/`monitoring` and still responds to the
   meeting (for example, open the participant list on B).
8. End meeting B (or run `stop <B-id-prefix>`) and verify B becomes terminal and
   the command exits.
9. Run `tasklist | findstr chrome` (after the command exits) and confirm no
   Chromium processes for the session profile directories remain; check
   `logs/sessions/<meeting_id>/worker.output.log` for errors and the session
   status in `state.json`.

## What it does
- Validated lifecycle (`app/meet/meeting_state.py`): created -> starting ->
  browser_launching -> authenticating -> navigating -> prejoin ->
  join_requested -> waiting_admission -> in_meeting -> leaving -> ended
  (plus denied/removed/failed/crashed/cancelled). Invalid transitions are
  rejected instead of silently recorded.
- Joins only after Meet shows real in-meeting controls ("Leave call" and
  "People"), never just because the page loaded. Reports lobby waits and
  denials, detects reconnects, and leaves gracefully.
- `CaptureCoordinator` (`app/capture/`) manages each media subsystem
  independently, so a failed capture never stops the meeting session and the
  report never claims complete capture when a subsystem is down.

## What is captured
- **Audio** (optional, `AUDIO_ENABLED=true`): Windows WASAPI loopback of the
  selected output device, resampled by a supervised FFmpeg process to 16 kHz
  mono s16le, chunked with timestamps and written to `audio/<session>.wav`
  plus `audio/<session>.index.jsonl`. Silence is padded so timestamps stay
  aligned with the wall clock.
- **Video**: not implemented yet. Google Meet does not expose individual
  participant or meeting streams to the page in a supported way, so the
  intended approach is a single screen/window capture (gallery view only), not
  per-participant video.
- **Chat, captions and participant metadata**: not implemented yet. Only
  visible DOM would be captured; there is no supported API and visible
  captions are not a complete transcript.

Audio capture is the only subsystem registered today. Video/text capture
modules will plug into the same coordinator lifecycle
(`initialize/start/get_status/stop/cleanup`).

## Tests
```powershell
pytest
```
Unit tests cover configuration, URL validation, the lifecycle graph, capture
coordination, audio chunking/timestamps/sinks, the FFmpeg pipeline (skipped
when FFmpeg is not installed) and orchestration (limits, duplicates, stop,
crash, startup timeout, cleanup, profile cloning). The integration tests in
`tests/test_orchestration_integration.py` launch real Chromium worker processes
in `browser_selftest` mode and verify concurrent isolation and stopping one
session while the other keeps running; they are skipped when Google Chrome
(`BROWSER_CHANNEL=chrome`) is not installed. They launch Chrome headless in a
temporary profile, never the signed-in `browser_profile/`. Live Google Meet join
and real meeting audio must be verified manually.

Limit the integration tests to the orchestration files with:
```powershell
pytest tests/test_orchestration.py tests/test_orchestration_integration.py -q
```

## Security
Never commit `.env`, `browser_profile/`, `profiles/`, `logs/`, `screenshots/`
or `audio/`. `profiles/` holds per-session copies of the authenticated browser
profile.
