"""Transcribe a local audio file through the ASR pipeline.

    python -m app.asr path/to/audio.wav
    python -m app.asr path/to/audio.wav --no-asr   # VAD/segments only
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from app.asr.audio import SAMPLE_RATE_HZ, normalize_pcm16, read_audio_file
from app.asr.mancha import ManchaASRClient
from app.asr.session import session_from_settings
from app.config.settings import Settings
from app.utils.logger import get_logger, setup_logging

log = get_logger("asr.cli")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run VAD + Mancha ASR on a local audio file")
    p.add_argument("audio_path", help="WAV (PCM16) or any file ffmpeg can decode")
    p.add_argument("--meeting-id", default="local-file")
    p.add_argument("--no-asr", action="store_true",
                   help="Segment only; do not call Mancha")
    p.add_argument("--output-dir", default=None)
    return p.parse_args(argv)


def load_pcm(path: Path, ffmpeg_path: str) -> bytes:
    suffix = path.suffix.lower()
    if suffix == ".wav":
        try:
            pcm, rate, channels = read_audio_file(path)
            return normalize_pcm16(pcm, rate, channels)
        except Exception as exc:
            log.info("WAV parse failed (%s); trying ffmpeg", exc)
    if shutil.which(ffmpeg_path) is None and not Path(ffmpeg_path).is_file():
        raise SystemExit(f"Need a PCM16 WAV, or ffmpeg at {ffmpeg_path}")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = [
            ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(path),
            "-ac", "1", "-ar", str(SAMPLE_RATE_HZ), "-sample_fmt", "s16",
            str(tmp_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"ffmpeg failed: {proc.stderr.strip()[:400]}")
        pcm, rate, channels = read_audio_file(tmp_path)
        return normalize_pcm16(pcm, rate, channels)
    finally:
        tmp_path.unlink(missing_ok=True)


def run_file(path: Path, settings: Settings, meeting_id: str, transcribe: bool) -> Path:
    pcm = load_pcm(path, settings.ffmpeg_path)
    session = session_from_settings(settings, meeting_id, transcribe=transcribe)
    if transcribe and session.client is None:
        raise SystemExit("MANCHA_ASR_URL is not set; use --no-asr to segment only")
    session.start()
    # Feed in 100 ms frames using the audio timeline starting at 0.
    frame = 3200
    offset = 0
    t = 0.0
    step_s = 0.1
    try:
        while offset < len(pcm):
            chunk = pcm[offset:offset + frame]
            if len(chunk) % 2:
                chunk = chunk[:-1]
            session.ingest(chunk, t)
            offset += frame
            t += step_s
    finally:
        stats = session.close()
    log.info("Wrote %s (%s segments, dropped=%s failures=%s)",
             stats["transcript_path"], stats["segments_written"],
             stats["dropped_frames"], stats["asr_failures"])
    return Path(stats["transcript_path"])


def main(argv=None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()
    if args.output_dir:
        from dataclasses import replace
        settings = replace(settings, asr_output_dir=Path(args.output_dir).resolve())
    setup_logging(settings.log_level, settings.log_dir)
    path = Path(args.audio_path)
    if not path.is_file():
        log.error("File not found: %s", path)
        return 2
    transcribe = not args.no_asr
    run_file(path, settings, args.meeting_id, transcribe=transcribe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
