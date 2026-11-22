# Adapted from KTS live-translate OpenAI-compatible ASR (server/app/providers/asr.py)
# and Mancha wiring (server/app/runtime.py). HTTP transcription contract only;
# KTS is not a runtime dependency.
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx

from app.asr.audio import pcm16le_to_wav_bytes
from app.asr.models import ASRResult

log = logging.getLogger(__name__)

OPENAI_TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"

UI_LANGUAGE_CODES = {"en", "fr", "hi", "bn", "ta", "mr", "gu", "kn", "ml", "te", "ur", "sa", "or", "pa", "auto"}

_UI_LANGUAGE_BY_NAME = {
    "english": "en",
    "french": "fr",
    "hindi": "hi",
    "bengali": "bn",
    "tamil": "ta",
    "marathi": "mr",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "telugu": "te",
    "urdu": "ur",
    "sanskrit": "sa",
    "odia": "or",
    "oriya": "or",
    "punjabi": "pa",
}

# Mancha / KTS pass UI codes through (en, hi, ta). Region tags can be overridden.
DEFAULT_LANGUAGE_TAGS = {"en": "en", "hi": "hi", "ta": "ta"}


def normalize_ui_language(value: str | None, language_hint: str | None) -> str:
    if not value:
        return language_hint or "en"
    raw = value.strip().lower()
    if not raw:
        return language_hint or "en"
    if raw in UI_LANGUAGE_CODES:
        return raw
    for separator in ("-", "_"):
        if separator in raw:
            prefix = raw.split(separator, 1)[0]
            if prefix in UI_LANGUAGE_CODES:
                return prefix
    if raw in _UI_LANGUAGE_BY_NAME:
        return _UI_LANGUAGE_BY_NAME[raw]
    return language_hint or "en"


def extract_transcript_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("text", "transcript", "transcription", "result"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = payload.get("data")
        if nested is not None:
            return extract_transcript_text(nested)
    return ""


def extract_transcript_language(payload: Any, language_hint: str | None) -> str:
    if isinstance(payload, dict):
        for key in ("language", "lang", "language_code", "detected_language"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return normalize_ui_language(value, language_hint)
        model_name = payload.get("model")
        if isinstance(model_name, str) and "hindi" in model_name.lower():
            return "hi"
        nested = payload.get("data")
        if nested is not None:
            nested_lang = extract_transcript_language(nested, None)
            if nested_lang:
                return normalize_ui_language(nested_lang, language_hint)
    return normalize_ui_language(None, language_hint)


def extract_transcript_confidence(payload: Any) -> float:
    if isinstance(payload, dict):
        for key in ("confidence", "score", "probability"):
            value = payload.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        nested = payload.get("data")
        if nested is not None:
            return extract_transcript_confidence(nested)
    return 0.0


def normalize_api_url(api_url: str | None) -> str:
    raw = (api_url or "").strip().rstrip("/")
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = f"http://{raw}"
    if raw.endswith("/audio/transcriptions"):
        return raw
    if raw.endswith("/v1"):
        return f"{raw}/audio/transcriptions"
    return f"{raw}{OPENAI_TRANSCRIPTIONS_PATH}"


def strip_repeated_lead(previous: str, current: str, *, max_words: int = 4) -> str:
    """Drop words that the 250 ms ASR overlap duplicated at the next segment start.

    Adapted from KTS server/app/providers/mancha.py.
    """
    prior = previous.split()
    words = current.split()
    normalize = lambda word: word.strip(".,!?;:।|\"'").lower()  # noqa: E731
    for size in range(min(max_words, len(prior), len(words) - 1), 0, -1):
        if [normalize(w) for w in prior[-size:]] == [normalize(w) for w in words[:size]]:
            return " ".join(words[size:])
    return current


class ManchaASRClient:
    """OpenAI-compatible `/v1/audio/transcriptions` client used by Mancha."""

    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        model: str = "",
        timeout_s: float = 15.0,
        language: str = "auto",
        retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        language_tags: dict[str, str] | None = None,
        file_field_name: str = "file",
    ):
        self.api_url = normalize_api_url(api_url)
        self.api_key = (api_key or "").strip()
        self.model = (model or "").strip()
        self.timeout_s = max(1.0, float(timeout_s))
        self.language = (language or "auto").strip().lower() or "auto"
        self.retries = max(0, int(retries))
        self.transport = transport
        self.language_tags = {**DEFAULT_LANGUAGE_TAGS, **(language_tags or {})}
        self.file_field_name = file_field_name

    def _language_tag(self, language_hint: str | None) -> str:
        raw = (language_hint or "").strip() or self.language
        if "-" in raw or "_" in raw:
            return raw
        lang = raw.lower()
        return self.language_tags.get(lang, lang)

    def build_request(self, pcm16le: bytes, sample_rate_hz: int,
                      language_hint: str | None = None) -> tuple[dict, dict, dict]:
        """Return (headers, files, data) for the multipart transcription POST."""
        lang = (language_hint or self.language or "auto").strip().lower()
        wav_bytes = pcm16le_to_wav_bytes(pcm16le, sample_rate_hz)
        tag = self._language_tag(lang)
        files = {self.file_field_name: (f"{tag}_segment.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {}
        if lang and lang != "auto":
            data["language"] = tag
        if self.model:
            data["model"] = self.model
        headers = {"correlation-id": str(uuid.uuid4())}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers, files, data

    def transcribe(self, pcm16le: bytes, sample_rate_hz: int,
                   language_hint: str | None = None) -> ASRResult:
        lang = (language_hint or self.language or "auto").strip().lower()
        if not pcm16le:
            return ASRResult(text="", language=lang, confidence=0.0)
        if not self.api_url:
            raise RuntimeError("MANCHA_ASR_URL is not configured")

        last_exc: BaseException | None = None
        attempts = self.retries + 1
        for attempt in range(attempts):
            started = time.perf_counter()
            try:
                result = self._post_once(pcm16le, sample_rate_hz, lang)
                elapsed_ms = (time.perf_counter() - started) * 1000
                log.info("asr.mancha ok elapsed_ms=%.0f chars=%s language=%s attempt=%s",
                         elapsed_ms, len(result.text), result.language, attempt + 1)
                return result
            except Exception as exc:
                last_exc = exc
                elapsed_ms = (time.perf_counter() - started) * 1000
                retryable = _is_retryable(exc)
                log.error("asr.mancha error elapsed_ms=%.0f attempt=%s retryable=%s error=%s",
                          elapsed_ms, attempt + 1, retryable, _safe_error(exc))
                if not retryable or attempt >= attempts - 1:
                    raise
                time.sleep(min(0.5 * (attempt + 1), 2.0))
        raise last_exc or RuntimeError("ASR failed")

    def _post_once(self, pcm16le: bytes, sample_rate_hz: int, lang: str) -> ASRResult:
        headers, files, data = self.build_request(pcm16le, sample_rate_hz, lang)
        timeout = httpx.Timeout(timeout=self.timeout_s)
        with httpx.Client(timeout=timeout, transport=self.transport) as client:
            response = client.post(self.api_url, headers=headers, files=files, data=data)
        if response.status_code >= 400:
            body_preview = response.text[:400] if response.text else ""
            log.error("asr.mancha.http_error status=%s url=%s body=%r",
                      response.status_code, self.api_url, body_preview)
            raise ManchaHTTPError(
                response.status_code,
                f"ASR API request failed status={response.status_code} url={self.api_url} "
                f"body={body_preview!r}",
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("ASR API returned a non-JSON body") from exc
        return ASRResult(
            text=extract_transcript_text(payload),
            language=extract_transcript_language(payload, lang),
            confidence=extract_transcript_confidence(payload),
        )


class ManchaHTTPError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, ManchaHTTPError) and exc.status_code >= 500:
        return True
    return False


def _safe_error(exc: BaseException) -> str:
    text = str(exc)
    for secret_marker in ("Bearer ", "api_key", "Authorization"):
        if secret_marker in text:
            return type(exc).__name__
    return f"{type(exc).__name__}: {text[:240]}"
