"""Safety clamping of AI-authored haptic scenes: this exercise's own object vocabulary."""
from __future__ import annotations
import pytest
from falcon_robot.exercises.haptic_rendering import scene


def test_unknown_type_is_dropped():
    assert scene.sanitise_object({"type": "black_hole"}) is None
    assert scene.sanitise_object("not a dict") is None


@pytest.mark.parametrize("field,asked,ceiling", [
    ("stiffness", 99_999.0, scene.K_MAX),
    ("radius", 5.0, scene.DIM_MAX),
    ("vibration_amp", 500.0, scene.FORCE_CLAMP_N),
    ("friction", 12.0, 1.0),
])
def test_solid_fields_are_clamped_to_their_ceiling(field, asked, ceiling):
    out = scene.sanitise_object({"type": "sphere", field: asked})
    assert out[field] == ceiling


def test_viscous_damping_is_clamped():
    assert scene.sanitise_object({"type": "viscous", "damping": 1e6})["damping"] == scene.D_MAX


def test_object_anchor_is_pulled_into_the_workspace():
    out = scene.sanitise_object({"type": "sphere", "point": [10.0, -10.0, 0.0]})
    assert out["point"] == [scene.POINT_MAX, -scene.POINT_MAX, 0.0]


def test_a_malformed_point_falls_back_instead_of_raising():
    assert scene.sanitise_object({"type": "sphere", "point": "left"})["point"] == [0.0, 0.0, 0.0]


# meshes are the widest input the model can author, so they get the most checks
def test_mesh_with_an_out_of_range_triangle_index_is_rejected():
    bad = {"type": "mesh", "vertices": [0, 0, 0, 1, 0, 0, 0, 1, 0], "triangles": [0, 1, 7]}
    assert scene.sanitise_object(bad) is None


def test_mesh_needs_whole_triangles_and_vertices():
    assert scene.sanitise_object({"type": "mesh", "vertices": [0, 0, 0], "triangles": [0]}) is None


def test_mesh_vertices_are_clamped_and_triangles_capped():
    nv = scene.MAX_TRIS + 50
    obj = {"type": "mesh",
           "vertices": [99.0, -99.0, 0.0] * 3,
           "triangles": [0, 1, 2] * nv}
    out = scene.sanitise_object(obj)
    assert max(out["vertices"]) == scene.VERT_MAX
    assert min(out["vertices"]) == -scene.VERT_MAX
    assert len(out["triangles"]) == scene.MAX_TRIS * 3


# a buzz cue is a force, not an object: it must survive clamping with no geometry
def test_vibration_cue_needs_no_geometry():
    out = scene.sanitise_object({"type": "vibration", "vibration_freq": 180,
                                 "vibration_amp": 0.8})
    assert out["vibration_freq"] == 180.0
    assert out["vibration_amp"] == 0.8
    assert "radius" not in out and "stiffness" not in out


def test_vibration_amplitude_is_capped_low():
    # A cue should never be strong enough to move the hand on its own.
    out = scene.sanitise_object({"type": "vibration", "vibration_freq": 9999,
                                 "vibration_amp": 99})
    assert out["vibration_amp"] == 2.0
    assert out["vibration_freq"] == 500.0


def test_scene_ceilings_track_the_spec_rather_than_repeating_it():
    """K_MAX and D_MAX were hardcoded copies of what spec.yaml already declared."""
    from falcon_robot.spec import FALCON_SPEC
    assert scene.K_MAX == FALCON_SPEC.param("stiffness").maximum
    assert scene.D_MAX == FALCON_SPEC.param("damping").maximum
    assert scene.FORCE_CLAMP_N == FALCON_SPEC.max_force
