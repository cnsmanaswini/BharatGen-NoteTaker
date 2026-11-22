"""Capture coordinator adapter for a per-meeting ASR session."""
from __future__ import annotations

from app.asr.session import AsrSession
from app.audio.chunk import AudioChunk
from app.capture.subsystem import CaptureState, CaptureSubsystem, SubsystemStatus
from app.utils.logger import get_logger

log = get_logger(__name__)


class AsrSubsystem(CaptureSubsystem):
    name = "asr"

    def __init__(self, session: AsrSession):
        super().__init__()
        self.session = session

    def listen_to(self, engine) -> None:
        engine.add_listener(self.on_chunk)

    def on_chunk(self, chunk: AudioChunk) -> None:
        try:
            self.session.ingest(chunk.pcm, chunk.start_ts)
        except Exception:
            log.exception("ASR ingest failed (audio capture continues)")

    def start(self) -> bool:
        self.state = CaptureState.STARTING
        try:
            self.session.start()
            self.state = CaptureState.RUNNING
            return True
        except Exception as exc:
            self.state = CaptureState.FAILED
            self.detail = str(exc)
            log.error("ASR subsystem failed to start: %s", exc)
            return False

    def stop(self) -> None:
        if self.state in (CaptureState.NOT_INITIALIZED, CaptureState.STOPPED):
            return
        was_failed = self.state == CaptureState.FAILED
        self.state = CaptureState.STOPPING
        try:
            stats = self.session.close()
            if stats.get("asr_failures") and not stats.get("segments_written"):
                self.detail = "no segments transcribed"
        except Exception as exc:
            log.error("ASR subsystem stop failed: %s", exc)
            self.state = CaptureState.FAILED
            self.detail = str(exc)
            return
        self.state = CaptureState.FAILED if was_failed else CaptureState.STOPPED

    def get_status(self) -> SubsystemStatus:
        return super().get_status()

    def _stats(self) -> dict:
        return self.session.stats()
