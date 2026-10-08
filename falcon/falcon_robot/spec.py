"""Falcon capability spec, declared in spec.yaml."""
from __future__ import annotations
from pathlib import Path
from robot_control.spec import load_spec

FALCON_SPEC = load_spec(Path(__file__).parent / "spec.yaml")
