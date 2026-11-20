import dataclasses
import shutil
import time
import wave

import pytest

from app.capture import CaptureCoordinator
from app.capture.audio import AudioSubsystem
from app.capture.subsystem import CaptureState, CaptureSubsystem
from app.config.settings import Settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


class Fake(CaptureSubsystem):
    def __init__(self, name, fail=False):
        super().__init__()
        self.name = name
        self.fail = fail

    def start(self):
        self.state = CaptureState.FAILED if self.fail else CaptureState.RUNNING
        return not self.fail

    def stop(self):
        self.state = CaptureState.STOPPED


class Boom(CaptureSubsystem):
    name = "boom"

    def start(self):
        raise RuntimeError("boom")

    def stop(self):
        raise RuntimeError("stop boom")


def test_coordinator_reports_subsystems_independently():
    coordinator = CaptureCoordinator("s1")
    coordinator.register(Fake("audio"))
    coordinator.register(Fake("video", fail=True))
    assert coordinator.start() is True             # audio still started
    statuses = coordinator.status()
    assert statuses["audio"].state is CaptureState.RUNNING
    assert statuses["video"].state is CaptureState.FAILED
    assert coordinator.all_healthy() is False
    coordinator.stop()
    assert all(s.state is CaptureState.STOPPED for s in coordinator.status().values())


def test_one_subsystem_exception_does_not_stop_the_rest():
    coordinator = CaptureCoordinator("s2")
    coordinator.register(Boom())
    coordinator.register(Fake("audio"))
    assert coordinator.start() is True
    assert coordinator.status()["boom"].state is CaptureState.FAILED
    assert "boom" in coordinator.status()["boom"].detail
    assert coordinator.status()["audio"].state is CaptureState.RUNNING
    coordinator.stop()                             # Boom.stop raises; must be swallowed


def test_all_healthy_only_when_everything_runs():
    coordinator = CaptureCoordinator("s3")
    coordinator.register(Fake("audio"))
    coordinator.register(Fake("text"))
    coordinator.start()
    assert coordinator.all_healthy() is True
    coordinator.stop()
    assert coordinator.all_healthy() is False


def test_empty_coordinator_never_claims_capture():
    coordinator = CaptureCoordinator("s4")
    assert coordinator.start() is False
    assert coordinator.all_healthy() is False
    report = coordinator.report()
    assert report["subsystems"] == {} and report["session_id"] == "s4"
    coordinator.stop()
    coordinator.stop()                             # idempotent


def test_stop_is_idempotent_and_never_raises():
    coordinator = CaptureCoordinator("s5")
    coordinator.register(Fake("audio"))
    coordinator.start()
    coordinator.stop()
    coordinator.stop()
    assert coordinator.status()["audio"].state is CaptureState.STOPPED


@needs_ffmpeg
def test_audio_subsystem_lifecycle(tmp_path):
    settings = dataclasses.replace(Settings.from_env(), audio_enabled=True,
                                   audio_source="synthetic", audio_output_dir=tmp_path,
                                   audio_chunk_ms=500)
    subsystem = AudioSubsystem(settings, "coord_audio")
    assert subsystem.start() is True
    time.sleep(1.2)
    status = subsystem.get_status()
    assert status.state is CaptureState.RUNNING and status.stats["chunks"] > 0
    subsystem.stop()
    assert subsystem.state is CaptureState.STOPPED
    with wave.open(str(tmp_path / "coord_audio.wav")) as wav:
        assert (wav.getnchannels(), wav.getframerate()) == (1, 16000)
