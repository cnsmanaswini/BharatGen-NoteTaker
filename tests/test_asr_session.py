import json
from dataclasses import replace

import httpx

from app.asr.mancha import ManchaASRClient
from app.asr.session import AsrSession, session_from_settings
from app.asr.storage import TranscriptStore
from app.asr.vad import energy_score_fn
from app.config.settings import Settings
from tests.asr_helpers import pcm_silence, pcm_tone


def _settings(tmp_path, **overrides):
    base = replace(
        Settings.from_env(),
        asr_enabled=True,
        asr_output_dir=tmp_path,
        asr_use_silero=False,
        asr_min_speech_ms=3000,
        asr_target_segment_ms=5000,
        asr_cut_silence_ms=1000,
        asr_force_cut_silence_ms=2500,
        asr_overlap_ms=250,
        asr_queue_max=8,
        mancha_asr_url="http://mancha.test",
        mancha_asr_api_key="",
        mancha_asr_language="en",
    )
    return replace(base, **overrides)


def test_transcript_store_append_only(tmp_path):
    path = tmp_path / "m1.transcript.jsonl"
    store = TranscriptStore(path)
    from app.asr.models import TranscriptSegment
    store.append(TranscriptSegment("m1", "segment-001", 0.0, 1.0, "one", "en", True))
    store.append(TranscriptSegment("m1", "segment-002", 1.0, 2.0, "two", "hi", True))
    store.close()
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert [r["segment_id"] for r in rows] == ["segment-001", "segment-002"]
    assert rows[0]["meeting_id"] == "m1"
    assert rows[0]["is_final"] is True


def test_session_transcribes_and_stores(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "we need to deploy", "language": "en"})

    client = ManchaASRClient("http://mancha.test", language="en", retries=0,
                             transport=httpx.MockTransport(handler))
    settings = _settings(tmp_path)
    session = session_from_settings(settings, "meeting-123",
                                    score_fn=energy_score_fn(0.02), client=client,
                                    transcribe=True)
    session.start()
    pcm = pcm_tone(3.2) + pcm_silence(1.5)
    t = 0.0
    for i in range(0, len(pcm), 3200):
        session.ingest(pcm[i:i + 3200], t)
        t += 0.1
    stats = session.close()
    assert stats["segments_written"] >= 1
    rows = [json.loads(l) for l in (tmp_path / "meeting-123.transcript.jsonl").read_text().splitlines()]
    assert rows[0]["meeting_id"] == "meeting-123"
    assert rows[0]["text"] == "we need to deploy"
    assert rows[0]["language"] == "en"
    assert rows[0]["is_final"] is True
    assert rows[0]["start_s"] < rows[0]["end_s"]


def test_queue_backpressure_is_observable(tmp_path):
    settings = _settings(tmp_path, asr_queue_max=2)
    session = session_from_settings(
        settings, "q1", score_fn=energy_score_fn(0.02),
        client=None, transcribe=False,
    )
    # Do not start the worker so the queue fills.
    dropped = 0
    for i in range(10):
        if not session.ingest(pcm_tone(0.1), i * 0.1):
            dropped += 1
    assert dropped >= 1
    assert session.dropped_frames == dropped
    assert session.queued_high_water >= 2
    session.close()


def test_meeting_isolation_and_shutdown_flush(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "short", "language": "hi"})

    client_a = ManchaASRClient("http://mancha.test", retries=0,
                               transport=httpx.MockTransport(handler))
    client_b = ManchaASRClient("http://mancha.test", retries=0,
                               transport=httpx.MockTransport(handler))
    settings = _settings(tmp_path)
    a = session_from_settings(settings, "meet-a", score_fn=energy_score_fn(0.02),
                              client=client_a, transcribe=True)
    b = session_from_settings(settings, "meet-b", score_fn=energy_score_fn(0.02),
                              client=client_b, transcribe=True)
    a.start()
    b.start()
    a.ingest(pcm_tone(0.8), 0.0)
    b.ingest(pcm_tone(0.8), 5.0)
    a.close()
    b.close()
    path_a = tmp_path / "meet-a.transcript.jsonl"
    path_b = tmp_path / "meet-b.transcript.jsonl"
    assert path_a.exists() and path_b.exists()
    rows_a = [json.loads(l) for l in path_a.read_text().splitlines()] if path_a.stat().st_size else []
    rows_b = [json.loads(l) for l in path_b.read_text().splitlines()] if path_b.stat().st_size else []
    assert all(r["meeting_id"] == "meet-a" for r in rows_a)
    assert all(r["meeting_id"] == "meet-b" for r in rows_b)


def test_asr_http_failure_does_not_raise(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    client = ManchaASRClient("http://mancha.test", retries=0,
                             transport=httpx.MockTransport(handler))
    settings = _settings(tmp_path)
    session = session_from_settings(settings, "fail-1", score_fn=energy_score_fn(0.02),
                                    client=client, transcribe=True)
    session.start()
    pcm = pcm_tone(3.2) + pcm_silence(1.5)
    t = 0.0
    for i in range(0, len(pcm), 3200):
        session.ingest(pcm[i:i + 3200], t)
        t += 0.1
    stats = session.close()
    assert stats["asr_failures"] >= 1
    rows = [json.loads(l) for l in (tmp_path / "fail-1.transcript.jsonl").read_text().splitlines()]
    assert rows[0]["error"]
    assert rows[0]["text"] == ""
