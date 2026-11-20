"""Console + file logging."""
import logging
from pathlib import Path


class _IgnoreDriverShutdownNoise(logging.Filter):
    """Playwright's driver dies with Ctrl+C on Windows; asyncio then logs a harmless error."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "asyncio" and "Future exception was never retrieved" in record.getMessage():
            return False
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None,
                  log_file: Path | None = None,
                  session_id: str | None = None) -> None:
    """Configure console + file logging.

    A worker process passes its own `log_file`/`session_id` so concurrent
    sessions write distinguishable logs instead of one shared file.
    """
    pattern = "%(asctime)s | %(levelname)-7s | %(name)s"
    if session_id:
        pattern += " | " + session_id.replace("|", "_")
    pattern += " | %(message)s"
    fmt = logging.Formatter(pattern)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(_IgnoreDriverShutdownNoise())
    root.addHandler(console)

    if log_file is None and log_dir:
        log_file = log_dir / "meetbot.log"
    if log_file:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(_IgnoreDriverShutdownNoise())
        root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)