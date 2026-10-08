"""Registry of live backends; robots advertise themselves via the robot_control.robots entry points."""
from __future__ import annotations
import importlib
import logging
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from .backend import RobotBackend
from .spec import RobotSpec, load_spec

log = logging.getLogger("robot_control.registry")

ENTRY_POINT_GROUP = "robot_control.robots"


@dataclass(frozen=True)
class RobotEntry:
    """One advertised robot, before any of its code is imported."""
    name: str
    package: str

    def module(self, kind: str = "backend") -> str:
        """Dotted path to one of the robot's conventional modules."""
        return f"{self.package}.{kind}"

    def path(self) -> Path:
        """Where the robot's package sits, for the data files beside its code."""
        module = importlib.import_module(self.package)
        if getattr(module, "__file__", None):
            return Path(module.__file__).resolve().parent
        for entry in getattr(module, "__path__", []):
            return Path(entry).resolve()
        raise RuntimeError(f"cannot locate {self.package} on disk")

    def spec_path(self) -> Path:
        return self.path() / "spec.yaml"

    def requires_hardware(self) -> bool:
        """Declared by the robot itself, in its own spec."""
        return bool(load_spec(self.spec_path()).requires_hardware)


def known_robots() -> tuple[RobotEntry, ...]:
    """Every robot advertised by an installed distribution, importing none of their code."""
    out, seen = [], set()
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        if ep.name in seen:
            raise ValueError(f"two distributions advertise a robot named {ep.name!r}")
        seen.add(ep.name)
        out.append(RobotEntry(name=ep.name, package=ep.value))
    return tuple(out)


def robot_names() -> list[str]:
    return [e.name for e in known_robots()]


def default_robot() -> str:
    """The robot used when none is named: one needing no hardware, if there is one."""
    entries = known_robots()
    if not entries:
        raise RuntimeError(
            f"no robots installed. Install a package advertising the "
            f"{ENTRY_POINT_GROUP!r} entry-point group, e.g. `pip install -e ../robots`.")
    for e in entries:
        try:
            if not e.requires_hardware():
                return e.name
        except Exception:            # a robot whose spec will not load is not a default
            continue
    return entries[0].name


def robot_entry(name: str) -> RobotEntry:
    for e in known_robots():
        if e.name == name:
            return e
    raise KeyError(f"unknown robot {name!r}; installed: {', '.join(robot_names()) or 'none'}")


def robot_spec(name: str) -> RobotSpec:
    """A robot's capability spec, read from its YAML without importing its backend."""
    return load_spec(robot_entry(name).spec_path())


class RobotRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, RobotBackend] = {}

    def add(self, backend: RobotBackend) -> None:
        name = backend.spec.name
        if name in self._backends:
            raise ValueError(f"robot {name!r} already registered")
        self._backends[name] = backend
        log.info("registered robot %r (backend %s)", name, type(backend).__name__)

    def get(self, name: str) -> RobotBackend:
        if name not in self._backends:
            raise KeyError(f"unknown robot {name!r}")
        return self._backends[name]

    def names(self) -> list[str]:
        return list(self._backends)

    def all(self) -> list[RobotBackend]:
        return list(self._backends.values())

    def __len__(self) -> int:
        return len(self._backends)
