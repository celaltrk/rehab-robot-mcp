"""RViz visualization of the Falcon haptic scene."""
from __future__ import annotations
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import Point
from visualization_msgs.msg import Marker, MarkerArray
from falcon_ros2.msg import DeviceState, HapticScene
from . import geometry
from . import marker_geometry

FRAME = "world"

_STATE_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                        durability=DurabilityPolicy.VOLATILE)
_SCENE_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                        durability=DurabilityPolicy.TRANSIENT_LOCAL)


class HapticViz(Node):
    def __init__(self) -> None:
        super().__init__("haptic_viz")
        self._pos = [0.0, 0.0, 0.0]
        self._objects: list = []
        self._prev_count = 0
        self.create_subscription(DeviceState, "/device_state", self._on_state, _STATE_QOS)
        self.create_subscription(HapticScene, "/haptic_scene", self._on_scene, _SCENE_QOS)
        self._pub = self.create_publisher(MarkerArray, "/haptic_markers", 1)
        self.create_timer(1.0 / 30.0, self._publish)
        self.get_logger().info("haptic_viz up — publishing /haptic_markers (frame '%s')" % FRAME)

    def _on_state(self, msg: DeviceState) -> None:
        self._pos = [msg.x, msg.y, msg.z]

    def _on_scene(self, msg: HapticScene) -> None:
        self._objects = list(msg.objects)

    @staticmethod
    def _as_dict(o) -> dict:
        """A HapticObject as the plain mapping geometry expects."""
        return {"type": o.type, "point": list(o.point), "normal": list(o.normal),
                "size": list(o.size), "radius": o.radius, "height": o.height}

    # contact test (for cursor colour)
    def _touching(self) -> bool:
        return geometry.touching(self._pos, [self._as_dict(o) for o in self._objects])

    # marker publishing
    def _publish(self) -> None:
        arr = MarkerArray()
        mid = 0
        for o in self._objects:
            m = self._object_marker(o, mid)
            if m is not None:
                arr.markers.append(m)
                mid += 1
        arr.markers.append(self._cursor_marker(mid))
        mid += 1
        # Delete markers left over from a previously larger scene.
        for old in range(mid, self._prev_count):
            d = Marker()
            d.header.frame_id = FRAME
            d.ns = "haptic"
            d.id = old
            d.action = Marker.DELETE
            arr.markers.append(d)
        self._prev_count = mid
        self._pub.publish(arr)

    def _base(self, mid: int) -> Marker:
        m = Marker()
        m.header.frame_id = FRAME
        m.header.stamp = self.get_clock().now().to_msg()
        m.ns = "haptic"
        m.id = mid
        m.action = Marker.ADD
        m.pose.orientation.w = 1.0
        return m

    def _object_marker(self, o, mid: int):
        m = self._base(mid)
        p = o.point if len(o.point) == 3 else [0.0, 0.0, 0.0]
        m.pose.position.x, m.pose.position.y, m.pose.position.z = p
        m.color.r, m.color.g, m.color.b, m.color.a = 0.2, 0.5, 1.0, 0.45
        # Warmer tint with increasing friction, so friction is visible.
        fr = getattr(o, "friction", 0.0) or 0.0
        m.color.r = min(1.0, 0.2 + 0.7 * fr)
        if o.type == "sphere":
            m.type = Marker.SPHERE
            m.scale.x = m.scale.y = m.scale.z = 2.0 * o.radius
        elif o.type == "ellipsoid" and len(o.size) == 3:
            m.type = Marker.SPHERE            # RViz sphere scales per-axis
            m.scale.x, m.scale.y, m.scale.z = (2 * o.size[0], 2 * o.size[1], 2 * o.size[2])
        elif o.type == "box" and len(o.size) == 3:
            m.type = Marker.CUBE
            m.scale.x, m.scale.y, m.scale.z = (2 * o.size[0], 2 * o.size[1], 2 * o.size[2])
        elif o.type == "cylinder":
            m.type = Marker.CYLINDER
            m.scale.x = m.scale.y = 2.0 * o.radius
            m.scale.z = o.height
        elif o.type == "cone":
            m.type = Marker.TRIANGLE_LIST
            m.scale.x = m.scale.y = m.scale.z = 1.0
            m.points = _cone_points_ros(o.radius, o.height)
        elif o.type == "mesh":
            m.type = Marker.TRIANGLE_LIST
            m.scale.x = m.scale.y = m.scale.z = 1.0
            m.points = _mesh_points_ros(o.vertices, o.triangles)
            if not m.points:
                return None
        elif o.type == "plane":
            m.type = Marker.CUBE          # a thin slab as a wall
            m.scale.x, m.scale.y, m.scale.z = 0.12, 0.12, 0.002
            _orient_to_normal(m, o.normal)
            m.color.a = 0.3
        else:
            return None                    # spring/viscous: not drawn
        return m

    def _cursor_marker(self, mid: int) -> Marker:
        m = self._base(mid)
        m.type = Marker.SPHERE
        m.pose.position.x, m.pose.position.y, m.pose.position.z = self._pos
        m.scale.x = m.scale.y = m.scale.z = 0.008
        if self._touching():
            m.color.r, m.color.g, m.color.b, m.color.a = 1.0, 0.1, 0.1, 1.0
        else:
            m.color.r, m.color.g, m.color.b, m.color.a = 0.1, 1.0, 0.2, 1.0
        return m


def _cone_points_ros(radius: float, height: float, n: int = 24) -> list:
    return [Point(x=x, y=y, z=z) for x, y, z in marker_geometry.cone_triangles(radius, height, n)]


def _mesh_points_ros(vertices, triangles) -> list:
    return [Point(x=x, y=y, z=z) for x, y, z in marker_geometry.mesh_triangles(vertices, triangles)]


def _orient_to_normal(m: Marker, normal) -> None:
    x, y, z, w = marker_geometry.quaternion_from_z_to(normal)
    m.pose.orientation.x, m.pose.orientation.y = x, y
    m.pose.orientation.z, m.pose.orientation.w = z, w


def main() -> None:
    rclpy.init()
    node = HapticViz()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
