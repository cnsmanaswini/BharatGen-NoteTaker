"""Common lifecycle for capture subsystems (audio now; video/text later).

Capture state is deliberately separate from meeting state: a meeting can be
IN_MEETING while audio capture is FAILED, and the coordinator reports that
instead of pretending the whole session was captured.
"""
import abc
from dataclasses import dataclass, field
from enum import Enum


class CaptureState(str, Enum):
    NOT_INITIALIZED = "not_initialized"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(frozen=True)
class SubsystemStatus:
    name: str
    state: CaptureState
    detail: str = ""
    stats: dict = field(default_factory=dict)


class CaptureSubsystem(abc.ABC):
    name = "capture"

    def __init__(self) -> None:
        self.state = CaptureState.NOT_INITIALIZED
        self.detail = ""

    def initialize(self) -> bool:
        self.state = CaptureState.READY
        self.detail = ""
        return True

    @abc.abstractmethod
    def start(self) -> bool:
        """Begin capturing. Return False on failure; never raise."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop capturing and release resources. Must never raise."""

    def cleanup(self) -> None:
        """Release anything stop() left behind. Must never raise."""

    def get_status(self) -> SubsystemStatus:
        try:
            stats = self._stats()
        except Exception:
            stats = {}
        return SubsystemStatus(self.name, self.state, self.detail, stats)

    def _stats(self) -> dict:
        return {}
