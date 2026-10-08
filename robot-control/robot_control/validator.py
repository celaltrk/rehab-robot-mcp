"""The safety core."""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from .spec import RobotSpec

_EPS = 1e-9

log = logging.getLogger("robot_control.validator")

@dataclass
class ValidationResult:
    ok: bool
    accepted: dict = field(default_factory=dict)
    rejected: dict = field(default_factory=dict)

    def reasons(self) -> str:
        return "; ".join(f"{k}: {v}" for k, v in self.rejected.items())


def _coerce(pdef, value):
    """Type-check a single value; raise ValueError on mismatch."""
    if pdef.type == "bool":
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
        return value
    if isinstance(value, bool):
        raise ValueError(f"expected a {pdef.type}, got boolean")
    if pdef.type == "int":
        if not isinstance(value, (int, float)):
            raise ValueError("expected an integer")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("expected a finite integer")
        if isinstance(value, float) and abs(value - round(value)) > _EPS:
            raise ValueError("expected an integer")
        return int(round(value))
    if pdef.type == "float":
        if not isinstance(value, (int, float)):
            raise ValueError("expected a number")
        # NaN compares false against every bound, so it has to be caught here
        # rather than by the range checks below.
        if not math.isfinite(value):
            raise ValueError("expected a finite number")
        return float(value)
    if pdef.type == "string":
        if not isinstance(value, str):
            raise ValueError("expected a string")
        if pdef.choices and value not in pdef.choices:
            raise ValueError(f"expected one of {list(pdef.choices)}")
        return value
    raise ValueError(f"unknown parameter type {pdef.type!r}")


def validate(spec: RobotSpec, params: dict, current: dict | None = None) -> ValidationResult:
    """Validate proposed updates against the spec and the current state."""
    current = current or {}
    accepted: dict = {}
    rejected: dict = {}

    for name, value in params.items():
        pdef = spec.param(name)
        if pdef is None:
            rejected[name] = "unknown parameter"
            continue
        try:
            v = _coerce(pdef, value)
        except ValueError as e:
            rejected[name] = str(e)
            continue
        if pdef.minimum is not None and v < pdef.minimum - _EPS:
            rejected[name] = f"below minimum {pdef.minimum}{_u(pdef)}"
            continue
        if pdef.maximum is not None and v > pdef.maximum + _EPS:
            rejected[name] = f"above maximum {pdef.maximum}{_u(pdef)}"
            continue
        if pdef.max_delta is not None and name in current:
            try:
                delta = abs(v - current[name])
                if delta > pdef.max_delta + _EPS:
                    rejected[name] = (
                        f"{delta:g}{_u(pdef)} away from the current "
                        f"{current[name]:g}{_u(pdef)} — exceeds max step "
                        f"{pdef.max_delta}{_u(pdef)}"
                    )
                    continue
            except TypeError:
                pass
        accepted[name] = v

    ok = not rejected
    # reject the command entirely if any parameter fails
    if not ok:
        log.debug("validate(%s): REJECT %s", spec.name, rejected)
        return ValidationResult(ok=False, accepted={}, rejected=rejected)
    log.debug("validate(%s): OK %s", spec.name, accepted)
    return ValidationResult(ok=True, accepted=accepted, rejected={})


def _u(pdef) -> str:
    return f" {pdef.unit}" if pdef.unit else ""
