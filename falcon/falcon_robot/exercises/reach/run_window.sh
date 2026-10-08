#!/usr/bin/env bash
# Open the reach window. The node and the MCP server must already be running;
# this only draws and reads.
set -e   # no `set -u`: the ROS setup scripts read unbound variables

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
source "$ROOT/falcon_robot/scripts/_python.sh"
PYTHON="$(resolve_python "$ROOT")" || exit 1
source /opt/ros/jazzy/setup.bash
source "$ROOT/falcon_robot/ros2_ws/install/setup.bash"
export LD_LIBRARY_PATH=/opt/forcedimension/lib/release/lin-x86_64-gcc:$LD_LIBRARY_PATH

cd "$ROOT"
exec "$PYTHON" -m falcon_robot.exercises.reach.window "$@"
