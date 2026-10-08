#!/usr/bin/env bash
# Visualise the haptic scene in RViz2
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
source "$ROOT/falcon_robot/scripts/_python.sh"
source /opt/ros/jazzy/setup.bash
source "$ROOT/falcon_robot/ros2_ws/install/setup.bash"
VENV="$(resolve_python "$ROOT")" || exit 1

# teardown on ctrl c
TF="" ; VIZ="" ; RVIZ=""
# The MCP server may start this script and later SIGTERM it, so the trap has
# to take RViz down as well; bash does not forward a signal to a foreground
# child, and a surviving rviz2 would hold the screen after the exercise ends.
cleanup() { kill "$TF" "$VIZ" "$RVIZ" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

# RViz needs a world frame to anchor to; device coordinates are world here.
ros2 run tf2_ros static_transform_publisher \
    --x 0 --y 0 --z 0 --frame-id world --child-frame-id falcon > /dev/null 2>&1 &
TF=$!

# venv python, so rclpy and the falcon_ros2 messages both resolve. PYTHONPATH
# adds the repo root so the falcon package resolves.
PYTHONPATH="$ROOT:$PYTHONPATH" "$VENV" -m falcon_robot.exercises.haptic_rendering.viewer &
VIZ=$!

echo ">>> RViz up. Shapes appear once a scene is published; an empty grid just" >&2
echo "    means no scene has been set yet." >&2

rviz2 -d "$HERE/falcon_haptic.rviz" &
RVIZ=$!
wait "$RVIZ" 2>/dev/null || true
