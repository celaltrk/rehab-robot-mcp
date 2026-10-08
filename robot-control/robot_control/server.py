"""The trusted core between the MCP adapter and a robot's backend."""
from __future__ import annotations
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
import yaml
from .audit import AuditLog
from .registry import RobotRegistry, robot_entry
from .spec import ActionDef, RobotSpec
from .validator import validate

TOOL_TEXT: dict[str, str] = yaml.safe_load(
    (Path(__file__).parent / "prompts" / "tool_descriptions.yaml").read_text())

EXERCISE_HZ = 30.0     # tick rate of the exercise runner
E_STOP = "e_stop"
RESUME = "resume"
GET_STATE = "get_state"

log = logging.getLogger("robot_control.server")


@dataclass
class ToolDesc:
    """A protocol-neutral description the MCP layer turns into an MCP Tool."""

    name: str            # exposed tool name
    robot: str           # which backend it routes to
    kind: str            # "set_parameters" | "action" | "e_stop" | "resume" | "get_state"
    description: str
    input_schema: dict
    action: str | None = None   # underlying action name
    exercise: str | None = None  # exercise the tool configures


class RobotServer:
    def __init__(self, registry: RobotRegistry, audit: AuditLog | None = None,
                 exercises: dict | None = None, open_window: bool = False) -> None:
        self.registry = registry
        self.open_window = open_window   # off by default: tests and library users get a headless server
        self.exercises: dict[str, dict] = exercises or {}   # {robot: {exercise name: instance}}
        self._active: dict = {}          # robot -> exercise name
        self._ex_lock = threading.Lock()
        self._ex_stop = threading.Event()
        self._ex_thread: threading.Thread | None = None
        self._windows: dict = {}         # (robot, viewer) -> Popen of that window
        self._display_env: dict | None = None   # session DISPLAY, looked up once
        self.audit = audit or AuditLog()

    # lifecycle
    def start(self) -> None:
        for b in self.registry.all():
            log.info("starting backend for robot %r", b.spec.name)
            b.start()

    def stop(self) -> None:
        self.stop_exercise()
        self._close_windows()
        for b in self.registry.all():
            log.info("stopping backend for robot %r", b.spec.name)
            b.stop()

    # introspection
    @property
    def _multi(self) -> bool:
        return len(self.registry) > 1

    def _prefix(self, robot: str) -> str:
        return f"{robot}_" if self._multi else ""

    def get_spec(self, robot: str) -> RobotSpec:
        return self.registry.get(robot).spec

    def get_state(self, robot: str) -> dict:
        state = self.registry.get(robot).get_state()
        name = self._active.get(robot)
        ex = self._exercise(robot, name) if name else None
        state["exercise"] = ({**ex.status(), "running": True}
                             if ex else {"running": False})
        return state

    # exercises
    def _exercise(self, robot: str, name: str):
        """One robot's exercise by name, or None; a robot only offers its own."""
        return self.exercises.get(robot, {}).get(name)

    def exercises_for(self, robot: str) -> dict:
        return self.exercises.get(robot, {})

    def start_exercise(self, robot: str, name: str, params: dict) -> dict:
        ex = self._exercise(robot, name)
        if ex is None:
            return {"status": "error",
                    "message": f"{robot} has no exercise {name!r}"}
        scalars, extra = self._split_exercise_args(ex, params)
        result = validate(ex.spec.as_robot_spec(), scalars, ex.status().get("parameters", {}))
        if not result.ok:
            self.audit.record("start_exercise", robot, exercise=name, requested=params,
                              verdict="rejected", rejected=result.rejected)
            return {"status": "rejected", "rejected": result.rejected,
                    "message": f"Rejected by safety validator — {result.reasons()}."}
        backend = self.registry.get(robot)
        state = backend.get_state()
        if state.get("status", {}).get("estopped"):
            return {"status": "error", "message": "device is e-stopped; call resume first"}
        pos = state.get("telemetry", {}).get("position", {})
        with self._ex_lock:
            ex.configure({**result.accepted, **extra})
            ex.reset([pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0)],
                     backend.spec.screen_axes)
            self._active[robot] = name
            self._ensure_runner()
        log.info("exercise %r started on %r with %s", name, robot, result.accepted)
        self._ensure_viewer(robot, ex.spec)
        self.audit.record("start_exercise", robot, exercise=name, requested=params,
                          verdict="applied", applied=result.accepted)
        return {"status": "started", "exercise": name, "applied": result.accepted,
                "state": ex.status()}

    def configure_exercise(self, robot: str, name: str, params: dict) -> dict:
        ex = self._exercise(robot, name)
        if ex is None:
            return {"status": "error",
                    "message": f"{robot} has no exercise {name!r}"}
        # Configuring an exercise nobody started used to succeed and do nothing:
        # only an active exercise is ticked, so the settings landed on an object
        # the runner never reads. A model that picks this tool instead of
        # start_ is told so, and told which tool it wanted.
        with self._ex_lock:
            running = self._active.get(robot)
        if running != name:
            msg = (f"{name!r} is not running on {robot}. "
                   f"Call {self._prefix(robot)}start_{name} to begin it.")
            self.audit.record("configure_exercise", robot, exercise=name,
                              requested=params, verdict="refused", error=msg)
            return {"status": "error", "message": msg}
        scalars, extra = self._split_exercise_args(ex, params)
        result = validate(ex.spec.as_robot_spec(), scalars, ex.status().get("parameters", {}))
        if not result.ok:
            self.audit.record("configure_exercise", robot, exercise=name, requested=params,
                              verdict="rejected", rejected=result.rejected)
            return {"status": "rejected", "rejected": result.rejected,
                    "message": f"Rejected by safety validator — {result.reasons()}."}
        with self._ex_lock:
            ex.configure({**result.accepted, **extra})
        self.audit.record("configure_exercise", robot, exercise=name, requested=params,
                          verdict="applied", applied=result.accepted)
        return {"status": "applied", "applied": result.accepted, "state": ex.status()}

    def stop_exercise(self, robot: str | None = None) -> dict:
        with self._ex_lock:
            stopped = list(self._active) if robot is None else [robot]
            for r in stopped:
                self._active.pop(r, None)
        for r in stopped:
            try:
                self._present(self.registry.get(r), {"view": {}, "scene": []})
            except Exception:
                pass
            self.audit.record("stop_exercise", r, verdict="applied")
        return {"status": "stopped", "robots": stopped}

    # exercise window
    def _has_display(self) -> bool:
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def _session_display_env(self) -> dict:
        """DISPLAY and friends, asked of systemd: an MCP client sanitises the environment."""
        if self._display_env is not None:
            return self._display_env
        self._display_env = {}
        # the user bus needs this, and it is derivable when the client did not pass it
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        try:
            out = subprocess.run(["systemctl", "--user", "show-environment"],
                                 capture_output=True, text=True, timeout=5,
                                 env={**os.environ, "XDG_RUNTIME_DIR": runtime})
            if out.returncode == 0:
                wanted = ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR")
                for line in out.stdout.splitlines():
                    key, sep, value = line.partition("=")
                    if sep and key in wanted:
                        self._display_env[key] = value
        except Exception as e:
            log.info("could not read the session environment: %s", e)
        return self._display_env

    def _viewer_command(self, robot: str, spec) -> list | None:
        """How to open this exercise's window: a program the robot ships, named relative to its package."""
        viewer = getattr(spec, "viewer", None)
        if not viewer:
            return None
        try:
            path = robot_entry(robot).path() / viewer
        except Exception:
            return None
        if not path.exists():
            log.warning("robot %r declares viewer %r, which is not there", robot, viewer)
            return None
        return [str(path)]

    def _ensure_viewer(self, robot: str, spec) -> None:
        """Open the exercise's window if it can be opened; every reason not to is a silent skip."""
        if not self.open_window:
            return
        viewer = getattr(spec, "viewer", None)
        key = (robot, viewer)
        live = self._windows.get(key)
        if live is not None and live.poll() is None:
            return                        # already up; one window, not one per start
        extra = {} if self._has_display() else self._session_display_env()
        if not self._has_display() and not (extra.get("DISPLAY")
                                            or extra.get("WAYLAND_DISPLAY")):
            log.info("no display anywhere; not opening a window for %r", robot)
            return
        cmd = self._viewer_command(robot, spec)
        if cmd is None:
            return
        try:
            # inherits the server's environment, where the ROS and SDK paths come from
            self._windows[key] = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, env={**os.environ, **extra})
            log.info("opened viewer %r for %r", viewer, robot)
        except Exception as e:
            log.warning("could not open viewer %r for %r: %s", viewer, robot, e)

    def _close_windows(self) -> None:
        for key, proc in list(self._windows.items()):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except Exception:
                    proc.kill()
            self._windows.pop(key, None)

    def _ensure_runner(self) -> None:
        if self._ex_thread and self._ex_thread.is_alive():
            return
        self._ex_stop.clear()
        self._ex_thread = threading.Thread(target=self._run_exercises, daemon=True)
        self._ex_thread.start()

    @staticmethod
    def _present(backend, payload: dict) -> None:
        """Hand one tick of exercise output to a device that can present it.

        Optional and duck-typed on purpose. The core has no contract for what a
        scene or an overlay means, so it declares none and never looks inside
        the payload. A device that can show or render an exercise implements
        `present_exercise` and owns all of it — the vocabulary, the clamping,
        and whether an unchanged scene is worth resending. One that cannot
        simply does not define the method.
        """
        present = getattr(backend, "present_exercise", None)
        if present is not None:
            present(payload)

    def _run_exercises(self) -> None:
        period = 1.0 / EXERCISE_HZ
        last = time.monotonic()
        while not self._ex_stop.is_set():
            time.sleep(period)
            now = time.monotonic()
            dt, last = now - last, now
            with self._ex_lock:
                active = dict(self._active)
            for robot, name in active.items():
                ex = self._exercise(robot, name)
                if ex is None:
                    continue
                try:
                    backend = self.registry.get(robot)
                    st = backend.get_state()
                    if st.get("status", {}).get("estopped"):
                        self.stop_exercise(robot)
                        continue
                    pos = st.get("telemetry", {}).get("position", {})
                    with self._ex_lock:
                        out = ex.update(dt, [pos.get("x", 0.0), pos.get("y", 0.0),
                                             pos.get("z", 0.0)])
                    self._present(backend, {"view": ex.view(),
                                            "scene": out.get("scene", [])})
                except Exception:
                    log.exception("exercise %r failed on %r; stopping it", name, robot)
                    self.stop_exercise(robot)

    # command surface (generated from specs)
    @staticmethod
    def _tool_text(key: str, spec: RobotSpec, prefix: str) -> str:
        """Render one server-added tool's description from prompts/tool_descriptions.yaml."""
        return TOOL_TEXT[key].strip().format(
            robot=spec.name, description=spec.description, prefix=prefix)

    def list_tools(self) -> list[ToolDesc]:
        tools: list[ToolDesc] = []
        for robot in self.registry.names():
            spec = self.registry.get(robot).spec
            p = self._prefix(robot)
            tools.append(
                ToolDesc(
                    name=f"{p}set_parameters",
                    robot=robot,
                    kind="set_parameters",
                    description=self._tool_text("set_parameters", spec, p),
                    input_schema=self._params_schema(spec),
                )
            )
            tools.append(
                ToolDesc(
                    name=f"{p}{GET_STATE}",
                    robot=robot,
                    kind="get_state",
                    description=self._tool_text(GET_STATE, spec, p),
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                )
            )
            for act in spec.actions:
                tools.append(
                    ToolDesc(
                        name=f"{p}{act.name}",
                        robot=robot,
                        kind="action",
                        action=act.name,
                        description=f"{act.description} ({spec.name}).",
                        input_schema=self._action_schema(act),
                    )
                )
            tools.append(
                ToolDesc(
                    name=f"{p}{E_STOP}",
                    robot=robot,
                    kind="e_stop",
                    description=self._tool_text(E_STOP, spec, p),
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                )
            )
            for ename, ex in self.exercises_for(robot).items():
                schema = self._exercise_schema(ex)
                tools.append(ToolDesc(
                    name=f"{p}start_{ename}", robot=robot, kind="start_exercise",
                    exercise=ename, input_schema=schema,
                    description=(f"BEGIN the '{ename}' exercise on the {spec.name}, "
                                 f"from a standing start. Use this whenever "
                                 f"'{ename}' is not already running, including the "
                                 f"first time; it replaces whatever else was "
                                 f"running. {ex.spec.description.strip()}. Progress "
                                 f"and score are reported by get_state."),
                ))
                tools.append(ToolDesc(
                    name=f"{p}configure_{ename}", robot=robot, kind="configure_exercise",
                    exercise=ename, input_schema=schema,
                    description=(f"Change settings of the '{ename}' exercise ONLY "
                                 f"while it is ALREADY RUNNING on the {spec.name}. "
                                 f"This does NOT start it: if '{ename}' is not "
                                 f"running the call fails, and you want "
                                 f"{p}start_{ename} instead. Only the settings you "
                                 f"name change."),
                ))
            if self.exercises_for(robot):
                tools.append(ToolDesc(
                    name=f"{p}stop_exercise", robot=robot, kind="stop_exercise",
                    description=f"Stop the exercise running on the {spec.name}.",
                    input_schema={"type": "object", "properties": {},
                                  "additionalProperties": False},
                ))
            tools.append(
                ToolDesc(
                    name=f"{p}{RESUME}",
                    robot=robot,
                    kind="resume",
                    description=self._tool_text(RESUME, spec, p),
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                )
            )
        return tools

    def _exercise_schema(self, ex) -> dict:
        schema = self._params_schema(ex.spec.as_robot_spec())
        schema["properties"].update((ex.spec.extra_schema or {}).get("properties", {}))
        return schema

    @staticmethod
    def _split_exercise_args(ex, params: dict) -> tuple[dict, dict]:
        """Scalars for the validator; structured fields for the exercise to clamp."""
        names = {p.name for p in ex.spec.parameters}
        return ({k: v for k, v in params.items() if k in names},
                {k: v for k, v in params.items() if k not in names})

    def _params_schema(self, spec: RobotSpec) -> dict:
        props: dict = {}
        for pdef in spec.parameters:
            s: dict = {"type": pdef.json_type()}
            desc = pdef.description
            if pdef.unit:
                desc += f" (unit: {pdef.unit})"
            if pdef.step is not None:
                desc += f", in steps of about {pdef.step:g}"
            s["description"] = desc
            if pdef.choices:
                s["enum"] = list(pdef.choices)
            if pdef.minimum is not None:
                s["minimum"] = pdef.minimum
            if pdef.maximum is not None:
                s["maximum"] = pdef.maximum
            # `step` is advisory: as multipleOf it made clients reject legal values (0.035 % 0.005 != 0)
            props[pdef.name] = s
        return {"type": "object", "properties": props, "additionalProperties": False}

    def _action_schema(self, act: ActionDef) -> dict:
        if act.input_schema is not None:
            return act.input_schema
        props = {
            p.name: {"type": p.json_type(), "description": p.description}
            for p in act.params
        }
        return {"type": "object", "properties": props, "additionalProperties": False}

    # command execution
    def set_parameters(self, robot: str, params: dict) -> dict:
        log.debug("set_parameters(%s) requested=%s", robot, params)
        backend = self.registry.get(robot)
        current = backend.actual_parameters()
        result = validate(backend.spec, params, current)
        if not result.ok:
            log.info("set_parameters(%s) REJECTED by validator: %s",
                     robot, result.rejected)
            self.audit.record("set_parameters", robot, requested=params,
                              verdict="rejected", rejected=result.rejected)
            return {
                "status": "rejected",
                "rejected": result.rejected,
                "message": f"Rejected by safety validator — {result.reasons()}. "
                           f"Nothing was applied.",
                "state": backend.get_state(),
            }
        applied = backend.apply_parameters(result.accepted)
        if isinstance(applied, dict) and "error" in applied:
            log.info("set_parameters(%s) REFUSED by backend: %s", robot, applied["error"])
            self.audit.record("set_parameters", robot, requested=params,
                              verdict="refused", error=applied["error"])
            return {"status": "error", "message": applied["error"],
                    "state": backend.get_state()}
        log.info("set_parameters(%s) APPLIED: %s", robot, applied)
        self.audit.record("set_parameters", robot, requested=params,
                          verdict="applied", applied=applied)
        return {"status": "applied", "applied": applied, "state": backend.get_state()}

    def do_action(self, robot: str, action: str, args: dict) -> dict:
        log.debug("do_action(%s, %s) args=%s", robot, action, args)
        backend = self.registry.get(robot)
        act = backend.spec.action(action)
        if act is None:
            return {"status": "error", "message": f"{robot} has no action {action!r}"}
        if act.input_schema is not None:
            accepted = args
        else:
            result = validate(RobotSpec(name=action, description="", parameters=act.params),
                              args, {})
            if not result.ok:
                self.audit.record("action", robot, action=action, requested=args,
                                  verdict="rejected", rejected=result.rejected)
                return {"status": "rejected", "rejected": result.rejected,
                        "message": f"Invalid arguments — {result.reasons()}."}
            accepted = result.accepted
        out = backend.do_action(action, accepted)
        if isinstance(out, dict) and "error" in out:
            self.audit.record("action", robot, action=action, requested=args,
                              verdict="refused", error=out["error"])
            return {"status": "error", "message": out["error"],
                    "state": backend.get_state()}
        self.audit.record("action", robot, action=action, requested=args,
                          verdict="applied")
        return {"status": "applied", "action": action, "result": out,
                "state": backend.get_state()}

    def emergency_stop(self, robot: str) -> dict:
        log.warning("EMERGENCY STOP requested for robot %r", robot)
        backend = self.registry.get(robot)
        out = backend.emergency_stop()
        self.audit.record("e_stop", robot, verdict="applied")
        return {"status": "estopped", "result": out, "state": backend.get_state()}

    def resume(self, robot: str) -> dict:
        log.info("resume requested for robot %r (clears e-stop latch)", robot)
        backend = self.registry.get(robot)
        out = backend.resume()
        self.audit.record("resume", robot, verdict="applied")
        return {"status": "resumed", "result": out, "state": backend.get_state()}
