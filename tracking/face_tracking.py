"""
Face Tracking Engine
Detects a face in a frame using MediaPipe Face Mesh and returns the
position of a single reference landmark (nose tip) used as the head-tracking
origin point. This is Day 1's "get a landmark moving a cursor" version —
neutral-point calibration, smoothing, and dead zone are added on Day 2.
"""

import mediapipe as mp

# Landmark index for the tip of the nose in MediaPipe's 468-point face mesh.
NOSE_TIP_INDEX = 1


class FaceTrackingEngine:
    """Wraps MediaPipe Face Mesh to expose a single tracked point per frame."""

    def __init__(self, max_num_faces: int = 1, min_detection_confidence: float = 0.5,
                 min_tracking_confidence: float = 0.5):
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            max_num_faces=max_num_faces,
            refine_landmarks=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )

    def get_nose_position(self, frame):
        """
        Run detection on a BGR frame (as read by OpenCV).
        Returns (x, y) as normalized coordinates in [0, 1], or None if no face found.
        """
        # MediaPipe expects RGB, OpenCV gives BGR
        rgb_frame = frame[:, :, ::-1]
        results = self.face_mesh.process(rgb_frame)

        if not results.multi_face_landmarks:
            return None

        landmarks = results.multi_face_landmarks[0].landmark
        nose = landmarks[NOSE_TIP_INDEX]
        return (nose.x, nose.y)

    def close(self):
        self.face_mesh.close()
