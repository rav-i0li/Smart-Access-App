"""
gestures/dwell_click_controller.py

Click mechanism shared by Head Tracking and Hand Tracking, replacing
pinch-to-click. See documentation/click_redesign_decision.md.

How it works: hold the cursor still, within `tolerance_radius_px`, for
`dwell_time_s` seconds, and a click fires. This is the same mechanism used
by macOS's built-in Dwell accessibility feature, Windows Eye Control, and
tools like Dwell Clicker 2.

9 September 2026 addition -- `cooldown_s`: after a click fires, dwelling
is ignored entirely for `cooldown_s` seconds, even if the anchor resets
due to natural tremor. This exists specifically to address the Midas
Touch problem (Jacob, 1995) -- the well-documented difficulty of telling
"resting here" from "wanting to click here" in any dwell/gaze-based
interface. Without a cooldown, a user with head or hand tremor holding
roughly the same spot could have the tolerance radius repeatedly exceeded
and re-entered by small involuntary movement, causing the dwell to
re-arm and fire again almost immediately. The cooldown creates a short,
deliberate gap after every click.

12 September 2026 addition -- `base_tolerance_radius_px` /
`set_radius_multiplier()`: closes the gap where AdaptiveSensitivity
computed a click_radius value but nothing ever consumed it. This class
now remembers its original tolerance radius as a fixed baseline, and
exposes a way to scale the *live* tolerance radius up or down from that
baseline. AdaptiveSensitivity calls set_radius_multiplier() each time it
recalculates click_radius, so a rising miss rate now genuinely widens the
dwell hit area (easier to trigger a click with a shaky hand/head), and a
falling miss rate genuinely tightens it back toward the original,
precise default -- rather than just changing a number on the live
readout with no real effect on click behavior.
"""

import time


class DwellClickController:
    def __init__(self, dwell_time_s: float = 0.5, tolerance_radius_px: float = 20,
                 cooldown_s: float = 0.0):
        self.dwell_time_s = dwell_time_s

        # tolerance_radius_px is the CURRENT, live tolerance -- it's what
        # update() actually checks against every frame, and it's what
        # set_radius_multiplier() below adjusts.
        self.tolerance_radius_px = tolerance_radius_px

        # base_tolerance_radius_px is the ORIGINAL value passed in at
        # construction (35 for head, 20 for hand, per settings). It never
        # changes after this point -- it's the fixed anchor the adaptive
        # system scales from, so repeated loosen/tighten cycles can't
        # drift the baseline itself over a long session.
        self.base_tolerance_radius_px = tolerance_radius_px

        self.cooldown_s = cooldown_s

        self._anchor = None
        self._start_time = None
        self._already_fired = False
        self._cooldown_until = 0.0

    def set_radius_multiplier(self, multiplier: float):
        """Called by AdaptiveSensitivity every time it recalculates
        click_radius. multiplier=1.0 restores the original base radius;
        >1.0 widens the dwell hit area (more forgiving, used when miss
        rate is high); <1.0 would tighten it (not currently used by the
        simplified adaptive algorithm, which only loosens and relaxes
        back toward 1.0, but supported here in case that changes)."""
        self.tolerance_radius_px = self.base_tolerance_radius_px * multiplier

    def update(self, x: float, y: float):
        """
        Feed the current cursor position once per frame. Returns:
            {"progress": 0.0-1.0, "just_clicked": bool, "anchor": (x, y)}
        """
        now = time.time()

        if now < self._cooldown_until:
            # Just fired -- ignore dwelling entirely until the cooldown
            # elapses, so tremor right after a click can't immediately
            # re-arm and fire again at the same spot.
            self._anchor = (x, y)
            self._start_time = now
            self._already_fired = False
            return {"progress": 0.0, "just_clicked": False, "anchor": self._anchor}

        if self._anchor is None:
            self._anchor = (x, y)
            self._start_time = now
            self._already_fired = False
            return {"progress": 0.0, "just_clicked": False, "anchor": self._anchor}

        dist = ((x - self._anchor[0]) ** 2 + (y - self._anchor[1]) ** 2) ** 0.5

        if dist > self.tolerance_radius_px:
            # Moved far enough to count as a new dwell target -- restart.
            self._anchor = (x, y)
            self._start_time = now
            self._already_fired = False
            return {"progress": 0.0, "just_clicked": False, "anchor": self._anchor}

        elapsed = now - self._start_time
        progress = min(1.0, elapsed / self.dwell_time_s)

        if progress >= 1.0 and not self._already_fired:
            self._already_fired = True
            self._cooldown_until = now + self.cooldown_s
            return {"progress": 1.0, "just_clicked": True, "anchor": self._anchor}

        return {"progress": progress, "just_clicked": False, "anchor": self._anchor}

    def reset(self):
        """Call this on mode switch so a stale dwell anchor from a previous
        mode can't cause an unexpected click right after switching."""
        self._anchor = None
        self._start_time = None
        self._already_fired = False
        self._cooldown_until = 0.0
