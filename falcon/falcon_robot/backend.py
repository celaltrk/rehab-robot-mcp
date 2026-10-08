"""ROS 2 backend for the Novint Falcon."""
from __future__ import annotations
import fcntl
import json
import math
import os
import threading
import time
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from falcon_ros2.msg import DeviceState, ForceCommand, HapticObject, HapticScene
from std_msgs.msg import String
from robot_control.backend import RobotBackend
from robot_control.spec import RobotSpec
from .exercises.haptic_rendering.scene import sanitise_object
from .spec import FALCON_SPEC

_SERVO_HZ = 500.0 # rate of backend's publish loop

FORCE_CLAMP_N = FALCON_SPEC.max_force # hard safety clamp from spec


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def clamp_mag(f: list[float], maxmag: float) -> list[float]:
    """Clamp a 3-vector's Euclidean magnitude, preserving direction."""
    mag = math.sqrt(f[0] * f[0] + f[1] * f[1] + f[2] * f[2])
    if mag <= maxmag or mag < 1e-9:
        return f
    s = maxmag / mag
    return [f[0] * s, f[1] * s, f[2] * s]

_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                  durability=DurabilityPolicy.VOLATILE) # Quality-of service. Queue is 1 message deep, best-effort (no retransmission), and volatile (nothing is retained for late-joining subscribers).

_CONTROL_LOCK_PATH = "/tmp/falcon_mcp_control.lock" # Lock so that only one process can control the Falcon.
# Wait this long: a restarting client races its own old server's exit.
_CONTROL_LOCK_WAIT_S = float(os.environ.get("FALCON_LOCK_WAIT_S", "6.0"))

class FalconRos2Backend(RobotBackend):
    def __init__(self, spec: RobotSpec = FALCON_SPEC) -> None:
        self._spec = spec
        self._lock = threading.Lock()

        # commanded impedance state
        d = spec.defaults()
        self._target = [float(d["target_x"]), float(d["target_y"]), float(d["target_z"])]
        self._K = float(d["stiffness"])
        self._D = float(d["damping"])
        self._scene: list[dict] = []       # rendered virtual objects
        self._last_scene: list = []        # last exercise scene accepted, for the diff
        self._estopped = False
        self._target_initialised = False
        self._target_bounds = [
            (spec.param(n).minimum, spec.param(n).maximum)
            for n in ("target_x", "target_y", "target_z")
        ]

        # latest telemetry — zeros until the first /device_state arrives
        self._pos = [0.0, 0.0, 0.0]          # handle position, m
        self._vel = [0.0, 0.0, 0.0]          # handle velocity, m/s, from the device
        self._last_force = [0.0, 0.0, 0.0]   # impedance force last published, N
        self._f_actual = [0.0, 0.0, 0.0]     # measured on the device, incl. scene
        self._f_human = [0.0, 0.0, 0.0]      # observer estimate of user effort
        self._button = False                 # grip button, as last reported
        self._seq = 0.0                      # the node's cycle counter, echoed back
        self._state_stamp = 0.0              # monotonic clock at that message
        self._device: dict = {}              # the node's own status flags
        self._have_state = False             # false until the first message: gates the force

        self._node: Node | None = None                         # everything below is built in start()
        self._pub = None                                       # -> /force_command
        self._scene_pub = None                                 # -> /haptic_scene, latched
        self._view_pub = None                                  # -> /exercise_view, latched
        self._executor: SingleThreadedExecutor | None = None   # spun by hand so stop() can end it
        self._owns_rclpy = False                               # did we init rclpy, or someone else?
        self._lock_fd: int | None = None                       # fd of the single-writer flock
        self._counter = 0                                      # our own outgoing sequence number
        self._running = False                                  # gates the servo loop
        self._spin_thread: threading.Thread | None = None      # runs the executor
        self._servo_thread: threading.Thread | None = None     # publishes at _SERVO_HZ

    @property
    def spec(self) -> RobotSpec:
        return self._spec

    # lifecycle
    def start(self) -> None:
        # Refuse to come up as a second controller before touching ROS at all.
        self._acquire_control_lock()
        if not rclpy.ok():
            rclpy.init()
            self._owns_rclpy = True
        self._node = Node("falcon_mcp_backend")
        self._pub = self._node.create_publisher(ForceCommand, "/force_command", _QOS)
        # Latched, so a node subscribing later still gets the current scene.
        self._scene_pub = self._node.create_publisher(
            HapticScene, "/haptic_scene",
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))
        # Latched, so a window opened mid-exercise still sees the current dot.
        self._view_pub = self._node.create_publisher(
            String, "/exercise_view",
            QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self._node.create_subscription(DeviceState, "/device_state", self._on_state, _QOS)
        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._running = True
        self._spin_thread = threading.Thread(target=self._spin, daemon=True)
        self._servo_thread = threading.Thread(target=self._servo, daemon=True)
        self._spin_thread.start()
        self._servo_thread.start()

    def stop(self) -> None:
        # Stop the servo first so nothing new is published.
        self._running = False
        if self._servo_thread:
            self._servo_thread.join(timeout=1.0)
        # Leave the device safe: publish zeros while the context is still up.
        self._publish_force(0.0, 0.0, 0.0)
        time.sleep(0.02)
        # Destroying the node while spin() is active core-dumps in C++.
        if self._executor is not None:
            self._executor.shutdown()
        if self._spin_thread:
            self._spin_thread.join(timeout=1.0)
        if self._node is not None:
            self._node.destroy_node()
            self._node = None
        if self._owns_rclpy and rclpy.ok():
            rclpy.shutdown()
            self._owns_rclpy = False
        self._release_control_lock()

    # single-writer control lock
    def _acquire_control_lock(self) -> None:
        """Take the single-writer flock, waiting briefly before giving up."""
        fd = os.open(_CONTROL_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o666)
        deadline = time.monotonic() + max(0.0, _CONTROL_LOCK_WAIT_S)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    holder = ""
                    try:
                        holder = os.pread(fd, 32, 0).decode(errors="ignore").strip()
                    except OSError:
                        pass
                    os.close(fd)
                    raise RuntimeError(
                        "another process is already controlling the Falcon (control lock "
                        f"{_CONTROL_LOCK_PATH} held"
                        + (f" by PID {holder}" if holder else "")
                        + "). Only one controller may command the device at a time — "
                        "close the other MCP client / backend before starting this one."
                    )
                time.sleep(0.25)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        os.fsync(fd)
        self._lock_fd = fd

    def _release_control_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None

    # ROS callbacks / threads
    def _on_state(self, msg: DeviceState) -> None:
        with self._lock:
            self._pos = [msg.x, msg.y, msg.z]
            self._vel = [msg.d_x, msg.d_y, msg.d_z]
            self._button = bool(msg.button_pressed)
            self._f_actual = [msg.f_actual_x, msg.f_actual_y, msg.f_actual_z]
            self._f_human = [msg.epsilon_x, msg.epsilon_y, msg.epsilon_z]
            self._seq = msg.counter
            self._state_stamp = time.monotonic()
            self._device = {
                "force_clamped": bool(msg.force_clamped),
                "deadman_required": bool(msg.require_button),
                "forces_enabled": bool(msg.forces_enabled),
                "device_error": bool(msg.device_error),
                "force_off_cause": int(msg.force_off_cause),
            }
            self._have_state = True
            if not self._target_initialised:
                # Adopt the measured position on connect, clamped to the spec.
                self._target = [clamp(v, lo, hi) for v, (lo, hi)
                                in zip((msg.x, msg.y, msg.z), self._target_bounds)]
                self._target_initialised = True

    def _spin(self) -> None:
        # Spin the executor (not rclpy.spin(node)) so stop() can end it cleanly.
        try:
            self._executor.spin()
        except Exception:
            pass

    def _servo(self) -> None:
        period = 1.0 / _SERVO_HZ
        while self._running:
            t0 = time.perf_counter()
            fx, fy, fz = self._compute_force()
            self._publish_force(fx, fy, fz)
            dt = time.perf_counter() - t0
            if dt < period:
                time.sleep(period - dt)

    def _compute_force(self) -> tuple[float, float, float]:
        with self._lock:
            if self._estopped or not self._have_state:
                self._last_force = [0.0, 0.0, 0.0]
                return 0.0, 0.0, 0.0
            f = [0.0, 0.0, 0.0]
            # Impedance only; the node renders scene forces and sums them.
            if self._target_initialised:
                for i in range(3):
                    f[i] += self._K * (self._target[i] - self._pos[i]) - self._D * self._vel[i]
            # Hard safety clamp on the impedance force magnitude.
            f = clamp_mag(f, FORCE_CLAMP_N)
            self._last_force = f
            return f[0], f[1], f[2]

    def _publish_force(self, fx: float, fy: float, fz: float) -> None:
        if self._pub is None:
            return
        msg = ForceCommand()
        msg.fx, msg.fy, msg.fz = fx, fy, fz
        self._counter += 1
        msg.counter = float(self._counter)
        msg.layer = 0.0
        self._pub.publish(msg)

    # RobotBackend contract
    def get_state(self) -> dict:
        with self._lock:
            return {
                "parameters": {
                    "target_x": self._target[0], "target_y": self._target[1],
                    "target_z": self._target[2],
                    "stiffness": self._K, "damping": self._D,
                },
                "telemetry": {
                    "position": {"x": self._pos[0], "y": self._pos[1], "z": self._pos[2]},
                    "velocity": {"x": self._vel[0], "y": self._vel[1], "z": self._vel[2]},
                    # Guidance term only; force_actual is what reaches the hand.
                    "force_cmd": {"x": self._last_force[0], "y": self._last_force[1],
                                  "z": self._last_force[2]},
                    "force_actual": {"x": self._f_actual[0], "y": self._f_actual[1],
                                     "z": self._f_actual[2]},
                    # Disturbance-observer estimate of the user's own effort.
                    "human_force": {"x": self._f_human[0], "y": self._f_human[1],
                                    "z": self._f_human[2]},
                    "seq": self._seq,
                    "state_age_s": (round(time.monotonic() - self._state_stamp, 3)
                                    if self._have_state else None),
                },
                "scene": [dict(o) for o in self._scene],
                "status": {"estopped": self._estopped, "connected": self._have_state,
                           "button_pressed": self._button, **self._device},
            }

    def present_exercise(self, payload: dict) -> None:
        """One tick of exercise output: what to draw, and what to render.

        The core calls this at its runner rate and does not interpret either
        half — the overlay is JSON for this robot's window, the scene is this
        robot's own object vocabulary. Diffing the scene is ours because the
        cost is ours: republishing rebuilds the renderer's CHAI3D world, so an
        unchanged scene must not be sent again. The overlay is idempotent and
        goes every tick.
        """
        with self._lock:
            if self._estopped:
                return
        self.set_view_overlay(payload.get("view") or {})
        scene = list(payload.get("scene") or [])
        with self._lock:
            if scene == self._last_scene:
                return
            self._last_scene = scene
        self.set_scene(scene)

    def set_view_overlay(self, overlay: dict) -> None:
        """Publish exercise geometry as JSON for the window to draw."""
        if self._view_pub is not None:
            self._view_pub.publish(String(data=json.dumps(overlay or {})))

    def actual_parameters(self) -> dict:
        """Rate-cap reference: measured position for the targets, commanded values for gains."""
        with self._lock:
            ref = self._pos if self._have_state else self._target
            return {
                "target_x": ref[0], "target_y": ref[1], "target_z": ref[2],
                "stiffness": self._K, "damping": self._D,
            }

    def apply_parameters(self, params: dict) -> dict:
        with self._lock:
            if self._estopped:
                return {"error": "device is e-stopped — call resume to re-enable "
                                 "before setting parameters"}
            if "target_x" in params:
                self._target[0] = params["target_x"]
            if "target_y" in params:
                self._target[1] = params["target_y"]
            if "target_z" in params:
                self._target[2] = params["target_z"]
            if "stiffness" in params:
                self._K = params["stiffness"]
            if "damping" in params:
                self._D = params["damping"]
            self._target_initialised = True   # explicit command overrides adopt-current
            return dict(params)

    def set_scene(self, objects: list) -> None:
        """The exercise runner's scene, rendered by CHAI3D in the node."""
        scene = [o for o in (sanitise_object(o) for o in objects or []) if o is not None]
        with self._lock:
            if self._estopped:
                return
            self._scene = scene
        self._publish_scene(scene)

    def do_action(self, name: str, args: dict) -> dict:
        if name != "release":
            raise NotImplementedError(name)
        # Bypasses the rate cap deliberately. The cap limits how fast guidance
        # may change; this only ever removes it, and the force it leaves is
        # zero — the same place e_stop lands, without the latch.
        with self._lock:
            if self._estopped:
                return {"error": "device is e-stopped — call resume to re-enable"}
            self._K = 0.0
            self._D = 0.0
        self._publish_force(0.0, 0.0, 0.0)
        return {"released": True, "stiffness": 0.0, "damping": 0.0}

    def _publish_scene(self, scene: list[dict]) -> None:
        if self._scene_pub is None:
            return
        msg = HapticScene()
        for o in scene:
            ho = HapticObject()
            ho.type = o["type"]
            ho.point = [float(x) for x in o.get("point", [0.0, 0.0, 0.0])]
            ho.normal = [float(x) for x in o.get("normal", [0.0, 1.0, 0.0])]
            ho.size = [float(x) for x in o.get("size", [0.0, 0.0, 0.0])]
            ho.force = [float(x) for x in o.get("force", [0.0, 0.0, 0.0])]
            ho.radius = float(o.get("radius", 0.0))
            ho.height = float(o.get("height", 0.0))
            ho.stiffness = float(o.get("stiffness", 0.0))
            ho.damping = float(o.get("damping", 0.0))
            ho.friction = float(o.get("friction", 0.0))
            ho.vibration_freq = float(o.get("vibration_freq", 0.0))
            ho.vibration_amp = float(o.get("vibration_amp", 0.0))
            ho.vertices = [float(x) for x in o.get("vertices", [])]
            ho.triangles = [int(x) for x in o.get("triangles", [])]
            msg.objects.append(ho)
        self._scene_pub.publish(msg)

    def emergency_stop(self) -> dict:
        with self._lock:
            self._estopped = True
            self._K = 0.0
            self._scene = []        # drop any rendered objects too
            self._last_scene = []   # ...and forget it, so resume republishes
        self._publish_force(0.0, 0.0, 0.0)
        self._publish_scene([])   # tell the node to stop rendering
        return {"estopped": True, "force": [0.0, 0.0, 0.0]}

    def resume(self) -> dict:
        with self._lock:
            self._estopped = False
            self._target_initialised = False
            return {"estopped": False, "stiffness": self._K}


BACKEND = FalconRos2Backend
