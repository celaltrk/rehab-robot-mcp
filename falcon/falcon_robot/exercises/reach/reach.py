"""The reaching exercise as the falcon offers it: the game, and this robot's limits."""
from __future__ import annotations
from pathlib import Path
from robot_control.exercise import Exercise, ExerciseSpec, load_exercise_spec
from .game import Exercise as Game


class ReachExercise(Exercise):
    def __init__(self, spec: ExerciseSpec) -> None:
        self._spec = spec
        self._p = dict(spec.defaults())
        self._game: Game | None = None
        self._axes = (0, 1)
        self._origin = [0.0, 0.0, 0.0]

    @property
    def spec(self) -> ExerciseSpec:
        return self._spec

    def configure(self, params: dict) -> None:
        self._p.update(params)
        if self._game is not None:      # take effect without losing the score
            self._game.radius = self._p["radius"]
            self._game.dwell = self._p["dwell"]
            self._game.total = self._p["targets"]
            self._game.reach = self._p["spread"]

    def reset(self, position, axes=(0, 1)) -> None:
        self._axes = tuple(axes)
        self._origin = list(position)
        self._game = Game(reach=self._p["spread"], radius=self._p["radius"],
                          dwell=self._p["dwell"], total=self._p["targets"],
                          seed=self._p["seed"] or None)
        self._game.spawn(self._flat(position))

    # the game reasons in 2D, the robot in 3D; convert using the plane the device declared
    def _flat(self, position) -> list:
        return [position[self._axes[0]], position[self._axes[1]]]

    def _spatial(self, flat) -> list:
        p = list(self._origin)
        p[self._axes[0]], p[self._axes[1]] = flat[0], flat[1]
        return p

    def update(self, dt: float, position) -> dict:
        if self._game is None or self._game.done:
            return {"scene": []}
        flat = self._flat(position)
        self._game.update(dt, flat)
        return {"scene": self._cue(flat)}

    def _cue(self, flat) -> list:
        """A dot you can feel: buzzes on arrival, or draws you in if asked."""
        g = self._game
        at = self._spatial(g.goal)
        if g.on_target(flat):
            # felt wherever the hand is, so arrival registers without touching a surface
            return [{"type": "vibration", "vibration_freq": 180.0,
                     "vibration_amp": 0.8}]
        if self._p["assist"] and g.distance(flat) < g.radius * 5:
            return [{"type": "spring", "point": [round(c, 5) for c in at],
                     "stiffness": round(120.0 * self._p["assist"], 1)}]
        return []

    def status(self) -> dict:
        if self._game is None:
            return {"exercise": self._spec.name, "parameters": dict(self._p)}
        return {"exercise": self._spec.name, "parameters": dict(self._p),
                **self._game.summary(), "done": self._game.done}

    def view(self) -> dict:
        g = self._game
        if g is None:
            return {}
        return {"goal": list(g.goal), "goal_radius": g.radius, "axes": list(self._axes),
                "dwell_progress": round(g.progress(), 3),
                "score": g.hits, "score_of": g.total,
                "elapsed": round(g.elapsed, 2), "done": g.done}


SPEC = load_exercise_spec(Path(__file__).parent / "reach.yaml")


def EXERCISE() -> ReachExercise:
    return ReachExercise(SPEC)
