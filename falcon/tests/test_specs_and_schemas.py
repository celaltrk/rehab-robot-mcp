"""Spec -> tool-schema generation, and sanity of the shipped robot specs."""
from __future__ import annotations
from dataclasses import replace
import pytest
from fake_robot import FAKE_SPEC, FakeBackend
from robot_control.registry import RobotRegistry, robot_names, robot_spec

from robot_control.server import RobotServer

# Every installed robot, discovered through the entry points.
ALL_IDS = robot_names()
ALL_SPECS = [robot_spec(n) for n in ALL_IDS]
FALCON_SPEC = robot_spec("falcon")


@pytest.fixture()
def server():
    registry = RobotRegistry()
    registry.add(FakeBackend())
    return RobotServer(registry)


def _params_schema(server):
    tool = next(t for t in server.list_tools() if t.name == "set_parameters")
    return tool.input_schema


# schema generation is faithful to the spec
def test_bounds_propagate_into_the_tool_schema(server):
    # The model sees the same limits the validator enforces.
    props = _params_schema(server)["properties"]
    for pdef in FAKE_SPEC.parameters:
        assert props[pdef.name]["minimum"] == pdef.minimum
        assert props[pdef.name]["maximum"] == pdef.maximum


def test_schema_forbids_unknown_properties(server):
    assert _params_schema(server)["additionalProperties"] is False


def test_schema_exposes_units_in_descriptions(server):
    props = _params_schema(server)["properties"]
    step = props["step_length"]["description"]
    assert "m" in step


def test_step_is_advisory_not_a_hard_constraint(server):
    # step as multipleOf made clients reject legal values: 0.035 % 0.005 != 0.
    props = _params_schema(server)["properties"]
    for pdef in FAKE_SPEC.parameters:
        if pdef.step is not None:
            assert "multipleOf" not in props[pdef.name]
            assert "steps of about" in props[pdef.name]["description"]


def test_every_spec_parameter_appears_in_the_schema(server):
    props = _params_schema(server)["properties"]
    assert set(props) == {p.name for p in FAKE_SPEC.parameters}


def test_safety_tools_are_always_generated(server):
    # Added by the server, not declared per robot.
    names = {t.name for t in server.list_tools()}
    assert {"e_stop", "resume", "get_state"} <= names


def test_declared_actions_become_tools(server):
    names = {t.name for t in server.list_tools()}
    assert {"start", "stop"} <= names


def test_privileged_tools_take_no_arguments(server):
    # e_stop must never be rejectable for having bad arguments.
    for name in ("e_stop", "resume", "get_state"):
        tool = next(t for t in server.list_tools() if t.name == name)
        assert tool.input_schema["properties"] == {}


# multi-robot namespacing
def test_tools_are_namespaced_when_several_robots_are_registered():
    registry = RobotRegistry()
    registry.add(FakeBackend())
    registry.add(FakeBackend(replace(FAKE_SPEC, name="second_bot")))

    names = {t.name for t in RobotServer(registry).list_tools()}
    assert "fake_robot_set_parameters" in names
    assert "second_bot_set_parameters" in names
    assert "set_parameters" not in names


# sanity of the shipped specs
@pytest.mark.parametrize("spec", ALL_SPECS, ids=ALL_IDS)
def test_bounds_are_ordered(spec):
    for p in spec.parameters:
        if p.minimum is not None and p.maximum is not None:
            assert p.minimum < p.maximum, f"{spec.name}.{p.name} has min >= max"


@pytest.mark.parametrize("spec", ALL_SPECS, ids=ALL_IDS)
def test_defaults_lie_inside_bounds(spec):
    for p in spec.parameters:
        if p.minimum is not None:
            assert p.default >= p.minimum, f"{spec.name}.{p.name} default below minimum"
        if p.maximum is not None:
            assert p.default <= p.maximum, f"{spec.name}.{p.name} default above maximum"


@pytest.mark.parametrize("spec", ALL_SPECS, ids=ALL_IDS)
def test_rate_caps_are_positive_and_reachable(spec):
    # Zero would freeze the parameter; wider than the range would never bind.
    for p in spec.parameters:
        if p.max_delta is None:
            continue
        assert p.max_delta > 0, f"{spec.name}.{p.name} has a non-positive max_delta"
        if p.minimum is not None and p.maximum is not None:
            span = p.maximum - p.minimum
            assert p.max_delta <= span, (
                f"{spec.name}.{p.name} max_delta {p.max_delta} exceeds its "
                f"full range {span} — the rate cap can never trigger"
            )


@pytest.mark.parametrize("spec", ALL_SPECS, ids=ALL_IDS)
def test_parameter_names_are_unique(spec):
    names = [p.name for p in spec.parameters]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("spec", ALL_SPECS, ids=ALL_IDS)
def test_actions_do_not_collide_with_reserved_tool_names(spec):
    reserved = {"e_stop", "resume", "get_state", "set_parameters"}
    for action in spec.actions:
        assert action.name not in reserved, (
            f"{spec.name} declares an action named {action.name!r}, "
            f"which would shadow a privileged tool"
        )


# Rendering is an exercise, not a standalone action.
def test_no_robot_still_declares_rendering_as_an_action():
    """It moved to <name>/exercises/haptic_rendering/, so it stops and e-stops."""
    for spec in ALL_SPECS:
        names = {a.name for a in spec.actions}
        assert not names & {"set_haptic_scene", "clear_haptic_scene"}, \
            f"{spec.name} still advertises rendering as an action"


@pytest.mark.parametrize("robot", ALL_IDS)
def test_each_robot_ships_a_render_exercise_with_a_structured_schema(robot):
    from robot_control.exercise import load_exercise_spec
    from exercise_layout import spec_path
    # Ask the registry: the module is named after the distribution, not the directory.
    spec = load_exercise_spec(spec_path(robot, "render"))
    assert spec.extra_schema is not None
    assert spec.extra_schema["required"] == ["objects"]


def test_the_render_schema_documents_the_centre_convention():
    # Ambiguity here previously put cylinders half a height off.
    from robot_control.exercise import load_exercise_spec
    from exercise_layout import spec_path
    spec = load_exercise_spec(spec_path("falcon", "render"))
    desc = spec.extra_schema["properties"]["objects"]["items"]["properties"]["point"]["description"]
    assert "CENTRE" in desc.upper()


# prompt / tool wording loaded from YAML
def test_load_prompts_ignores_non_prompt_yaml():
    from robot_control.mcp_server import PROMPTS_DIR, load_prompts

    prompts = load_prompts()
    assert "operate_robot" in prompts
    # The directory also holds tool_descriptions.yaml, which is not a prompt.
    assert len(list(PROMPTS_DIR.glob("*.yaml"))) > len(prompts)


def test_tool_descriptions_exist_for_every_server_added_tool():
    from robot_control.server import TOOL_TEXT

    registry = RobotRegistry()
    registry.add(FakeBackend())
    for tool in RobotServer(registry).list_tools():
        assert tool.description.strip(), f"{tool.name} has no description"
        assert "{" not in tool.description, f"{tool.name} has an unrendered placeholder"
    for key in ("set_parameters", "get_state", "e_stop", "resume"):
        assert key in TOOL_TEXT


def test_load_spec_rejects_an_unknown_key(tmp_path):
    from robot_control.spec import load_spec

    bad = tmp_path / "broken.yaml"
    bad.write_text("name: broken\ndescription: d\n"
                   "parameters:\n  - name: gain\n    type: float\n"
                   "    description: x\n    maximumm: 1.0\n")
    with pytest.raises(ValueError, match="maximumm"):
        load_spec(bad)


# The Falcon's one action. Everything else it does is a setting or an activity;
# this is neither, and until it existed the action path was never exercised on
# a real robot.
def test_release_is_offered_as_an_action():
    from falcon_robot.spec import FALCON_SPEC
    action = FALCON_SPEC.action("release")
    assert action is not None, "the Falcon declares no release action"
    assert action.params == (), "release takes no arguments"


def test_release_fills_the_gap_the_rate_cap_leaves():
    """Stiffness cannot fall to zero in one set_parameters call; this can."""
    from falcon_robot.spec import FALCON_SPEC
    stiffness = FALCON_SPEC.param("stiffness")
    assert stiffness.maximum > stiffness.max_delta, (
        "if a single step spanned the whole range, release would be redundant")


def test_release_is_refused_while_estopped():
    """It leaves the device where e_stop does, but must not defeat the latch."""
    backend_mod = pytest.importorskip("falcon_robot.backend",
                                      reason="needs the ROS 2 workspace on the path")
    b = backend_mod.FalconRos2Backend.__new__(backend_mod.FalconRos2Backend)
    import threading
    b._lock, b._estopped = threading.Lock(), True
    assert "error" in b.do_action("release", {})


def test_an_unknown_action_is_still_not_implemented():
    backend_mod = pytest.importorskip("falcon_robot.backend",
                                      reason="needs the ROS 2 workspace on the path")
    b = backend_mod.FalconRos2Backend.__new__(backend_mod.FalconRos2Backend)
    with pytest.raises(NotImplementedError):
        b.do_action("fly", {})
