"""Audio capture as a coordinated subsystem."""
from app.audio.capture_engine import AudioCaptureEngine
from app.capture.subsystem import CaptureState, CaptureSubsystem, SubsystemStatus


class AudioSubsystem(CaptureSubsystem):
    """Wraps AudioCaptureEngine so the coordinator can manage it independently."""

    name = "audio"

    def __init__(self, settings, session_id: str, source=None):
        super().__init__()
        self.engine = AudioCaptureEngine(settings, session_id, source)

    def start(self) -> bool:
        self.state = CaptureState.STARTING
        self.detail = ""
        if self.engine.start():
            self.state = CaptureState.RUNNING
            return True
        self.state = CaptureState.FAILED
        self.detail = "capture did not start (see log)"
        return False

    def stop(self) -> None:
        if self.state in (CaptureState.NOT_INITIALIZED, CaptureState.STOPPED):
            return
        was_failed = self.state == CaptureState.FAILED
        self.state = CaptureState.STOPPING
        stats = self.engine.stop()  # never raises
        self.state = CaptureState.FAILED if was_failed else CaptureState.STOPPED
        if was_failed:
            return
        if stats.get("failed"):
            self.state = CaptureState.FAILED
            self.detail = "capture ended with an error"
        elif stats.get("chunks") == 0:
            self.detail = "no audio was captured"

    def get_status(self) -> SubsystemStatus:
        if self.state == CaptureState.RUNNING and self.engine.failed:
            self.state = CaptureState.FAILED
            self.detail = "capture stopped unexpectedly (see log)"
        return super().get_status()

    def _stats(self) -> dict:
        return self.engine.stats()
