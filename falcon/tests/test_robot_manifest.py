"""What this package advertises, and whether each advertised robot is complete.

Robots reach robot-control through the `robot_control.robots` entry-point group
declared in pyproject.toml. Installing this package is what makes them
discoverable, so these tests are the contract between the two repositories.
"""
from __future__ import annotations
import importlib
from pathlib import Path
import pytest
from robot_control.registry import (ENTRY_POINT_GROUP, default_robot, known_robots,
                                    robot_entry, robot_names, robot_spec)
from exercise_layout import exercise_modules, spec_path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {"falcon"}


def test_this_package_advertises_its_robots():
    """If the entry points are missing, nothing downstream can find these robots."""
    assert set(robot_names()) == EXPECTED, (
        f"expected {EXPECTED}; check the {ENTRY_POINT_GROUP!r} entry points in "
        f"pyproject.toml, and that this package is installed")


def test_every_advertised_robot_exists_on_disk():
    for e in known_robots():
        assert e.path().is_dir(), f"{e.name}: {e.package} is not importable to a directory"
        assert e.spec_path().exists(), f"{e.name} has no spec.yaml"


def test_every_robot_ships_the_conventional_modules():
    for e in known_robots():
        for kind in ("backend",):
            assert (e.path() / f"{kind}.py").exists(), f"{e.name} has no {kind}.py"
            assert e.module(kind) == f"{e.package}.{kind}"


def test_every_robot_ships_its_own_exercises():
    for e in known_robots():
        assert (e.path() / "exercises").is_dir(), f"{e.name} has no exercises/"
        assert exercise_modules(e.name), f"{e.name} ships no exercises"
        for name in ("reach", "render"):
            assert spec_path(e.name, name).exists(), f"{e.name} has no spec for {name}"


def test_spec_loads_without_importing_a_backend():
    """Bounds and axes must be readable with no ROS and no device present."""
    for name in robot_names():
        assert robot_spec(name).name == name


def test_each_robot_declares_whether_it_needs_hardware():
    flags = {n: robot_spec(n).requires_hardware for n in robot_names()}
    assert flags == {"falcon": True}


def test_the_default_robot_is_one_that_is_installed():
    """With every robot needing hardware, the default is simply the first."""
    assert default_robot() in robot_names()


def test_declared_name_matches_the_backend_spec():
    """The entry-point name is what --backend takes; the spec name is what MCP reports."""
    for e in known_robots():
        try:
            module = importlib.import_module(e.module("backend"))
        except ImportError as exc:          # the Falcon's SDK need not be present
            if robot_spec(e.name).requires_hardware:
                continue
            raise AssertionError(f"{e.name} backend will not import: {exc}")
        assert module.BACKEND().spec.name == e.name


def test_an_unknown_robot_names_the_installed_ones():
    with pytest.raises(KeyError) as exc:
        robot_entry("nope")
    for name in robot_names():
        assert name in str(exc.value)
