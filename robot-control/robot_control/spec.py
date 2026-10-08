"""Declarative robot capability specification."""
from __future__ import annotations
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal
import yaml

ParamType = Literal["float", "int", "bool", "string"]


@dataclass(frozen=True)
class ParamDef:
    name: str
    type: ParamType
    description: str
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    max_delta: float | None = None     # max gap between command and actual state
    default: float | int | bool | str = 0
    choices: tuple[str, ...] | None = None   # type == "string": the allowed values

    def json_type(self) -> str:
        return {"float": "number", "int": "integer",
                "bool": "boolean", "string": "string"}[self.type]


@dataclass(frozen=True)
class ActionDef:
    name: str
    description: str
    params: tuple[ParamDef, ...] = ()
    input_schema: dict | None = None


@dataclass(frozen=True)
class RobotSpec:
    name: str
    description: str
    parameters: tuple[ParamDef, ...] = ()
    actions: tuple[ActionDef, ...] = ()
    screen_axes: tuple[int, int] = (0, 1)   # the two axes a flat screen shows, as (x, y, z) indices
    requires_hardware: bool = False          # needs its device present to start; the robot decides
    # A ceiling the device is rated for but no single parameter expresses — a
    # total force or effort, say, that several parameters can sum into. The core
    # never enforces it: it is here so a robot declares its limits in one place,
    # its spec, rather than repeating them as constants in its own code.
    max_force: float | None = None

    def param(self, name: str) -> ParamDef | None:
        return next((p for p in self.parameters if p.name == name), None)

    def action(self, name: str) -> ActionDef | None:
        return next((a for a in self.actions if a.name == name), None)

    def defaults(self) -> dict[str, float | int | bool]:
        return {p.name: p.default for p in self.parameters}


def _build(cls, data: dict, where: str):
    """Construct one spec dataclass from a mapping, rejecting unknown keys."""
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}; allowed: {sorted(known)}")
    data = dict(data)
    for key in ("choices", "screen_axes"):
        if isinstance(data.get(key), list):
            data[key] = tuple(data[key])
    return cls(**data)


def load_spec(path: Path | str) -> RobotSpec:
    """Build a RobotSpec from a YAML declaration."""
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    params = tuple(_build(ParamDef, p, f"{path.name}: parameter {p.get('name')!r}")
                   for p in data.pop("parameters", []))
    actions = []
    for a in data.pop("actions", []):
        a = dict(a)
        a["params"] = tuple(_build(ParamDef, q, f"{path.name}: action param {q.get('name')!r}")
                            for q in a.get("params", []))
        actions.append(_build(ActionDef, a, f"{path.name}: action {a.get('name')!r}"))
    return _build(RobotSpec, {**data, "parameters": params, "actions": tuple(actions)},
                  f"{path.name}: spec")
