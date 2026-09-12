"""
hardware/switch_controller.py

NEW FILE — does not replace anything.

Day 5 simplified Switch Access: no scanning-highlight UI yet (that's
deferred to Day 7+, per 04_Feature_Spec_and_Adaptive_Algorithm.md and the
10-day build plan). For now, a switch press simply triggers a left click
wherever the cursor already is. This lets the physical Arduino button work
as a real, demoable prop while the scanning interface itself is built later.
"""

import pyautogui


class SwitchController:
    def __init__(self, arduino_manager):
        self.arduino_manager = arduino_manager

    def update(self):
        """Call once per main-loop iteration while in switch mode. Checks
        for a pending switch press and clicks if one occurred. Returns True
        if a click was fired this call, otherwise False."""
        event = self.arduino_manager.poll()
        if event == "PRESS":
            pyautogui.click()
            return True
        return False
