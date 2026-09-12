"""
adaptive/adaptive_sensitivity.py

NEW FILE — does not replace anything.

Day 6 simplified adaptive sensitivity, per 04_Feature_Spec_and_Adaptive_Algorithm.md:
  - Track hit/miss over a small rolling window (not the full 20-attempt
    window described as the future full version).
  - Auto-adjust exactly one parameter: click_radius.
  - Expose a live readout (miss rate, click radius) for the UI overlay.

HOW A "MISS" IS DETECTED (worth understanding, and worth explaining if a
judge asks how this works without any real on-screen click targets yet):
SmartAccess has no clickable targets with known hit-boxes to test against
yet -- that's Day 7+ (on-screen control panel) and later. So this uses a
standard, well-established heuristic instead: if a new click lands close
in both time and distance to the previous click, the previous click is
retroactively counted as a miss (the user was correcting an inaccurate
click). This is the same "correction click" signal cursor-accuracy tools
commonly use when no ground-truth target is available. It will only get
more accurate once Day 7's real buttons exist, at which point "hit" can
be determined directly from whether the click landed inside a button's
bounds instead of this heuristic.

Thresholds and caps are taken directly from the full-version spec (miss
rate > 0.3 loosens, < 0.1 tightens, capped at +60% of base) so the
simplified version stays a real subset of the target design, not a
different algorithm.
"""

import math
import time
from collections import deque


class AdaptiveSensitivity:
    def __init__(
        self,
        base_click_radius=1.0,
        window_size=6,
        correction_window_s=1.0,
        correction_distance_px=60,
        max_radius_multiplier=1.6,
    ):
        self.base_click_radius = base_click_radius
        self.click_radius = base_click_radius
        self.window_size = window_size
        self.correction_window_s = correction_window_s
        self.correction_distance_px = correction_distance_px
        self.max_click_radius = base_click_radius * max_radius_multiplier

        self.recent_hits = deque(maxlen=window_size)
        self._last_click_time = None
        self._last_click_pos = None

    def record_click(self, x, y):
        """Call this once per real click attempt (hand pinch click, switch
        press click, and eventually head dwell click), with the screen
        coordinates the click landed at."""
        now = time.time()

        if self._last_click_time is not None:
            dt = now - self._last_click_time
            dist = math.hypot(x - self._last_click_pos[0], y - self._last_click_pos[1])
            if dt <= self.correction_window_s and dist <= self.correction_distance_px:
                # This click is close in time and space to the last one --
                # treat it as a correction, meaning the last click missed.
                if self.recent_hits:
                    self.recent_hits[-1] = False

        # Assume this new click is a hit unless a future click corrects it.
        self.recent_hits.append(True)
        self._last_click_time = now
        self._last_click_pos = (x, y)

        self._maybe_adjust()

    def miss_rate(self):
        if not self.recent_hits:
            return 0.0
        misses = sum(1 for hit in self.recent_hits if not hit)
        return misses / len(self.recent_hits)

    def _maybe_adjust(self):
        if len(self.recent_hits) < self.window_size:
            return  # not enough data yet -- avoid reacting to noise early on

        rate = self.miss_rate()
        if rate > 0.3:
            self.click_radius = min(self.click_radius * 1.15, self.max_click_radius)
        elif rate < 0.1 and self.click_radius > self.base_click_radius:
            self.click_radius = max(self.click_radius * 0.9, self.base_click_radius)

    def get_readout(self):
        """For the live UI overlay -- doubles as a debugging tool and a
        presentation demo aid, per the feature spec's demo tip."""
        return {
            "miss_rate": round(self.miss_rate(), 2),
            "click_radius": round(self.click_radius, 2),
            "samples": len(self.recent_hits),
        }
