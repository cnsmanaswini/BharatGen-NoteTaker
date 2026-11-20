"""Worker process management and per-session browser profile preparation.

Windows-safe process handling: each worker gets its own process group, and it is
killed with `taskkill /T` so its Chromium tree goes with it. Nothing here touches
other workers' processes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from app.config.settings import Settings
from app.orchestration.session import MeetingSession, WorkerConfig
from app.utils.logger import get_logger

log = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IS_WINDOWS = os.name == "nt"

# Regenerable Chromium caches and lock files; copying the authenticated cookies
# and Local State is what matters, and a copied Singleton* lock would be wrong.
_PROFILE_IGNORE = shutil.ignore_patterns(
    "Singleton*", "lockfile", "*LOCK", "*LOG",
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
    "DawnCache", "GraphiteDawnCache", "Crashpad", "BrowserMetrics",
    "DeferredBrowserMetrics", "*.pma",
)


def prepare_profile(seed: Path, dest: Path, clone: bool = True) -> Path:
    """Create `dest` for one session, optionally seeded from `seed`.

    Returns the profile directory to use. Failure to copy is not fatal: the
    session falls back to a fresh profile (which may require manual sign-in)
    instead of blocking the other meetings.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if not clone or not seed or not Path(seed).is_dir():
        return dest
    try:
        if any(dest.iterdir()):
            return dest  # already prepared
    except OSError:
        return dest
    try:
        shutil.copytree(seed, dest, dirs_exist_ok=True, ignore=_PROFILE_IGNORE)
        log.info("Prepared browser profile %s from %s", dest, seed)
    except Exception as exc:
        log.warning("Could not clone profile %s -> %s (%s); using a fresh profile",
                    seed, dest, exc)
    return dest


class WorkerHandle(Protocol):
    pid: int | None

    def poll(self) -> int | None:
        """Return the exit code, or None while the worker is still running."""

    def stop(self, timeout_s: float) -> None:
        """Ask the worker to exit and force-kill its tree after `timeout_s`."""

    def kill(self) -> None:
        """Force-kill the worker process tree."""


class ProcessWorkerHandle:
    """One `python -m app.orchestration.worker` child process."""

    def __init__(self, config: WorkerConfig, config_path: Path,
                 stdout_file: Path, python: str | None = None,
                 cwd: Path | None = None) -> None:
        self.config = config
        self.config_path = Path(config_path)
        self.stdout_file = Path(stdout_file)
        self.python = python or sys.executable
        self.cwd = cwd or PROJECT_ROOT
        self._proc: subprocess.Popen | None = None
        self._out = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def start(self) -> "ProcessWorkerHandle":
        self.stdout_file.parent.mkdir(parents=True, exist_ok=True)
        self._out = self.stdout_file.open("a", encoding="utf-8", errors="replace")
        env = os.environ.copy()
        env.setdefault("PYTHONUTF8", "1")
        command = [self.python, "-m", "app.orchestration.worker",
                   "--config", str(self.config_path)]
        kwargs: dict = {
            "cwd": str(self.cwd),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": self._out,
            "stderr": subprocess.STDOUT,
        }
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(command, **kwargs)
        log.info("Started worker %s (pid %s) for %s", self.config.meeting_id,
                 self._proc.pid, self.stdout_file)
        return self

    def poll(self) -> int | None:
        return self._proc.poll() if self._proc else None

    def stop(self, timeout_s: float) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                try:
                    self._proc.wait(timeout=max(0.0, timeout_s))
                except subprocess.TimeoutExpired:
                    log.warning("Worker %s did not exit within %.1fs; killing its tree",
                                self.config.meeting_id, timeout_s)
                    self.kill()
        finally:
            self._close_output()

    def kill(self) -> None:
        if self._proc is None:
            return
        pid = self._proc.pid
        if self._proc.poll() is not None:
            return
        try:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15, check=False)
            else:
                os.killpg(os.getpgid(pid), 9)
        except Exception as exc:
            log.warning("Could not kill worker %s tree (%s); terminating process", pid, exc)
            try:
                self._proc.terminate()
            except Exception:
                pass
        try:
            self._proc.wait(timeout=10)
        except Exception:
            pass

    def _close_output(self) -> None:
        if self._out is not None:
            try:
                self._out.close()
            except Exception:
                pass
            self._out = None

    def __del__(self):  # pragma: no cover - best effort on interpreter exit
        self._close_output()


def default_worker_factory(session: MeetingSession, config: WorkerConfig,
                           settings: Settings) -> WorkerHandle:
    run_dir = Path(session.run_dir) if session.run_dir else Path(config.state_file).parent
    handle = ProcessWorkerHandle(
        config,
        config_path=run_dir / "config.json",
        stdout_file=(Path(session.log_file).parent / "worker.output.log")
        if session.log_file else run_dir / "worker.output.log",
    )
    return handle.start()
