import dataclasses
import json
import shutil
import time
import wave

import pytest

from app.audio.capture_engine import AudioCaptureEngine, make_source
from app.audio.chunk import AudioCaptureError
from app.audio.synthetic_source import SyntheticSource
from app.config.settings import Settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _settings(tmp_path, **overrides):
    base = dataclasses.replace(Settings.from_env(), audio_enabled=True, audio_source="synthetic",
                               audio_output_dir=tmp_path, audio_chunk_ms=500, ffmpeg_path="ffmpeg")
    return dataclasses.replace(base, **overrides)


def test_audio_disabled_by_default(monkeypatch):
    monkeypatch.delenv("AUDIO_ENABLED", raising=False)
    assert Settings.from_env().audio_enabled is False


def test_missing_ffmpeg_fails_gracefully(tmp_path):
    engine = AudioCaptureEngine(_settings(tmp_path, ffmpeg_path="no-such-ffmpeg-binary"), "t1")
    assert engine.start() is False
    assert engine.stop()["running"] is False     # must not raise


def test_invalid_chunk_duration(tmp_path):
    assert AudioCaptureEngine(_settings(tmp_path, audio_chunk_ms=5), "t2").start() is False


def test_unknown_source_rejected(tmp_path):
    with pytest.raises(AudioCaptureError):
        make_source(_settings(tmp_path, audio_source="bogus"))


def test_synthetic_source_format():
    src = SyntheticSource()
    assert src.open() == (48000, 2)
    block = None
    for _ in range(5):
        block = src.read(0.1)  # 0.1 s @ 48 kHz = 4800 frames
        if block:
            break
    assert block is not None and len(block) == 4800 * 2 * 2


@needs_ffmpeg
def test_pipeline_produces_16k_mono_timestamped_chunks(tmp_path):
    engine = AudioCaptureEngine(_settings(tmp_path), "t3")
    assert engine.start() is True
    time.sleep(2.5)
    stats = engine.stop()
    assert stats["chunks"] >= 3 and not stats["failed"]
    assert stats["peak_max_dbfs"] > -20           # the tone is clearly present
    with wave.open(str(tmp_path / "t3.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
    rows = [json.loads(l) for l in (tmp_path / "t3.index.jsonl").read_text().splitlines()]
    stamps = [r["start_ts"] for r in rows]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


@needs_ffmpeg
def test_sessions_are_independent_and_stop_is_idempotent(tmp_path):
    a = AudioCaptureEngine(_settings(tmp_path), "sess_a")
    b = AudioCaptureEngine(_settings(tmp_path), "sess_b")
    assert a.start() and b.start()
    time.sleep(1.2)
    a.stop()
    a.stop()                                       # second stop must be harmless
    time.sleep(0.6)
    sb = b.stop()
    assert sb["chunks"] > a.stats()["chunks"] - 1
    assert (tmp_path / "sess_a.wav").exists() and (tmp_path / "sess_b.wav").exists()