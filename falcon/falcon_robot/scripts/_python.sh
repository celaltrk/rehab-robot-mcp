# Resolve an interpreter that can actually run the server, and say so plainly if
# none can. Sourced by every launcher.
#
# MCP clients spawn servers with a sanitised environment, so this cannot rely on
# an activated virtualenv: the interpreter has to be named outright.
resolve_python() {
    local root="$1" candidate
    # `$root/../.venv` is the documented layout: robot-control, this robot and
    # one shared venv side by side. Without it a client spawning us with a
    # sanitised PATH finds only the system python, which has neither package.
    for candidate in "$ROBOT_PYTHON" "$root/.venv/bin/python" \
                     "$root/../.venv/bin/python" "$(command -v python3)"; do
        [ -n "$candidate" ] && [ -x "$candidate" ] || continue
        if "$candidate" -c "import robot_control, falcon_robot" 2>/dev/null; then
            echo "$candidate"; return 0
        fi
    done
    cat >&2 <<MSG
No interpreter found with both robot-control and falcon importable.

  Install them into one environment, robot-control first:
      pip install -e /path/to/robot-control
      pip install -e /path/to/falcon

  Then either put that environment at $root/.venv or $root/../.venv, or point
  ROBOT_PYTHON at its python.
MSG
    return 1
}
