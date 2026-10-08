#!/usr/bin/env bash
# Launch the MCP server with the Falcon backend. This is what MCP clients are
# pointed at: it sources ROS itself, because a client will not pass one.
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$(dirname "${BASH_SOURCE[0]}")/_python.sh"
PYTHON="$(resolve_python "$ROOT")" || exit 1
source /opt/ros/jazzy/setup.bash
source "$ROOT/falcon_robot/ros2_ws/install/setup.bash"
export ForceDimensionSDK_DIR=/opt/forcedimension
export LD_LIBRARY_PATH=/opt/forcedimension/lib/release/lin-x86_64-gcc:$LD_LIBRARY_PATH
cd "$ROOT"
exec "$PYTHON" -m robot_control.mcp_server --backend falcon "$@"
