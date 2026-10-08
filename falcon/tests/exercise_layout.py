"""Where a robot's exercises live, for tests that must find them on disk.

One place, so the layout is asserted once rather than spelled out in every
test — and so moving an exercise is a directory move, not a search-and-replace.
"""
from pathlib import Path
from robot_control.mcp_server import _exercise_modules
from robot_control.registry import robot_entry

__all__ = ["exercise_modules", "spec_path"]


def exercise_modules(robot: str) -> list[str]:
    return _exercise_modules(robot_entry(robot).path() / "exercises")


def spec_path(robot: str, name: str) -> Path:
    """The yaml declaring one exercise, wherever the robot keeps it."""
    root = robot_entry(robot).path() / "exercises"
    hits = sorted(root.glob(f"*/{name}.yaml")) + sorted(root.glob(f"{name}.yaml"))
    assert hits, f"{robot} ships no spec for exercise {name!r} under {root}"
    return hits[0]
