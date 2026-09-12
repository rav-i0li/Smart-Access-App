"""
Camera Manager
Wraps OpenCV video capture with basic device selection, error handling,
and a simple frame-read interface used by all tracking engines.

9 September 2026: explicitly requests the DirectShow (CAP_DSHOW) backend
instead of OpenCV's default on Windows (Media Foundation / MSMF).
Confirmed via research this is a known issue specifically affecting the
Logitech Brio and other webcams: MSMF can be substantially slower to
acquire frames when resolution is set via cap.set(), and in some cases
silently ignores the requested resolution and rescales a different
capture size instead rather than actually capturing at the requested
size -- meaning the earlier resolution-lowering fix may not have actually
taken effect. DSHOW does not have either issue. This is a well-documented
OpenCV/Windows-specific fix, not a workaround specific to this project.
"""

import cv2


class CameraManager:
    """Handles webcam capture so tracking engines don't touch OpenCV directly."""

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.cap = None

    def open(self) -> bool:
        """Open the camera. Returns True on success, False otherwise."""
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            # Fall back to OpenCV's default backend if DSHOW isn't
            # available for some reason, rather than failing outright.
            self.cap = cv2.VideoCapture(self.camera_index)
            if not self.cap.isOpened():
                return False
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        print(f"[CameraManager] Requested {self.width}x{self.height}, "
              f"camera granted {actual_w:.0f}x{actual_h:.0f}")
        return True

    def read_frame(self):
        """Read one frame. Returns (success, frame) like cv2.VideoCapture.read()."""
        if self.cap is None:
            raise RuntimeError("Camera not opened. Call open() first.")
        success, frame = self.cap.read()
        if success:
            # Mirror horizontally so movement feels natural (like a mirror, not a security cam)
            frame = cv2.flip(frame, 1)
        return success, frame

    def release(self):
        """Release the camera device."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    @staticmethod
    def list_available_cameras(max_index: int = 5):
        """Probe camera indices 0..max_index-1 and return the ones that open successfully.

        13 September 2026 fix -- this always requested CAP_DSHOW with no
        fallback, unlike open() above. CAP_DSHOW doesn't exist outside
        Windows, so on Mac/Linux every probe would fail to open and this
        would always return an empty list, even with cameras genuinely
        available. Not currently called anywhere in the app (camera
        selection in the GUI is still a placeholder), so this was
        dormant rather than an active bug -- fixing it now so it's
        correct on all platforms before it gets wired up."""
        available = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(i)
            cap.release()
        return available
