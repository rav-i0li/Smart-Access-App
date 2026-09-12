"""
backend/main.py

REPLACES the existing backend/main.py.

This is the known-good, single-hand state as of 9 September 2026, after a
two-hand (right=cursor, left=drag/scroll) attempt was reverted due to a
real FPS regression and limited remaining time. Full history in
documentation/handoff.md -- read that file if starting a fresh session.

Condensed history of what's live in this file:
  1. Hand Tracking's dropout choppiness during fast motion -- fixed by
     briefly holding the last known position through short MediaPipe
     detection gaps (HAND_DROPOUT_GRACE_S).
  2. ROOT CAUSE of FPS collapsing to ~10 whenever another window was
     focused/maximized: Windows deprioritizes background processes' CPU
     scheduling in favor of the focused window. Fixed via
     raise_process_priority() (HIGH_PRIORITY_CLASS) and switching
     CameraManager to the DirectShow backend (a known Logitech Brio +
     OpenCV/MSMF issue). This fix is why Hand Tracking now works well.
  3. Head Tracking's remaining choppiness (even with FPS fixed) was two
     independent smoothing stages chained together -- head_filter
     (OneEuroFilter2D) feeding into CursorController's own internal
     moving-average+deadzone. CursorController is no longer used for Head
     Tracking at all; the same neutral-relative mapping math is computed
     directly here, fed only by head_filter's output.
  4. Head-clicks-while-resting: a neutral-zone dwell gate
     (dwell_gate_radius) plus a LEASHED auto-recentering drift
     (max_neutral_drift, bounded to the original calibration point) so
     genuine postural shift over a session doesn't make true rest register
     as off-neutral. The first version of the auto-recenter had no bound
     and caused runaway one-directional drift -- fixed by leashing it.

12 September 2026 addition -- adaptive click_radius is now actually wired
into click behavior. Previously AdaptiveSensitivity computed click_radius
every time a click was recorded, but nothing ever read it back -- it only
showed up on the live overlay readout, with no real effect on clicking.
Both dwell controllers now have their live tolerance radius rescaled from
adaptive.click_radius immediately after every recorded click, via
DwellClickController.set_radius_multiplier(). See
gestures/dwell_click_controller.py for the mechanism.

Hotkeys (work from any window, not just the preview window):
    Ctrl+Alt+H -> Head Tracking mode
    Ctrl+Alt+N -> Hand Tracking mode
    Ctrl+Alt+V -> show/hide preview window
    Ctrl+Alt+Q -> quit

Switch Access (Arduino) was removed from the product entirely on
12 September 2026 -- SmartAccess now ships with exactly two modes, Head
and Hand Tracking. hardware/arduino_serial_manager.py, hardware/
switch_controller.py, and hardware/firmware/switch_button.ino are no
longer imported here; they're left on disk, unused, pending an explicit
decision on whether to delete them (see documentation/handoff.md).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import threading
from collections import deque

import cv2
import pyautogui
from pynput import keyboard

from tracking.camera_manager import CameraManager
from tracking.face_tracking import FaceTrackingEngine
from tracking.hand_tracking import HandTrackingEngine
from tracking.calibration import calibrate_neutral_position
from tracking.mapping import expand_range
from tracking.one_euro_filter import OneEuroFilter2D
from gestures.dwell_click_controller import DwellClickController
from settings.settings_manager import SettingsManager
from adaptive.adaptive_sensitivity import AdaptiveSensitivity

MODE_HEAD = "head"
MODE_HAND = "hand"

# How long to hold the last known hand position through a detection
# dropout before treating the hand as genuinely gone. Bridges the brief,
# common MediaPipe misses that happen during fast movement.
HAND_DROPOUT_GRACE_S = 0.15

PREVIEW_WINDOW_NAME = "SmartAccess Preview (Ctrl+Alt+V to hide/show)"


class ToggleState:
    """Thread-safe on/off flag, same pattern as ModeState, used for the
    preview window's shown/hidden state so the pynput listener thread can
    flip it safely."""

    def __init__(self, initial: bool = True):
        self._lock = threading.Lock()
        self._value = initial

    def toggle(self):
        with self._lock:
            self._value = not self._value
            return self._value

    def get(self):
        with self._lock:
            return self._value


class ModeState:
    """Thread-safe box so the pynput listener thread and the main loop
    thread agree on the current mode and quit flag without a race."""

    def __init__(self, initial_mode=MODE_HEAD):
        self._lock = threading.Lock()
        self._mode = initial_mode
        self._quit = False

    def set_mode(self, mode):
        with self._lock:
            self._mode = mode

    def get_mode(self):
        with self._lock:
            return self._mode

    def request_quit(self):
        with self._lock:
            self._quit = True

    def should_quit(self):
        with self._lock:
            return self._quit


def make_hotkeys(state: ModeState, head_filter: OneEuroFilter2D, hand_filter: OneEuroFilter2D,
                  dwell_head: DwellClickController, dwell_hand: DwellClickController,
                  preview_visible: ToggleState):
    """Ctrl+Alt+<letter> chords via pynput's GlobalHotKeys -- requires the
    full chord held together, so it won't fire on ordinary typing."""

    def switch_to(mode, label):
        def _handler():
            if state.get_mode() != mode:
                dwell_head.reset()
                dwell_hand.reset()
                if mode == MODE_HEAD:
                    head_filter.reset()
                elif mode == MODE_HAND:
                    hand_filter.reset()
                state.set_mode(mode)
                print(f"[main] Switched to {label} mode.")
        return _handler

    def quit_app():
        print("[main] Quit requested.")
        state.request_quit()

    def toggle_preview():
        now_visible = preview_visible.toggle()
        print(f"[main] Preview window {'shown' if now_visible else 'hidden'}.")

    return {
        "<ctrl>+<alt>+h": switch_to(MODE_HEAD, "HEAD tracking"),
        "<ctrl>+<alt>+n": switch_to(MODE_HAND, "HAND tracking"),
        "<ctrl>+<alt>+v": toggle_preview,
        "<ctrl>+<alt>+q": quit_app,
    }


def make_deadband_mover(min_move_px: float = 3.0):
    """Returns a move(x, y) function that ignores any move smaller than
    min_move_px from the cursor's last commanded position -- a hard floor
    against residual filter jitter."""
    state = {"last": None}

    def move(x, y):
        if state["last"] is not None:
            dx = x - state["last"][0]
            dy = y - state["last"][1]
            if (dx * dx + dy * dy) ** 0.5 < min_move_px:
                return
        pyautogui.moveTo(x, y, _pause=False)
        state["last"] = (x, y)

    return move


def draw_dwell_ring(frame, center, progress, color=(0, 255, 0)):
    """Visual dwell progress feedback -- a filling circular ring around the
    tracked point, the same pattern used by commercial dwell-click tools."""
    cv2.circle(frame, center, 18, (90, 90, 90), 2)
    if progress > 0:
        end_angle = int(360 * progress) - 90
        cv2.ellipse(frame, center, (18, 18), 0, -90, end_angle, color, 3)


def raise_process_priority():
    """Raises this process's CPU scheduling priority on Windows -- fixes
    the confirmed root cause of FPS collapsing whenever another window is
    focused/maximized (Windows deprioritizes background processes).
    Wrapped in try/except so a missing psutil install doesn't crash the
    app."""
    try:
        import psutil
        psutil.Process().nice(psutil.HIGH_PRIORITY_CLASS)
        print("[main] Process priority raised to HIGH -- helps keep tracking "
              "responsive even while another application is focused/maximized.")
    except ImportError:
        print("[main] psutil not installed -- skipping process priority boost. "
              "Run: pip install psutil")
    except Exception as e:
        print(f"[main] Could not raise process priority: {e}")


def main():
    raise_process_priority()

    settings = SettingsManager("default")

    camera = CameraManager(width=480, height=360)
    if not camera.open():
        print("[main] Could not open the webcam. Check it's connected and not in use by another app.")
        return

    face_engine = FaceTrackingEngine()
    hand_engine = HandTrackingEngine()

    screen_w, screen_h = pyautogui.size()

    head_filter = OneEuroFilter2D(
        min_cutoff=settings.get("head_tracking", "one_euro_min_cutoff", default=0.2),
        beta=settings.get("head_tracking", "one_euro_beta", default=0.4),
    )

    hand_filter = OneEuroFilter2D(
        min_cutoff=settings.get("hand_tracking", "one_euro_min_cutoff", default=0.7),
        beta=settings.get("hand_tracking", "one_euro_beta", default=0.6),
    )

    dwell_head = DwellClickController(
        dwell_time_s=settings.get("dwell_head", "dwell_time_s", default=0.5),
        tolerance_radius_px=settings.get("dwell_head", "tolerance_radius_px", default=35),
        cooldown_s=settings.get("dwell_head", "cooldown_s", default=0.8),
    )
    dwell_hand = DwellClickController(
        dwell_time_s=settings.get("dwell_hand", "dwell_time_s", default=0.5),
        tolerance_radius_px=settings.get("dwell_hand", "tolerance_radius_px", default=20),
        cooldown_s=settings.get("dwell_hand", "cooldown_s", default=0.3),
    )
    DWELL_LOCK_FRACTION = 0.2

    move_cursor = make_deadband_mover(
        min_move_px=settings.get("cursor", "deadband_px", default=5.0)
    )

    adaptive = AdaptiveSensitivity(
        base_click_radius=settings.get("adaptive", "click_radius", default=1.0)
    )

    print("[main] Calibrating Head Tracking neutral point -- hold still...")
    neutral = calibrate_neutral_position(camera, face_engine)
    if neutral is None:
        print("[main] No face detected during calibration -- using screen-center "
              "default instead. Recalibrate by restarting once your face is clearly "
              "visible to the camera.")
        neutral = (0.5, 0.5)
    print(f"[main] Calibration complete. Neutral point: {neutral}")
    original_neutral = neutral  # fixed anchor -- auto-recentering is leashed
                                 # to this, see the main loop below

    state = ModeState(initial_mode=MODE_HEAD)
    preview_visible = ToggleState(initial=True)
    listener = keyboard.GlobalHotKeys(
        make_hotkeys(state, head_filter, hand_filter, dwell_head, dwell_hand, preview_visible)
    )
    listener.start()

    print("[main] SmartAccess running. Hotkeys work from any window:")
    print("       Ctrl+Alt+H = head | Ctrl+Alt+N = hand | "
          "Ctrl+Alt+V = show/hide preview | Ctrl+Alt+Q = quit")
    print("       Hold the cursor still on a target to click -- no gesture needed.")

    last_hand_landmarks = None
    last_hand_seen_time = 0.0
    was_preview_visible = True

    frame_times = deque(maxlen=30)
    last_fps_print = time.time()

    try:
        while not state.should_quit():
            success, frame = camera.read_frame()
            if not success or frame is None:
                continue

            mode = state.get_mode()
            frame_h, frame_w = frame.shape[:2]

            if mode == MODE_HEAD:
                nose_pos = face_engine.get_nose_position(frame)
                if nose_pos is not None:
                    smoothed_nose = head_filter.filter(*nose_pos)

                    head_sensitivity = settings.get("head_tracking", "sensitivity", default=1.0)
                    dx = smoothed_nose[0] - neutral[0]
                    dy = smoothed_nose[1] - neutral[1]
                    raw_x = int((0.5 + dx * head_sensitivity) * screen_w)
                    raw_y = int((0.5 + dy * head_sensitivity) * screen_h)
                    screen_x = max(2, min(screen_w - 3, raw_x))
                    screen_y = max(2, min(screen_h - 3, raw_y))

                    px, py = int(nose_pos[0] * frame_w), int(nose_pos[1] * frame_h)
                    dwell_gate_radius = settings.get(
                        "head_tracking", "dwell_gate_radius", default=0.035
                    )
                    dist_from_neutral = (dx ** 2 + dy ** 2) ** 0.5

                    if dist_from_neutral < dwell_gate_radius:
                        dwell_head.reset()
                        move_cursor(screen_x, screen_y)

                        neutral_drift_rate = settings.get(
                            "head_tracking", "neutral_drift_rate", default=0.01
                        )
                        max_neutral_drift = settings.get(
                            "head_tracking", "max_neutral_drift", default=0.05
                        )
                        candidate_x = neutral[0] + neutral_drift_rate * (smoothed_nose[0] - neutral[0])
                        candidate_y = neutral[1] + neutral_drift_rate * (smoothed_nose[1] - neutral[1])
                        leash_dx = candidate_x - original_neutral[0]
                        leash_dy = candidate_y - original_neutral[1]
                        leash_dist = (leash_dx ** 2 + leash_dy ** 2) ** 0.5
                        if leash_dist > max_neutral_drift:
                            scale = max_neutral_drift / leash_dist
                            candidate_x = original_neutral[0] + leash_dx * scale
                            candidate_y = original_neutral[1] + leash_dy * scale
                        neutral = (candidate_x, candidate_y)
                    else:
                        dwell_result = dwell_head.update(screen_x, screen_y)
                        draw_dwell_ring(frame, (px, py), dwell_result["progress"])
                        if dwell_result["progress"] > DWELL_LOCK_FRACTION:
                            move_x, move_y = dwell_result["anchor"]
                        else:
                            move_x, move_y = screen_x, screen_y
                        move_cursor(move_x, move_y)
                        if dwell_result["just_clicked"]:
                            pyautogui.click()
                            adaptive.record_click(screen_x, screen_y)
                            # 12 Sept addition: push the freshly-recalculated
                            # click_radius back into BOTH dwell controllers
                            # (one shared AdaptiveSensitivity instance covers
                            # both modes, by design -- see chat history).
                            dwell_head.set_radius_multiplier(adaptive.click_radius)
                            dwell_hand.set_radius_multiplier(adaptive.click_radius)

            elif mode == MODE_HAND:
                landmarks = hand_engine.get_hand_landmarks(frame)
                now = time.time()

                if landmarks is not None:
                    last_hand_landmarks = landmarks
                    last_hand_seen_time = now
                elif (last_hand_landmarks is not None
                      and (now - last_hand_seen_time) < HAND_DROPOUT_GRACE_S):
                    landmarks = last_hand_landmarks
                else:
                    last_hand_landmarks = None

                if landmarks is not None:
                    ix, iy = landmarks["index_tip"]
                    tx, ty = landmarks["thumb_tip"]
                    cv2.circle(frame, (int(tx * frame_w), int(ty * frame_h)), 6, (255, 0, 0), -1)

                    edge_margin = settings.get("hand_tracking", "edge_margin", default=0.15)
                    index_x = expand_range(ix, edge_margin)
                    index_y = expand_range(iy, edge_margin)
                    raw_screen_x = max(2, min(screen_w - 3, int(index_x * screen_w)))
                    raw_screen_y = max(2, min(screen_h - 3, int(index_y * screen_h)))

                    filtered_x, filtered_y = hand_filter.filter(raw_screen_x, raw_screen_y)
                    screen_x = int(max(2, min(screen_w - 3, filtered_x)))
                    screen_y = int(max(2, min(screen_h - 3, filtered_y)))

                    px, py = int(ix * frame_w), int(iy * frame_h)
                    dwell_result = dwell_hand.update(screen_x, screen_y)
                    draw_dwell_ring(frame, (px, py), dwell_result["progress"])
                    if dwell_result["progress"] > DWELL_LOCK_FRACTION:
                        move_x, move_y = dwell_result["anchor"]
                    else:
                        move_x, move_y = screen_x, screen_y
                    move_cursor(move_x, move_y)
                    if dwell_result["just_clicked"]:
                        pyautogui.click()
                        adaptive.record_click(screen_x, screen_y)
                        # Same wire-up as the head branch above.
                        dwell_head.set_radius_multiplier(adaptive.click_radius)
                        dwell_hand.set_radius_multiplier(adaptive.click_radius)
                else:
                    dwell_hand.reset()
                    hand_filter.reset()

            frame_times.append(time.time())
            current_fps = 0.0
            if len(frame_times) >= 2:
                current_fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
            if time.time() - last_fps_print > 2.0:
                print(f"[main] Loop FPS: {current_fps:.1f}")
                last_fps_print = time.time()

            is_preview_visible = preview_visible.get()

            if is_preview_visible:
                readout = adaptive.get_readout()
                overlay_text = (
                    f"Mode: {mode.upper()}  |  "
                    f"FPS: {current_fps:.0f}  |  "
                    f"Miss rate: {readout['miss_rate']*100:.0f}%  |  "
                    f"Click radius: {readout['click_radius']:.2f}  |  "
                    f"Samples: {readout['samples']}"
                )
                cv2.putText(
                    frame, overlay_text, (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2, cv2.LINE_AA,
                )
                cv2.imshow(PREVIEW_WINDOW_NAME, frame)
                cv2.waitKey(1)
            elif was_preview_visible:
                cv2.destroyWindow(PREVIEW_WINDOW_NAME)

            was_preview_visible = is_preview_visible

            time.sleep(0.01)

    finally:
        listener.stop()
        face_engine.close()
        hand_engine.close()
        camera.release()
        cv2.destroyAllWindows()
        print("[main] Shut down cleanly.")


if __name__ == "__main__":
    main()
