"""Environment-based configuration."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(value: str, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    browser_channel: str
    headless: bool
    user_data_dir: Path
    bot_display_name: str
    log_level: str
    log_dir: Path
    default_timeout_ms: int
    auth_wait_timeout_s: int
    admission_timeout_s: int
    alone_timeout_s: int
    alone_participant_limit: int
    reconnect_timeout_s: int
    audio_enabled: bool
    audio_source: str
    audio_device: str
    ffmpeg_path: str
    audio_chunk_ms: int
    audio_output_dir: Path
    audio_save_wav: bool
    # Concurrent orchestration (one worker process per meeting)
    max_concurrent_meetings: int
    worker_startup_timeout_s: int
    worker_shutdown_timeout_s: int
    profiles_dir: Path
    profile_source: Path
    profile_clone: bool
    allow_duplicate_meetings: bool
    # ASR (off by default; never blocks audio capture)
    asr_enabled: bool
    asr_output_dir: Path
    asr_queue_max: int
    asr_use_silero: bool
    asr_vad_threshold: float
    asr_vad_min_silence_ms: int
    asr_target_segment_ms: int
    asr_cut_silence_ms: int
    asr_force_cut_silence_ms: int
    asr_min_speech_ms: int
    asr_overlap_ms: int
    mancha_asr_url: str
    mancha_asr_api_key: str
    mancha_asr_model: str
    mancha_asr_language: str
    mancha_asr_timeout_s: float
    mancha_asr_retries: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            browser_channel=os.getenv("BROWSER_CHANNEL", "chrome").strip(),
            headless=_bool(os.getenv("HEADLESS", "false")),
            user_data_dir=Path(os.getenv("USER_DATA_DIR", "./browser_profile")).resolve(),
            bot_display_name=os.getenv("BOT_DISPLAY_NAME", "BharatGen Notetaker"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_dir=Path(os.getenv("LOG_DIR", "./logs")).resolve(),
            default_timeout_ms=int(os.getenv("DEFAULT_TIMEOUT_MS", "30000")),
            auth_wait_timeout_s=int(os.getenv("AUTH_WAIT_TIMEOUT_S", "600")),
            admission_timeout_s=int(os.getenv("ADMISSION_TIMEOUT_S", "900")),
            # Leave after this many seconds with <= alone_participant_limit people (0 = never).
            alone_timeout_s=int(os.getenv("ALONE_TIMEOUT_S", "120")),
            alone_participant_limit=int(os.getenv("ALONE_PARTICIPANT_LIMIT", "1")),
            reconnect_timeout_s=int(os.getenv("RECONNECT_TIMEOUT_S", "60")),
            # Milestone 4: audio capture (off by default; the bot is unchanged when off)
            audio_enabled=_bool(os.getenv("AUDIO_ENABLED", "false")),
            audio_source=os.getenv("AUDIO_SOURCE", "loopback").strip().lower(),
            audio_device=os.getenv("AUDIO_DEVICE", "").strip(),
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg").strip(),
            audio_chunk_ms=int(os.getenv("AUDIO_CHUNK_MS", "500")),
            audio_output_dir=Path(os.getenv("AUDIO_OUTPUT_DIR", "./audio")).resolve(),
            audio_save_wav=_bool(os.getenv("AUDIO_SAVE_WAV", "true")),
            max_concurrent_meetings=int(os.getenv("MAX_CONCURRENT_MEETINGS", "3")),
            worker_startup_timeout_s=int(os.getenv("WORKER_STARTUP_TIMEOUT_S", "120")),
            worker_shutdown_timeout_s=int(os.getenv("WORKER_SHUTDOWN_TIMEOUT_S", "15")),
            # Each orchestrated session gets its own copy of this profile so
            # Chromium never opens the same profile directory twice.
            profiles_dir=Path(os.getenv("PROFILES_DIR", "./profiles")).resolve(),
            profile_source=Path(os.getenv("PROFILE_SOURCE", "./browser_profile")).resolve(),
            profile_clone=_bool(os.getenv("PROFILE_CLONE", "true")),
            allow_duplicate_meetings=_bool(os.getenv("ALLOW_DUPLICATE_MEETINGS", "false")),
            asr_enabled=_bool(os.getenv("ASR_ENABLED", "false")),
            asr_output_dir=Path(os.getenv("ASR_OUTPUT_DIR", "./transcripts")).resolve(),
            asr_queue_max=int(os.getenv("ASR_QUEUE_MAX", "300")),
            asr_use_silero=_bool(os.getenv("ASR_USE_SILERO", "true")),
            asr_vad_threshold=_float(os.getenv("ASR_VAD_THRESHOLD", "0.5"), 0.5),
            asr_vad_min_silence_ms=int(os.getenv("ASR_VAD_MIN_SILENCE_MS", "250")),
            asr_target_segment_ms=int(os.getenv("ASR_TARGET_SEGMENT_MS", "5000")),
            asr_cut_silence_ms=int(os.getenv("ASR_CUT_SILENCE_MS", "1000")),
            asr_force_cut_silence_ms=int(os.getenv("ASR_FORCE_CUT_SILENCE_MS", "2500")),
            asr_min_speech_ms=int(os.getenv("ASR_MIN_SPEECH_MS", "3000")),
            asr_overlap_ms=int(os.getenv("ASR_OVERLAP_MS", "250")),
            mancha_asr_url=os.getenv("MANCHA_ASR_URL", "").strip(),
            mancha_asr_api_key=os.getenv("MANCHA_ASR_API_KEY", "").strip(),
            mancha_asr_model=os.getenv("MANCHA_ASR_MODEL", "").strip(),
            mancha_asr_language=os.getenv("MANCHA_ASR_LANGUAGE", "auto").strip() or "auto",
            mancha_asr_timeout_s=_float(os.getenv("MANCHA_ASR_TIMEOUT", "15"), 15.0),
            mancha_asr_retries=int(os.getenv("MANCHA_ASR_RETRIES", "2")),
        )