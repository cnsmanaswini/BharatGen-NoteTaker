"""Orchestration tests. Browser/process components are faked here; the real
Chromium concurrency check lives in tests/test_orchestration_integration.py."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.meet.meeting_state import MeetingState
from app.orchestration.orchestrator import (ConcurrencyLimitError,
                                            DuplicateMeetingError,
                                            MeetingOrchestrator)
from app.orchestration.process import prepare_profile
from app.orchestration.session import (MeetingSession, SessionStatus,
                                       WorkerConfig, atomic_write_json,
                                       new_meeting_id, status_for)
from app.orchestration.worker import settings_for_worker
from app.utils.redact import redact_meeting_url

URL_A = "https://meet.google.com/aaa-bbbb-ccc?authuser=0"
URL_B = "https://meet.google.com/ddd-eeee-fff"
URL_C = "https://meet.google.com/ggg-hhhh-iii"
URL_D = "https://meet.google.com/jjj-kkkk-lll"


class FakeHandle:
    def __init__(self, pid: int):
        self.pid = pid
        self.exit_code: int | None = None
        self.stopped: list[float] = []
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def stop(self, timeout_s: float) -> None:
        self.stopped.append(timeout_s)
        if self.exit_code is None:
            self.exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9


class FakeFactory:
    def __init__(self, fail_times: int = 0):
        self.handles: list[FakeHandle] = []
        self.calls: list[tuple[MeetingSession, WorkerConfig]] = []
        self.fail_times = fail_times

    def __call__(self, session, config, settings):
        self.calls.append((session, config))
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError("could not spawn worker")
        handle = FakeHandle(pid=4000 + len(self.handles))
        self.handles.append(handle)
        return handle


@pytest.fixture
def settings(tmp_path) -> Settings:
    return replace(
        Settings.from_env(),
        log_dir=tmp_path / "logs",
        profiles_dir=tmp_path / "profiles",
        profile_source=tmp_path / "seed",
        profile_clone=False,
        max_concurrent_meetings=3,
        worker_startup_timeout_s=60,
        worker_shutdown_timeout_s=5,
    )


@pytest.fixture
def factory() -> FakeFactory:
    return FakeFactory()


@pytest.fixture
def orchestrator(settings, factory) -> MeetingOrchestrator:
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               startup_timeout_s=60, shutdown_timeout_s=5)
    yield orch
    orch.shutdown()


def write_worker_state(orch: MeetingOrchestrator, meeting_id: str,
                       status: SessionStatus, *, reason: str = "",
                       error: str | None = None) -> None:
    session = orch.get_session(meeting_id)
    assert session and session.run_dir
    atomic_write_json(Path(session.run_dir) / "state.json", {
        "meeting_id": meeting_id,
        "status": status.value,
        "reason": reason,
        "error": error,
        "pid": session.worker_pid,
    })


# ---- starting sessions -------------------------------------------------------

def test_start_one_session(orchestrator, factory, settings):
    session = orchestrator.start_meeting(URL_A)

    assert session.status is SessionStatus.STARTING
    assert session.is_active
    assert session.worker_pid == 4000
    assert session.ended_at is None
    assert session.started_at is not None
    assert len(orchestrator.active_sessions()) == 1
    assert session.profile_dir.is_dir()

    config_path = Path(session.run_dir) / "config.json"
    config = WorkerConfig.load(config_path)
    assert config.meeting_id == session.meeting_id
    assert config.meeting_url == URL_A
    assert config.state_file == str(Path(session.run_dir) / "state.json")
    assert config.stop_file == str(Path(session.run_dir) / "stop.request")
    assert Path(session.log_file).parent == Path(session.run_dir)
    assert config.settings_overrides["headless"] == settings.headless


def test_worker_inherits_parent_settings_but_gets_its_own_paths(tmp_path):
    config = WorkerConfig(
        meeting_id="mid", meeting_url=URL_A,
        profile_dir=str(tmp_path / "profile"),
        log_file=str(tmp_path / "logs" / "session.log"),
        state_file=str(tmp_path / "state.json"),
        stop_file=str(tmp_path / "stop.request"),
        events_file=str(tmp_path / "events.jsonl"),
        settings_overrides={
            "headless": True,
            "browser_channel": "",
            "audio_output_dir": str(tmp_path / "audio"),
            "user_data_dir": str(tmp_path / "ignored"),
        },
    )
    settings = settings_for_worker(config)
    assert settings.headless is True
    assert settings.browser_channel == ""
    assert settings.audio_output_dir == tmp_path / "audio"
    assert settings.user_data_dir == tmp_path / "profile"
    assert settings.log_dir == tmp_path / "logs"


def test_start_two_sessions_are_independent(orchestrator, factory):
    a = orchestrator.start_meeting(URL_A)
    b = orchestrator.start_meeting(URL_B)

    assert a.meeting_id != b.meeting_id
    assert a.worker_pid != b.worker_pid
    assert a.profile_dir != b.profile_dir
    assert a.run_dir != b.run_dir
    assert {s.meeting_id for s in orchestrator.active_sessions()} == {
        a.meeting_id, b.meeting_id}


def test_start_several_sessions_and_unique_ids(settings, factory):
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               max_concurrent=5, monitor_interval_s=None)
    try:
        urls = [URL_A, URL_B, URL_C, URL_D,
                "https://meet.google.com/mmm-nnnn-ooo"]
        sessions = [orch.start_meeting(url) for url in urls]

        assert len({s.meeting_id for s in sessions}) == len(urls)
        assert len({s.profile_dir for s in sessions}) == len(urls)
        assert len({s.worker_pid for s in sessions}) == len(urls)
        assert len(orch.active_sessions()) == len(urls)
        assert all(s.status is SessionStatus.STARTING for s in sessions)
    finally:
        orch.shutdown()


def test_one_worker_state_does_not_affect_the_other(orchestrator):
    a = orchestrator.start_meeting(URL_A)
    b = orchestrator.start_meeting(URL_B)

    write_worker_state(orchestrator, a.meeting_id, SessionStatus.JOINED,
                       reason="admitted")
    orchestrator.refresh()

    assert orchestrator.get_session(a.meeting_id).status is SessionStatus.JOINED
    assert orchestrator.get_session(b.meeting_id).status is SessionStatus.STARTING


def test_status_file_drives_terminal_states(orchestrator):
    a = orchestrator.start_meeting(URL_A)
    write_worker_state(orchestrator, a.meeting_id, SessionStatus.FAILED,
                       reason="join failed", error="no join button")
    orchestrator.refresh()

    session = orchestrator.get_session(a.meeting_id)
    assert session.status is SessionStatus.FAILED
    assert session.error == "no join button"
    assert session.is_terminal
    assert orchestrator.active_sessions() == []


# ---- limits ------------------------------------------------------------------

def test_concurrency_limit_is_enforced(settings, factory):
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               max_concurrent=2, monitor_interval_s=None)
    try:
        orch.start_meeting(URL_A)
        orch.start_meeting(URL_B)
        with pytest.raises(ConcurrencyLimitError) as excinfo:
            orch.start_meeting(URL_C)
        assert excinfo.value.limit == 2
        assert len(orch.active_sessions()) == 2

        # A finished meeting releases its slot.
        orch.stop_meeting(orch.active_sessions()[0].meeting_id)
        assert orch.start_meeting(URL_C).is_active
    finally:
        orch.shutdown()


def test_duplicate_meeting_is_rejected_while_active(orchestrator):
    a = orchestrator.start_meeting(URL_A)
    with pytest.raises(DuplicateMeetingError) as excinfo:
        orchestrator.start_meeting("https://meet.google.com/aaa-bbbb-ccc")
    assert excinfo.value.meeting_id == a.meeting_id

    orchestrator.stop_meeting(a.meeting_id)
    assert orchestrator.start_meeting(URL_A).meeting_id != a.meeting_id


def test_duplicates_can_be_allowed(settings, factory):
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               allow_duplicate=True, monitor_interval_s=None)
    try:
        a = orch.start_meeting(URL_A)
        b = orch.start_meeting(URL_A)
        assert a.meeting_id != b.meeting_id
        assert len(orch.active_sessions()) == 2
    finally:
        orch.shutdown()


def test_invalid_url_is_refused(orchestrator):
    with pytest.raises(ValueError):
        orchestrator.start_meeting("https://example.com/aaa-bbbb-ccc")


# ---- stopping and cleanup ----------------------------------------------------

def test_stopping_a_leaves_b_running(orchestrator, factory):
    a = orchestrator.start_meeting(URL_A)
    b = orchestrator.start_meeting(URL_B)

    assert orchestrator.stop_meeting(a.meeting_id) is True

    stopped = orchestrator.get_session(a.meeting_id)
    assert stopped.status is SessionStatus.CANCELLED
    assert stopped.ended_at is not None
    assert factory.handles[0].stopped
    assert Path(stopped.run_dir, "stop.request").exists()

    still_running = orchestrator.get_session(b.meeting_id)
    assert still_running.status is SessionStatus.STARTING
    assert still_running.is_active
    assert orchestrator.active_sessions()[0].meeting_id == b.meeting_id
    assert factory.handles[1].poll() is None


def test_stop_is_idempotent_and_unknown_is_false(orchestrator):
    a = orchestrator.start_meeting(URL_A)
    assert orchestrator.stop_meeting(a.meeting_id) is True
    assert orchestrator.stop_meeting(a.meeting_id) is False
    assert orchestrator.stop_meeting("does-not-exist") is False


def test_shutdown_stops_all_sessions(settings, factory):
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               monitor_interval_s=None)
    a = orch.start_meeting(URL_A)
    b = orch.start_meeting(URL_B)

    orch.shutdown()

    assert orch.active_sessions() == []
    assert orch.get_session(a.meeting_id).status is SessionStatus.CANCELLED
    assert orch.get_session(b.meeting_id).status is SessionStatus.CANCELLED
    assert all(handle.stopped for handle in factory.handles)


def test_cleanup_after_cancellation_releases_worker(orchestrator, factory):
    a = orchestrator.start_meeting(URL_A)
    orchestrator.stop_meeting(a.meeting_id)

    assert orchestrator.active_sessions() == []
    assert factory.handles[0].exit_code is not None
    assert orchestrator.wait(timeout_s=1) is True


# ---- failures ----------------------------------------------------------------

def test_worker_startup_failure_marks_session_failed(settings, factory):
    factory.fail_times = 1
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               max_concurrent=1, monitor_interval_s=None)
    try:
        failed = orch.start_meeting(URL_A)
        assert failed.status is SessionStatus.FAILED
        assert failed.is_terminal
        assert failed.ended_at is not None
        assert "could not spawn worker" in (failed.error or "")
        assert failed.worker_pid is None
        assert orch.active_sessions() == []

        # A failed start must not consume the only concurrency slot.
        assert orch.start_meeting(URL_B).is_active
    finally:
        orch.shutdown()


def test_worker_crash_does_not_affect_other_sessions(orchestrator, factory):
    a = orchestrator.start_meeting(URL_A)
    b = orchestrator.start_meeting(URL_B)
    factory.handles[0].exit_code = 3

    orchestrator.refresh()

    crashed = orchestrator.get_session(a.meeting_id)
    assert crashed.status is SessionStatus.FAILED
    assert crashed.exit_code == 3
    assert "terminal status" in (crashed.error or "")
    assert orchestrator.stop_meeting(a.meeting_id) is False

    healthy = orchestrator.get_session(b.meeting_id)
    assert healthy.status is SessionStatus.STARTING
    assert healthy.is_active


def test_worker_exit_after_completion_keeps_completed_status(orchestrator, factory):
    a = orchestrator.start_meeting(URL_A)
    write_worker_state(orchestrator, a.meeting_id, SessionStatus.COMPLETED,
                       reason="meeting ended")
    factory.handles[0].exit_code = 0

    orchestrator.refresh()

    session = orchestrator.get_session(a.meeting_id)
    assert session.status is SessionStatus.COMPLETED
    assert session.exit_code == 0


def test_startup_timeout_marks_timed_out(settings, factory):
    clock = {"now": 0.0}
    orch = MeetingOrchestrator(settings, worker_factory=factory,
                               startup_timeout_s=5, shutdown_timeout_s=2,
                               monitor_interval_s=None,
                               clock=lambda: clock["now"])
    try:
        a = orch.start_meeting(URL_A)
        clock["now"] = 6.0
        orch.refresh()

        session = orch.get_session(a.meeting_id)
        assert session.status is SessionStatus.TIMED_OUT
        assert session.is_terminal
        assert session.ended_at is not None
        assert factory.handles[0].stopped
        assert Path(session.run_dir, "stop.request").exists()
    finally:
        orch.shutdown()


def test_cleanup_after_failure_releases_worker(orchestrator, factory):
    a = orchestrator.start_meeting(URL_A)
    factory.handles[0].exit_code = 1
    orchestrator.refresh()

    assert orchestrator.active_sessions() == []
    assert orchestrator.get_session(a.meeting_id).is_terminal
    assert orchestrator.wait(timeout_s=1) is True


def test_unknown_worker_status_is_ignored(orchestrator):
    a = orchestrator.start_meeting(URL_A)
    session = orchestrator.get_session(a.meeting_id)
    atomic_write_json(Path(session.run_dir) / "state.json",
                      {"meeting_id": a.meeting_id, "status": "nonsense"})
    orchestrator.refresh()
    assert orchestrator.get_session(a.meeting_id).status is SessionStatus.STARTING


# ---- helpers and model -------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    (MeetingState.IN_MEETING, SessionStatus.JOINED),
    (MeetingState.WAITING_ADMISSION, SessionStatus.WAITING_FOR_ADMISSION),
    (MeetingState.PREJOIN, SessionStatus.JOINING),
    (MeetingState.RECONNECTING, SessionStatus.JOINED),
    (MeetingState.ENDED, SessionStatus.COMPLETED),
    (MeetingState.DENIED, SessionStatus.FAILED),
    (MeetingState.CRASHED, SessionStatus.FAILED),
    (MeetingState.CANCELLED, SessionStatus.CANCELLED),
])
def test_status_mapping(state, expected):
    assert status_for(state) is expected


def test_new_meeting_ids_are_unique_and_contain_the_meeting_code():
    ids = {new_meeting_id(URL_A) for _ in range(20)}
    assert len(ids) == 20
    assert all(meeting_id.startswith("aaa-bbbb-ccc_") for meeting_id in ids)


def test_worker_config_roundtrip_and_stop_file(tmp_path):
    config = WorkerConfig(
        meeting_id="mid", meeting_url=URL_A,
        profile_dir=str(tmp_path / "profile"), log_file=str(tmp_path / "log"),
        state_file=str(tmp_path / "state.json"),
        stop_file=str(tmp_path / "stop.request"),
        events_file=str(tmp_path / "events.jsonl"),
    )
    config.save(tmp_path / "config.json")
    loaded = WorkerConfig.load(tmp_path / "config.json")
    assert loaded == config
    assert loaded.stop_requested() is False
    Path(loaded.stop_file).write_text("{}", encoding="utf-8")
    assert loaded.stop_requested() is True


def test_prepare_profile_clones_auth_state_but_not_locks_or_caches(tmp_path):
    seed = tmp_path / "seed"
    (seed / "Default" / "Network").mkdir(parents=True)
    (seed / "Default" / "Network" / "Cookies").write_text("cookie", encoding="utf-8")
    (seed / "Local State").write_text("{}", encoding="utf-8")
    (seed / "SingletonLock").write_text("lock", encoding="utf-8")
    (seed / "Default" / "Cache").mkdir(parents=True)

    dest = prepare_profile(seed, tmp_path / "profiles" / "mid", clone=True)

    assert (dest / "Default" / "Network" / "Cookies").read_text(encoding="utf-8") == "cookie"
    assert (dest / "Local State").exists()
    assert not (dest / "SingletonLock").exists()
    assert not (dest / "Default" / "Cache").exists()


def test_prepare_profile_without_clone_is_a_fresh_directory(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "cookie").write_text("x", encoding="utf-8")
    dest = prepare_profile(seed, tmp_path / "profile", clone=False)
    assert dest.is_dir()
    assert list(dest.iterdir()) == []


def test_redact_meeting_url():
    assert redact_meeting_url(URL_A) == "https://meet.google.com/aaa-bbbb-ccc"
    assert redact_meeting_url("https://meet.google.com/ddd-eeee-fff") \
        == "https://meet.google.com/ddd-eeee-fff"
    assert redact_meeting_url(None) == ""


def test_session_to_dict_is_serialisable(tmp_path):
    session = MeetingSession(meeting_id="mid", meeting_url=URL_A,
                             run_dir=tmp_path)
    data = session.to_dict()
    assert data["status"] == "created"
    assert data["is_active"] is True
    json.dumps(data)  # must not raise
