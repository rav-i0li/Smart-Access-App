"""
analytics/session_history.py

NEW FILE -- does not replace anything.

Persists a small, REAL record of each SmartAccess session's adaptive
sensitivity performance, so the Profile tab in Settings can show genuine
improvement over time rather than a static mockup pretending to be live
data. One entry is appended per session (when the engine stops), not per
frame or per click -- that keeps the file tiny and keeps each point
meaningful (a session-to-session trend), rather than a firehose of every
individual click.

Honest limitation, worth knowing before demoing this: a graph is only as
convincing as the number of real sessions behind it. On a machine used
for the first time tonight, this will genuinely only have one or two
points -- that's correct, not a bug. The GUI's Profile tab is written to
say so plainly rather than stretching one point into a misleading trend
line. See gui/app_gui.py's ProfileTab for how that's handled.
"""

import json
import os
import time

from settings.paths import user_data_dir

HISTORY_FILENAME_SUFFIX = "_session_history.json"


class SessionHistory:
    """One instance per profile, same pattern as SettingsManager."""

    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name
        self.path = os.path.join(
            user_data_dir(), f"{profile_name}{HISTORY_FILENAME_SUFFIX}"
        )
        self.entries = self._load()

    def _load(self) -> list:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[SessionHistory] Could not read {self.path}, starting fresh: {e}")
                return []
        return []

    def _save(self) -> None:
        try:
            with open(self.path, "w") as f:
                json.dump(self.entries, f, indent=2)
        except Exception as e:
            print(f"[SessionHistory] Could not write {self.path}: {e}")

    def record_session(self, miss_rate: float, click_radius: float, samples: int) -> None:
        """Call once, when a session genuinely ends (TrackingEngine.stop()).
        Skips recording if the session produced no real click data at all
        (samples == 0) -- an empty session shouldn't pollute the trend
        with a meaningless zero."""
        if samples <= 0:
            return
        self.entries.append({
            "timestamp": time.time(),
            "miss_rate": miss_rate,
            "click_radius": click_radius,
            "samples": samples,
        })
        self._save()

    def get_entries(self) -> list:
        """Returns entries oldest-first, safe to iterate for a graph."""
        return list(self.entries)
