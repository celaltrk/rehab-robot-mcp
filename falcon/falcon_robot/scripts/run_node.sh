#!/usr/bin/env bash
# Usage:
#   run_node.sh                   # hands-free
#   run_node.sh --require-button  # forces apply only while a grip button is held
#   run_node.sh -- --ros-args -p some_param:=x
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FD_SDK=/opt/forcedimension
REQUIRE_BUTTON=false
EXTRA=()
while [ $# -gt 0 ]; do
    case "$1" in
        --require-button) REQUIRE_BUTTON=true; shift ;;
        --)               shift; EXTRA+=("$@"); break ;;
        *)                EXTRA+=("$1"); shift ;;
    esac
done

# only one process may open the device
NODE_RE='^[^ ]*/lib/falcon_ros2/falcon_haptic_node'
if pgrep -f "$NODE_RE" >/dev/null; then
    echo "error: a falcon_haptic_node is already running (it holds the USB device)." >&2
    echo "       stop it first:  kill \$(pgrep -f '$NODE_RE')" >&2
    exit 1
fi

source /opt/ros/jazzy/setup.bash
source "$ROOT/falcon_robot/ros2_ws/install/setup.bash"
export ForceDimensionSDK_DIR="$FD_SDK"
export LD_LIBRARY_PATH="$FD_SDK/lib/release/lin-x86_64-gcc:$LD_LIBRARY_PATH"

echo ">>> starting falcon_haptic_node (require_button=$REQUIRE_BUTTON)" >&2
cd "$ROOT"
exec ros2 run falcon_ros2 falcon_haptic_node \
    --ros-args -p "require_button:=$REQUIRE_BUTTON" "${EXTRA[@]}"
