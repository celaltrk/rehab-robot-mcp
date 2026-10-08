"""Haptic rendering as the falcon offers it: the scene, and this robot's object vocabulary."""
from __future__ import annotations
from pathlib import Path
from robot_control.exercise import Exercise, ExerciseSpec, load_exercise_spec
from . import geometry


class RenderExercise(Exercise):
    """Holds a scene steady; the objects come from the robot's own schema."""
    def __init__(self, spec: ExerciseSpec) -> None:
        self._spec = spec
        self._p = dict(spec.defaults())
        self._objects: list[dict] = []
        self._contacts = 0
        self._touching = False
        self._elapsed = 0.0

    @property
    def spec(self) -> ExerciseSpec:
        return self._spec

    def configure(self, params: dict) -> None:
        if "objects" in params:
            objects = params["objects"]
            self._objects = list(objects) if isinstance(objects, list) else []
        self._p.update({k: v for k, v in params.items() if k != "objects"})

    def reset(self, position, axes=(0, 1)) -> None:
        self._axes = tuple(axes)
        self._contacts = 0
        self._touching = False
        self._elapsed = 0.0

    def update(self, dt: float, position) -> dict:
        self._elapsed += dt
        now = geometry.touching(list(position), self._objects)
        if now and not self._touching:
            self._contacts += 1
        self._touching = now
        return {"scene": self._objects}

    def status(self) -> dict:
        return {"exercise": self._spec.name, "objects": len(self._objects),
                "contacts": self._contacts, "touching": self._touching,
                "elapsed": round(self._elapsed, 2)}

    def view(self) -> dict:
        return {"scene": self._objects, "axes": list(getattr(self, "_axes", (0, 1))),
                "contacts": self._contacts, "touching": self._touching}


SPEC = load_exercise_spec(Path(__file__).parent / "render.yaml")


def EXERCISE() -> RenderExercise:
    return RenderExercise(SPEC)
