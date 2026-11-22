import wave

from app.asr.audio import (FRAME_BYTES, SAMPLE_RATE_HZ, FrameSplitter,
                           bytes_for_ms, bytes_to_ms, normalize_pcm16,
                           pcm16le_to_wav_bytes, resample_pcm16,
                           stereo_to_mono, wav_bytes_to_pcm16le)
from tests.asr_helpers import pcm_tone


def test_frame_bytes_are_100ms():
    assert FRAME_BYTES == 3200
    assert bytes_for_ms(100) == 3200
    assert bytes_to_ms(3200) == 100.0
    assert bytes_for_ms(250) == 8000


def test_stereo_to_mono_averages_channels():
    stereo = b"\x00\x10\x00\x20"  # two samples: 4096 and 8192
    mono = stereo_to_mono(stereo, 2)
    assert len(mono) == 2
    import struct
    assert struct.unpack("<h", mono)[0] == (0x1000 + 0x2000) // 2


def test_resample_doubles_rate():
    src = pcm_tone(0.1, rate=8000)
    dst = resample_pcm16(src, 8000, 16000)
    assert abs(len(dst) - len(src) * 2) <= 4


def test_normalize_48k_stereo_to_16k_mono():
    from array import array
    import math
    n = 4800
    stereo = array("h")
    for i in range(n):
        s = int(0.4 * 32767 * math.sin(2 * math.pi * 440 * i / 48000))
        stereo.extend((s, s))
    out = normalize_pcm16(stereo.tobytes(), 48000, 2)
    duration = bytes_to_ms(len(out), SAMPLE_RATE_HZ)
    assert 90 < duration < 110


def test_wav_roundtrip():
    pcm = pcm_tone(0.25)
    wav = pcm16le_to_wav_bytes(pcm, SAMPLE_RATE_HZ)
    back, rate, channels = wav_bytes_to_pcm16le(wav)
    assert rate == SAMPLE_RATE_HZ and channels == 1
    assert back == pcm
    import io
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)


def test_frame_splitter_and_flush():
    splitter = FrameSplitter(FRAME_BYTES)
    pcm = pcm_tone(0.25)  # 250 ms = 8000 bytes
    frames = splitter.feed(pcm)
    assert len(frames) == 2
    leftover = splitter.flush()
    assert len(leftover) == 1600
    assert splitter.feed(b"") == []
