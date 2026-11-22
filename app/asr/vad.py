# Adapted from KTS live-translate server/app/providers/vad.py
# (originally copied there from iitb_bgen_s2s at commit f9095ec).
# Silero VAD algorithm only; KTS broadcast/runtime is not a dependency.
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from app.asr.models import VADDecision

SAMPLE_RATE = 16000


def silence_gate(pcm_float32: np.ndarray, floor: float = 0.008) -> bool:
    if pcm_float32.size == 0:
        return True
    rms = float(np.sqrt(np.mean(np.square(pcm_float32.astype(np.float32, copy=False)))))
    return rms < floor


def load_silero():
    """Silero is a stateful RNN; each meeting must have its own instance."""
    from silero_vad import load_silero_vad

    model = load_silero_vad()
    model.eval()
    return model


def run_silero(pcm_float32: np.ndarray, model) -> float:
    import torch

    if silence_gate(pcm_float32, floor=0.008):
        return 0.0

    window = 512
    if len(pcm_float32) < window:
        pcm_float32 = np.pad(pcm_float32, (0, window - len(pcm_float32)))

    probs: list[float] = []
    for i in range(0, len(pcm_float32) - window + 1, window):
        chunk = pcm_float32[i : i + window].astype(np.float32, copy=False)
        tensor = torch.from_numpy(chunk).unsqueeze(0)
        with torch.no_grad():
            prob = model(tensor, SAMPLE_RATE).item()
        probs.append(float(prob))

    return float(max(probs)) if probs else 0.0


class SileroVAD:
    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 250,
        target_sample_rate_hz: int = SAMPLE_RATE,
        frame_size_samples: int = 512,
        score_fn: Callable[[np.ndarray], float] | None = None,
    ):
        self.threshold = float(threshold)
        self.neg_threshold = max(0.05, self.threshold - 0.15)
        self.min_silence_duration_ms = max(0, int(min_silence_duration_ms))
        self.target_sample_rate_hz = int(target_sample_rate_hz)
        self.frame_size_samples = int(frame_size_samples)

        self._stream_buffer = np.zeros((0,), dtype=np.float32)
        self._triggered = False
        self._temp_end_sample = 0
        self._current_sample = 0
        self._last_prob = 0.0

        self._silero_model = None
        if score_fn is None:
            self._silero_model = load_silero()
            self._score_fn = lambda pcm: run_silero(pcm, self._silero_model)
        else:
            self._score_fn = score_fn

    def reset(self) -> None:
        self._stream_buffer = np.zeros((0,), dtype=np.float32)
        self._triggered = False
        self._temp_end_sample = 0
        self._current_sample = 0
        self._last_prob = 0.0
        if self._silero_model is not None:
            self._silero_model.reset_states()

    def _resample_to_target(self, audio: np.ndarray, sample_rate_hz: int) -> np.ndarray:
        if sample_rate_hz <= 0 or sample_rate_hz == self.target_sample_rate_hz or audio.size == 0:
            return audio
        if audio.size == 1:
            repeats = max(1, self.target_sample_rate_hz // sample_rate_hz)
            return np.repeat(audio, repeats).astype(np.float32)

        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False, dtype=np.float32)
        target_size = max(1, int(round(audio.size * (self.target_sample_rate_hz / sample_rate_hz))))
        target_positions = np.linspace(0.0, 1.0, num=target_size, endpoint=False, dtype=np.float32)
        return np.interp(target_positions, source_positions, audio).astype(np.float32)

    def _score_frame(self, frame: np.ndarray) -> float:
        score = float(self._score_fn(frame.astype(np.float32, copy=False)))
        return max(0.0, min(1.0, score))

    def detect(self, pcm16le: bytes, sample_rate_hz: int) -> VADDecision:
        if not pcm16le:
            confidence = self._last_prob if self._triggered else (1.0 - self._last_prob)
            return VADDecision(
                speaking=self._triggered,
                confidence=max(0.0, min(1.0, confidence)),
                score=max(0.0, min(1.0, self._last_prob)),
                rms=0.0,
            )

        audio = np.frombuffer(pcm16le, dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size == 0:
            return VADDecision(
                speaking=self._triggered,
                confidence=0.0,
                score=max(0.0, min(1.0, self._last_prob)),
                rms=0.0,
            )

        audio = self._resample_to_target(audio, sample_rate_hz)
        if audio.size == 0:
            return VADDecision(
                speaking=self._triggered,
                confidence=0.0,
                score=max(0.0, min(1.0, self._last_prob)),
                rms=0.0,
            )

        chunk_rms = float(np.sqrt(np.mean(np.square(audio.astype(np.float32, copy=False))))) if audio.size else 0.0

        if self._stream_buffer.size:
            audio = np.concatenate([self._stream_buffer, audio])

        min_silence_samples = int(round(self.target_sample_rate_hz * (self.min_silence_duration_ms / 1000.0)))
        frame_count = int(audio.size // self.frame_size_samples)

        for index in range(frame_count):
            start = index * self.frame_size_samples
            end = start + self.frame_size_samples
            frame = audio[start:end]
            speech_prob = self._score_frame(frame)
            self._last_prob = speech_prob
            self._current_sample += self.frame_size_samples

            if speech_prob >= self.threshold and self._temp_end_sample:
                self._temp_end_sample = 0

            if speech_prob >= self.threshold and not self._triggered:
                self._triggered = True
                continue

            if speech_prob < self.neg_threshold and self._triggered:
                if not self._temp_end_sample:
                    self._temp_end_sample = self._current_sample
                if self._current_sample - self._temp_end_sample >= min_silence_samples:
                    self._triggered = False
                    self._temp_end_sample = 0

        consumed = frame_count * self.frame_size_samples
        self._stream_buffer = audio[consumed:].astype(np.float32, copy=False)

        confidence = self._last_prob if self._triggered else (1.0 - self._last_prob)
        return VADDecision(
            speaking=self._triggered,
            confidence=max(0.0, min(1.0, confidence)),
            score=max(0.0, min(1.0, self._last_prob)),
            rms=chunk_rms,
        )


def energy_score_fn(floor: float = 0.02) -> Callable[[np.ndarray], float]:
    """Deterministic stand-in for Silero used in tests (and if Silero is missing)."""

    def score(pcm: np.ndarray) -> float:
        if pcm.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32, copy=False)))))
        return 1.0 if rms >= floor else 0.0

    return score


def make_vad(*, threshold: float = 0.5, min_silence_duration_ms: int = 250,
             score_fn: Callable | None = None, use_silero: bool = True) -> SileroVAD:
    if score_fn is not None:
        return SileroVAD(threshold=threshold, min_silence_duration_ms=min_silence_duration_ms,
                         score_fn=score_fn)
    if use_silero:
        try:
            return SileroVAD(threshold=threshold, min_silence_duration_ms=min_silence_duration_ms)
        except Exception:
            from app.utils.logger import get_logger
            get_logger(__name__).warning("Silero VAD unavailable; using energy gate")
    return SileroVAD(threshold=threshold, min_silence_duration_ms=min_silence_duration_ms,
                     score_fn=energy_score_fn())
