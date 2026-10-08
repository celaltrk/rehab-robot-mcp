"""An in-memory backend used only by the tests."""
from __future__ import annotations
import threading
from robot_control.backend import RobotBackend
from robot_control.spec import ActionDef, ParamDef, RobotSpec

FAKE_SPEC = RobotSpec(
    name="fake_robot",
    description="an in-memory stand-in device",
    parameters=(
        ParamDef(name="step_length", type="float", description="Length of each step",
                 unit="m", minimum=0.10, maximum=0.70, step=0.01, max_delta=0.10,
                 default=0.40),
        ParamDef(name="step_height", type="float",
                 description="Foot clearance height of each step",
                 unit="m", minimum=0.02, maximum=0.20, step=0.005, max_delta=0.05,
                 default=0.05),
        ParamDef(name="cadence", type="float", description="Stepping rate",
                 unit="steps/min", minimum=10.0, maximum=80.0, step=1.0,
                 max_delta=15.0, default=40.0),
        ParamDef(name="assist_level", type="float",
                 description="How much the robot assists the patient's motion",
                 unit="fraction", minimum=0.0, maximum=1.0, step=0.05,
                 max_delta=0.30, default=0.50),
    ),
    actions=(
        ActionDef(name="start", description="Begin the cycle"),
        ActionDef(name="stop", description="Stop the cycle"),
    ),
)


class FakeBackend(RobotBackend):
    def __init__(self, spec: RobotSpec = FAKE_SPEC) -> None:
        self._spec = spec
        self._lock = threading.Lock()
        self._params: dict = dict(spec.defaults())
        self._running = False
        self._estopped = False
        self._scene: list = []      # what an exercise has asked to be rendered

    @property
    def spec(self) -> RobotSpec:
        return self._spec

    def get_state(self) -> dict:
        with self._lock:
            return {
                "parameters": dict(self._params),
                "telemetry": {"walking": self._running,
                              "position": {"x": 0.0, "y": 0.0, "z": 0.0}},
                "scene": [dict(o) for o in self._scene],
                "status": {"estopped": self._estopped},
            }

    def apply_parameters(self, params: dict) -> dict:
        with self._lock:
            if self._estopped:
                return {"error": "device is e-stopped — call resume to re-enable "
                                 "before setting parameters"}
            self._params.update(params)
            return dict(params)

    def present_exercise(self, payload: dict) -> None:
        """The core's exercise hook; this fake keeps whatever it is handed."""
        self.set_view_overlay(payload.get("view") or {})
        self.set_scene(payload.get("scene") or [])

    def set_scene(self, objects: list) -> None:
        with self._lock:
            self._scene = list(objects or [])

    def set_view_overlay(self, overlay: dict) -> None:
        """Nothing draws this fake; the scene is what the tests assert on."""

    def do_action(self, name: str, args: dict) -> dict:
        with self._lock:
            if self._estopped and name == "start":
                return {"error": "device is e-stopped; cannot start"}
            if name == "start":
                self._running = True
            elif name == "stop":
                self._running = False
            else:
                raise NotImplementedError(name)
            return {"walking": self._running}

    def emergency_stop(self) -> dict:
        with self._lock:
            self._estopped = True
            self._running = False
            self._params["assist_level"] = 0.0
            return {"walking": False, "assist_level": 0.0, "estopped": True}

    def resume(self) -> dict:
        with self._lock:
            self._estopped = False
            return {"estopped": False, "walking": self._running}
