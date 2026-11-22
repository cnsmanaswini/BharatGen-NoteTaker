"""PCM16LE helpers for the ASR pipeline.

Internal contract: 16_000 Hz, mono, signed PCM16 little-endian.
Preferred frame: 100 ms = 3_200 bytes.
"""
from __future__ import annotations

import io
import math
import wave
from array import array
from pathlib import Path

SAMPLE_RATE_HZ = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2
FRAME_MS = 100
FRAME_BYTES = SAMPLE_RATE_HZ * SAMPLE_WIDTH * FRAME_MS // 1000  # 3200


def bytes_for_ms(duration_ms: float, sample_rate_hz: int = SAMPLE_RATE_HZ) -> int:
    if duration_ms <= 0 or sample_rate_hz <= 0:
        return 0
    samples = int(round(sample_rate_hz * (duration_ms / 1000.0)))
    return max(0, samples * SAMPLE_WIDTH)


def bytes_to_ms(byte_count: int, sample_rate_hz: int = SAMPLE_RATE_HZ) -> float:
    if byte_count <= 0 or sample_rate_hz <= 0:
        return 0.0
    return (byte_count / SAMPLE_WIDTH / sample_rate_hz) * 1000.0


def align_pcm16(pcm: bytes) -> bytes:
    n = len(pcm) - (len(pcm) % SAMPLE_WIDTH)
    return pcm[:n]


def stereo_to_mono(pcm: bytes, channels: int) -> bytes:
    pcm = align_pcm16(pcm)
    if channels <= 1:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    if len(samples) < channels:
        return b""
    n_frames = len(samples) // channels
    out = array("h")
    for i in range(n_frames):
        acc = 0
        base = i * channels
        for c in range(channels):
            acc += samples[base + c]
        out.append(int(acc / channels))
    return out.tobytes()


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int = SAMPLE_RATE_HZ) -> bytes:
    pcm = align_pcm16(pcm)
    if not pcm or src_rate <= 0 or src_rate == dst_rate:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    if len(samples) == 1:
        repeats = max(1, dst_rate // src_rate)
        return (samples * repeats).tobytes()
    dst_n = max(1, int(round(len(samples) * (dst_rate / src_rate))))
    out = array("h")
    last = len(samples) - 1
    for i in range(dst_n):
        pos = i * last / max(1, dst_n - 1) if dst_n > 1 else 0.0
        idx = int(math.floor(pos))
        frac = pos - idx
        if idx >= last:
            out.append(samples[last])
            continue
        a = samples[idx]
        b = samples[idx + 1]
        out.append(int(a + (b - a) * frac))
    return out.tobytes()


def normalize_pcm16(pcm: bytes, sample_rate_hz: int, channels: int = 1) -> bytes:
    """Convert arbitrary PCM16LE to 16 kHz mono."""
    mono = stereo_to_mono(pcm, channels)
    return resample_pcm16(mono, sample_rate_hz, SAMPLE_RATE_HZ)


def pcm16le_to_wav_bytes(pcm: bytes, sample_rate_hz: int = SAMPLE_RATE_HZ) -> bytes:
    pcm = align_pcm16(pcm)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(int(sample_rate_hz))
        wav.writeframes(pcm)
    return buf.getvalue()


def wav_bytes_to_pcm16le(wav_bytes: bytes) -> tuple[bytes, int, int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width != SAMPLE_WIDTH:
        raise ValueError(f"unsupported sample width {width}; expected 2")
    return frames, rate, channels


def read_audio_file(path: Path) -> tuple[bytes, int, int]:
    """Load a WAV as PCM16. Other formats require ffmpeg (caller converts)."""
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width != SAMPLE_WIDTH:
        raise ValueError(f"unsupported sample width {width}; expected PCM16")
    return frames, rate, channels


class FrameSplitter:
    """Buffer incoming PCM into fixed-size frames (default 100 ms / 3200 bytes)."""

    def __init__(self, frame_bytes: int = FRAME_BYTES):
        if frame_bytes <= 0 or frame_bytes % SAMPLE_WIDTH:
            raise ValueError("frame_bytes must be a positive even number")
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def feed(self, pcm: bytes) -> list[bytes]:
        self._buf.extend(align_pcm16(pcm))
        frames: list[bytes] = []
        while len(self._buf) >= self.frame_bytes:
            frames.append(bytes(self._buf[:self.frame_bytes]))
            del self._buf[:self.frame_bytes]
        return frames

    def flush(self) -> bytes:
        leftover = align_pcm16(bytes(self._buf))
        self._buf.clear()
        return leftover
