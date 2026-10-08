"""The device clamp the backend applies to every command, independent of any exercise."""
from __future__ import annotations
import math
import pytest


def _backend():
    """Skipped without ROS on the path, as the SDK need not be here."""
    try:
        import falcon_robot.backend as backend
    except ImportError as exc:
        pytest.skip(f"needs the ROS 2 workspace on the path: {exc}")
    return backend


def test_clamp_mag_preserves_direction():
    out = _backend().clamp_mag([30.0, 40.0, 0.0], 5.0)
    assert math.isclose(math.dist(out, [0, 0, 0]), 5.0)
    assert math.isclose(out[0] / out[1], 30.0 / 40.0)


def test_the_force_ceiling_comes_from_the_spec_not_a_constant():
    """Repeating it in Python is what let it drift from spec.yaml."""
    from falcon_robot.spec import FALCON_SPEC
    assert FALCON_SPEC.max_force == 8.0, "the Falcon's rated total force"
    assert _backend().FORCE_CLAMP_N == FALCON_SPEC.max_force
