"""
test_tracking_engine.py

THROWAWAY test script -- not part of the app, not meant to be kept
long-term or wired into any build. Delete it once TrackingEngine is
confirmed working, or keep it around in a scratch/ folder if you find it
useful for future backend-only sanity checks.

Purpose: exercise TrackingEngine exactly the way the real GUI will --
start(), poll calibration to completion, read a few frames, poll live
stats, switch mode, read a few more frames, then stop() -- WITHOUT
needing CustomTkinter (or any GUI toolkit) installed. This is the
fastest way to confirm the engine itself works correctly on your machine
before wiring up app_gui.py, and to isolate "is this an engine bug" from
"is this a GUI wiring bug" if something goes wrong later.

Run this from the smartaccess project root (same place you'd run
`python backend/main.py` from), so it can find the tracking/, gestures/,
settings/, adaptive/ packages the same way main.py does:

    python test_tracking_engine.py

What you should see, in order:
  1. "[TrackingEngine] Loop FPS: ..." printed periodically (from the
     engine's own background thread) -- confirms the camera opened and
     the loop is running.
  2. This script printing calibration progress from 0.0 towards 1.0
     over about 3 seconds, then "Calibration done: success=True" (or
     False if your face wasn't visible/detected during that window --
     try again facing the camera if so).
  3. A line reporting the shape of a captured frame (e.g. (360, 480, 3))
     confirming get_current_frame() is returning real image data.
  4. A live adaptive-stats readout and FPS value, printed a few times a
     second apart.
  5. Mode switched to "hand" mid-script, with a fresh frame/stats read
     after switching, confirming set_mode() takes effect.
  6. "Shutting down..." followed by a clean exit -- no hung process, no
     leftover camera light staying on after the script ends.

If step 1 never happens or start() returns False: check
get_status()["last_error"] (this script prints it) -- almost always
means the webcam is in use by another application (close any other
camera app, including a leftover backend/main.py run) or genuinely not
connected.

If calibration keeps reporting success=False: make sure your face is
clearly visible and well-lit during the first ~3 seconds after start()
is called -- same requirement as the original calibrate_neutral_position().
"""

import time

from backend.tracking_engine import TrackingEngine


def main():
    engine = TrackingEngine(settings_profile="default")

    print("Starting engine...")
    started = engine.start()
    if not started:
        print("start() returned False.")
        print("get_status():", engine.get_status())
        return

    print("Engine started. Waiting for calibration to complete...")
    print("(Look at the camera and hold still for ~3 seconds.)")

    # Poll calibration status until done, same pattern the GUI would use.
    while True:
        status = engine.get_calibration_status()
        print(f"  calibration progress: {status['progress']:.2f}")
        if status["done"]:
            print(f"Calibration done: success={status['success']}")
            break
        time.sleep(0.2)

    # Read a few frames and confirm real image data comes back.
    time.sleep(0.5)
    frame = engine.get_current_frame()
    if frame is None:
        print("get_current_frame() returned None -- unexpected after "
              "calibration completed. Check get_status() below.")
    else:
        print(f"Got a frame with shape: {frame.shape}")

    # Watch the live readout for a couple of seconds in Head Tracking mode.
    print("\n-- Head Tracking mode, live readout for 3 seconds --")
    print(f"Current mode: {engine.get_mode()}")
    for _ in range(6):
        stats = engine.get_adaptive_stats()
        fps = engine.get_fps()
        print(f"  fps={fps:.1f}  miss_rate={stats['miss_rate']:.2f}  "
              f"click_radius={stats['click_radius']:.2f}  samples={stats['samples']}")
        time.sleep(0.5)

    # Switch to Hand Tracking and confirm the switch takes effect.
    print("\n-- Switching to Hand Tracking mode --")
    engine.set_mode("hand")
    print(f"Current mode after switch: {engine.get_mode()}")
    time.sleep(0.5)
    frame = engine.get_current_frame()
    print(f"Frame after switch: "
          f"{'None (unexpected)' if frame is None else frame.shape}")

    for _ in range(4):
        stats = engine.get_adaptive_stats()
        fps = engine.get_fps()
        print(f"  fps={fps:.1f}  miss_rate={stats['miss_rate']:.2f}  "
              f"click_radius={stats['click_radius']:.2f}  samples={stats['samples']}")
        time.sleep(0.5)

    print("\nFinal status:", engine.get_status())

    print("\nShutting down...")
    engine.stop()
    print("Stopped cleanly. is_running():", engine.is_running())


if __name__ == "__main__":
    main()
