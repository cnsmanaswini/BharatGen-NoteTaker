"""Console + file logging."""
import logging
from pathlib import Path


class _IgnoreDriverShutdownNoise(logging.Filter):
    """Playwright's driver dies with Ctrl+C on Windows; asyncio then logs a harmless error."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "asyncio" and "Future exception was never retrieved" in record.getMessage():
            return False
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(_IgnoreDriverShutdownNoise())
    root.addHandler(console)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "meetbot.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(_IgnoreDriverShutdownNoise())
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)