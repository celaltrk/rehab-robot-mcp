"""Exercise logic for the 2D reaching game: pure, no UI and no robot."""
from __future__ import annotations
import math
import random
from dataclasses import dataclass, field


@dataclass
class Reach:
    """One completed reach."""
    seconds: float
    straight: float          # shortest distance that would have done it
    travelled: float         # distance the hand actually covered

    @property
    def efficiency(self) -> float:
        return min(1.0, self.straight / self.travelled) if self.travelled > 1e-6 else 1.0


@dataclass
class Exercise:
    """A dot appears, the player moves onto it and holds; then it moves."""
    reach: float = 0.05          # workspace half-extent the dots stay inside
    radius: float = 0.008        # how close counts as on the dot
    dwell: float = 0.3           # seconds to hold before it counts
    total: int = 10              # dots in a round
    seed: int | None = None

    goal: list = field(default_factory=lambda: [0.0, 0.0])
    hits: int = 0
    misses: int = 0              # dots left by drifting off before the dwell ended
    elapsed: float = 0.0
    done: bool = False
    reaches: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._held = 0.0
        self._since_spawn = 0.0
        self._from = [0.0, 0.0]
        self._travelled = 0.0
        self._last: list | None = None
        self._left_early = False
        self.spawn([0.0, 0.0])

    # --- state ------------------------------------------------------------
    def spawn(self, hand) -> None:
        """Place the next dot, never on top of the hand."""
        for _ in range(20):
            g = [self._rng.uniform(-self.reach, self.reach) for _ in range(2)]
            if math.dist(g, hand[:2]) > self.radius * 4:
                break
        self.goal = g
        self._held = 0.0
        self._since_spawn = 0.0
        self._from = list(hand[:2])
        self._travelled = 0.0
        self._last = list(hand[:2])
        self._left_early = False

    def distance(self, hand) -> float:
        return math.dist(hand[:2], self.goal)

    def on_target(self, hand) -> bool:
        return self.distance(hand) <= self.radius

    def progress(self) -> float:
        """How much of the dwell is done, 0..1, for drawing a ring that fills."""
        return min(1.0, self._held / self.dwell) if self.dwell > 0 else 1.0

    # --- the loop ---------------------------------------------------------
    def update(self, dt: float, hand) -> None:
        """Advance one frame."""
        if self.done:
            return
        self.elapsed += dt
        self._since_spawn += dt
        if self._last is not None:
            self._travelled += math.dist(hand[:2], self._last)
        self._last = list(hand[:2])

        if self.on_target(hand):
            self._held += dt
        else:
            if self._held > 0:
                self._left_early = True     # drifted off mid-dwell
            self._held = 0.0

        if self._held >= self.dwell:
            self.hits += 1
            if self._left_early:
                self.misses += 1
            self.reaches.append(Reach(seconds=round(self._since_spawn, 3),
                                      straight=math.dist(self._from, self.goal),
                                      travelled=self._travelled))
            if self.hits >= self.total:
                self.done = True
                return
            self.spawn(hand)

    # --- score ------------------------------------------------------------
    def summary(self) -> dict:
        times = [r.seconds for r in self.reaches]
        effs = [r.efficiency for r in self.reaches]
        return {
            "hits": self.hits,
            "of": self.total,
            "seconds": round(self.elapsed, 2),
            "mean_reach_s": round(sum(times) / len(times), 3) if times else None,
            "best_reach_s": min(times) if times else None,
            "mean_efficiency": round(sum(effs) / len(effs), 3) if effs else None,
            "wobbles": self.misses,
        }
