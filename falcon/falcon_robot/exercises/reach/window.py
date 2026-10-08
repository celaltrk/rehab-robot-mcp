#!/usr/bin/env python3
"""2D window for the Falcon's reaching exercise, drawn with tkinter.

Its link to the device is here too. That link was `falcon_robot/view_source.py`,
a module the core resolved by name so its own generic window could attach to any
robot; that window is gone, this window is the only thing that ever imported it,
and both run in the same process. If a second robot ever wants to share a
window, the contract belongs in the core rather than in one robot's package.
"""
from __future__ import annotations
import argparse
import json
import signal
import tkinter as tk
import tkinter.font as tkfont

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from falcon_ros2.msg import DeviceState

from ...spec import FALCON_SPEC

_STATE_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                        durability=DurabilityPolicy.VOLATILE)
_VIEW_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL)


class FalconSource:
    axes = (1, 2)          # device axes this robot puts on a flat screen
    keyboard = False         # the handle moves the cursor, not the arrow keys
    label = "Falcon"

    def __init__(self) -> None:
        self._own = not rclpy.ok()
        if self._own:
            rclpy.init()
        self._node = Node("exercise_window")
        self._node.create_subscription(DeviceState, "/device_state", self._on_state, _STATE_QOS)
        self._node.create_subscription(String, "/exercise_view", self._on_view, _VIEW_QOS)
        self._state: dict | None = None
        self._view: dict = {}
        self._reach = abs(FALCON_SPEC.param("target_x").maximum)

    def where(self) -> str:
        return "/device_state"

    def _on_state(self, msg: DeviceState) -> None:
        self._state = {"pos": [msg.x, msg.y, msg.z], "vel": [msg.d_x, msg.d_y, msg.d_z],
                       "force": [msg.f_actual_x, msg.f_actual_y, msg.f_actual_z],
                       "button": bool(msg.button_pressed)}

    def _on_view(self, msg: String) -> None:
        try:
            self._view = json.loads(msg.data) or {}
        except ValueError:
            self._view = {}

    def send_input(self, key_dir) -> None:
        """Nothing to send: the user's hand is already the input."""

    def poll(self) -> dict | None:
        rclpy.spin_once(self._node, timeout_sec=0.0)
        if self._state is None:
            return None
        return {**self._state, "reach": self._reach, "scene": [],
                # 0 means the backend publishes no exercise geometry at all.
                "view_publishers": self._node.count_publishers("/exercise_view"),
                **self._view}

    def close(self) -> None:
        self._node.destroy_node()
        if self._own:
            rclpy.shutdown()


SOURCE = FalconSource


FPS = 60
PAD = 30
# negative sizes are pixels to Tk; positive means points, which this display scales by four
FONT_BIG, FONT_SMALL = -15, -12
BG, INK, DIM = "#11141a", "#e8ecf2", "#3a4356"
GOAL, GOAL_HIT, HAND, TRAIL = "#ffa53d", "#4ade80", "#63b3ff", "#26304a"


class Window:
    def __init__(self, size: int) -> None:
        self.source = SOURCE()
        self.px = size
        self.keys: set[str] = set()
        self.trail: list = []
        self.state: dict | None = None

        self.root = tk.Tk()
        self.root.title(f"exercise — {self.source.label}")
        self.root.configure(bg=BG)
        self.big = tkfont.Font(font=("TkFixedFont", FONT_BIG))
        self.small = tkfont.Font(font=("TkFixedFont", FONT_SMALL))
        # Rows are as tall as the text actually is, measured, not assumed.
        self.row = max(self.big.metrics("linespace"),
                       self.small.metrics("linespace")) + 4
        self.footer = PAD + 3 * self.row
        self.canvas = tk.Canvas(self.root, width=size + 2 * PAD,
                                height=size + PAD + self.footer,
                                bg=BG, highlightthickness=0)
        self.canvas.pack()
        # Tk ignores SIGTERM until woken, so record it and let the next tick quit properly
        self._terminated = False
        signal.signal(signal.SIGTERM, self._on_sigterm)
        self.root.bind("<KeyPress>", lambda e: self.keys.add(e.keysym))
        self.root.bind("<KeyRelease>", lambda e: self.keys.discard(e.keysym))
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # --- robot link -------------------------------------------------------
    def poll(self) -> dict | None:
        if self.source.keyboard:
            d = [0.0, 0.0, 0.0]
            if "Left" in self.keys:  d[0] -= 1
            if "Right" in self.keys: d[0] += 1
            if "Up" in self.keys:    d[1] += 1
            if "Down" in self.keys:  d[1] -= 1
            self.source.send_input(d)
        return self.source.poll()

    # --- geometry ---------------------------------------------------------
    def reach(self) -> float:
        return (self.state or {}).get("reach", 0.06)

    def flat(self, p3) -> tuple[float, float]:
        """A device-frame point in the two axes this robot puts on screen."""
        a, b = (self.state or {}).get("axes", self.source.axes)
        return p3[a], p3[b]

    def to_px(self, x: float, y: float):
        r = self.reach()
        return (PAD + (x + r) / (2 * r) * self.px, PAD + (r - y) / (2 * r) * self.px)

    def fit(self, text: str, font, room: int) -> str:
        """Trim a string until it fits, so two labels can never run into each other."""
        if font.measure(text) <= room:
            return text
        while text and font.measure(text + "…") > room:
            text = text[:-1]
        return text + "…"

    def scale(self, metres: float) -> float:
        return metres / (2 * self.reach()) * self.px

    # --- drawing ----------------------------------------------------------
    def draw_waiting(self) -> None:
        c = self.canvas
        c.delete("all")
        mid = self.px / 2 + PAD
        c.create_text(mid, self.px / 2, fill=INK, font=self.big,
                      text=self.fit(f"waiting for the {self.source.label} on "
                                    f"{self.source.where()}", self.big, self.px))
        c.create_text(mid, self.px / 2 + self.row, fill=DIM, font=self.small,
                      text=self.fit("start the robot, or connect its MCP server",
                                    self.small, self.px))

    def draw(self) -> None:
        st = self.state
        c = self.canvas
        c.delete("all")
        c.create_rectangle(PAD, PAD, PAD + self.px, PAD + self.px, outline=DIM)
        mid = PAD + self.px / 2
        c.create_line(PAD, mid, PAD + self.px, mid, fill=DIM)
        c.create_line(mid, PAD, mid, PAD + self.px, fill=DIM)

        hand = self.flat(st["pos"])
        if len(self.trail) > 1:
            c.create_line(*[v for p in self.trail for v in self.to_px(*p)],
                          fill=TRAIL, width=2)

        goal = st.get("goal")
        if goal:
            gx, gy = self.to_px(goal[0], goal[1])
            r = max(4.0, self.scale(st.get("goal_radius", 0.008)))
            on = (st.get("dwell_progress") or 0) > 0
            c.create_oval(gx - r, gy - r, gx + r, gy + r,
                          outline=GOAL_HIT if on else GOAL, width=2)
            if on:
                c.create_arc(gx - r - 6, gy - r - 6, gx + r + 6, gy + r + 6, start=90,
                             extent=-360 * st["dwell_progress"], style=tk.ARC,
                             outline=GOAL_HIT, width=3)
            c.create_oval(gx - 2, gy - 2, gx + 2, gy + 2, fill=GOAL, outline="")

        hx, hy = self.to_px(hand[0], hand[1])
        c.create_oval(hx - 7, hy - 7, hx + 7, hy + 7, fill=HAND, outline="")

        f = st.get("force", [0, 0, 0])
        mag = (f[0] ** 2 + f[1] ** 2 + f[2] ** 2) ** 0.5
        # three rows; force sits right on row 1, so row 1's text is trimmed to what is left
        row1 = PAD + self.px + self.row
        force = f"|F| {mag:4.1f} N"
        force_w = self.small.measure(force)
        c.create_text(PAD + self.px, row1, anchor="e", fill=DIM, font=self.small,
                      text=force)

        scored = goal is not None or st.get("score") is not None
        scene = st.get("scene") or []
        if scored:
            status, colour = (f"{st.get('score', 0)}/{st.get('score_of', 0)}"
                              f"   {st.get('elapsed', 0):.1f} s"), INK
            note = ""
        elif scene:
            # renders rather than scores: objects to feel, no goal and no score
            n = len(scene)
            status, colour = f"rendering {n} object{'s' if n != 1 else ''}", INK
            note = ("in contact" if st.get("touching") else "not in contact")
            note += f" · {st.get('contacts', 0)} contact(s) so far"
        elif st.get("view_publishers") == 0:
            status, colour = "robot is not publishing the exercise", GOAL
            note = "its MCP server predates this window — restart the client"
        else:
            status, colour = "no exercise running", DIM
            note = "ask the model to start one"

        c.create_text(PAD, row1, anchor="w", fill=colour, font=self.big,
                      text=self.fit(status, self.big, self.px - force_w - 16))
        if note:
            c.create_text(PAD, row1 + self.row, anchor="w", fill=DIM, font=self.small,
                          text=self.fit(note, self.small, self.px))
        moves = "arrow keys move" if self.source.keyboard else "move the handle"
        c.create_text(PAD, row1 + 2 * self.row, anchor="w", fill=DIM, font=self.small,
                      text=self.fit(f"{moves} · q quits", self.small, self.px))
        if st.get("done"):
            c.create_text(mid, mid, fill=GOAL_HIT, font=self.big, text="done")

    # --- loop -------------------------------------------------------------
    def _on_sigterm(self, *_args) -> None:
        self._terminated = True

    def tick(self) -> None:
        if self._terminated:
            return self.quit()
        if "q" in self.keys or "Escape" in self.keys:
            return self.quit()
        st = self.poll()
        if st is None:
            self.state = None
            self.draw_waiting()
        else:
            self.state = st
            self.trail.append(self.flat(st["pos"]))
            self.trail = self.trail[-240:]
            self.draw()
        self.root.after(int(1000 / FPS), self.tick)

    def run(self) -> None:
        self.tick()
        self.root.mainloop()

    def quit(self) -> None:
        try:
            self.source.close()
        finally:
            self.root.destroy()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", type=int, default=560)
    args = ap.parse_args()
    Window(args.size).run()


if __name__ == "__main__":
    main()
