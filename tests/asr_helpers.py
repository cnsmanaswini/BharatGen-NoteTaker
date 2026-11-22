from array import array
import math

from app.asr.audio import SAMPLE_RATE_HZ


def pcm_tone(seconds: float, amp: float = 0.4, rate: int = SAMPLE_RATE_HZ, freq: float = 440.0) -> bytes:
    n = int(round(seconds * rate))
    scale = int(amp * 32767)
    return array("h", (int(scale * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))).tobytes()


def pcm_silence(seconds: float, rate: int = SAMPLE_RATE_HZ) -> bytes:
    return bytes(int(round(seconds * rate)) * 2)


def iter_frames(pcm: bytes, frame_bytes: int = 3200):
    pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    i = 0
    while i < len(pcm):
        chunk = pcm[i:i + frame_bytes]
        if len(chunk) < 2:
            break
        if len(chunk) % 2:
            chunk = chunk[:-1]
        yield chunk
        i += frame_bytes
