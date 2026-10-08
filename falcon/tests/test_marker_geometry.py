"""The Falcon's RViz marker helpers: triangles and quaternions, pure."""
from __future__ import annotations
import math
import pytest
from falcon_robot.exercises.haptic_rendering import marker_geometry as g


def test_cone_triangles_are_centred_and_complete():
    pts = g.cone_triangles(0.02, 0.04, segments=8)
    assert len(pts) == 8 * 6                      # side + base cap per segment
    zs = [p[2] for p in pts]
    assert max(zs) == pytest.approx(0.02)
    assert min(zs) == pytest.approx(-0.02)

def test_mesh_triangles_skip_out_of_range_indices():
    verts = [0, 0, 0, 1, 0, 0, 0, 1, 0]
    assert len(g.mesh_triangles(verts, [0, 1, 2])) == 3
    assert g.mesh_triangles(verts, [0, 1, 99]) == [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)]
    assert g.mesh_triangles([0, 0, 0], [0, 1, 2]) == []

def test_quaternion_maps_z_onto_the_normal():
    for n in ([0, 0, 1], [1, 0, 0], [0, 1, 0], [1, 1, 1]):
        x, y, z, w = g.quaternion_from_z_to(n)
        assert math.isclose(x * x + y * y + z * z + w * w, 1.0, abs_tol=1e-9)
    assert g.quaternion_from_z_to([0, 0, 1]) == (0.0, 0.0, 0.0, 1.0)
    assert g.quaternion_from_z_to([0, 0, 0]) == (0.0, 0.0, 0.0, 1.0)
