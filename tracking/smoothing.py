"""
Generic moving-average smoothing for a stream of (x, y) points.
Used by Hand Tracking directly, and conceptually mirrors the smoothing
built into Head Tracking's Cursor Controller — per the "smoothing, shared
logic with Head Tracking where possible" note in Phase 5 of the blueprint.
"""

from collections import deque


class MovingAverageSmoother:
    def __init__(self, window: int = 5):
        self._history = deque(maxlen=window)

    def update(self, x: float, y: float):
        """Feed a new point, return the smoothed (x, y) average so far."""
        self._history.append((x, y))
        avg_x = sum(p[0] for p in self._history) / len(self._history)
        avg_y = sum(p[1] for p in self._history) / len(self._history)
        return avg_x, avg_y

    def reset(self):
        self._history.clear()
