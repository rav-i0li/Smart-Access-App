"""
Settings Manager (Day 1/2 stub)
Loads and saves per-profile configuration — calibration values, sensitivity
parameters, adaptive state — as a local JSON file in the OS-standard
per-user data directory (see paths.user_data_dir). Single default profile
only for now; a profile picker for shared/library computers is later work
(Phase 5 of the architecture blueprint).
"""

import json
import os

from .paths import user_data_dir

DEFAULT_CONFIG = {
    "profile_name": "default",
    "head_tracking": {
        "neutral_x": None,          # set by calibration (Day 2)
        "neutral_y": None,
        "sensitivity": 1.0,
        "smoothing_window": 5,
        "dead_zone_radius": 0.01,   # normalized units, 0-1 scale
    },
    "hand_tracking": {
        "pinch_threshold": 0.05,
        "smoothing_window": 5,
        "edge_margin": 0.15,   # stretches usable range to screen edges — increase if
                                # the cursor still doesn't reach edges, decrease if
                                # it reaches the edge too easily / hand leaves frame
    },
    "adaptive": {
        "click_radius": 1.0,        # multiplier on the base click radius
    },
}


class SettingsManager:
    def __init__(self, profile_name: str = "default"):
        self.profile_name = profile_name
        self.data_dir = user_data_dir()
        self.config_path = os.path.join(self.data_dir, f"{profile_name}.json")
        self.config = self._load_or_create()

    def _load_or_create(self) -> dict:
        if os.path.exists(self.config_path):
            with open(self.config_path, "r") as f:
                return json.load(f)
        config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
        config["profile_name"] = self.profile_name
        self._write(config)
        return config

    def _write(self, config: dict):
        with open(self.config_path, "w") as f:
            json.dump(config, f, indent=2)

    def save(self):
        self._write(self.config)

    def reset_to_default(self):
        config = json.loads(json.dumps(DEFAULT_CONFIG))
        config["profile_name"] = self.profile_name
        self.config = config
        self.save()

    def get(self, *keys, default=None):
        """Nested get, e.g. settings.get('head_tracking', 'sensitivity')."""
        node = self.config
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, *keys_and_value):
        """Nested set, e.g. settings.set('head_tracking', 'sensitivity', 1.2)."""
        *keys, value = keys_and_value
        node = self.config
        for key in keys[:-1]:
            node = node.setdefault(key, {})
        node[keys[-1]] = value
