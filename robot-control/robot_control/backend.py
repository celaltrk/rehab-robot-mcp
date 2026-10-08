"""The robot-agnostic backend interface."""
from __future__ import annotations
from abc import ABC, abstractmethod
from .spec import RobotSpec

class RobotBackend(ABC):
    """One physical (or simulated) device."""

    @property
    @abstractmethod
    def spec(self) -> RobotSpec:
        """The declarative capability spec for this device."""

    # lifecycle
    def start(self) -> None:
        """Bring the backend up."""

    def stop(self) -> None:
        """Tear the backend down cleanly."""

    # state
    @abstractmethod
    def get_state(self) -> dict:
        """JSON snapshot: {"parameters": ..., "telemetry": ..., "status": {"estopped": bool}}, ..."""

    def actual_parameters(self) -> dict:
        """Where each parameter really is, not what it was last told — what the rate cap checks against."""
        return self.get_state().get("parameters", {})

    # commands
    @abstractmethod
    def apply_parameters(self, params: dict) -> dict:
        """Apply a validated set of parameter updates. Returns applied values."""

    def do_action(self, name: str, args: dict) -> dict:
        """Execute a named action. Override if the spec declares actions."""
        raise NotImplementedError(f"{self.spec.name} has no action {name!r}")

    @abstractmethod
    def emergency_stop(self) -> dict:
        """Make the device safe immediately. Must always succeed and latch."""

    @abstractmethod
    def resume(self) -> dict:
        """Clear a latched e-stop."""
