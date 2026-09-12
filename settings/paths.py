"""
Path helpers for locating bundled resources and writable user-data files,
correctly whether running from source (development) or from inside a
PyInstaller-built executable (the eventual downloaded app).
"""

import os
import sys


def resource_path(relative_path: str) -> str:
    """
    Return the correct path to a bundled, read-only resource (icons, model
    files, etc.). PyInstaller unpacks bundled files to a temp folder at
    runtime and exposes it as sys._MEIPASS; this falls back to the repo
    root when running from source so the same call works in both cases.
    """
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base_path, relative_path)


def user_data_dir(app_name: str = "SmartAccess") -> str:
    """
    Return (creating if needed) the OS-standard per-user, writable data
    directory for this app. Profiles, settings, and adaptive state are
    saved here — never inside the app bundle itself, which may be
    read-only or get wiped on every update.

    Windows: %APPDATA%\\SmartAccess
    macOS:   ~/Library/Application Support/SmartAccess
    Linux:   $XDG_DATA_HOME/SmartAccess (or ~/.local/share/SmartAccess)
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))

    path = os.path.join(base, app_name)
    os.makedirs(path, exist_ok=True)
    return path
