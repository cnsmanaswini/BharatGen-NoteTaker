import json
import wave
from array import array
import math

from app.audio.chunk import Chunker, chunk_size_bytes, levels_dbfs, SILENCE_DBFS
from app.audio.sinks import IndexSink, WavSink


def _tone(seconds=1.0, amp=0.5, rate=16000, freq=440):
    n = int(seconds * rate)
    return array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n))).tobytes()


def test_chunk_size():
    assert chunk_size_bytes(500) == 16000
    assert chunk_size_bytes(100) == 3200


def test_chunker_splits_and_timestamps():
    c = Chunker(500, 1000.0)
    assert c.feed(bytes(10001)) == []
    out = c.feed(bytes(22000))
    assert [x.seq for x in out] == [0, 1]
    assert [x.start_ts for x in out] == [1000.0, 1000.5]
    assert all(x.duration_ms == 500 for x in out)
    assert c.flush() == []          # 1 leftover byte is not a full sample


def test_chunker_flush_partial():
    c = Chunker(500, 0.0)
    c.feed(bytes(8000))
    last = c.flush()
    assert len(last) == 1 and last[0].duration_ms == 250


def test_levels():
    assert levels_dbfs(bytes(3200)) == (SILENCE_DBFS, SILENCE_DBFS)
    rms, peak = levels_dbfs(_tone(0.5, amp=0.5))
    assert abs(peak - (-6.0)) < 0.2
    assert -10 < rms < -8


def test_sinks(tmp_path):
    c = Chunker(500, 100.0)
    chunks = c.feed(_tone(1.5))
    wav, idx = WavSink(tmp_path / "a.wav"), IndexSink(tmp_path / "a.jsonl")
    for ch in chunks:
        wav.write(ch)
        idx.write(ch)
    wav.close()
    idx.close()
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (1, 16000, 2)
        assert w.getnframes() == 24000
    rows = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert [r["seq"] for r in rows] == [0, 1, 2]
    assert rows[1]["start_ts"] == 100.5 and rows[0]["bytes"] == 16000