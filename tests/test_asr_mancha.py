import httpx
import pytest

from app.asr.mancha import (ManchaASRClient, ManchaHTTPError,
                            extract_transcript_language, extract_transcript_text,
                            normalize_api_url, strip_repeated_lead)
from tests.asr_helpers import pcm_tone


def test_normalize_api_url():
    assert normalize_api_url("http://host:4000").endswith("/v1/audio/transcriptions")
    assert normalize_api_url("http://host:4000/v1") == "http://host:4000/v1/audio/transcriptions"
    assert normalize_api_url("http://host:4000/v1/audio/transcriptions") == (
        "http://host:4000/v1/audio/transcriptions"
    )


def test_parse_text_and_language():
    payload = {"text": "नमस्ते", "language": "hi-IN", "confidence": 0.9}
    assert extract_transcript_text(payload) == "नमस्ते"
    assert extract_transcript_language(payload, "auto") == "hi"
    nested = {"data": {"transcript": "hello", "lang": "English"}}
    assert extract_transcript_text(nested) == "hello"
    assert extract_transcript_language(nested, "auto") == "en"
    assert extract_transcript_text({"text": ""}) == ""
    assert extract_transcript_text(None) == ""


def test_strip_overlap_words():
    assert strip_repeated_lead("we need to deploy", "deploy this model") == "this model"
    assert strip_repeated_lead("hello", "world") == "world"


def test_build_request_auto_omits_language_and_sets_auth():
    client = ManchaASRClient("http://mancha.example/v1", api_key="secret-key",
                             model="whisper-large-v3", language="auto")
    headers, files, data = client.build_request(pcm_tone(0.2), 16000)
    assert "Authorization" in headers
    assert headers["Authorization"] == "Bearer secret-key"
    assert data.get("model") == "whisper-large-v3"
    assert "language" not in data
    name, wav, ctype = files["file"]
    assert ctype == "audio/wav" and wav[:4] == b"RIFF"


def test_build_request_language_hi():
    client = ManchaASRClient("http://mancha.example", language="hi")
    _, _, data = client.build_request(pcm_tone(0.1), 16000, "hi")
    assert data["language"] == "hi"


def test_transcribe_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/audio/transcriptions")
        return httpx.Response(200, json={"text": "hello world", "language": "en"})

    client = ManchaASRClient("http://mancha.example", language="en",
                             transport=httpx.MockTransport(handler))
    result = client.transcribe(pcm_tone(0.2), 16000)
    assert result.text == "hello world"
    assert result.language == "en"


def test_http_4xx_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    client = ManchaASRClient("http://mancha.example", retries=3,
                             transport=httpx.MockTransport(handler))
    with pytest.raises(ManchaHTTPError):
        client.transcribe(pcm_tone(0.1), 16000)
    assert calls["n"] == 1


def test_http_5xx_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"text": "ok"})

    client = ManchaASRClient("http://mancha.example", retries=2,
                             transport=httpx.MockTransport(handler))
    result = client.transcribe(pcm_tone(0.1), 16000)
    assert result.text == "ok"
    assert calls["n"] == 3


def test_timeout_is_retryable():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    client = ManchaASRClient("http://mancha.example", retries=1,
                             transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ReadTimeout):
        client.transcribe(pcm_tone(0.1), 16000)
    assert calls["n"] == 2


def test_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    client = ManchaASRClient("http://mancha.example", retries=0,
                             transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError):
        client.transcribe(pcm_tone(0.1), 16000)
