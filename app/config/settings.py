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
        )