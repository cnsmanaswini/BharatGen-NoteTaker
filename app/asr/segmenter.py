"""Speech segmentation driven by per-meeting VAD.

Algorithm adapted from KTS RealtimeSessionOrchestrator ingest/cut/flush
(silence cuts, target duration, overlap tail, trailing-silence trim).
No TTS/MT/broadcast coupling.
"""
from __future__ import annotations

from app.asr.audio import SAMPLE_RATE_HZ, bytes_for_ms, bytes_to_ms
from app.asr.models import SpeechSegment
from app.utils.logger import get_logger

log = get_logger(__name__)


class SpeechSegmenter:
    def __init__(
        self,
        vad,
        *,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        target_segment_ms: int = 5000,
        cut_silence_ms: int = 1000,
        force_cut_silence_ms: int = 2500,
        min_speech_ms: int = 3000,
        overlap_ms: int = 250,
    ):
        self.vad = vad
        self.sample_rate_hz = int(sample_rate_hz)
        self.target_segment_ms = max(0, int(target_segment_ms))
        self.cut_silence_ms = max(0, int(cut_silence_ms))
        self.force_cut_silence_ms = max(0, int(force_cut_silence_ms))
        self.min_speech_ms = max(0, int(min_speech_ms))
        self.overlap_ms = max(0, int(overlap_ms))
        self.speech_buffer = bytearray()
        self.last_speaking = False
        self._collecting = False
        self._trailing_silence_ms = 0.0
        self._voiced_speech_bytes = 0
        self._timeline_s = 0.0
        self._segment_start_s = 0.0
        self._overlap_tail = b""
        self._cut_count = 0

    def reset(self) -> None:
        self.speech_buffer.clear()
        self.last_speaking = False
        self._collecting = False
        self._trailing_silence_ms = 0.0
        self._voiced_speech_bytes = 0
        self._timeline_s = 0.0
        self._segment_start_s = 0.0
        self._overlap_tail = b""
        self._cut_count = 0
        if hasattr(self.vad, "reset"):
            self.vad.reset()

    def feed(self, pcm16le: bytes, start_s: float | None = None) -> list[SpeechSegment]:
        if not pcm16le:
            return []
        if start_s is not None and not self._collecting and not self.speech_buffer:
            self._timeline_s = float(start_s)
        chunk_ms = bytes_to_ms(len(pcm16le), self.sample_rate_hz)
        vad = self.vad.detect(pcm16le, self.sample_rate_hz)
        if vad.speaking != self.last_speaking:
            self.last_speaking = vad.speaking
            log.info("vad.state speaking=%s score=%.3f rms=%.5f segment_ms=%.1f",
                     vad.speaking, float(vad.score), float(vad.rms), self._segment_ms())

        if not self._collecting:
            if not vad.speaking:
                self._timeline_s += chunk_ms / 1000.0
                return []
            self._collecting = True
            self._trailing_silence_ms = 0.0
            self._segment_start_s = self._timeline_s
            log.info("segment.collecting_started at=%.3f", self._segment_start_s)

        self.speech_buffer.extend(pcm16le)
        if vad.speaking:
            self._voiced_speech_bytes += len(pcm16le)
            self._trailing_silence_ms = 0.0
        else:
            self._trailing_silence_ms += chunk_ms
        self._timeline_s += chunk_ms / 1000.0

        reason = self._cut_reason()
        if not reason:
            return []
        voiced_ms = self._voiced_ms()
        bypass = reason == "target_segment_boundary"
        if voiced_ms < self.min_speech_ms and not bypass:
            log.info("audio.hold reason=min_segment_guard voiced_ms=%.1f trailing_silence_ms=%.1f",
                     voiced_ms, self._trailing_silence_ms)
            return []
        return self._emit(reason, bypass_min=bypass)

    def flush(self, *, force: bool = True) -> list[SpeechSegment]:
        if not self.speech_buffer:
            self._reset_cycle()
            return []
        return self._emit("meeting_end_flush", bypass_min=force)

    def _emit(self, reason: str, bypass_min: bool) -> list[SpeechSegment]:
        raw = bytes(self.speech_buffer)
        trailing = self._trailing_silence_bytes()
        speech = raw[:-trailing] if trailing > 0 else raw
        min_bytes = bytes_for_ms(self.min_speech_ms, self.sample_rate_hz)
        if not speech:
            self._reset_cycle()
            return []
        if not bypass_min and self._voiced_speech_bytes < min_bytes:
            log.info("asr.skip reason=voiced_below_min voiced_ms=%.1f", self._voiced_ms())
            self._reset_cycle()
            return []
        if not bypass_min and len(speech) < min_bytes:
            log.info("asr.skip reason=speech_below_min speech_ms=%.1f",
                     bytes_to_ms(len(speech), self.sample_rate_hz))
            self._reset_cycle()
            return []

        overlap_bytes = bytes_for_ms(self.overlap_ms, self.sample_rate_hz)
        asr_input = (self._overlap_tail + speech) if overlap_bytes and self._overlap_tail else speech
        start_s = self._segment_start_s
        end_s = start_s + bytes_to_ms(len(speech), self.sample_rate_hz) / 1000.0
        self._cut_count += 1
        log.info("audio.cut cut_index=%s reason=%s start_s=%.3f end_s=%.3f voiced_ms=%.1f",
                 self._cut_count, reason, start_s, end_s, self._voiced_ms())
        segment = SpeechSegment(
            pcm16le=speech,
            start_s=start_s,
            end_s=end_s,
            reason=reason,
            voiced_s=self._voiced_ms() / 1000.0,
            asr_pcm16le=asr_input,
        )
        self._overlap_tail = speech[-overlap_bytes:] if overlap_bytes else b""
        self._reset_cycle()
        return [segment]

    def _cut_reason(self) -> str | None:
        if not self._collecting or not self.speech_buffer:
            return None
        if self.target_segment_ms > 0 and self._segment_ms() >= self.target_segment_ms:
            return "target_segment_boundary"
        if self._trailing_silence_ms <= 0:
            return None
        if self.force_cut_silence_ms > 0 and self._trailing_silence_ms >= self.force_cut_silence_ms:
            return "vad_force_silence_boundary"
        if self.cut_silence_ms <= 0:
            return "vad_silence_boundary"
        if self._trailing_silence_ms >= self.cut_silence_ms:
            return "vad_silence_boundary"
        return None

    def _segment_ms(self) -> float:
        return bytes_to_ms(len(self.speech_buffer), self.sample_rate_hz)

    def _voiced_ms(self) -> float:
        return bytes_to_ms(self._voiced_speech_bytes, self.sample_rate_hz)

    def _trailing_silence_bytes(self) -> int:
        if not self.speech_buffer or self._trailing_silence_ms <= 0:
            return 0
        n = bytes_for_ms(int(round(self._trailing_silence_ms)), self.sample_rate_hz)
        return min(len(self.speech_buffer), n)

    def _reset_cycle(self) -> None:
        self.speech_buffer.clear()
        self._collecting = False
        self._trailing_silence_ms = 0.0
        self._voiced_speech_bytes = 0
        self._segment_start_s = self._timeline_s
