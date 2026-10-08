"""Exercises belong to robots, and rendering is one of them.

Split by what each check needs. The declarative half reads specs off disk and
runs anywhere. The behavioural half drives a live server, and since every robot
here needs its device, it uses a fake backend carrying a real robot's exercises —
so the wiring is exercised without the Falcon being present.
"""
from __future__ import annotations
import time
from pathlib import Path
import pytest
from fake_robot import FakeBackend
from robot_control.exercise import load_exercise_spec
from exercise_layout import exercise_modules, spec_path
from robot_control.registry import RobotRegistry, known_robots, robot_names, robot_spec
from robot_control.server import RobotServer

ROOT = Path(__file__).resolve().parent.parent
ALL = robot_names()


def _exercises_of(robot: str) -> dict:
    """A robot's exercises, instantiated without importing its backend."""
    import importlib
    from robot_control.registry import robot_entry
    entry = robot_entry(robot)
    out = {}
    for name in exercise_modules(robot):
        module = importlib.import_module(f"{entry.module('exercises')}.{name}")
        if hasattr(module, "EXERCISE"):
            ex = module.EXERCISE()
            out[ex.spec.name] = ex
    return out


@pytest.fixture()
def server():
    """A live server whose robot carries the Falcon's exercises, on a fake device."""
    backend = FakeBackend()
    reg = RobotRegistry(); reg.add(backend)
    rs = RobotServer(reg, exercises={backend.spec.name: _exercises_of("falcon")})
    rs.start()
    yield rs, backend.spec.name
    rs.stop()


# --- ownership, from the specs alone ---------------------------------------

@pytest.mark.parametrize("robot", ALL)
def test_every_robot_ships_its_own_exercises(robot):
    """A robot that ships none would silently offer no activity at all."""
    declared = set(exercise_modules(robot))
    assert declared, f"{robot} ships no exercises"
    for name in ("reach", "render"):
        assert spec_path(robot, name).exists(), f"{robot} has no spec for {name}"


@pytest.mark.parametrize("robot", ALL)
def test_each_robots_bounds_are_its_own(robot):
    """Shared mechanics, separate limits: the point of per-robot ownership."""
    spec = load_exercise_spec(spec_path(robot, "reach"))
    assert spec.name == "reach"
    assert {p.name for p in spec.parameters} == {
        "targets", "spread", "radius", "dwell", "assist", "seed"}


@pytest.mark.parametrize("robot", ALL)
def test_render_is_offered_with_the_robots_own_object_vocabulary(robot):
    spec = load_exercise_spec(spec_path(robot, "render"))
    types = spec.extra_schema["properties"]["objects"]["items"]["properties"]["type"]["enum"]
    assert "sphere" in types, f"{robot} renders nothing solid"


def test_rendering_is_not_an_action_on_any_robot():
    """It moved to exercises/haptic_rendering/, so it starts, stops and e-stops."""
    for e in known_robots():
        names = {a.name for a in robot_spec(e.name).actions}
        assert not names & {"set_haptic_scene", "clear_haptic_scene"}


# --- behaviour, against a live server --------------------------------------

def test_a_robot_is_offered_only_its_own_exercises(server):
    rs, robot = server
    assert set(rs.exercises_for(robot)) == {"reach", "render"}
    assert rs.exercises_for("no_such_robot") == {}


def test_starting_an_exercise_a_robot_does_not_have_is_refused(server):
    rs, robot = server
    out = rs.start_exercise(robot, "walk_on_water", {})
    assert out["status"] == "error" and "no exercise" in out["message"]


def test_rendering_is_not_in_the_tool_surface(server):
    rs, _ = server
    names = {t.name for t in rs.list_tools()}
    assert "set_haptic_scene" not in names and "clear_haptic_scene" not in names
    assert {"start_render", "configure_render", "stop_exercise"} <= names


def test_render_reaches_the_device_and_stops_with_the_exercise(server):
    rs, robot = server
    out = rs.start_exercise(robot, "render", {"objects": [
        {"type": "sphere", "point": [0.0, 0.0, 0.0], "radius": 0.02}]})
    assert out["status"] == "started"
    time.sleep(0.2)
    assert [o["type"] for o in rs.get_state(robot)["scene"]] == ["sphere"]

    # The scene is the exercise's, so stopping the exercise clears it.
    rs.stop_exercise(robot)
    time.sleep(0.2)
    assert rs.get_state(robot)["scene"] == []


def test_render_reports_progress_through_get_state(server):
    rs, robot = server
    rs.start_exercise(robot, "render", {"objects": [
        {"type": "sphere", "point": [0.0, 0.0, 0.0], "radius": 0.03}]})
    time.sleep(0.2)
    ex = rs.get_state(robot)["exercise"]
    assert ex["running"] and ex["exercise"] == "render" and ex["objects"] == 1


def test_an_estop_takes_the_exercise_down_with_it(server):
    rs, robot = server
    rs.start_exercise(robot, "render", {"objects": [
        {"type": "sphere", "point": [0.0, 0.0, 0.0], "radius": 0.02}]})
    time.sleep(0.2)
    rs.emergency_stop(robot)
    time.sleep(0.3)
    state = rs.get_state(robot)
    assert state["status"]["estopped"]
    assert state["exercise"]["running"] is False
