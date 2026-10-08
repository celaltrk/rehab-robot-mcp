"""The exercise runs server-side, so its settings reach the model as tools."""
from __future__ import annotations
from fake_robot import FakeBackend
from falcon_robot.exercises.reach.reach import ReachExercise
from falcon_robot.exercises.reach import SPEC as REACH_SPEC
from robot_control.registry import RobotRegistry
from robot_control.server import RobotServer


def _server():
    reg = RobotRegistry(); reg.add(FakeBackend())
    return RobotServer(reg, exercises={"fake_robot": {"reach": ReachExercise(REACH_SPEC)}})


def test_every_setting_is_exposed_as_a_tool_parameter():
    tool = next(t for t in _server().list_tools() if t.name == "start_reach")
    assert set(tool.input_schema["properties"]) == {p.name for p in REACH_SPEC.parameters}
    assert tool.input_schema["properties"]["radius"]["maximum"] == 0.03


def test_the_three_exercise_tools_exist():
    names = {t.name for t in _server().list_tools()}
    assert {"start_reach", "configure_reach", "stop_exercise"} <= names


def test_settings_outside_their_range_are_rejected():
    rs = _server()
    out = rs.start_exercise("fake_robot", "reach", {"radius": 9.0})
    assert out["status"] == "rejected" and "radius" in out["rejected"]
    assert rs.get_state("fake_robot")["exercise"] == {"running": False}


def test_unknown_exercise_is_an_error():
    assert _server().start_exercise("fake_robot", "nope", {})["status"] == "error"


def test_state_reports_the_running_exercise_and_its_score():
    rs = _server()
    rs.start_exercise("fake_robot", "reach", {"targets": 3, "seed": 1})
    ex = rs.get_state("fake_robot")["exercise"]
    assert ex["running"] and ex["of"] == 3 and ex["hits"] == 0
    rs.stop_exercise()
    assert rs.get_state("fake_robot")["exercise"] == {"running": False}


def test_configure_changes_settings_without_losing_the_score():
    rs = _server()
    rs.start_exercise("fake_robot", "reach", {"targets": 5, "radius": 0.01})
    out = rs.configure_exercise("fake_robot", "reach", {"radius": 0.02})
    assert out["status"] == "applied"
    assert rs.get_state("fake_robot")["exercise"]["parameters"]["radius"] == 0.02


def test_the_view_carries_what_a_window_needs_to_draw():
    ex = ReachExercise(REACH_SPEC)
    ex.configure(REACH_SPEC.defaults())
    ex.reset([0.0, 0.0, 0.0])
    v = ex.view()
    assert {"goal", "goal_radius", "dwell_progress", "score", "score_of"} <= set(v)


# What the window calls on its device link, now that both live together.
def test_the_reach_window_source_satisfies_what_the_window_calls():
    """Skipped without ROS on the path, as the SDK need not be here."""
    import importlib
    import pytest
    try:
        mod = importlib.import_module("falcon_robot.exercises.reach.window")
    except ImportError as exc:
        pytest.skip(f"needs the ROS 2 workspace on the path: {exc}")
    src = mod.SOURCE
    assert isinstance(src.keyboard, bool), "must say whether keys drive the hand"
    assert isinstance(src.label, str) and src.label
    for method in ("poll", "send_input", "close", "where"):
        assert callable(getattr(src, method, None)), f"source needs {method}()"


# axes: a robot declares which plane a flat screen shows, and exercises obey it
def test_each_robot_declares_a_screen_plane():
    from robot_control.registry import robot_names, robot_spec
    for name in robot_names():
        axes = robot_spec(name).screen_axes
        assert len(axes) == 2 and len(set(axes)) == 2
        assert all(0 <= a <= 2 for a in axes), f"{name}: axes index (x, y, z)"

    # Force Dimension frame: +x toward the user, +y right, +z up.
    falcon = robot_spec("falcon")
    assert falcon.screen_axes == (1, 2)
    assert "left/right" in falcon.param("target_y").description
    assert "up/down" in falcon.param("target_z").description


def test_reach_places_dots_in_the_robots_plane():
    ex = ReachExercise(REACH_SPEC)
    ex.configure(REACH_SPEC.defaults())
    ex.reset([0.01, 0.02, 0.03], axes=(1, 2))
    assert ex.view()["axes"] == [1, 2]
    point = ex._spatial(ex.view()["goal"])
    assert point[0] == 0.01, "the axis not on screen keeps the hand's value"

def test_advisory_step_is_not_emitted_as_a_hard_constraint():
    # step as multipleOf made clients reject legal values: 0.035 % 0.005 != 0.
    rs = _server()
    spread = next(t for t in rs.list_tools()
                  if t.name == "start_reach").input_schema["properties"]["spread"]
    assert "multipleOf" not in spread
    assert "steps of about" in spread["description"]
    assert rs.start_exercise("fake_robot", "reach", {"spread": 0.035})["status"] == "started"
