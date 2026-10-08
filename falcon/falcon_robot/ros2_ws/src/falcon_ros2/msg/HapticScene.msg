# Written here, not in the original falcon_ros2: a whole scene, replaced atomically.
# A complete haptic scene. Publishing one replaces whatever was rendered before;
# an empty list clears the scene. Published by the robot_control Falcon backend,
# consumed by the haptic node which renders it with CHAI3D.
HapticObject[] objects
