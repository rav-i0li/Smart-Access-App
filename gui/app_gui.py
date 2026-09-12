"""
gui/app_gui.py

REPLACES the previous gui/app_gui.py (that version was built against a
draft SmartAccessEngine class that has been superseded -- see
documentation/handoff_to_frontend_session.md if you're wondering why).
Goes in: <project root>/gui/app_gui.py.

This version is built directly against the real, current
backend/tracking_engine.py -- specifically the TrackingEngine class,
read in full before writing this file (never guessed from the handoff
summary or an earlier draft). backend/main.py is NOT imported here at
all; it stays the separate headless/testing entry point with its own
cv2 window and hotkeys. This file plus TrackingEngine are the real app.

Also applies the SmartAccess brand system (from Manus's design-tokens
doc) to the layout that was already working: the dark #1B3A4B sidebar,
#4ADE80 accent on the active mode, pill-shaped controls, 16px panel
radius, and the mascot next to the product name.

Two things NOT carried over from the Manus mockups, deliberately:
  - Custom-drawn window chrome (the mockups show both Mac- and
    Windows-style title bars, inconsistently, and neither should be
    built -- CustomTkinter gives you the real OS-native window frame
    for free; hand-drawing a fake one would be wasted effort). Only
    what's *inside* the window frame is styled here.
  - The "Switch" status field the mockups still show -- leftover from
    before Switch Access was cut, already dropped from this file.

One open decision, not resolved here -- see FONT_FAMILY below.

Threading model (important if you touch this file):
  - Tkinter/CustomTkinter's mainloop() must run on the MAIN thread --
    a hard Tkinter requirement, not a style choice.
  - TrackingEngine() construction is cheap, but engine.start() blocks
    briefly (opens the camera, loads the MediaPipe models) before
    returning -- done on a background thread here so the window shows
    immediately. Calibration itself is NOT blocking on TrackingEngine's
    side (it runs progressively inside the engine's own loop thread),
    so the GUI just polls get_calibration_status() on its normal
    update tick instead of needing its own thread for it.
  - The global hotkey listener (pynput) fires callbacks on ITS OWN
    thread. Every hotkey handler hands off to self.after(0, ...) before
    touching any widget -- Tkinter widgets aren't safe to touch from a
    non-main thread directly.

Requires: customtkinter, pillow, matplotlib (pip install customtkinter
pillow matplotlib) -- pillow is usually pulled in already since
customtkinter depends on it. matplotlib is new as of the 12 September
2026 Settings/Profile panel addition below -- needed for the Profile
tab's real session-history chart.

12 September 2026 addition -- Settings panel (gear icon in the sidebar,
opens a modal with two tabs):
  - "Settings" tab: a handful of sliders (head sensitivity, dwell time,
    per-mode click tolerance) that read their starting value from
    engine.get_setting() and write back via engine.update_setting() on
    release (not on every drag tick, to avoid hammering disk writes).
    Camera selection is intentionally NOT here yet -- it needs a change
    to tracking/camera_manager.py (picking a device index) that hasn't
    been reviewed, so a real dropdown isn't built rather than faking one
    that does nothing. See the disabled placeholder row below for why.
  - "Profile" tab: a real chart of this profile's session history
    (engine.get_session_history()), not a mockup. Honestly handles the
    case where there's only 0-1 real sessions so far (a fresh machine,
    like tonight) by showing a plain explanatory message instead of a
    misleadingly confident one-point "trend" line.
"""

import os
import sys
import platform

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import threading
import tkinter as tk

import cv2
import customtkinter as ctk
from PIL import Image, ImageTk
from pynput import keyboard

import matplotlib
matplotlib.use("TkAgg")  # must be set before importing pyplot/backends
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from backend.tracking_engine import TrackingEngine, MODE_HEAD, MODE_HAND
from voice.sage_voice import SageVoiceController

MODE_LABELS = {
    MODE_HEAD: "Head Tracking",
    MODE_HAND: "Hand Tracking",
}
MODE_ORDER = [MODE_HEAD, MODE_HAND]

PREVIEW_W, PREVIEW_H = 640, 480

# ---- SmartAccess brand tokens, from the Manus design-tokens doc ----
COLOR_PRIMARY = "#1B3A4B"        # sidebar
COLOR_ACCENT = "#4ADE80"         # active mode, preview-toggle-on, dwell ring (drawn by the engine itself)
COLOR_NEUTRAL_LIGHT = "#FAF8F5"  # main window / status-strip surface
COLOR_NEUTRAL_DARK = "#232323"   # video panel + Quit button
COLOR_SIDEBAR_MUTED = "#9FB4C2"  # muted light-blue-gray text/borders on the dark sidebar
COLOR_STATUS_MUTED = "#8A8F94"   # muted label color on the light status strip
CORNER_RADIUS_PANEL = 16         # app-shell-internal panels: video frame, status strip
CORNER_RADIUS_PILL = 999         # CTk treats radius >= half the widget's height as fully rounded

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
MASCOT_PATH = os.path.join(ASSETS_DIR, "mascot.jpeg")
PROFILE_MOCKUP_PATH = os.path.join(ASSETS_DIR, "profile_analytics_mockup.png")

# OPEN DECISION (see handoff_to_frontend_session.md -- flagged there as
# something to ask Matthew, not default silently): the token doc
# specifies Manrope throughout. Getting a real Manrope look means
# bundling a .ttf and registering it at runtime, which is an extra
# PyInstaller packaging step this close to the deadline. This currently
# uses a per-platform system-font fallback until that's confirmed.
# To switch to bundled Manrope later: drop Manrope-Regular.ttf and
# Manrope-Bold.ttf into gui/assets/fonts/, change FONT_FAMILY below to
# "Manrope", and load the files at startup (e.g. via
# ctypes.windll.gdi32.AddFontResourceExW on Windows, or a Core Text
# call on Mac) before the first CTkFont() is created.
#
# 12 September 2026 fix: "Segoe UI" is a Windows-only system font and
# does not exist on macOS at all. SmartAccess must run on both, and the
# demo specifically runs on Mac -- so this is chosen per-platform rather
# than hardcoded, using each OS's own default rounded-humanist-ish
# system sans as the safe fallback.
if platform.system() == "Darwin":
    FONT_FAMILY = "Helvetica Neue"
elif platform.system() == "Windows":
    FONT_FAMILY = "Segoe UI"
else:
    FONT_FAMILY = "Arial"


def _make_font(size, weight="normal"):
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.engine = None
        self._current_photo = None  # keep a reference -- Tkinter drops
                                     # images with no live Python reference
        self.hotkey_listener = None
        self._shutting_down = False
        self._was_calibrating = False  # edge-detects calibration finishing
        self.settings_window = None  # tracks the single open instance, if any
        self.sage = None  # SageVoiceController instance, created lazily on first enable

        self.title("SmartAccess")
        self.geometry("1000x650")
        self.minsize(820, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()

        # 13 September 2026 addition: don't open the camera immediately.
        # Show a Start screen first; engine.start() (which blocks briefly
        # for camera + MediaPipe model load, hence still off the main
        # thread) only fires once the user clicks Start. That means the
        # "position your face" screen that follows can show REAL
        # calibration progress from the moment it appears, instead of
        # racing an engine boot the user never asked for yet.
        self._start_overlay = None
        self._start_overlay_content = None
        self._build_start_overlay()

    # ---------------------------------------------------------------
    # Layout
    # ---------------------------------------------------------------

    def _load_mascot_image(self, size):
        """12 September 2026 fix: mascot.jpeg is a wide image with the
        character small and centered against a lot of padding. Resizing
        that whole wide canvas straight to a square (the previous
        behavior) stretched the character and left mostly padding
        visible at sidebar-icon size. Center-cropping to a square first
        -- taking a square region the height of the source image, centered
        horizontally -- captures the character without distortion, since
        Manus consistently generates this mascot centered in its frame."""
        try:
            pil_image = Image.open(MASCOT_PATH)
            w, h = pil_image.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            square = pil_image.crop((left, top, left + side, top + side))
            return ctk.CTkImage(light_image=square, dark_image=square, size=size)
        except Exception as e:
            print(f"[gui] Could not load mascot image ({MASCOT_PATH}): {e}")
            return None

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=COLOR_PRIMARY)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        title_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        title_row.pack(padx=20, pady=(24, 0), anchor="w", fill="x")

        mascot_image = self._load_mascot_image(size=(36, 36))
        if mascot_image is not None:
            ctk.CTkLabel(title_row, text="", image=mascot_image).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(
            title_row, text="SmartAccess", font=_make_font(22, "bold"), text_color="#FFFFFF",
        ).pack(side="left")

        ctk.CTkLabel(
            sidebar, text="Hands-free computer access",
            font=_make_font(12), text_color=COLOR_SIDEBAR_MUTED,
        ).pack(padx=20, pady=(4, 24), anchor="w")

        ctk.CTkLabel(
            sidebar, text="MODE", font=_make_font(11, "bold"), text_color=COLOR_SIDEBAR_MUTED,
        ).pack(padx=20, pady=(0, 8), anchor="w")

        self.mode_buttons = {}
        for mode in MODE_ORDER:
            btn = ctk.CTkButton(
                sidebar, text=MODE_LABELS[mode], height=48, corner_radius=CORNER_RADIUS_PILL,
                font=_make_font(14, "bold"), state="disabled",
                fg_color="transparent", border_width=2, border_color=COLOR_SIDEBAR_MUTED,
                text_color="#FFFFFF", hover_color="#24485C",
                command=self._make_mode_handler(mode),
            )
            btn.pack(padx=20, pady=6, fill="x")
            self.mode_buttons[mode] = btn

        ctk.CTkFrame(sidebar, height=1, fg_color=COLOR_SIDEBAR_MUTED).pack(
            padx=20, pady=16, fill="x"
        )

        self.preview_visible_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(
            sidebar, text="Show camera preview", variable=self.preview_visible_var,
            onvalue=True, offvalue=False, font=_make_font(13),
            text_color="#FFFFFF", progress_color=COLOR_ACCENT,
            fg_color="#12303F", button_color="#FFFFFF", button_hover_color="#EAEAEA",
        ).pack(padx=20, pady=6, anchor="w")

        # 13 September 2026 addition: SAGE voice commands, off by default
        # (starting a microphone stream unasked isn't something to do
        # silently). Lazily creates the SageVoiceController on first
        # enable, since that's also the point where we know Vosk's model
        # is actually present -- see voice/sage_voice.py.
        self.voice_enabled_var = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            sidebar, text="Voice commands (SAGE)", variable=self.voice_enabled_var,
            onvalue=True, offvalue=False, font=_make_font(13),
            text_color="#FFFFFF", progress_color=COLOR_ACCENT,
            fg_color="#12303F", button_color="#FFFFFF", button_hover_color="#EAEAEA",
            command=self._on_voice_toggle,
        ).pack(padx=20, pady=(0, 4), anchor="w")
        self.voice_status_label = ctk.CTkLabel(
            sidebar, text="", font=_make_font(11), text_color=COLOR_SIDEBAR_MUTED,
        )
        self.voice_status_label.pack(padx=20, pady=(0, 6), anchor="w")

        # 12 September 2026 addition: gear icon opens the Settings modal.
        # Unicode glyph instead of an image asset -- no new PNG needed,
        # renders consistently across Mac/Windows system fonts.
        ctk.CTkButton(
            sidebar, text="\u2699  Settings", height=40, corner_radius=CORNER_RADIUS_PILL,
            font=_make_font(13), fg_color="transparent", border_width=2,
            border_color=COLOR_SIDEBAR_MUTED, text_color="#FFFFFF", hover_color="#24485C",
            command=self.open_settings_window,
        ).pack(padx=20, pady=(0, 12), fill="x")

        self.recalibrate_button = ctk.CTkButton(
            sidebar, text="Recalibrate Head Tracking", height=40, corner_radius=CORNER_RADIUS_PILL,
            font=_make_font(13), state="disabled",
            fg_color="transparent", border_width=2, border_color=COLOR_SIDEBAR_MUTED,
            text_color="#FFFFFF", hover_color="#24485C",
            command=self.on_recalibrate,
        )
        self.recalibrate_button.pack(padx=20, pady=(12, 0), fill="x")
        self.recalibrate_hint = ctk.CTkLabel(
            sidebar, text="", font=_make_font(11), text_color=COLOR_SIDEBAR_MUTED,
        )
        self.recalibrate_hint.pack(padx=20, pady=(4, 0), anchor="w")

        ctk.CTkButton(
            sidebar, text="Quit", height=40, corner_radius=CORNER_RADIUS_PILL,
            font=_make_font(13, "bold"), fg_color=COLOR_NEUTRAL_DARK, hover_color="#3A3A3A",
            text_color="#FFFFFF",
            command=self.on_close,
        ).pack(padx=20, pady=20, side="bottom", fill="x")

    def _build_main_area(self):
        main_area = ctk.CTkFrame(self, fg_color=COLOR_NEUTRAL_LIGHT, corner_radius=0)
        main_area.grid(row=0, column=1, sticky="nsew", padx=(0, 12), pady=12)
        main_area.grid_rowconfigure(0, weight=1)
        main_area.grid_columnconfigure(0, weight=1)

        video_frame = ctk.CTkFrame(
            main_area, fg_color=COLOR_NEUTRAL_DARK, corner_radius=CORNER_RADIUS_PANEL,
        )
        video_frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))

        # 12 September 2026 fix: this was a ctk.CTkLabel with a CTkImage,
        # then a raw PhotoImage -- both threw the identical
        # "image doesn't exist" TclError under continuous 15-30Hz
        # replacement. CTkLabel/CTkImage's HighDPI-scaling bookkeeping is
        # built for an icon that changes rarely, not continuous video --
        # that's the actual root cause, not which image class was used.
        # A plain tk.Label with a plain PhotoImage is the standard,
        # battle-tested pattern for real-time video in any Tkinter app
        # for exactly this reason. Styled manually since it doesn't get
        # CTk's automatic theming.
        self.video_label = tk.Label(
            video_frame, text="Starting SmartAccess...",
            fg="#FFFFFF", bg=COLOR_NEUTRAL_DARK,
            font=_make_font(14), borderwidth=0, highlightthickness=0,
        )
        self.video_label.pack(expand=True)

        status_bar = ctk.CTkFrame(main_area, fg_color="#FFFFFF", corner_radius=CORNER_RADIUS_PANEL)
        status_bar.grid(row=1, column=0, sticky="ew", padx=12, pady=(6, 12))
        for i in range(4):
            status_bar.grid_columnconfigure(i, weight=1)

        self.mode_value_label = self._add_status_field(status_bar, 0, "Mode")
        self.fps_value_label = self._add_status_field(status_bar, 1, "FPS")
        self.miss_rate_value_label = self._add_status_field(status_bar, 2, "Miss rate")
        self.click_radius_value_label = self._add_status_field(status_bar, 3, "Click radius")

    def _add_status_field(self, parent, column, label_text):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=0, column=column, padx=16, pady=16, sticky="ew")
        ctk.CTkLabel(
            frame, text=label_text, font=_make_font(11), text_color=COLOR_STATUS_MUTED,
        ).pack(anchor="w")
        value_label = ctk.CTkLabel(
            frame, text="--", font=_make_font(16, "bold"), text_color=COLOR_PRIMARY,
        )
        value_label.pack(anchor="w")
        return value_label

    # ---------------------------------------------------------------
    # Start screen (13 September 2026 addition)
    #
    # A full-window overlay, placed on top of the sidebar/main area
    # that are already built underneath. Three states, swapped in
    # place by clearing and re-populating self._start_overlay_content:
    #   1. Start menu -- just a Start button.
    #   2. "Position your face" -- a purely decorative face-position
    #      guide (per Matthew: no functional tie to the real tracking
    #      math, just cosmetic) plus a progress bar/label driven by
    #      the engine's REAL get_calibration_status() poll, via
    #      _show_calibrating() below -- not a guessed timer.
    #   3. Fatal-error fallback, if the camera/engine fails to start
    #      while the user is still on this screen (see _show_fatal_error).
    # Torn down entirely in _on_calibration_finished() once the first
    # real calibration completes, revealing the already-built app
    # underneath.
    # ---------------------------------------------------------------

    def _build_start_overlay(self):
        overlay = ctk.CTkFrame(self, fg_color=COLOR_PRIMARY, corner_radius=0)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        overlay.lift()
        self._start_overlay = overlay

        self._start_overlay_content = ctk.CTkFrame(overlay, fg_color="transparent")
        self._start_overlay_content.place(relx=0.5, rely=0.5, anchor="center")

        self._render_start_menu()

    def _clear_overlay_content(self):
        for widget in self._start_overlay_content.winfo_children():
            widget.destroy()

    def _render_start_menu(self):
        self._clear_overlay_content()
        parent = self._start_overlay_content

        mascot_image = self._load_mascot_image(size=(96, 96))
        if mascot_image is not None:
            ctk.CTkLabel(parent, text="", image=mascot_image).pack(pady=(0, 16))

        ctk.CTkLabel(
            parent, text="SmartAccess", font=_make_font(32, "bold"), text_color="#FFFFFF",
        ).pack()
        ctk.CTkLabel(
            parent, text="Hands-free computer access",
            font=_make_font(14), text_color=COLOR_SIDEBAR_MUTED,
        ).pack(pady=(4, 32))

        self._start_button = ctk.CTkButton(
            parent, text="Start", width=200, height=52, corner_radius=CORNER_RADIUS_PILL,
            font=_make_font(16, "bold"), fg_color=COLOR_ACCENT, text_color=COLOR_PRIMARY,
            hover_color="#3FCB70", command=self.on_click_start,
        )
        self._start_button.pack()

    def on_click_start(self):
        self._start_button.configure(state="disabled", text="Starting...")
        self._render_positioning_screen()
        # engine.start() blocks briefly (camera + MediaPipe model load) --
        # do it off the main thread so the UI stays responsive.
        threading.Thread(target=self._start_engine, daemon=True).start()

    def _render_positioning_screen(self):
        self._clear_overlay_content()
        parent = self._start_overlay_content

        ctk.CTkLabel(
            parent, text="Position your face",
            font=_make_font(24, "bold"), text_color="#FFFFFF",
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            parent, text="Center your face in the guide and hold still",
            font=_make_font(13), text_color=COLOR_SIDEBAR_MUTED,
        ).pack(pady=(0, 24))

        # Purely decorative face-position guide -- a dashed oval with
        # camera-style corner brackets. This has no functional tie to
        # the real face tracking (the engine reads the nose position
        # from the raw camera frame regardless of where the user's face
        # actually falls on this canvas) -- it exists to make the
        # calibration wait feel purposeful, per Matthew's explicit note
        # that it's cosmetic only.
        canvas = tk.Canvas(
            parent, width=220, height=220, bg=COLOR_PRIMARY, highlightthickness=0,
        )
        canvas.pack(pady=(0, 24))
        canvas.create_oval(30, 20, 190, 200, outline=COLOR_ACCENT, width=3, dash=(6, 4))
        bracket = 24
        for x0, y0, dx, dy in [(10, 10, 1, 1), (210, 10, -1, 1), (10, 210, 1, -1), (210, 210, -1, -1)]:
            canvas.create_line(x0, y0, x0 + bracket * dx, y0, fill=COLOR_ACCENT, width=3)
            canvas.create_line(x0, y0, x0, y0 + bracket * dy, fill=COLOR_ACCENT, width=3)

        self._calibration_progress_label = ctk.CTkLabel(
            parent, text="Starting camera...", font=_make_font(14), text_color="#FFFFFF",
        )
        self._calibration_progress_label.pack()
        self._calibration_progress_bar = ctk.CTkProgressBar(
            parent, width=260, progress_color=COLOR_ACCENT,
        )
        self._calibration_progress_bar.set(0.0)
        self._calibration_progress_bar.pack(pady=(8, 0))

    def _render_start_error(self, message):
        """13 September 2026 addition: if the engine fails to start while
        the user is still on the Start/positioning screen, show the real
        error right there instead of leaving them stuck on a "Starting
        camera..." progress bar that will never move -- update_loop()
        (which would otherwise reveal this) never even starts on a
        failed boot, so nothing else would ever surface it."""
        self._clear_overlay_content()
        parent = self._start_overlay_content

        ctk.CTkLabel(
            parent, text="Couldn't start SmartAccess",
            font=_make_font(20, "bold"), text_color="#FF6B6B",
        ).pack(pady=(0, 8))
        ctk.CTkLabel(
            parent, text=message, font=_make_font(13), text_color="#FFFFFF",
            wraplength=320, justify="center",
        ).pack(pady=(0, 24))
        ctk.CTkButton(
            parent, text="Quit", width=160, height=44, corner_radius=CORNER_RADIUS_PILL,
            font=_make_font(14, "bold"), fg_color=COLOR_NEUTRAL_DARK, hover_color="#3A3A3A",
            command=self.on_close,
        ).pack()

    # ---------------------------------------------------------------
    # Engine lifecycle
    # ---------------------------------------------------------------

    def _start_engine(self):
        try:
            engine = TrackingEngine(settings_profile="default")
        except Exception as e:
            self.after(0, lambda: self._show_fatal_error(f"Couldn't initialize: {e}"))
            return

        success = engine.start()
        if not success:
            error = engine.get_status().get("last_error") or "Unknown startup error."
            self.after(0, lambda: self._show_fatal_error(error))
            return

        self.engine = engine
        self.after(0, self._on_engine_ready)

    def _on_engine_ready(self):
        self._setup_hotkeys()
        self.update_loop()

    def _show_fatal_error(self, message):
        self.video_label.configure(
            image="", text=f"SmartAccess couldn't start:\n{message}", fg="#FF6B6B",
        )
        for btn in self.mode_buttons.values():
            btn.configure(state="disabled")
        self.recalibrate_button.configure(state="disabled")

        if self._start_overlay is not None and self._start_overlay.winfo_exists():
            self._render_start_error(message)

    # ---------------------------------------------------------------
    # Main update tick: calibration status, live preview, status strip.
    # Runs on the main/Tk thread via self.after -- never touches the
    # engine's internals directly, only its public methods.
    # ---------------------------------------------------------------

    def update_loop(self):
        if self.engine is None or self._shutting_down:
            return

        if not self.engine.is_running():
            error = self.engine.get_status().get("last_error") or "Tracking stopped unexpectedly."
            self._show_fatal_error(error)
            return

        # 12 September 2026 fix: state tracking is now updated FIRST,
        # unconditionally, before any rendering is attempted. Previously
        # self._was_calibrating was only set as the line right after a
        # rendering call -- if that rendering call threw, this line
        # never ran, permanently desyncing the GUI's calibration-finished
        # detection from the engine's real state even after calibration
        # genuinely completed on the backend. That was the actual cause
        # of Recalibrate hard-locking the screen. Rendering calls are
        # each wrapped individually now too, so one failing render can
        # never block a sibling render or the status-label update.
        calibration = self.engine.get_calibration_status()
        in_progress = calibration["in_progress"]
        just_finished = (not in_progress) and self._was_calibrating
        self._was_calibrating = in_progress

        if in_progress:
            try:
                self._show_calibrating(calibration["progress"])
            except Exception as e:
                print(f"[gui] calibration display error (skipped): {e}")
        else:
            if just_finished:
                try:
                    self._on_calibration_finished(calibration["success"])
                except Exception as e:
                    print(f"[gui] calibration-finished display error (skipped): {e}")

            # Throttle the expensive video render to every other tick,
            # ~15Hz, while status labels below still refresh every tick.
            self._video_tick = not getattr(self, "_video_tick", False)
            if self._video_tick:
                try:
                    self._show_live_preview()
                except Exception as e:
                    print(f"[gui] video preview error (skipped): {e}")

        try:
            self._update_status_labels()
        except Exception as e:
            print(f"[gui] status label error (skipped): {e}")

        self.after(33, self.update_loop)  # ~30 Hz UI refresh

    def _show_calibrating(self, progress):
        for btn in self.mode_buttons.values():
            btn.configure(state="disabled")
        self.recalibrate_button.configure(state="disabled", text="Calibrating...")
        percent = int(progress * 100)
        self.video_label.configure(
            image="", text=f"Calibrating -- hold still and look at the screen...\n{percent}%",
            fg="#FFFFFF",
        )
        self._current_photo = None

        # 13 September 2026 addition: also drive the Start screen's
        # "position your face" progress bar/label, if that's still up --
        # true only for the very first calibration right after Start,
        # since the overlay is gone by the time a later manual
        # Recalibrate can happen.
        if self._start_overlay is not None and self._start_overlay.winfo_exists():
            self._calibration_progress_bar.set(progress)
            self._calibration_progress_label.configure(text=f"Hold still... {percent}%")

    def _on_calibration_finished(self, success):
        for btn in self.mode_buttons.values():
            btn.configure(state="normal")
        self.recalibrate_button.configure(state="normal", text="Recalibrate Head Tracking")
        self.recalibrate_hint.configure(
            text="Calibrated." if success else "No face detected -- try again."
        )
        self.after(2500, lambda: self.recalibrate_hint.configure(text=""))

        # 13 September 2026 addition: this is the first real calibration
        # (right after Start) if the overlay is still up -- tear it down
        # now, revealing the already-built sidebar/main area underneath.
        # Proceeds either way, success or not, same as a failed manual
        # Recalibrate already did before this change -- the hint above
        # tells the user to try again rather than blocking them here.
        if self._start_overlay is not None and self._start_overlay.winfo_exists():
            self._start_overlay.destroy()
            self._start_overlay = None

    def _show_live_preview(self):
        """Plain tk.Label + plain PhotoImage -- see the video_label
        construction comment above for why CTkLabel/CTkImage don't work
        here. The image reference is held in two places (self and the
        widget itself via .image) -- the standard idiom every
        OpenCV+Tkinter tutorial uses, since Tkinter drops a PhotoImage
        with no live Python reference."""
        if self.preview_visible_var.get():
            frame = self.engine.get_current_frame()
            if frame is not None:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_image = Image.fromarray(rgb_frame).resize((PREVIEW_W, PREVIEW_H))
                photo = ImageTk.PhotoImage(image=pil_image)
                self.video_label.configure(image=photo, text="")
                self.video_label.image = photo  # belt-and-suspenders reference
                self._current_photo = photo
        else:
            if self._current_photo is not None:
                self.video_label.configure(image="", text="Preview hidden", fg="#FFFFFF")
                self.video_label.image = None
                self._current_photo = None

    def _update_status_labels(self):
        mode = self.engine.get_mode()
        stats = self.engine.get_adaptive_stats()
        self.mode_value_label.configure(text=MODE_LABELS[mode])
        self.fps_value_label.configure(text=f"{self.engine.get_fps():.0f}")
        self.miss_rate_value_label.configure(text=f"{stats['miss_rate'] * 100:.0f}%")
        self.click_radius_value_label.configure(text=f"{stats['click_radius']:.2f}")
        self._highlight_active_mode(mode)

    # ---------------------------------------------------------------
    # Mode switching
    # ---------------------------------------------------------------

    def _make_mode_handler(self, mode):
        def handler():
            if self.engine is not None:
                self.engine.set_mode(mode)
                self._highlight_active_mode(mode)
        return handler

    def _highlight_active_mode(self, active_mode):
        for mode, btn in self.mode_buttons.items():
            if mode == active_mode:
                btn.configure(
                    fg_color=COLOR_ACCENT, text_color=COLOR_PRIMARY,
                    border_width=0, hover_color=COLOR_ACCENT,
                )
            else:
                btn.configure(
                    fg_color="transparent", text_color="#FFFFFF",
                    border_width=2, border_color=COLOR_SIDEBAR_MUTED, hover_color="#24485C",
                )

    # ---------------------------------------------------------------
    # Recalibrate -- calibrate() is non-blocking on TrackingEngine, so
    # this just kicks it off; update_loop()'s calibration-status poll
    # handles disabling controls, showing progress, and re-enabling
    # everything once it's done.
    # ---------------------------------------------------------------

    def on_recalibrate(self):
        if self.engine is not None:
            self.engine.calibrate()

    # ---------------------------------------------------------------
    # Settings panel (12 September 2026 addition)
    # ---------------------------------------------------------------

    def open_settings_window(self):
        if self.engine is None:
            return  # engine still starting up -- nothing to configure yet
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus()
            return
        self.settings_window = SettingsWindow(self, self.engine)

    # ---------------------------------------------------------------
    # Global hotkeys -- fire on the pynput listener thread, so every
    # handler hands off to self.after(0, ...) before touching any
    # widget or calling into the engine's GUI-facing state.
    # ---------------------------------------------------------------

    def _setup_hotkeys(self):
        def relay(fn):
            return lambda: self.after(0, fn)

        self.hotkey_listener = keyboard.GlobalHotKeys({
            "<ctrl>+<alt>+h": relay(lambda: self._make_mode_handler(MODE_HEAD)()),
            "<ctrl>+<alt>+n": relay(lambda: self._make_mode_handler(MODE_HAND)()),
            "<ctrl>+<alt>+v": relay(self._hotkey_toggle_preview),
            "<ctrl>+<alt>+q": relay(self.on_close),
        })
        self.hotkey_listener.start()

    def _hotkey_toggle_preview(self):
        self.preview_visible_var.set(not self.preview_visible_var.get())

    # ---------------------------------------------------------------
    # SAGE voice commands (13 September 2026 addition)
    # ---------------------------------------------------------------

    def _on_voice_toggle(self):
        if self.voice_enabled_var.get():
            if self.engine is None:
                # Engine not up yet (still on the Start/calibration
                # screen) -- nothing to wire the voice controller to.
                self.voice_enabled_var.set(False)
                return
            if self.sage is None:
                try:
                    self.sage = SageVoiceController(
                        self.engine,
                        on_status=self._set_voice_status,
                        on_quit=self._handle_sage_quit,
                    )
                except Exception as e:
                    print(f"[gui] Could not set up SAGE voice: {e}")
                    self.voice_enabled_var.set(False)
                    self.voice_status_label.configure(text="Voice setup failed -- see console")
                    return
            self.sage.start()
        else:
            if self.sage is not None:
                self.sage.stop()
            self.voice_status_label.configure(text="")

    def _set_voice_status(self, message):
        # Called from SageVoiceController's listener thread -- hop back
        # to the main thread before touching any widget, same rule this
        # file already applies to pynput hotkeys.
        self.after(0, lambda: self.voice_status_label.configure(text=message))

    def _handle_sage_quit(self):
        # 13 September 2026 addition. Called from SageVoiceController's
        # listener thread ONLY after sage_quit.mp3 (+ echo tail) has
        # fully finished playing -- see on_quit's docstring in
        # sage_voice.py. Same main-thread-hop rule as _set_voice_status:
        # on_close() touches widgets (and the engine/hotkey listener),
        # so it can't run directly on the listener thread.
        self.after(0, self.on_close)

    # ---------------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------------

    def on_close(self):
        if self._shutting_down:
            return
        self._shutting_down = True

        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()

        if self.sage is not None:
            self.sage.stop()

        def _stop_and_destroy():
            if self.engine is not None:
                self.engine.stop()
            self.after(0, self.destroy)

        threading.Thread(target=_stop_and_destroy, daemon=True).start()


# =====================================================================
# Settings panel -- 12 September 2026 addition
# =====================================================================

# (section, key, default, min, max, step, label) -- one tuple per slider.
# "step" is only used to round the displayed value; CTkSlider itself is
# continuous. Kept as a flat list rather than nested dicts so adding a
# new slider later is a one-line addition, not a schema change.
SLIDER_SPECS = [
    ("head_tracking", "sensitivity", 1.0, 0.5, 2.0, 0.05, "Head Sensitivity"),
    ("dwell_head", "dwell_time_s", 0.5, 0.2, 1.5, 0.05, "Dwell Time (seconds)"),
    ("dwell_head", "tolerance_radius_px", 35, 10, 80, 1, "Head Click Tolerance (px)"),
    ("dwell_hand", "tolerance_radius_px", 20, 10, 80, 1, "Hand Click Tolerance (px)"),
]


class SettingsWindow(ctk.CTkToplevel):
    """Modal-ish settings window (not a strict OS modal -- CTkToplevel
    doesn't force that, and nothing here needs it, since the main window
    stays fully functional, including tracking, while this is open)."""

    def __init__(self, master, engine: TrackingEngine):
        super().__init__(master)
        self.engine = engine

        self.title("SmartAccess Settings")
        self.geometry("700x760")
        self.minsize(620, 640)
        # Keep this window on top of the main app window without fully
        # locking input to it -- simplest reliable CTkToplevel pattern.
        self.transient(master)

        tabs = ctk.CTkTabview(self, corner_radius=CORNER_RADIUS_PANEL)
        tabs.pack(fill="both", expand=True, padx=12, pady=12)
        settings_tab = tabs.add("Settings")
        profile_tab = tabs.add("Profile")

        self._build_settings_tab(settings_tab)
        self._build_profile_tab(profile_tab)

    # -----------------------------------------------------------------
    # Settings tab
    # -----------------------------------------------------------------

    def _build_settings_tab(self, parent):
        for section, key, default, lo, hi, step, label in SLIDER_SPECS:
            self._add_slider_row(parent, section, key, default, lo, hi, step, label)

        ctk.CTkFrame(parent, height=1, fg_color=COLOR_SIDEBAR_MUTED).pack(
            fill="x", padx=4, pady=(12, 8)
        )

        # Camera selection is deliberately a disabled placeholder, not a
        # working control -- see the docstring at the top of this file
        # for why (tracking/camera_manager.py hasn't been reviewed for
        # device-index selection support yet, and this file's own rule is
        # never to guess at another module's contents before wiring
        # something to it).
        ctk.CTkLabel(
            parent, text="Camera Selection", font=_make_font(12, "bold"),
            text_color=COLOR_STATUS_MUTED,
        ).pack(anchor="w", padx=4, pady=(0, 2))
        ctk.CTkOptionMenu(
            parent, values=["Default camera"], state="disabled",
        ).pack(fill="x", padx=4, pady=(0, 2))
        ctk.CTkLabel(
            parent, text="Coming soon -- needs a small camera_manager.py update first.",
            font=_make_font(10), text_color=COLOR_STATUS_MUTED,
        ).pack(anchor="w", padx=4)

    def _add_slider_row(self, parent, section, key, default, lo, hi, step, label):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=4, pady=6)

        header = ctk.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text=label, font=_make_font(13), text_color=COLOR_PRIMARY,
        ).pack(side="left")
        value_label = ctk.CTkLabel(
            header, text="", font=_make_font(13, "bold"), text_color=COLOR_PRIMARY,
        )
        value_label.pack(side="right")

        current_value = self.engine.get_setting(section, key, default=default)

        def format_value(v):
            return f"{v:.2f}" if step < 1 else f"{int(round(v))}"

        value_label.configure(text=format_value(current_value))

        # 13 September 2026 fix: sliders visually moved and the value
        # label updated, but nothing ever reached the engine -- the
        # write was wired through slider.bind("<ButtonRelease-1>", ...)
        # below, but CTkSlider is a composite widget: mouse events are
        # handled by an internal canvas sub-widget, not the outer frame
        # this bind() was attached to, so that handler never fired on
        # any platform. CTkSlider's own `command=` callback (used below
        # for the label) is the only mouse-drag hook that's actually
        # reliable, so the engine write now happens there too --
        # debounced 150ms after the last drag tick (a timer that keeps
        # getting cancelled and re-armed on every tick) so a fast drag
        # across the whole slider fires one write, not dozens.
        #
        # 13 September 2026, second pass: that fix alone wasn't enough
        # (confirmed by testing against the real backend/tracking_engine.py,
        # whose update_setting()/get_setting() are correctly implemented --
        # this isn't a backend problem). Rather than guess again, this now
        # prints at every stage so the actual failure point shows up in
        # the terminal you launched `python gui/app_gui.py` from. Once
        # it's confirmed working, these four print()s can come back out.
        pending_after_id = {"id": None}

        def apply_setting(section, key, v):
            print(f"[settings-debug] TIMER FIRED -- calling engine.update_setting({section!r}, {key!r}, {v})")
            self.engine.update_setting(section, key, v)
            readback = self.engine.get_setting(section, key, default=None)
            print(f"[settings-debug] engine.get_setting({section!r}, {key!r}) now returns: {readback}")
            pending_after_id["id"] = None

        def on_drag(v, section=section, key=key):
            print(f"[settings-debug] on_drag fired: {section}.{key} = {v}")
            value_label.configure(text=format_value(v))
            if pending_after_id["id"] is not None:
                try:
                    self.after_cancel(pending_after_id["id"])
                except Exception as e:
                    print(f"[settings-debug] after_cancel raised (harmless if timer already fired): {e}")
            pending_after_id["id"] = self.after(150, lambda: apply_setting(section, key, v))
            print(f"[settings-debug] debounce timer armed, id={pending_after_id['id']}")

        slider = ctk.CTkSlider(
            row, from_=lo, to=hi, number_of_steps=int((hi - lo) / step),
            progress_color=COLOR_ACCENT, button_color=COLOR_PRIMARY,
            button_hover_color="#12303F",
            command=on_drag,
        )
        slider.set(current_value)
        slider.pack(fill="x", pady=(4, 0))


    # -----------------------------------------------------------------
    # Profile tab
    # -----------------------------------------------------------------

    def _load_profile_mockup_image(self, max_width=580):
        """Illustrative-only placeholder for the empty-state below: a
        generated mockup of what the real chart looks like once enough
        sessions exist, not real user data. Unlike _load_mascot_image,
        this does NOT center-crop to a square -- the mockup is a wide
        dashboard illustration (1350x825 source), so cropping it would
        cut off real chart content. Scaled down to max_width, preserving
        aspect ratio, since the source is much wider than this window."""
        try:
            pil_image = Image.open(PROFILE_MOCKUP_PATH)
            w, h = pil_image.size
            scale = max_width / w
            size = (max_width, round(h * scale))
            return ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=size)
        except Exception as e:
            print(f"[gui] Could not load profile mockup image ({PROFILE_MOCKUP_PATH}): {e}")
            return None

    def _build_profile_tab(self, parent):
        entries = self.engine.get_session_history()

        # 13 September 2026 fix: the mockup was only ever shown inside the
        # "<2 real sessions" branch below, which returns immediately after
        # -- so once real session data existed, the mockup vanished
        # entirely instead of coexisting with the real chart. Matthew
        # wants both visible together, so everything now stacks inside a
        # scrollable frame rather than precisely budgeting fixed vertical
        # space for two variable-sized visuals in one tab.
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        mockup_image = self._load_profile_mockup_image(max_width=480)
        if mockup_image is not None:
            ctk.CTkLabel(scroll, text="", image=mockup_image).pack(padx=16, pady=(16, 4))
            ctk.CTkLabel(
                scroll, text="Illustrative example -- not your real data",
                font=_make_font(11), text_color=COLOR_STATUS_MUTED,
            ).pack(pady=(0, 12))

        ctk.CTkFrame(scroll, height=1, fg_color=COLOR_SIDEBAR_MUTED).pack(
            fill="x", padx=24, pady=(0, 16)
        )

        if len(entries) < 2:
            # Honest handling of the "no real history yet" case -- a
            # single point can't show a trend, and pretending otherwise
            # with fabricated extra points would misrepresent a feature
            # that's genuinely just getting started. See
            # analytics/session_history.py for why this threshold is 2.
            ctk.CTkLabel(
                scroll,
                text=(
                    "Your personalized accuracy profile builds up as you use "
                    "SmartAccess.\n\nCome back after a few more sessions to see "
                    "your miss rate and click tolerance improve over time."
                ),
                font=_make_font(13), text_color=COLOR_STATUS_MUTED,
                wraplength=380, justify="center",
            ).pack(padx=16, pady=(0, 24))
            return

        sessions = list(range(1, len(entries) + 1))
        miss_rates = [e["miss_rate"] * 100 for e in entries]
        click_radii = [e["click_radius"] for e in entries]

        fig = Figure(figsize=(4.2, 3.2), dpi=100)
        fig.patch.set_facecolor(COLOR_NEUTRAL_LIGHT)
        ax = fig.add_subplot(111)
        ax.set_facecolor(COLOR_NEUTRAL_LIGHT)

        ax.plot(sessions, miss_rates, marker="o", color="#EF4444", label="Miss rate (%)")
        ax2 = ax.twinx()
        ax2.plot(sessions, click_radii, marker="o", color=COLOR_PRIMARY, label="Click radius")

        ax.set_xlabel("Session")
        ax.set_ylabel("Miss rate (%)", color="#EF4444")
        ax2.set_ylabel("Click radius", color=COLOR_PRIMARY)
        ax.set_xticks(sessions)
        fig.tight_layout()

        canvas = FigureCanvasTkAgg(fig, master=scroll)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="x", padx=4, pady=(0, 16))


if __name__ == "__main__":
    ctk.set_appearance_mode("Light")
    app = App()
    app.mainloop()

