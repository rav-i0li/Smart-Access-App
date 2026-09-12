# This is NOT a new file to add to your project.
# main.py works fine without any of this being added to
# settings/settings_manager.py's DEFAULT_CONFIG, because every value is
# read via settings.get("section", "key", default=...) -- these are just
# the defaults main.py falls back to. Add this block to DEFAULT_CONFIG
# only if you want these values to actually persist to default.json and
# be editable there, rather than living as hardcoded fallbacks in main.py.

# DEFAULT_CONFIG_ADDITION = {
#     "switch": {
#         "serial_port": "COM3",
#         "baud_rate": 9600,
#     },
#     "head_tracking": {
#         # ... your existing keys stay as they are, these are additions ...
#         "one_euro_min_cutoff": 0.3,   # baseline smoothing for the nose
#                                        # position -- lower = smoother but
#                                        # more lag when moving slowly
#         "one_euro_beta": 0.4,         # how much smoothing loosens as head
#                                        # movement speeds up -- higher =
#                                        # less lag on fast movement
#         "dwell_gate_radius": 0.025,   # how far off neutral you must move
#                                        # before dwell starts counting at
#                                        # all -- deliberately looser than
#                                        # dead_zone_radius, to avoid firing
#                                        # clicks from ordinary tracking
#                                        # noise while resting
#     },
#     "hand_tracking": {
#         # ... your existing keys stay as they are, these are additions ...
#         "one_euro_min_cutoff": 1.0,
#         "one_euro_beta": 0.5,
#     },
#     "dwell_head": {
#         "dwell_time_s": 0.5,
#         "tolerance_radius_px": 35,    # larger than hand's -- absorbs head
#                                        # tremor without constantly
#                                        # resetting the dwell timer
#         "cooldown_s": 0.6,            # pause after a click before dwell
#                                        # can arm again, at the same spot
#     },
#     "dwell_hand": {
#         "dwell_time_s": 0.5,
#         "tolerance_radius_px": 20,
#         "cooldown_s": 0.3,
#     },
#     "left_hand": {
#         "min_swipe_distance": 0.12,   # fraction of frame height a swipe
#                                        # must cover -- filters out tiny
#                                        # movements and natural jitter
#         "max_swipe_window_s": 0.5,    # swipe must complete within this
#                                        # long, or it's treated as a slow
#                                        # drift, not a flick
#         "swipe_cooldown_s": 0.4,      # hard minimum gap after a swipe
#                                        # fires, before another can register
#         "return_tolerance": 0.03,     # how close to the swipe's start
#                                        # position counts as "returned",
#                                        # unlocking the next swipe
#         "scroll_amount": 3,           # pyautogui.scroll() units per swipe
#     },
# }

# Reminder from 06_Day1-4_Progress_and_Open_Issues.md: if default.json
# already exists on disk from an earlier run, adding a new key to
# DEFAULT_CONFIG in code won't retroactively add it to that file -- delete
# %APPDATA%\SmartAccess\default.json (in PowerShell:
# `del $env:APPDATA\SmartAccess\default.json`) so it regenerates with
# whatever new sections you've added included.
