"""Meeting session model shared by the orchestrator and its workers.

The meeting lifecycle graph in `app.meet.meeting_state` is unchanged; this
module adds the coarser, orchestration-facing status plus the per-session
worker configuration and the on-disk state file used to follow a worker
process from its parent.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from app.meet.meeting_state import MeetingState
from app.session.session_manager import make_session_id
from app.utils.logger import get_logger

log = get_logger(__name__)


class SessionStatus(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    BROWSER_STARTED = "browser_started"
    JOINING = "joining"
    WAITING_FOR_ADMISSION = "waiting_for_admission"
    JOINED = "joined"
    MONITORING = "monitoring"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


TERMINAL_STATUSES = frozenset({
    SessionStatus.COMPLETED, SessionStatus.FAILED,
    SessionStatus.CANCELLED, SessionStatus.TIMED_OUT,
})

# Coarse status for each worker-reported meeting state.
_STATUS_BY_MEETING_STATE = {
    MeetingState.STARTING: SessionStatus.STARTING,
    MeetingState.BROWSER_LAUNCHING: SessionStatus.STARTING,
    MeetingState.AUTHENTICATING: SessionStatus.BROWSER_STARTED,
    MeetingState.NAVIGATING: SessionStatus.BROWSER_STARTED,
    MeetingState.PREJOIN: SessionStatus.JOINING,
    MeetingState.JOIN_REQUESTED: SessionStatus.JOINING,
    MeetingState.WAITING_ADMISSION: SessionStatus.WAITING_FOR_ADMISSION,
    MeetingState.IN_MEETING: SessionStatus.JOINED,
    MeetingState.RECONNECTING: SessionStatus.JOINED,
    MeetingState.LEAVING: SessionStatus.MONITORING,
    MeetingState.ENDED: SessionStatus.COMPLETED,
    MeetingState.COMPLETED: SessionStatus.COMPLETED,
    MeetingState.DENIED: SessionStatus.FAILED,
    MeetingState.REMOVED: SessionStatus.FAILED,
    MeetingState.FAILED: SessionStatus.FAILED,
    MeetingState.CRASHED: SessionStatus.FAILED,
    MeetingState.CANCELLED: SessionStatus.CANCELLED,
}

MEETING_URL_RE = re.compile(r"^https://meet\.google\.com/[a-z0-9\-]+(\?.*)?$", re.I)

_MEETING_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def is_valid_meeting_url(url: str) -> bool:
    return bool(url) and bool(MEETING_URL_RE.match(url))


def status_for(state: MeetingState) -> SessionStatus:
    return _STATUS_BY_MEETING_STATE.get(state, SessionStatus.FAILED)


def new_meeting_id(url: str, now: datetime | None = None) -> str:
    """Unique per launched session: meeting code + start time + random suffix."""
    stamp = make_session_id(url, now)
    return f"{stamp}_{uuid.uuid4().hex[:6]}"


def safe_component(value: str) -> str:
    """Make a value safe for use as one filesystem path component."""
    cleaned = _MEETING_ID_RE.sub("-", value).strip("-.")
    return cleaned or "session"


@dataclass
class MeetingSession:
    """Registry record for one meeting, owned by the orchestrator."""

    meeting_id: str
    meeting_url: str
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    worker_pid: int | None = None
    profile_dir: Path | None = None
    log_file: Path | None = None
    run_dir: Path | None = None
    reason: str = ""
    error: str | None = None
    exit_code: int | None = None

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_STATUSES

    @property
    def is_terminal(self) -> bool:
        return not self.is_active

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        for key in ("created_at", "started_at", "ended_at"):
            value = data[key]
            data[key] = value.isoformat() if value else None
        for key in ("profile_dir", "log_file", "run_dir"):
            value = data[key]
            data[key] = str(value) if value else None
        data["is_active"] = self.is_active
        return data


@dataclass(frozen=True)
class WorkerConfig:
    """Everything one worker process needs. Written to `<run_dir>/config.json`."""

    meeting_id: str
    meeting_url: str | None
    profile_dir: str
    log_file: str
    state_file: str
    stop_file: str
    events_file: str
    mode: str = "meet"  # "meet" | "browser_selftest"
    selftest_seconds: float = 3.0
    # Resolved parent Settings, so a worker does not diverge when env changes
    # between the orchestrator launch and the worker process.
    settings_overrides: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkerConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path) -> None:
        atomic_write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: Path) -> "WorkerConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def stop_requested(self) -> bool:
        return Path(self.stop_file).exists()


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically so a reader never sees a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_worker_state(path: Path) -> dict | None:
    """Read a worker's last reported state, or None if it is missing/unreadable."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class SessionReporter:
    """Writes a worker's status to disk so its parent can follow it.

    Each update replaces `state.json` atomically and appends one event line to
    `events.jsonl`. A missing parent (single-meeting CLI run) is harmless.
    """

    def __init__(self, config: WorkerConfig, on_status=None) -> None:
        self.config = config
        self.state_file = Path(config.state_file)
        self.events_file = Path(config.events_file)
        self.on_status = on_status
        self.status = SessionStatus.CREATED

    def update(self, status: SessionStatus, reason: str = "",
               error: str | None = None) -> None:
        self.status = status
        now = datetime.now()
        payload = {
            "meeting_id": self.config.meeting_id,
            "status": status.value,
            "reason": reason,
            "error": error,
            "pid": os.getpid(),
            "updated_at": now.isoformat(),
        }
        log.info("Status: %s%s", status.value,
                 f" ({reason})" if reason else "")
        try:
            atomic_write_json(self.state_file, payload)
            self.events_file.parent.mkdir(parents=True, exist_ok=True)
            with self.events_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({**payload, "event": "status"}) + "\n")
        except OSError:
            # Reporting must never take the meeting down.
            pass
        if self.on_status is not None:
            try:
                self.on_status(status, reason)
            except Exception:
                pass
