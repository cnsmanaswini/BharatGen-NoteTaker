"""Append-only JSONL transcript store, one file per meeting."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.asr.models import TranscriptSegment
from app.utils.logger import get_logger

log = get_logger(__name__)


class TranscriptStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fp = self.path.open("a", encoding="utf-8")
        self.written = 0

    def append(self, segment: TranscriptSegment) -> None:
        line = json.dumps(segment.to_dict(), ensure_ascii=False)
        with self._lock:
            self._fp.write(line + "\n")
            self._fp.flush()
            self.written += 1

    def close(self) -> None:
        with self._lock:
            try:
                self._fp.flush()
                self._fp.close()
            except Exception:
                log.exception("Transcript store close failed path=%s", self.path)

    def __enter__(self) -> "TranscriptStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
