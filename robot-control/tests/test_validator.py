"""Unit tests for the safety validator: bounds, types, rate cap, all-or-nothing."""
from __future__ import annotations
import pytest
from robot_control.spec import ActionDef, ParamDef, RobotSpec
from robot_control.validator import validate

# A small synthetic robot exercising every ParamDef feature.
SPEC = RobotSpec(
    name="testbot",
    description="synthetic robot for validator tests",
    parameters=(
        ParamDef(name="speed", type="float", description="speed", unit="m/s",
                 minimum=0.0, maximum=10.0, step=0.5, max_delta=2.0, default=1.0),
        ParamDef(name="count", type="int", description="repetitions",
                 minimum=1, maximum=20, max_delta=5, default=5),
        ParamDef(name="enabled", type="bool", description="on/off", default=False),
        ParamDef(name="free", type="float", description="unbounded, no rate cap",
                 default=0.0),
    ),
    actions=(ActionDef(name="go", description="go"),),
)


# bounds
def test_accepts_value_inside_bounds():
    r = validate(SPEC, {"speed": 5.0})
    assert r.ok and r.accepted == {"speed": 5.0}


@pytest.mark.parametrize("value", [10.0, 0.0])
def test_accepts_values_exactly_on_the_bound(value):
    assert validate(SPEC, {"speed": value}).ok


def test_rejects_above_maximum():
    r = validate(SPEC, {"speed": 10.5})
    assert not r.ok
    assert "above maximum" in r.rejected["speed"]
    assert r.accepted == {}


def test_rejects_below_minimum():
    r = validate(SPEC, {"speed": -0.1})
    assert not r.ok
    assert "below minimum" in r.rejected["speed"]


def test_unknown_parameter_is_rejected():
    r = validate(SPEC, {"nonexistent": 1.0})
    assert not r.ok
    assert r.rejected["nonexistent"] == "unknown parameter"


def test_parameter_without_bounds_accepts_anything_numeric():
    assert validate(SPEC, {"free": 1e6}).ok


# types
def test_rejects_non_numeric_for_float():
    r = validate(SPEC, {"speed": "fast"})
    assert not r.ok
    assert "expected a number" in r.rejected["speed"]


def test_rejects_bool_passed_as_number():
    # bool subclasses int, so True must not slip through as 1.
    r = validate(SPEC, {"speed": True})
    assert not r.ok
    assert "boolean" in r.rejected["speed"]


def test_rejects_number_passed_as_bool():
    r = validate(SPEC, {"enabled": 1})
    assert not r.ok


def test_accepts_actual_bool():
    r = validate(SPEC, {"enabled": True})
    assert r.ok and r.accepted == {"enabled": True}


def test_int_accepts_integral_float_and_coerces():
    r = validate(SPEC, {"count": 7.0})
    assert r.ok
    assert r.accepted["count"] == 7
    assert isinstance(r.accepted["count"], int)


def test_int_rejects_fractional_value():
    r = validate(SPEC, {"count": 7.5})
    assert not r.ok
    assert "integer" in r.rejected["count"]


def test_int_accepts_value_from_float_input_type():
    # MCP delivers JSON numbers, so 5 and 5.0 must both work.
    assert validate(SPEC, {"count": 5}).ok
    assert validate(SPEC, {"count": 5.0}).ok


# rate cap (max_delta): the gap to actual state, not to the previous command.


def test_rate_cap_is_measured_against_actual_state_not_last_command():
    # the device sits at 1.0 whatever it was last told: commanding 5.0 is a 4.0 gap against a cap of 2.0
    r = validate(SPEC, {"speed": 5.0}, current={"speed": 1.0})
    assert not r.ok
    assert "away from the current" in r.rejected["speed"]


def test_command_close_to_actual_is_accepted():
    r = validate(SPEC, {"speed": 1.5}, current={"speed": 1.0})
    assert r.ok


def test_rate_cap_allows_change_within_max_delta():
    assert validate(SPEC, {"speed": 3.0}, current={"speed": 1.0}).ok


def test_rate_cap_rejects_jump_larger_than_max_delta():
    r = validate(SPEC, {"speed": 9.0}, current={"speed": 1.0})
    assert not r.ok
    assert "exceeds max step" in r.rejected["speed"]


def test_rate_cap_applies_to_decreases_too():
    r = validate(SPEC, {"speed": 1.0}, current={"speed": 9.0})
    assert not r.ok


def test_rate_cap_allows_change_exactly_equal_to_max_delta():
    assert validate(SPEC, {"speed": 3.0}, current={"speed": 1.0}).ok


def test_rate_cap_is_skipped_when_no_current_value_known():
    # First command after startup has no baseline; bounds still apply.
    r = validate(SPEC, {"speed": 9.0}, current={})
    assert r.ok


def test_rate_capped_value_still_subject_to_bounds():
    r = validate(SPEC, {"speed": 11.0}, current={"speed": 10.0})
    assert not r.ok
    assert "above maximum" in r.rejected["speed"]


def test_rate_cap_on_int_parameter():
    r = validate(SPEC, {"count": 20}, current={"count": 1})
    assert not r.ok
    assert "exceeds max step" in r.rejected["count"]


def test_parameter_without_max_delta_permits_any_jump():
    assert validate(SPEC, {"free": 1e6}, current={"free": 0.0}).ok


# all-or-nothing
def test_one_bad_field_rejects_the_whole_command():
    r = validate(SPEC, {"speed": 2.0, "count": 999}, current={"speed": 1.0, "count": 5})
    assert not r.ok
    assert r.accepted == {}, "a valid field was applied alongside an invalid one"
    assert "count" in r.rejected


def test_all_valid_fields_are_accepted_together():
    r = validate(SPEC, {"speed": 2.0, "count": 6}, current={"speed": 1.0, "count": 5})
    assert r.ok
    assert r.accepted == {"speed": 2.0, "count": 6}


def test_multiple_bad_fields_are_all_reported():
    # Every reason at once, not one per round trip.
    r = validate(SPEC, {"speed": 99.0, "count": 99})
    assert not r.ok
    assert set(r.rejected) == {"speed", "count"}


def test_empty_command_is_vacuously_valid():
    r = validate(SPEC, {})
    assert r.ok and r.accepted == {}


# reporting
def test_rejection_reason_names_the_unit():
    r = validate(SPEC, {"speed": 99.0})
    assert "m/s" in r.rejected["speed"]


def test_reasons_joins_all_rejections():
    r = validate(SPEC, {"speed": 99.0, "count": 99})
    text = r.reasons()
    assert "speed" in text and "count" in text


# non-finite values: found by fuzzing the server, since NaN compares false
# against every bound and so passed all of the range checks below it.
def test_nan_is_refused_although_it_breaks_no_bound():
    r = validate(SPEC, {"speed": float("nan")})
    assert not r.ok and "finite" in r.rejected["speed"]


def test_infinities_are_refused():
    for v in (float("inf"), -float("inf")):
        r = validate(SPEC, {"speed": v})
        assert not r.ok


def test_nan_is_refused_for_integers_too():
    r = validate(SPEC, {"count": float("nan")})
    assert not r.ok and "finite" in r.rejected["count"]
