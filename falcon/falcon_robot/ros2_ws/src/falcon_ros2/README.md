# falcon_ros2 — partial

This directory holds only the files written for this project. The ROS 2 driver
package it belongs to is **not redistributed here**.

## What is here

| File | |
|---|---|
| `src/haptic_renderer.cpp` | wraps CHAI3D; turns a scene description into forces, using the finger-proxy algorithm so a solid cannot be pushed through by shoving harder |
| `src/haptic_renderer.hpp` | its header; CHAI3D types are forward-declared so the header stays light |
| `msg/HapticObject.msg` | one virtual object — shape, pose, size, stiffness, damping, friction, vibration, and arbitrary mesh geometry |
| `msg/HapticScene.msg` | a whole scene, replaced atomically; an empty one clears it |

## What is not, and why

The driver node is `falcon_ros2` by **Sathvik Kadimisetty** (BIROMED-Lab,
University of Basel), licensed MPL-2.0. This project forked it and modified it:
added a deadman switch and a force ceiling as ROS parameters, added the
`/haptic_scene` subscription that drives the renderer above, extended
`DeviceState` with what the device actually reports, and dropped the UDP bridge
and the separate visualiser node.

Those changes are edits to someone else's source, so the files stay out of this
repository:

```
src/falcon_haptic_node.cpp        the node: USB device, 1 kHz loop, force ceiling, deadman
src/falcon_haptic_node.hpp
src/falcon_haptic_main.cpp        19 lines, untouched from the original
msg/ForceCommand.msg              unchanged from the original
msg/DeviceState.msg               the original plus the observability fields added here
CMakeLists.txt  package.xml       modified from the original
LICENSE.txt                       the original's, MPL-2.0
ForceDimensionSDKConfig.cmake     where libdhd and libdrd live
11-forcedimension.rules           udev rule, so the Falcon opens without root
launch/falcon_only.launch.py      stock launch file
```

The original is not published anywhere this project can reach; it came from the
lab directly. To build the hardware path you need that package, with the four
files above added to it.

## Without it

Nothing else in this repository depends on these files at build time. The Python
side imports, runs and tests on its own — the message bindings are only needed
when talking to real hardware. The full test suite passes with no ROS 2 present.
