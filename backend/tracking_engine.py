"""
backend/tracking_engine.py

NEW FILE. Does not replace backend/main.py -- main.py is left exactly as
it is (hotkeys, its own cv2 preview window, headless single-process
loop) and keeps working standalone for backend-only testing, per the
"recommended refactor scope" in documentation/frontend_backend_interface.md.

This file is the actual GUI-facing surface: the TrackingEngine class
documented in that contract file. Every line of tracking / filter /
dwell / adaptive-sensitivity math below is copied unchanged from the
current, real backend/main.py (verified by reading that file directly,
not inferred) -- only the orchestration around it changes: it now runs
on a background thread inside a class, instead of blocking main()
directly, so a GUI event loop can own the main thread instead.

Two deliberate deviations from the original contract wording, both
agreed with Matthew and recorded in documentation/handoff_to_frontend_session.md:

1. Switch Access is gone. set_mode() only accepts "head"/"hand", and
   get_status() has no switch_connected field.
2. get_current_frame() no longer burns the Mode/FPS/miss-rate/click-radius
   text into the frame. That data is exposed separately via get_mode(),
   get_fps(), and get_adaptive_stats() instead, so the GUI can render it
   as real status-strip widgets instead of cv2.putText overlay text --
   which is exactly the "cramped, cut-off green text" problem the GUI
   was built to get rid of. get_current_frame() still draws the dwell
   progress ring and the hand-tracking thumb-tip dot, since those are
   genuine live tracking feedback, not just stats.

12 September 2026 addition -- adaptive click_radius is now actually wired
into click behavior, matching the same fix applied to backend/main.py.
Previously AdaptiveSensitivity computed click_radius every time a click
was recorded, but nothing ever read it back -- it only showed up via
get_adaptive_stats(), with no real effect on clicking. Both dwell
controllers now have their live tolerance radius rescaled from
adaptive.click_radius immediately after every recorded click, via
DwellClickController.set_radius_multiplier(). See
gestures/dwell_click_controller.py for the mechanism. One shared
AdaptiveSensitivity instance still covers both modes (unchanged, kept
deliberately simple rather than splitting into a per-mode instance).

12 September 2026, second addition -- Settings panel support:
  - update_setting(section, key, value) lets the GUI persist a changed
    setting AND, for the handful of values that were previously only
    read once at start() (dwell_time_s, tolerance_radius_px base,
    cooldown_s), applies the change live to the running dwell
    controllers too -- so a slider change takes effect immediately,
    without needing a restart or recalibration. Settings that were
    already read fresh every frame (e.g. head_tracking.sensitivity)
    don't need this special handling; a plain settings.set()+save() is
    enough for those, since next frame picks it up automatically.
  - get_setting(section, key, default) is a thin read-only pass-through,
    so the GUI can populate slider starting positions without reaching
    into self._settings directly.
  - SessionHistory: one real entry is appended per session (in stop())
    with that session's final adaptive-sensitivity readout, so the
    Profile tab in Settings can show genuine improvement across
    sessions over time -- not a mockup. See analytics/session_history.py.

Threading model: ONE background thread owns the camera and the whole
per-frame pipeline (matches the existing ModeState/ToggleState
lock-protected pattern already used in main.py for the pynput hotkey
thread). Calibration does NOT get its own thread or its own camera
reads -- calibrate_neutral_position() in tracking/calibration.py opens
its own blocking loop and its own cv2 window, which is unusable from a
GUI and would also race the engine thread for the same camera object.
Instead, calibration is a state flag the engine's own loop checks each
frame it already reads, exactly like head/hand tracking mode is a state
flag it checks each frame. tracking/calibration.py is left on disk,
unused by this file -- still fine for main.py's headless mode to keep
using directly.
"""

import time
import threading
from collections import deque

import cv2
import pyautogui

from tracking.camera_manager import CameraManager
from tracking.face_tracking import FaceTrackingEngine
from tracking.hand_tracking import HandTrackingEngine
from tracking.mapping import expand_range
from tracking.one_euro_filter import OneEuroFilter2D
from gestures.dwell_click_controller import DwellClickController
from settings.settings_manager import SettingsManager
from adaptive.adaptive_sensitivity import AdaptiveSensitivity
from analytics.session_history import SessionHistory

MODE_HEAD = "head"
MODE_HAND = "hand"

# Same constants as main.py -- kept in sync manually since this file
# doesn't import main.py (avoids pulling in pynput/hotkey/cv2-window
# code that has no purpose here).
HAND_DROPOUT_GRACE_S = 0.15
DWELL_LOCK_FRACTION = 0.2
CALIBRATION_DURATION_S = 6.0  # 13 September 2026: was 3.0 -- felt rushed, per Matthew's live test


def _make_deadband_mover(min_move_px: float = 5.0):
    """Identical to main.py's make_deadband_mover -- copied, not
    imported, for the same reason noted above."""
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


def _draw_dwell_ring(frame, center, progress, color=(0, 255, 0)):
    """Identical to main.py's draw_dwell_ring."""
    cv2.circle(frame, center, 18, (90, 90, 90), 2)
    if progress > 0:
        end_angle = int(360 * progress) - 90
        cv2.ellipse(frame, center, (18, 18), 0, -90, end_angle, color, 3)


def _raise_process_priority():
    """Identical to main.py's raise_process_priority."""
    try:
        import psutil
        psutil.Process().nice(psutil.HIGH_PRIORITY_CLASS)
    except ImportError:
        print("[TrackingEngine] psutil not installed -- skipping process "
              "priority boost. Run: pip install psutil")
    except Exception as e:
        print(f"[TrackingEngine] Could not raise process priority: {e}")


class TrackingEngine:
    """GUI-facing engine. See documentation/frontend_backend_interface.md
    for the full contract this class implements. The GUI thread only
    ever calls the public methods below -- it never reaches into
    tracking/gestures/settings/adaptive internals directly."""

    def __init__(self, settings_profile: str = "default"):
        self._settings = SettingsManager(settings_profile)
        self._lock = threading.RLock()

        self._running = False
        self._quit_requested = False
        self._thread = None

        self._camera = None
        self._face_engine = None
        self._hand_engine = None
        self._head_filter = None
        self._hand_filter = None
        self._dwell_head = None
        self._dwell_hand = None
        self._adaptive = None
        self._move_cursor = None
        self._session_history = SessionHistory(settings_profile)

        self._mode = MODE_HEAD
        self._screen_w, self._screen_h = pyautogui.size()

        self._neutral = (0.5, 0.5)
        self._original_neutral = (0.5, 0.5)

        self._last_hand_landmarks = None
        self._last_hand_seen_time = 0.0

        self._latest_frame = None
        self._current_fps = 0.0
        self._frame_times = deque(maxlen=30)

        self._calibration = {
            "in_progress": False, "progress": 0.0,
            "done": False, "success": False,
        }
        self._calibration_samples = []
        self._calibration_start = None

        self._status = {"camera_ok": True, "last_error": None}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Opens the camera and starts the background tracking thread.
        Returns False if the webcam couldn't be opened. Safe to call
        again after a failed start()."""
        with self._lock:
            if self._running:
                return True

            _raise_process_priority()

            camera = CameraManager(width=480, height=360)
            if not camera.open():
                self._status["camera_ok"] = False
                self._status["last_error"] = (
                    "Could not open the webcam. Check it's connected and "
                    "not in use by another app."
                )
                return False

            self._camera = camera
            self._face_engine = FaceTrackingEngine()
            self._hand_engine = HandTrackingEngine()
            self._screen_w, self._screen_h = pyautogui.size()

            self._head_filter = OneEuroFilter2D(
                min_cutoff=self._settings.get("head_tracking", "one_euro_min_cutoff", default=0.2),
                beta=self._settings.get("head_tracking", "one_euro_beta", default=0.4),
            )
            self._hand_filter = OneEuroFilter2D(
                min_cutoff=self._settings.get("hand_tracking", "one_euro_min_cutoff", default=0.7),
                beta=self._settings.get("hand_tracking", "one_euro_beta", default=0.6),
            )
            self._dwell_head = DwellClickController(
                dwell_time_s=self._settings.get("dwell_head", "dwell_time_s", default=0.5),
                tolerance_radius_px=self._settings.get("dwell_head", "tolerance_radius_px", default=35),
                cooldown_s=self._settings.get("dwell_head", "cooldown_s", default=0.8),
            )
            self._dwell_hand = DwellClickController(
                dwell_time_s=self._settings.get("dwell_hand", "dwell_time_s", default=0.5),
                tolerance_radius_px=self._settings.get("dwell_hand", "tolerance_radius_px", default=20),
                cooldown_s=self._settings.get("dwell_hand", "cooldown_s", default=0.3),
            )
            self._move_cursor = _make_deadband_mover(
                min_move_px=self._settings.get("cursor", "deadband_px", default=5.0)
            )
            self._adaptive = AdaptiveSensitivity(
                base_click_radius=self._settings.get("adaptive", "click_radius", default=1.0)
            )

            self._mode = MODE_HEAD
            self._neutral = (0.5, 0.5)
            self._original_neutral = (0.5, 0.5)
            self._last_hand_landmarks = None
            self._last_hand_seen_time = 0.0
            self._latest_frame = None
            self._frame_times.clear()
            self._status = {"camera_ok": True, "last_error": None}

            self._quit_requested = False
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

        # Auto-calibrate on startup, same behavior as the old script --
        # non-blocking, so this returns immediately; the GUI can also
        # call calibrate() again later via a Recalibrate button.
        self.calibrate()
        return True

    def stop(self) -> None:
        """Stops the background thread, releases the camera. Safe to
        call even if start() was never called, and safe to call more
        than once."""
        with self._lock:
            if not self._running:
                return
            self._quit_requested = True
            thread = self._thread

        if thread is not None:
            thread.join(timeout=2.0)

        with self._lock:
            # Record this session's final adaptive readout, if it had any
            # real click data -- SessionHistory itself skips zero-sample
            # sessions, so calling this unconditionally on every stop() is
            # safe and doesn't need its own guard here.
            if self._adaptive is not None:
                readout = self._adaptive.get_readout()
                self._session_history.record_session(
                    miss_rate=readout["miss_rate"],
                    click_radius=readout["click_radius"],
                    samples=readout["samples"],
                )
            if self._face_engine is not None:
                self._face_engine.close()
            if self._hand_engine is not None:
                self._hand_engine.close()
            if self._camera is not None:
                self._camera.release()
            self._face_engine = None
            self._hand_engine = None
            self._camera = None
            self._thread = None
            self._running = False

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    # ------------------------------------------------------------------
    # Mode control
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        if mode not in (MODE_HEAD, MODE_HAND):
            raise ValueError(f"Unknown mode {mode!r}; expected 'head' or 'hand'")
        with self._lock:
            if mode != self._mode:
                self._dwell_head.reset()
                self._dwell_hand.reset()
                if mode == MODE_HEAD:
                    self._head_filter.reset()
                else:
                    self._hand_filter.reset()
                self._mode = mode

    def get_mode(self) -> str:
        with self._lock:
            return self._mode

    # ------------------------------------------------------------------
    # Calibration (non-blocking)
    # ------------------------------------------------------------------

    def calibrate(self) -> None:
        """Starts calibration in the background thread and returns
        immediately. Poll get_calibration_status() for progress."""
        with self._lock:
            self._calibration = {
                "in_progress": True, "progress": 0.0,
                "done": False, "success": False,
            }
            self._calibration_samples = []
            self._calibration_start = time.time()

    def get_calibration_status(self) -> dict:
        with self._lock:
            return dict(self._calibration)

    # ------------------------------------------------------------------
    # Frame / preview
    # ------------------------------------------------------------------

    def get_current_frame(self):
        """Returns a BGR numpy array with the dwell-progress ring and
        hand-tracking thumb dot already drawn, or None if no frame is
        available yet. Does NOT include Mode/FPS/stats text -- use
        get_mode() / get_fps() / get_adaptive_stats() for those."""
        with self._lock:
            if self._latest_frame is None:
                return None
            return self._latest_frame.copy()

    # ------------------------------------------------------------------
    # Live readout
    # ------------------------------------------------------------------

    def get_adaptive_stats(self) -> dict:
        with self._lock:
            if self._adaptive is None:
                return {"miss_rate": 0.0, "click_radius": 0.0, "samples": 0}
            return self._adaptive.get_readout()

    def get_fps(self) -> float:
        with self._lock:
            return self._current_fps

    # ------------------------------------------------------------------
    # Status / errors
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # ------------------------------------------------------------------
    # Settings panel support (12 September 2026 addition)
    # ------------------------------------------------------------------

    def get_setting(self, section: str, key: str, default=None):
        """Read-only pass-through so the GUI can populate slider starting
        positions without reaching into self._settings directly."""
        with self._lock:
            return self._settings.get(section, key, default=default)

    def update_setting(self, section: str, key: str, value) -> None:
        """Persists a changed setting, and live-applies it if it's one of
        the values that's normally only read once at start() time rather
        than fresh every frame. Safe to call whether or not the engine is
        currently running (e.g. GUI settings changes before the camera
        has finished opening)."""
        with self._lock:
            self._settings.set(section, key, value)
            self._settings.save()

            multiplier = self._adaptive.click_radius if self._adaptive is not None else 1.0

            if section == "dwell_head" and self._dwell_head is not None:
                if key == "dwell_time_s":
                    self._dwell_head.dwell_time_s = value
                elif key == "cooldown_s":
                    self._dwell_head.cooldown_s = value
                elif key == "tolerance_radius_px":
                    self._dwell_head.base_tolerance_radius_px = value
                    self._dwell_head.tolerance_radius_px = value * multiplier

            elif section == "dwell_hand" and self._dwell_hand is not None:
                if key == "dwell_time_s":
                    self._dwell_hand.dwell_time_s = value
                elif key == "cooldown_s":
                    self._dwell_hand.cooldown_s = value
                elif key == "tolerance_radius_px":
                    self._dwell_hand.base_tolerance_radius_px = value
                    self._dwell_hand.tolerance_radius_px = value * multiplier

            # Everything else (head_tracking.sensitivity, dwell_gate_radius,
            # neutral_drift_rate, hand_tracking.edge_margin, etc.) is read
            # fresh via self._settings.get(...) every single frame already
            # -- the settings.set()+save() above is sufficient, no extra
            # live-apply branch needed.

    def get_session_history(self) -> list:
        """Returns this profile's real, persisted session-by-session
        adaptive readouts (oldest first) for the Profile tab's chart."""
        with self._lock:
            return self._session_history.get_entries()

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run_loop(self):
        last_fps_print = time.time()

        try:
            while True:
                with self._lock:
                    if self._quit_requested:
                        break
                    camera = self._camera

                success, frame = camera.read_frame()
                if not success or frame is None:
                    continue

                frame_h, frame_w = frame.shape[:2]

                with self._lock:
                    calibrating = self._calibration["in_progress"]
                    mode = self._mode

                try:
                    if calibrating:
                        self._process_calibration_frame(frame, frame_w, frame_h)
                    elif mode == MODE_HEAD:
                        self._process_head(frame, frame_w, frame_h)
                    elif mode == MODE_HAND:
                        self._process_hand(frame, frame_w, frame_h)
                except Exception as e:
                    # A single bad frame (e.g. a MediaPipe hiccup)
                    # shouldn't kill the whole tracking thread -- record
                    # it as the last error and keep going.
                    with self._lock:
                        self._status["last_error"] = f"Frame processing error: {e}"

                now = time.time()
                self._frame_times.append(now)
                fps = 0.0
                if len(self._frame_times) >= 2:
                    fps = (len(self._frame_times) - 1) / (self._frame_times[-1] - self._frame_times[0])
                if now - last_fps_print > 2.0:
                    print(f"[TrackingEngine] Loop FPS: {fps:.1f}")
                    last_fps_print = now

                with self._lock:
                    self._current_fps = fps
                    self._latest_frame = frame

                time.sleep(0.01)
        except Exception as e:
            with self._lock:
                self._status["last_error"] = f"Tracking thread crashed: {e}"
                self._running = False
            print(f"[TrackingEngine] Background thread crashed: {e}")

    def _process_calibration_frame(self, frame, frame_w, frame_h):
        """Non-blocking replacement for tracking/calibration.py's
        calibrate_neutral_position(): same math (average nose position
        over a still window), but as one step per already-read frame
        instead of its own blocking loop with its own camera reads and
        its own cv2 window."""
        nose_pos = self._face_engine.get_nose_position(frame)

        with self._lock:
            elapsed = time.time() - self._calibration_start
            if nose_pos is not None:
                self._calibration_samples.append(nose_pos)

            progress = min(1.0, elapsed / CALIBRATION_DURATION_S)
            self._calibration["progress"] = progress

            if progress >= 1.0:
                samples = self._calibration_samples
                if samples:
                    avg_x = sum(s[0] for s in samples) / len(samples)
                    avg_y = sum(s[1] for s in samples) / len(samples)
                    self._neutral = (avg_x, avg_y)
                    self._original_neutral = (avg_x, avg_y)
                    success = True
                else:
                    self._neutral = (0.5, 0.5)
                    self._original_neutral = (0.5, 0.5)
                    success = False
                self._calibration["in_progress"] = False
                self._calibration["done"] = True
                self._calibration["success"] = success

        remaining = max(0.0, CALIBRATION_DURATION_S - elapsed)
        cv2.putText(frame, "Calibrating -- hold still...", (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(frame, f"{remaining:0.1f}s", (20, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

    def _process_head(self, frame, frame_w, frame_h):
        """Copied unchanged (math-wise) from main.py's MODE_HEAD branch."""
        nose_pos = self._face_engine.get_nose_position(frame)
        if nose_pos is None:
            return

        with self._lock:
            neutral = self._neutral
            original_neutral = self._original_neutral

        smoothed_nose = self._head_filter.filter(*nose_pos)

        head_sensitivity = self._settings.get("head_tracking", "sensitivity", default=1.0)
        dx = smoothed_nose[0] - neutral[0]
        dy = smoothed_nose[1] - neutral[1]
        raw_x = int((0.5 + dx * head_sensitivity) * self._screen_w)
        raw_y = int((0.5 + dy * head_sensitivity) * self._screen_h)
        screen_x = max(2, min(self._screen_w - 3, raw_x))
        screen_y = max(2, min(self._screen_h - 3, raw_y))

        px, py = int(nose_pos[0] * frame_w), int(nose_pos[1] * frame_h)
        dwell_gate_radius = self._settings.get("head_tracking", "dwell_gate_radius", default=0.035)
        dist_from_neutral = (dx ** 2 + dy ** 2) ** 0.5

        if dist_from_neutral < dwell_gate_radius:
            self._dwell_head.reset()
            self._move_cursor(screen_x, screen_y)

            neutral_drift_rate = self._settings.get("head_tracking", "neutral_drift_rate", default=0.01)
            max_neutral_drift = self._settings.get("head_tracking", "max_neutral_drift", default=0.05)
            candidate_x = neutral[0] + neutral_drift_rate * (smoothed_nose[0] - neutral[0])
            candidate_y = neutral[1] + neutral_drift_rate * (smoothed_nose[1] - neutral[1])
            leash_dx = candidate_x - original_neutral[0]
            leash_dy = candidate_y - original_neutral[1]
            leash_dist = (leash_dx ** 2 + leash_dy ** 2) ** 0.5
            if leash_dist > max_neutral_drift:
                scale = max_neutral_drift / leash_dist
                candidate_x = original_neutral[0] + leash_dx * scale
                candidate_y = original_neutral[1] + leash_dy * scale

            with self._lock:
                self._neutral = (candidate_x, candidate_y)
        else:
            dwell_result = self._dwell_head.update(screen_x, screen_y)
            _draw_dwell_ring(frame, (px, py), dwell_result["progress"])
            if dwell_result["progress"] > DWELL_LOCK_FRACTION:
                move_x, move_y = dwell_result["anchor"]
            else:
                move_x, move_y = screen_x, screen_y
            self._move_cursor(move_x, move_y)
            if dwell_result["just_clicked"]:
                pyautogui.click()
                self._adaptive.record_click(screen_x, screen_y)
                # 12 Sept addition: push the freshly-recalculated
                # click_radius back into BOTH dwell controllers (one
                # shared AdaptiveSensitivity instance covers both modes,
                # by design -- see chat history).
                self._dwell_head.set_radius_multiplier(self._adaptive.click_radius)
                self._dwell_hand.set_radius_multiplier(self._adaptive.click_radius)

    def _process_hand(self, frame, frame_w, frame_h):
        """Copied unchanged (math-wise) from main.py's MODE_HAND branch."""
        landmarks = self._hand_engine.get_hand_landmarks(frame)
        now = time.time()

        if landmarks is not None:
            self._last_hand_landmarks = landmarks
            self._last_hand_seen_time = now
        elif (self._last_hand_landmarks is not None
              and (now - self._last_hand_seen_time) < HAND_DROPOUT_GRACE_S):
            landmarks = self._last_hand_landmarks
        else:
            self._last_hand_landmarks = None

        if landmarks is None:
            self._dwell_hand.reset()
            self._hand_filter.reset()
            return

        ix, iy = landmarks["index_tip"]
        tx, ty = landmarks["thumb_tip"]
        cv2.circle(frame, (int(tx * frame_w), int(ty * frame_h)), 6, (255, 0, 0), -1)

        edge_margin = self._settings.get("hand_tracking", "edge_margin", default=0.15)
        index_x = expand_range(ix, edge_margin)
        index_y = expand_range(iy, edge_margin)
        raw_screen_x = max(2, min(self._screen_w - 3, int(index_x * self._screen_w)))
        raw_screen_y = max(2, min(self._screen_h - 3, int(index_y * self._screen_h)))

        filtered_x, filtered_y = self._hand_filter.filter(raw_screen_x, raw_screen_y)
        screen_x = int(max(2, min(self._screen_w - 3, filtered_x)))
        screen_y = int(max(2, min(self._screen_h - 3, filtered_y)))

        px, py = int(ix * frame_w), int(iy * frame_h)
        dwell_result = self._dwell_hand.update(screen_x, screen_y)
        _draw_dwell_ring(frame, (px, py), dwell_result["progress"])
        if dwell_result["progress"] > DWELL_LOCK_FRACTION:
            move_x, move_y = dwell_result["anchor"]
        else:
            move_x, move_y = screen_x, screen_y
        self._move_cursor(move_x, move_y)
        if dwell_result["just_clicked"]:
            pyautogui.click()
            self._adaptive.record_click(screen_x, screen_y)
            # Same wire-up as _process_head above.
            self._dwell_head.set_radius_multiplier(self._adaptive.click_radius)
            self._dwell_hand.set_radius_multiplier(self._adaptive.click_radius)
