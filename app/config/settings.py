"""Environment-based configuration."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
        )