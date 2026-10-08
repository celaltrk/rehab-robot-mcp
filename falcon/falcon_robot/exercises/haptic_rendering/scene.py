"""Safety clamping for AI-authored haptic scenes.

Scenes reach the device through `backend.present_exercise()`, which the core
calls for whichever exercise is running — `reach` publishes a felt cue too — so
the object vocabulary and its limits belong here, with the exercise that can
author arbitrary geometry, not with the device-wide clamp `backend.py` applies
to every command regardless of any exercise. `clamp` is duplicated in both rather
than shared: sharing it would make backend.py and this module import each
other, and it is three lines that will not change.
"""
from __future__ import annotations
from ...spec import FALCON_SPEC

# From the spec, which already declares them; repeated here they could drift.
K_MAX = FALCON_SPEC.param("stiffness").maximum   # N/m, per-object stiffness ceiling
D_MAX = FALCON_SPEC.param("damping").maximum     # N.s/m, viscous damping ceiling
FORCE_CLAMP_N = FALCON_SPEC.max_force            # N, total rated force

# Scene-only limits: geometry the model may author, which no parameter bounds.
DIM_MAX = 0.10      # m, ceiling for radius / height / box half-extents
POINT_MAX = 0.08    # m, clamp object anchor points near the workspace envelope
VERT_MAX = 0.12     # m, ceiling for mesh vertex coordinates
MAX_TRIS = 4000     # cap on AI-authored mesh triangle count


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


SOLIDS = {"sphere", "box", "cylinder", "ellipsoid", "cone", "mesh"}
VALID_TYPES = SOLIDS | {"plane", "spring", "viscous", "vibration"}


def vec3(v, default=(0.0, 0.0, 0.0), lo=-POINT_MAX, hi=POINT_MAX) -> list[float]:
    try:
        out = [float(v[0]), float(v[1]), float(v[2])]
    except (TypeError, ValueError, IndexError):
        return list(default)
    return [clamp(x, lo, hi) for x in out]


def sanitise_object(obj: dict) -> dict | None:
    """Coerce and clamp one scene object into the fields the node expects."""
    if not isinstance(obj, dict):
        return None
    t = obj.get("type")
    if t not in VALID_TYPES:
        return None
    out: dict = {"type": t}
    # anchor/centre used by every geometric/field type
    out["point"] = vec3(obj.get("point"))
    if t in SOLIDS or t in ("plane", "spring"):
        # default to a firm wall when unspecified
        out["stiffness"] = clamp(float(obj.get("stiffness", 1200.0) or 0.0), 0.0, K_MAX)
    if t == "sphere":
        out["radius"] = clamp(float(obj.get("radius", 0.02) or 0.0), 0.0, DIM_MAX)
    if t == "box":
        out["size"] = vec3(obj.get("size", [0.02, 0.02, 0.02]),
                            (0.02, 0.02, 0.02), lo=0.0, hi=DIM_MAX)
    if t == "ellipsoid":
        out["size"] = vec3(obj.get("size", [0.02, 0.03, 0.02]),
                            (0.02, 0.03, 0.02), lo=1e-4, hi=DIM_MAX)
    if t == "cylinder":
        out["radius"] = clamp(float(obj.get("radius", 0.02) or 0.0), 0.0, DIM_MAX)
        out["height"] = clamp(float(obj.get("height", 0.04) or 0.0), 0.0, 2 * DIM_MAX)
    if t == "cone":
        out["radius"] = clamp(float(obj.get("radius", 0.02) or 0.0), 0.0, DIM_MAX)
        out["height"] = clamp(float(obj.get("height", 0.04) or 0.0), 0.0, 2 * DIM_MAX)
    if t == "mesh":
        m = sanitise_mesh(obj)
        if m is None:
            return None
        out["vertices"], out["triangles"] = m
    if t == "plane":
        out["normal"] = vec3(obj.get("normal", [0.0, 1.0, 0.0]), (0.0, 1.0, 0.0),
                              lo=-1.0, hi=1.0)
    if t == "viscous":
        out["damping"] = clamp(float(obj.get("damping", 1.0) or 0.0), 0.0, D_MAX)
    if t == "vibration":
        # A cue, not an object: no geometry, no net push.
        out["vibration_freq"] = clamp(float(obj.get("vibration_freq", 0.0) or 0.0), 0.0, 500.0)
        out["vibration_amp"] = clamp(float(obj.get("vibration_amp", 0.0) or 0.0), 0.0, 2.0)
    # surface texture applies to any solid
    if t in SOLIDS:
        out["friction"] = clamp(float(obj.get("friction", 0.0) or 0.0), 0.0, 1.0)
        out["vibration_freq"] = clamp(float(obj.get("vibration_freq", 0.0) or 0.0), 0.0, 500.0)
        out["vibration_amp"] = clamp(float(obj.get("vibration_amp", 0.0) or 0.0), 0.0, FORCE_CLAMP_N)
    return out


def sanitise_mesh(obj: dict):
    """Validate and clamp AI-authored mesh geometry, or None if unusable."""
    try:
        v = [float(x) for x in (obj.get("vertices") or [])]
        tri = [int(x) for x in (obj.get("triangles") or [])]
    except (TypeError, ValueError):
        return None
    if len(v) < 9 or len(v) % 3 != 0 or len(tri) < 3 or len(tri) % 3 != 0:
        return None
    nv = len(v) // 3
    tri = tri[: MAX_TRIS * 3]
    if any(i < 0 or i >= nv for i in tri):
        return None
    v = [clamp(x, -VERT_MAX, VERT_MAX) for x in v]
    return v, tri
