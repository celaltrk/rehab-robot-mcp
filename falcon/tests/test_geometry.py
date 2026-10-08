"""Shared visualisation geometry, including the centring convention."""
from __future__ import annotations
import pytest
from falcon_robot.exercises.haptic_rendering import geometry as g

CENTRED = [
    ({"type": "sphere", "point": [0, 0, 0], "radius": 0.02}, [0.019, 0, 0], [0.021, 0, 0]),
    ({"type": "box", "point": [0, 0, 0], "size": [0.01, 0.01, 0.01]}, [0.009, 0, 0], [0.011, 0, 0]),
    ({"type": "ellipsoid", "point": [0, 0, 0], "size": [0.01, 0.02, 0.03]},
     [0.009, 0, 0], [0, 0.021, 0]),
]


@pytest.mark.parametrize("obj,inside,outside", CENTRED)
def test_solids_are_centred_on_point(obj, inside, outside):
    assert g.contains(inside, obj)
    assert not g.contains(outside, obj)


# the cylinder/cone convention once put felt objects half a height from drawn ones
def test_cylinder_spans_half_the_height_each_way():
    cyl = {"type": "cylinder", "point": [0, 0, 0], "radius": 0.02, "height": 0.04}
    assert g.contains([0, 0, 0.019], cyl)
    assert g.contains([0, 0, -0.019], cyl)
    assert not g.contains([0, 0, 0.021], cyl)
    assert not g.contains([0, 0, -0.021], cyl)

def test_cone_tapers_from_base_to_apex():
    cone = {"type": "cone", "point": [0, 0, 0], "radius": 0.02, "height": 0.04}
    assert g.contains([0.019, 0, -0.02], cone)      # wide at the base
    assert not g.contains([0.019, 0, 0.019], cone)  # narrow at the apex
    assert not g.contains([0, 0, 0.021], cone)      # above the apex

def test_plane_is_one_sided():
    plane = {"type": "plane", "point": [0, 0, 0], "normal": [0, 1, 0]}
    assert g.contains([0, -0.01, 0], plane)
    assert not g.contains([0, 0.01, 0], plane)

def test_touching_scans_the_whole_scene():
    scene = [{"type": "sphere", "point": [0.05, 0, 0], "radius": 0.01},
             {"type": "sphere", "point": [0, 0, 0], "radius": 0.01}]
    assert g.touching([0, 0, 0], scene)
    assert not g.touching([0.2, 0.2, 0.2], scene)

def test_malformed_objects_are_not_contacts():
    assert not g.contains([0, 0, 0], {"type": "sphere", "point": [0, 0]})
    assert not g.contains([0, 0, 0], {"type": "box", "point": [0, 0, 0], "size": [1, 2]})
    assert not g.contains([0, 0, 0], {"type": "unknown", "point": [0, 0, 0]})
