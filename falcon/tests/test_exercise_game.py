"""The 2D reaching exercise: scoring, timing and target placement."""
from __future__ import annotations
import math
import pytest
from falcon_robot.exercises.reach.game import Exercise, Reach


def _play(e: Exercise, dt: float = 0.05) -> None:
    """Hold the dot showing now for one dwell; the goal is captured first, not re-read each frame."""
    goal = list(e.goal)
    for _ in range(int(e.dwell / dt) + 1):
        e.update(dt, goal)


def test_a_held_dot_counts_and_the_next_appears_elsewhere():
    e = Exercise(total=3, seed=1, dwell=0.1, radius=0.01)
    first = list(e.goal)
    _play(e)
    assert e.hits == 1
    assert e.goal != first


def test_the_round_finishes_after_the_last_dot():
    e = Exercise(total=2, seed=2, dwell=0.1)
    _play(e)
    assert e.hits == 1 and not e.done
    _play(e)
    assert e.hits == 2 and e.done


def test_leaving_before_the_dwell_ends_resets_the_hold():
    e = Exercise(total=1, seed=3, dwell=0.5, radius=0.01)
    e.update(0.2, list(e.goal))
    assert e.progress() == pytest.approx(0.4)
    e.update(0.1, [e.goal[0] + 0.05, e.goal[1]])      # drift away
    assert e.progress() == 0.0
    assert e.hits == 0


def test_a_wobble_is_recorded_but_still_counts():
    e = Exercise(total=1, seed=4, dwell=0.2, radius=0.01)
    e.update(0.1, list(e.goal))
    e.update(0.1, [e.goal[0] + 0.05, e.goal[1]])      # off
    for _ in range(3):                                # back on and held
        e.update(0.1, list(e.goal))
    assert e.hits == 1 and e.misses == 1


def test_dots_stay_inside_the_workspace_and_off_the_hand():
    e = Exercise(total=50, seed=5, reach=0.04, radius=0.008)
    for _ in range(50):
        e.spawn([0.0, 0.0])
        assert all(abs(c) <= 0.04 for c in e.goal)
        assert math.dist(e.goal, [0.0, 0.0]) > 0.008 * 4


def test_efficiency_is_one_for_a_straight_reach_and_lower_when_wandering():
    assert Reach(1.0, straight=0.05, travelled=0.05).efficiency == pytest.approx(1.0)
    assert Reach(1.0, straight=0.05, travelled=0.20).efficiency == pytest.approx(0.25)
    assert Reach(1.0, straight=0.05, travelled=0.0).efficiency == 1.0


def test_summary_reports_timing_only_once_there_is_data():
    e = Exercise(total=2, seed=6, dwell=0.1)
    assert e.summary()["mean_reach_s"] is None
    _play(e)
    s = e.summary()
    assert s["hits"] == 1 and s["mean_reach_s"] > 0 and s["of"] == 2


def test_the_same_seed_gives_the_same_dots():
    a, b = Exercise(total=5, seed=42), Exercise(total=5, seed=42)
    assert a.goal == b.goal
    a.spawn([0, 0]); b.spawn([0, 0])
    assert a.goal == b.goal
