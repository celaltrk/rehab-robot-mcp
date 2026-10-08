"""Server-level safety: e-stop latching, refusal, recovery, audit trail."""
from __future__ import annotations
import json
import pytest
from fake_robot import FakeBackend
from robot_control.audit import AuditLog
from robot_control.exercise import Exercise, ExerciseSpec
from robot_control.registry import RobotRegistry
from robot_control.spec import ParamDef
from robot_control.server import RobotServer

ROBOT = "fake_robot"


@pytest.fixture()
def server(tmp_path):
    registry = RobotRegistry()
    registry.add(FakeBackend())
    return RobotServer(registry, audit=AuditLog(tmp_path / "audit.jsonl"))


def _audit_lines(server) -> list[dict]:
    path = server.audit.path
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# e-stop latching
def test_e_stop_sets_estopped_state(server):
    out = server.emergency_stop(ROBOT)
    assert out["status"] == "estopped"
    assert server.get_state(ROBOT)["status"]["estopped"] is True


def test_set_parameters_is_refused_while_estopped(server):
    server.emergency_stop(ROBOT)
    out = server.set_parameters(ROBOT, {"step_length": 0.42})
    assert out["status"] == "error"
    assert "e-stop" in out["message"].lower()


def test_refusal_while_estopped_does_not_change_state(server):
    before = dict(server.get_state(ROBOT)["parameters"])
    server.emergency_stop(ROBOT)
    server.set_parameters(ROBOT, {"step_length": 0.42})
    after = server.get_state(ROBOT)["parameters"]
    assert after["step_length"] == before["step_length"]


def test_e_stop_is_idempotent(server):
    server.emergency_stop(ROBOT)
    out = server.emergency_stop(ROBOT)
    assert out["status"] == "estopped"
    assert server.get_state(ROBOT)["status"]["estopped"] is True


def test_e_stop_succeeds_even_from_a_running_state(server):
    server.do_action(ROBOT, "start", {})
    assert server.get_state(ROBOT)["telemetry"]["walking"] is True
    server.emergency_stop(ROBOT)
    assert server.get_state(ROBOT)["telemetry"]["walking"] is False


# recovery
def test_resume_clears_the_latch(server):
    server.emergency_stop(ROBOT)
    out = server.resume(ROBOT)
    assert out["status"] == "resumed"
    assert server.get_state(ROBOT)["status"]["estopped"] is False


def test_resume_leaves_device_passive(server):
    # Recovery must not restart motion by itself.
    server.do_action(ROBOT, "start", {})
    server.emergency_stop(ROBOT)
    server.resume(ROBOT)
    assert server.get_state(ROBOT)["telemetry"]["walking"] is False


def test_parameters_apply_again_after_resume(server):
    server.emergency_stop(ROBOT)
    server.resume(ROBOT)
    out = server.set_parameters(ROBOT, {"step_length": 0.42})
    assert out["status"] == "applied"


# validator wiring at server level
def test_server_rejects_out_of_range_and_applies_nothing(server):
    before = dict(server.get_state(ROBOT)["parameters"])
    out = server.set_parameters(ROBOT, {"step_length": 99.0})
    assert out["status"] == "rejected"
    assert server.get_state(ROBOT)["parameters"] == before


def test_server_rejection_message_is_explanatory(server):
    out = server.set_parameters(ROBOT, {"step_length": 99.0})
    assert "step_length" in out["message"]
    assert "Nothing was applied" in out["message"]


def test_all_or_nothing_at_server_level(server):
    before = dict(server.get_state(ROBOT)["parameters"])
    out = server.set_parameters(ROBOT, {"step_length": 0.42, "cadence": 9999})
    assert out["status"] == "rejected"
    assert server.get_state(ROBOT)["parameters"] == before


# audit trail
def test_applied_command_is_audited(server):
    server.set_parameters(ROBOT, {"step_length": 0.42})
    entries = _audit_lines(server)
    assert any(e["event"] == "set_parameters" and e["verdict"] == "applied" for e in entries)


def test_rejected_command_is_audited_with_reason(server):
    server.set_parameters(ROBOT, {"step_length": 99.0})
    entries = _audit_lines(server)
    rejected = [e for e in entries if e.get("verdict") == "rejected"]
    assert rejected, "a rejected command left no audit entry"
    assert "step_length" in rejected[-1]["rejected"]


def test_e_stop_is_audited(server):
    server.emergency_stop(ROBOT)
    assert any(e["event"] == "e_stop" for e in _audit_lines(server))


def test_every_command_produces_exactly_one_audit_entry(server):
    server.set_parameters(ROBOT, {"step_length": 0.42})   # applied
    server.set_parameters(ROBOT, {"step_length": 99.0})   # rejected
    server.emergency_stop(ROBOT)                          # estop
    server.set_parameters(ROBOT, {"step_length": 0.43})   # refused (latched)
    server.resume(ROBOT)                                  # resumed
    assert len(_audit_lines(server)) == 5


# rate cap is judged against actual state
class _DriftingBackend(FakeBackend):
    """Measured value sits away from the commanded one, as on a real device."""

    measured = 0.10   # spec default is 0.40

    def actual_parameters(self) -> dict:
        params = dict(self.get_state()["parameters"])
        params["step_length"] = self.measured
        return params


def _drifting_server(tmp_path) -> RobotServer:
    registry = RobotRegistry()
    registry.add(_DriftingBackend())
    return RobotServer(registry, audit=AuditLog(tmp_path / "audit.jsonl"))


def test_default_actual_parameters_are_the_commanded_values(server):
    backend = server.registry.get(ROBOT)
    assert backend.actual_parameters() == backend.get_state()["parameters"]


def test_cap_is_judged_against_measured_value_not_last_command(tmp_path):
    # commanded 0.40, measured 0.10, cap 0.10: re-commanding 0.40 is a 0.30 gap and must be refused
    srv = _drifting_server(tmp_path)
    out = srv.set_parameters(ROBOT, {"step_length": 0.40})
    assert out["status"] == "rejected"
    assert "away from the current" in out["rejected"]["step_length"]


def test_command_near_the_measured_value_is_accepted(tmp_path):
    srv = _drifting_server(tmp_path)
    assert srv.set_parameters(ROBOT, {"step_length": 0.15})["status"] == "applied"


def test_get_state_still_reports_commanded_values(tmp_path):
    srv = _drifting_server(tmp_path)
    srv.set_parameters(ROBOT, {"step_length": 0.15})
    assert srv.get_state(ROBOT)["parameters"]["step_length"] == 0.15


# tool surface
def test_get_state_is_exposed_as_a_tool(server):
    # Resources are application-controlled, so the loop needs a tool.
    assert "get_state" in {t.name for t in server.list_tools()}


def test_single_robot_tool_names_are_unprefixed(server):
    names = {t.name for t in server.list_tools()}
    assert "set_parameters" in names
    assert not any(n.startswith(f"{ROBOT}_") for n in names)


# configuring an exercise nobody started
class _CountingExercise(Exercise):
    """Minimal exercise: records how often it was configured."""

    def __init__(self) -> None:
        self.configured = 0

    @property
    def spec(self) -> ExerciseSpec:
        return ExerciseSpec(
            name="drill", description="a drill",
            parameters=(ParamDef(name="reps", type="int", description="how many",
                                 minimum=1, maximum=10, default=3),))

    def configure(self, params: dict) -> None:
        self.configured += 1

    def reset(self, position, axes=(0, 1)) -> None:
        pass

    def update(self, dt: float, position) -> dict:
        return {}

    def status(self) -> dict:
        return {"parameters": {"reps": 3}}


@pytest.fixture()
def drill_server(tmp_path):
    registry = RobotRegistry()
    backend = FakeBackend()
    ex = _CountingExercise()
    registry.add(backend)
    srv = RobotServer(registry,
                      audit=AuditLog(tmp_path / "audit.jsonl"),
                      exercises={backend.spec.name: {"drill": ex}})
    return srv, ex


def test_configuring_an_exercise_that_is_not_running_is_refused(drill_server):
    # It used to succeed and do nothing: only an active exercise is ticked.
    srv, ex = drill_server
    out = srv.configure_exercise(ROBOT, "drill", {"reps": 5})
    assert out["status"] == "error"
    assert ex.configured == 0


def test_that_refusal_names_the_tool_the_model_wanted(drill_server):
    srv, _ = drill_server
    out = srv.configure_exercise(ROBOT, "drill", {"reps": 5})
    assert "start_drill" in out["message"]


def test_configure_works_once_the_exercise_is_running(drill_server):
    srv, ex = drill_server
    assert srv.start_exercise(ROBOT, "drill", {"reps": 4})["status"] == "started"
    assert srv.configure_exercise(ROBOT, "drill", {"reps": 5})["status"] == "applied"
    assert ex.configured == 2          # once by start, once by configure


def test_refused_configure_is_audited(drill_server):
    srv, _ = drill_server
    srv.configure_exercise(ROBOT, "drill", {"reps": 5})
    assert [e["verdict"] for e in _audit_lines(srv)] == ["refused"]
