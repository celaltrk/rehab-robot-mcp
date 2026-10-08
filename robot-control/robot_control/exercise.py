"""Abstract base for exercises, and a declarative spec for their parameters."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import yaml
from .spec import ParamDef, RobotSpec, _build


@dataclass(frozen=True)
class ExerciseSpec:
    name: str
    description: str
    parameters: tuple[ParamDef, ...] = ()
    extra_schema: dict | None = None   # structured input the scalar validator cannot express
    viewer: str | None = None          # "window" is the core's own, else a path the robot ships

    def defaults(self) -> dict:
        return {p.name: p.default for p in self.parameters}

    def as_robot_spec(self) -> RobotSpec:
        """Exercise parameters reuse the robot validator unchanged."""
        return RobotSpec(name=self.name, description=self.description,
                         parameters=self.parameters)


def load_exercise_spec(path: Path | str) -> ExerciseSpec:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    params = tuple(_build(ParamDef, p, f"{path.name}: parameter {p.get('name')!r}")
                   for p in data.pop("parameters", []))
    return _build(ExerciseSpec, {**data, "parameters": params}, f"{path.name}: exercise")



class Exercise(ABC):
    @property
    @abstractmethod
    def spec(self) -> ExerciseSpec:
        """The declarative parameter surface of this exercise."""

    @abstractmethod
    def configure(self, params: dict) -> None:
        """Apply already-validated parameters. May be called while running."""

    @abstractmethod
    def reset(self, position, axes=(0, 1)) -> None:
        """Begin from the device's current position, in the robot's screen plane."""

    @abstractmethod
    def update(self, dt: float, position) -> dict:
        """Advance by dt; return {"scene": [...]} for anything the user should feel."""

    @abstractmethod
    def status(self) -> dict:
        """Score and progress for get_state; must be JSON-serialisable."""

    def view(self) -> dict:
        """Geometry for whatever draws this exercise."""
        return {}
