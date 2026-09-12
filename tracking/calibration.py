"""
Calibration System (Day 2, head tracking only)
Asks the user to hold a comfortable neutral head position for a few
seconds, then averages the tracked landmark position across that window
as the baseline "center" used by the Cursor Controller. Averaging over a
window, rather than trusting a single frame, avoids one noisy frame
setting a bad baseline — see Phase 8 of the architecture blueprint.
"""

import time

import cv2


def calibrate_neutral_position(camera, face_tracker, duration_seconds: float = 3.0,
                                window_name: str = "SmartAccess - Calibration"):
    """
    Show a live preview and collect nose-position samples for
    `duration_seconds`, then return the averaged (x, y) as the neutral
    point. Returns None if no valid samples were collected (e.g. face
    never detected during the window).
    """
    samples = []
    start_time = time.time()

    while time.time() - start_time < duration_seconds:
        success, frame = camera.read_frame()
        if not success:
            continue

        nose_pos = face_tracker.get_nose_position(frame)
        if nose_pos is not None:
            samples.append(nose_pos)

        remaining = duration_seconds - (time.time() - start_time)
        cv2.putText(frame, "Hold still - calibrating neutral position...",
                    (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(frame, f"{remaining:0.1f}s", (20, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        cv2.imshow(window_name, frame)
        cv2.waitKey(1)

    cv2.destroyWindow(window_name)

    if not samples:
        return None

    avg_x = sum(s[0] for s in samples) / len(samples)
    avg_y = sum(s[1] for s in samples) / len(samples)
    return avg_x, avg_y
