"""
hardware/arduino_serial_manager.py

NEW FILE — does not replace anything.

Listens to the Arduino switch over a serial connection in a background
thread and exposes press/release events to the rest of the app via a
non-blocking queue, so the main tracking loop never stalls waiting on
serial I/O.
"""

import threading
import queue
import time

try:
    import serial
except ImportError:
    serial = None


class ArduinoSerialManager:
    def __init__(self, port="COM3", baud_rate=9600, timeout=1):
        self.port = port
        self.baud_rate = baud_rate
        self.timeout = timeout
        self._serial = None
        self._thread = None
        self._running = False
        self.events = queue.Queue()
        self.connected = False

    def connect(self):
        if serial is None:
            print("[ArduinoSerialManager] pyserial is not installed. "
                  "Run: pip install pyserial")
            return False
        try:
            self._serial = serial.Serial(self.port, self.baud_rate, timeout=self.timeout)
            time.sleep(2)  # Arduino resets when the serial port opens; give it time to boot
            self.connected = True
            self._running = True
            self._thread = threading.Thread(target=self._listen, daemon=True)
            self._thread.start()
            print(f"[ArduinoSerialManager] Connected on {self.port} at {self.baud_rate} baud.")
            return True
        except Exception as e:
            print(f"[ArduinoSerialManager] Could not open {self.port}: {e}")
            self.connected = False
            return False

    def _listen(self):
        while self._running and self._serial and self._serial.is_open:
            try:
                line = self._serial.readline().decode("utf-8", errors="ignore").strip()
            except Exception as e:
                print(f"[ArduinoSerialManager] Read error: {e}")
                break
            if line == "PRESS":
                self.events.put("PRESS")
            elif line == "RELEASE":
                self.events.put("RELEASE")

    def poll(self):
        """Return the next pending event ('PRESS' / 'RELEASE') or None. Non-blocking."""
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def disconnect(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._serial is not None and self._serial.is_open:
            self._serial.close()
        self.connected = False
