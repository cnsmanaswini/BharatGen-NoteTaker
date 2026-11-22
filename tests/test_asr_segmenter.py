from app.asr.audio import bytes_to_ms
from app.asr.segmenter import SpeechSegmenter
from app.asr.vad import SileroVAD, energy_score_fn, make_vad
from tests.asr_helpers import iter_frames, pcm_silence, pcm_tone


def _segmenter(**kwargs) -> SpeechSegmenter:
    vad = SileroVAD(score_fn=energy_score_fn(floor=0.02), min_silence_duration_ms=250)
    params = dict(
        target_segment_ms=5000,
        cut_silence_ms=1000,
        force_cut_silence_ms=2500,
        min_speech_ms=3000,
        overlap_ms=250,
    )
    params.update(kwargs)
    return SpeechSegmenter(vad, **params)


def _feed(seg: SpeechSegmenter, pcm: bytes, t0: float = 0.0):
    out = []
    t = t0
    for frame in iter_frames(pcm):
        out.extend(seg.feed(frame, t))
        t += bytes_to_ms(len(frame)) / 1000.0
    return out, t


def test_vad_detects_tone_and_silence():
    vad = make_vad(score_fn=energy_score_fn(0.02), use_silero=False)
    speech = vad.detect(pcm_tone(0.1), 16000)
    silence = vad.detect(pcm_silence(1.0), 16000)
    assert speech.speaking is True
    assert speech.rms > 0.05
    assert silence.speaking is False


def test_silence_cut_after_min_voiced():
    seg = _segmenter()
    parts, t = _feed(seg, pcm_tone(3.2) + pcm_silence(1.5))
    assert len(parts) == 1
    s = parts[0]
    assert s.reason == "vad_silence_boundary"
    assert 3.0 <= (s.end_s - s.start_s) <= 3.5
    assert abs(s.start_s) < 0.15


def test_force_silence_cut():
    seg = _segmenter(cut_silence_ms=10_000, force_cut_silence_ms=2500, min_speech_ms=500)
    parts, _ = _feed(seg, pcm_tone(0.8) + pcm_silence(3.0))
    assert parts
    assert parts[0].reason == "vad_force_silence_boundary"


def test_target_segment_duration():
    seg = _segmenter(min_speech_ms=3000)
    parts, _ = _feed(seg, pcm_tone(5.2))
    assert parts
    assert parts[0].reason == "target_segment_boundary"
    duration = parts[0].end_s - parts[0].start_s
    assert 4.9 <= duration <= 5.3


def test_overlap_prepended_to_asr_input():
    seg = _segmenter(min_speech_ms=300, target_segment_ms=400, overlap_ms=250)
    first, t = _feed(seg, pcm_tone(0.45))
    assert first
    tail = first[0].pcm16le[-8000:]  # 250 ms
    second, _ = _feed(seg, pcm_tone(0.45), t)
    assert second
    assert second[0].asr_pcm16le.startswith(tail)
    assert len(second[0].asr_pcm16le) > len(second[0].pcm16le)


def test_timestamps_follow_audio_timeline():
    seg = _segmenter()
    lead = pcm_silence(1.0)
    speech = pcm_tone(3.2)
    parts, _ = _feed(seg, lead + speech + pcm_silence(1.5))
    assert parts
    assert 0.9 <= parts[0].start_s <= 1.2


def test_meeting_end_flush_emits_short_utterance():
    seg = _segmenter(min_speech_ms=3000)
    parts, _ = _feed(seg, pcm_tone(0.8))
    assert parts == []
    flushed = seg.flush(force=True)
    assert len(flushed) == 1
    assert flushed[0].reason == "meeting_end_flush"
    assert 0.7 <= (flushed[0].end_s - flushed[0].start_s) <= 0.9


def test_trailing_silence_trimmed_before_asr():
    seg = _segmenter()
    parts, _ = _feed(seg, pcm_tone(3.2) + pcm_silence(1.4))
    assert parts
    speech_ms = bytes_to_ms(len(parts[0].pcm16le))
    assert 3000 <= speech_ms <= 3400


def test_sessions_have_isolated_vad_state():
    a = _segmenter()
    b = _segmenter()
    _feed(a, pcm_tone(1.0))
    assert a._collecting is True
    assert b._collecting is False
    assert a.vad._triggered != b.vad._triggered or a.vad._current_sample != b.vad._current_sample
