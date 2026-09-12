"""
Click Detection (Day 4) — pinch-to-click / drag state machine.
Wraps a PinchDetector and translates pinch state transitions into real
OS-level mouse button events via PyAutoGUI: idle -> down -> (drag while
held, since the cursor keeps moving with the button down) -> up.

This is Hand Tracking's input into the shared "Click Detection" concept
from Phase 3 of the architecture blueprint. A dwell-timer input from Head
Tracking is future work, not built this sprint — Head Tracking only
moves the cursor for now.
"""

import pyautogui

from gestures.pinch_detector import PinchDetector


class PinchClickController:
    def __init__(self, engage_threshold: float = 0.05, release_threshold: float = None):
        self.detector = PinchDetector(engage_threshold, release_threshold)

    def update(self, index_tip, thumb_tip):
        """
        Feed the latest fingertip positions. Fires mouseDown/mouseUp on
        state transitions. Returns (is_pinching, distance) for UI display.
        """
        was_pinching = self.detector.is_pinching
        is_pinching, distance = self.detector.update(index_tip, thumb_tip)

        if is_pinching and not was_pinching:
            pyautogui.mouseDown()
        elif was_pinching and not is_pinching:
            pyautogui.mouseUp()

        return is_pinching, distance

    def release_if_held(self):
        """Safety valve: call this on mode switch or quit so a pinch never
        leaves the mouse button stuck down if the hand leaves frame mid-pinch."""
        if self.detector.is_pinching:
            pyautogui.mouseUp()
            self.detector.is_pinching = False
