"""
Pinch Detector
Calculates the normalized distance between thumb tip and index fingertip
to determine pinch state (down/up). Uses two thresholds (engage vs.
release) rather than one, so the state doesn't flicker rapidly when the
distance sits right at the boundary.
"""


class PinchDetector:
    def __init__(self, engage_threshold: float = 0.05, release_threshold: float = None):
        self.engage_threshold = engage_threshold
        # Release threshold defaults to a bit larger than engage, so a pinch
        # has to visibly open back up before it counts as released.
        self.release_threshold = release_threshold or engage_threshold * 1.4
        self.is_pinching = False

    def update(self, index_tip, thumb_tip):
        """
        index_tip, thumb_tip: (x, y) normalized coordinates.
        Returns (is_pinching, distance).
        """
        dx = index_tip[0] - thumb_tip[0]
        dy = index_tip[1] - thumb_tip[1]
        distance = (dx ** 2 + dy ** 2) ** 0.5

        if not self.is_pinching and distance < self.engage_threshold:
            self.is_pinching = True
        elif self.is_pinching and distance > self.release_threshold:
            self.is_pinching = False

        return self.is_pinching, distance
