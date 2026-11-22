"""ASR data models."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class VADDecision:
    speaking: bool
    confidence: float
    score: float = 0.0
    rms: float = 0.0


@dataclass
class ASRResult:
    text: str
    language: str
    confidence: float = 0.0


@dataclass
class SpeechSegment:
    pcm16le: bytes
    start_s: float
    end_s: float
    reason: str
    voiced_s: float = 0.0
    asr_pcm16le: bytes = b""


@dataclass
class TranscriptSegment:
    meeting_id: str
    segment_id: str
    start_s: float
    end_s: float
    text: str
    language: str
    is_final: bool = True
    confidence: float = 0.0
    cut_reason: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["start_s"] = round(self.start_s, 3)
        data["end_s"] = round(self.end_s, 3)
        return data
