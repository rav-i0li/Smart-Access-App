"""
Hand Tracking Engine
Detects a hand in a frame using MediaPipe Hands and returns the index
fingertip position (used for cursor mapping, Day 3) alongside the thumb
tip position (needed for pinch-distance calculation, Day 4). Mirrors the
Face Tracking Engine's shape so both plug into the same Camera Manager
loop, per the Phase 3 architecture.

Single-hand version -- this is the known-good state as of 9 September
2026, confirmed by live testing as working well ("nearly perfect": fast,
smooth, no left/right complications). A two-hand version (right hand =
cursor, left hand = drag/scroll) was attempted the same day and then
reverted due to a real FPS regression from tracking two hands
simultaneously plus limited remaining time before the deadline -- not
because the core idea was wrong. See documentation/handoff.md if picking
this back up later.
"""

import mediapipe as mp

# Landmark indices in MediaPipe's 21-point hand model
INDEX_FINGERTIP_INDEX = 8
THUMB_TIP_INDEX = 4


class HandTrackingEngine:
    """Wraps MediaPipe Hands to expose the two points needed for cursor + pinch."""

    def __init__(self, max_num_hands: int = 1, min_detection_confidence: float = 0.6,
                 min_tracking_confidence: float = 0.6, model_complexity: int = 1):
        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            max_num_hands=max_num_hands,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
            model_complexity=model_complexity,
        )

    def get_hand_landmarks(self, frame):
        """
        Run detection on a BGR frame (as read by OpenCV).
        Returns {'index_tip': (x, y), 'thumb_tip': (x, y)} as normalized
        [0, 1] coordinates, or None if no hand was found.
        """
        rgb_frame = frame[:, :, ::-1]
        results = self.hands.process(rgb_frame)

        if not results.multi_hand_landmarks:
            return None

        landmarks = results.multi_hand_landmarks[0].landmark
        index_tip = landmarks[INDEX_FINGERTIP_INDEX]
        thumb_tip = landmarks[THUMB_TIP_INDEX]

        return {
            "index_tip": (index_tip.x, index_tip.y),
            "thumb_tip": (thumb_tip.x, thumb_tip.y),
        }

    def close(self):
        self.hands.close()
