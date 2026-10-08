"""Static checks over the tracked sources, for defects that only surface at runtime."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _tracked_sources() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return [line for line in out.stdout.split() if line]


def _pyflakes() -> list[str]:
    files = _tracked_sources()
    assert files, "no tracked Python sources found"
    out = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         cwd=ROOT, capture_output=True, text=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def test_no_undefined_names():
    """A name used but never bound raises NameError only on the branch that reaches it."""
    bad = [line for line in _pyflakes() if "undefined name" in line]
    assert not bad, "undefined names:\n" + "\n".join(bad)


def test_no_unused_imports():
    bad = [line for line in _pyflakes() if "imported but unused" in line]
    assert not bad, "unused imports:\n" + "\n".join(bad)
