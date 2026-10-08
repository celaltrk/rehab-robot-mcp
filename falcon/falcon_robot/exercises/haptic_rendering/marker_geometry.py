"""Triangle and quaternion helpers for the Falcon's RViz markers.

Only this robot draws markers, so these live with it.
"""
from __future__ import annotations
import math


def cone_triangles(radius: float, height: float, segments: int = 24) -> list:
    """Triangle-list vertices for a cone centred on the origin, spanning z +-h/2."""
    half = 0.5 * height
    apex = (0.0, 0.0, half)
    base_c = (0.0, 0.0, -half)
    ring = [(radius * math.cos(2 * math.pi * i / segments),
             radius * math.sin(2 * math.pi * i / segments), -half)
            for i in range(segments)]
    pts: list = []
    for i in range(segments):
        a, b = ring[i], ring[(i + 1) % segments]
        pts += [apex, a, b]        # side
        pts += [base_c, b, a]      # base cap
    return pts


def mesh_triangles(vertices, triangles) -> list:
    """Triangle-list vertices from flat coordinates + indices, skipping bad indices."""
    if len(vertices) < 9 or len(triangles) < 3:
        return []
    nv = len(vertices) // 3
    pts: list = []
    for i in range(0, len(triangles) - 2, 3):
        for j in range(3):
            k = triangles[i + j]
            if 0 <= k < nv:
                pts.append((float(vertices[3 * k]), float(vertices[3 * k + 1]),
                            float(vertices[3 * k + 2])))
    return pts


def quaternion_from_z_to(normal) -> tuple:
    """Rotation taking +z onto `normal`, as (x, y, z, w). Identity if degenerate."""
    if len(normal) != 3:
        return (0.0, 0.0, 0.0, 1.0)
    nx, ny, nz = normal
    nm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if nm < 1e-9:
        return (0.0, 0.0, 0.0, 1.0)
    nx, ny, nz = nx / nm, ny / nm, nz / nm
    ax, ay, az = -ny, nx, 0.0            # axis = z x n
    al = math.sqrt(ax * ax + ay * ay + az * az)
    dot = max(-1.0, min(1.0, nz))        # cos(angle) = z . n
    if al < 1e-9:                        # parallel to z: identity or a flip
        return (0.0, 0.0, 0.0, 1.0) if dot > 0 else (1.0, 0.0, 0.0, 0.0)
    ax, ay, az = ax / al, ay / al, az / al
    s = math.sin(math.acos(dot) / 2.0)
    return (ax * s, ay * s, az * s, math.cos(math.acos(dot) / 2.0))
