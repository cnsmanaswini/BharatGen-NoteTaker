"""Per-meeting ASR session: non-blocking ingest, VAD, Mancha, JSONL.

Capture threads only enqueue PCM. HTTP never runs on the capture thread.
"""
from __future__ import annotations

import queue
import threading
from pathlib import Path

from app.asr.audio import FRAME_BYTES, SAMPLE_RATE_HZ, FrameSplitter, bytes_to_ms
from app.asr.mancha import ManchaASRClient, strip_repeated_lead
from app.asr.models import TranscriptSegment
from app.asr.segmenter import SpeechSegmenter
from app.asr.storage import TranscriptStore
from app.asr.vad import make_vad
from app.utils.logger import get_logger

log = get_logger(__name__)

_STOP = object()


class AsrSession:
    def __init__(
        self,
        meeting_id: str,
        store: TranscriptStore,
        client: ManchaASRClient | None,
        segmenter: SpeechSegmenter,
        *,
        max_queue: int = 300,
        transcribe: bool = True,
    ):
        self.meeting_id = meeting_id
        self.store = store
        self.client = client
        self.segmenter = segmenter
        self.transcribe = transcribe and client is not None
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, int(max_queue)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames = FrameSplitter(FRAME_BYTES)
        self._seq = 0
        self._previous_text = ""
        self.dropped_frames = 0
        self.queued_high_water = 0
        self.asr_failures = 0
        self.segments_written = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"asr-{self.meeting_id}", daemon=True,
        )
        self._thread.start()

    def ingest(self, pcm16le: bytes, start_s: float) -> bool:
        """Non-blocking. Returns False if the frame was dropped due to backpressure."""
        if self._stop.is_set() or not pcm16le:
            return True
        item = (bytes(pcm16le), float(start_s))
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            with self._lock:
                self.dropped_frames += 1
                dropped = self.dropped_frames
            log.warning("asr.queue_full meeting=%s dropped=%s qsize=%s",
                        self.meeting_id, dropped, self._queue.qsize())
            return False
        qsize = self._queue.qsize()
        with self._lock:
            if qsize > self.queued_high_water:
                self.queued_high_water = qsize
        return True

    def close(self, timeout_s: float = 30.0) -> dict:
        self._stop.set()
        try:
            self._queue.put(_STOP, timeout=1)
        except queue.Full:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_s)
        self._thread = None
        self.store.close()
        return self.stats()

    def stats(self) -> dict:
        with self._lock:
            return {
                "meeting_id": self.meeting_id,
                "queue_size": self._queue.qsize(),
                "queue_high_water": self.queued_high_water,
                "dropped_frames": self.dropped_frames,
                "segments_written": self.segments_written,
                "asr_failures": self.asr_failures,
                "transcript_path": str(self.store.path),
            }

    def _run(self) -> None:
        try:
            while True:
                try:
                    item = self._queue.get(timeout=0.2)
                except queue.Empty:
                    if self._stop.is_set():
                        break
                    continue
                if item is _STOP:
                    break
                pcm, start_s = item
                self._handle_pcm(pcm, start_s)
            leftover = self._frames.flush()
            if leftover:
                last_start = self.segmenter._timeline_s
                for segment in self.segmenter.feed(leftover, last_start):
                    self._transcribe_segment(segment)
            for segment in self.segmenter.flush(force=True):
                self._transcribe_segment(segment)
        except Exception:
            log.exception("ASR session loop failed meeting=%s (bot continues)", self.meeting_id)

    def _handle_pcm(self, pcm: bytes, start_s: float) -> None:
        offset_s = start_s
        frames = self._frames.feed(pcm)
        if not frames and not self._frames._buf:
            return
        # Attribute 100 ms frames using the capture chunk's audio timeline.
        frame_s = bytes_to_ms(FRAME_BYTES, SAMPLE_RATE_HZ) / 1000.0
        if not frames:
            return
        # If this is the first data of a capture chunk, align the first frame
        # to start_s; leftover PCM inside FrameSplitter is shorter than 100 ms.
        for i, frame in enumerate(frames):
            ts = offset_s + i * frame_s
            for segment in self.segmenter.feed(frame, ts):
                self._transcribe_segment(segment)

    def _transcribe_segment(self, segment) -> None:
        self._seq += 1
        segment_id = f"segment-{self._seq:03d}"
        text = ""
        language = ""
        confidence = 0.0
        error = None
        if self.transcribe and self.client is not None:
            try:
                result = self.client.transcribe(segment.asr_pcm16le or segment.pcm16le, SAMPLE_RATE_HZ)
                text = (result.text or "").strip()
                if text:
                    text = strip_repeated_lead(self._previous_text, text)
                    if text:
                        self._previous_text = (result.text or "").strip()
                language = result.language
                confidence = result.confidence
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                with self._lock:
                    self.asr_failures += 1
                log.error("asr.segment_failed meeting=%s segment=%s error=%s",
                          self.meeting_id, segment_id, error)
        row = TranscriptSegment(
            meeting_id=self.meeting_id,
            segment_id=segment_id,
            start_s=segment.start_s,
            end_s=segment.end_s,
            text=text,
            language=language or "",
            is_final=True,
            confidence=confidence,
            cut_reason=segment.reason,
            error=error,
        )
        self.store.append(row)
        with self._lock:
            self.segments_written += 1
        log.info("asr.segment meeting=%s id=%s start_s=%.2f end_s=%.2f chars=%s lang=%s reason=%s",
                 self.meeting_id, segment_id, segment.start_s, segment.end_s,
                 len(text), language, segment.reason)


def session_from_settings(settings, meeting_id: str, *,
                          score_fn=None, client: ManchaASRClient | None = None,
                          transcribe: bool | None = None) -> AsrSession:
    vad = make_vad(
        threshold=settings.asr_vad_threshold,
        min_silence_duration_ms=settings.asr_vad_min_silence_ms,
        score_fn=score_fn,
        use_silero=settings.asr_use_silero,
    )
    segmenter = SpeechSegmenter(
        vad,
        target_segment_ms=settings.asr_target_segment_ms,
        cut_silence_ms=settings.asr_cut_silence_ms,
        force_cut_silence_ms=settings.asr_force_cut_silence_ms,
        min_speech_ms=settings.asr_min_speech_ms,
        overlap_ms=settings.asr_overlap_ms,
    )
    out_dir = Path(settings.asr_output_dir)
    store = TranscriptStore(out_dir / f"{meeting_id}.transcript.jsonl")
    if client is None and (transcribe is not False):
        url = settings.mancha_asr_url
        if not url:
            log.warning("ASR enabled but MANCHA_ASR_URL is empty; segments will be stored without text")
        if url:
            client = ManchaASRClient(
                api_url=url,
                api_key=settings.mancha_asr_api_key,
                model=settings.mancha_asr_model,
                timeout_s=settings.mancha_asr_timeout_s,
                language=settings.mancha_asr_language,
                retries=settings.mancha_asr_retries,
            )
    do_asr = settings.asr_enabled if transcribe is None else transcribe
    return AsrSession(
        meeting_id,
        store,
        client,
        segmenter,
        max_queue=settings.asr_queue_max,
        transcribe=do_asr,
    )
