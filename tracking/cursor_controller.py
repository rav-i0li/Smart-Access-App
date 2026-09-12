"""
Cursor Controller (Day 2)
Converts a raw normalized tracked point into an actual on-screen cursor
position, applying neutral-point calibration, a dead zone to suppress
jitter, and a moving-average smoothing filter. Replaces Day 1's raw 1:1
mapping and is what makes Head Tracking demo-reliable.
"""

from collections import deque


class CursorController:
    def __init__(self, screen_width: int, screen_height: int,
                 sensitivity: float = 1.0, dead_zone_radius: float = 0.01,
                 smoothing_window: int = 5):
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.sensitivity = sensitivity
        self.dead_zone_radius = dead_zone_radius

        self.neutral_x = 0.5
        self.neutral_y = 0.5
        self._history = deque(maxlen=smoothing_window)

    def set_neutral(self, x: float, y: float):
        """Set the calibrated neutral (center) position, in normalized [0,1] coords."""
        self.neutral_x = x
        self.neutral_y = y

    def update(self, norm_x: float, norm_y: float):
        """
        Feed a new raw normalized position. Returns (screen_x, screen_y)
        as the cursor position to move to this frame.
        """
        dx = norm_x - self.neutral_x
        dy = norm_y - self.neutral_y

        distance = (dx ** 2 + dy ** 2) ** 0.5
        if distance < self.dead_zone_radius:
            dx, dy = 0.0, 0.0  # inside dead zone: treat as "no movement" this frame

        # Smoothing averages the last N raw deltas (not the last N cursor
        # positions), so the dead zone above still applies crisply frame to frame.
        self._history.append((dx, dy))
        avg_dx = sum(p[0] for p in self._history) / len(self._history)
        avg_dy = sum(p[1] for p in self._history) / len(self._history)

        screen_x = int((0.5 + avg_dx * self.sensitivity) * self.screen_width)
        screen_y = int((0.5 + avg_dy * self.sensitivity) * self.screen_height)

        # Keep a small buffer off the literal edge — landing exactly on a
        # corner pixel (0,0) etc. triggers PyAutoGUI's failsafe abort.
        screen_x = max(2, min(self.screen_width - 3, screen_x))
        screen_y = max(2, min(self.screen_height - 3, screen_y))

        return screen_x, screen_y

    def reset_smoothing(self):
        self._history.clear()
