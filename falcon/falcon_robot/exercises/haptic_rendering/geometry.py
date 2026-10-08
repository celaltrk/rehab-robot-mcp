"""Scene geometry: where the hand sits relative to rendered objects."""
from __future__ import annotations
import math


def distance(a, b) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def contains(pos, obj: dict) -> bool:
    """Is `pos` inside this scene object? Fields follow the haptic-scene schema."""
    t = obj.get("type")
    p = obj.get("point") or [0.0, 0.0, 0.0]
    if len(p) != 3:
        return False
    x, y, z = pos
    if t == "sphere":
        return distance(pos, p) <= float(obj.get("radius", 0.0))
    if t == "ellipsoid":
        s = obj.get("size") or []
        if len(s) != 3:
            return False
        return sum(((pos[i] - p[i]) / s[i]) ** 2 for i in range(3) if s[i] > 1e-9) <= 1.0
    if t == "box":
        s = obj.get("size") or []
        if len(s) != 3:
            return False
        return all(abs(pos[i] - p[i]) <= s[i] for i in range(3))
    if t == "cylinder":
        return (math.hypot(x - p[0], y - p[1]) <= float(obj.get("radius", 0.0))
                and abs(z - p[2]) <= 0.5 * float(obj.get("height", 0.0)))
    if t == "cone":
        h = float(obj.get("height", 0.0))
        if h <= 1e-9:
            return False
        frac = (z - (p[2] - 0.5 * h)) / h          # 0 at the base, 1 at the apex
        return (0.0 <= frac <= 1.0
                and math.hypot(x - p[0], y - p[1])
                <= float(obj.get("radius", 0.0)) * (1.0 - frac))
    if t == "plane":
        n = obj.get("normal") or []
        if len(n) != 3:
            return False
        nm = math.sqrt(sum(c * c for c in n)) or 1.0
        return sum((pos[i] - p[i]) * n[i] for i in range(3)) / nm < 0.0
    return False


def touching(pos, objects) -> bool:
    """Is the hand inside any object in the scene?"""
    return any(contains(pos, o) for o in objects)
