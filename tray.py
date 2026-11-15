"""System tray icon, start-with-Windows and single-instance handling."""

import ctypes
import sys
import threading
import winreg

import pystray
from PIL import Image, ImageDraw

APP_NAME = "YTMusicPresence"
TITLE = "YT Music Presence"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SETTINGS_KEY = rf"Software\{APP_NAME}"  # exists once we've done first-run setup

_mutex = None  # kept alive for the life of the process


def claim_single_instance():
    """False if another copy is already running (two copies would fight over the status)."""
    global _mutex
    _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, f"Local\\{APP_NAME}")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def message(text):
    print(text)
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x40)  # MB_ICONINFORMATION


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def setup_autostart():
    """Turn start-with-Windows on the first time the app runs; respect the user's choice after."""
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY).Close()
        first_run = False
    except OSError:
        winreg.CreateKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY).Close()
        first_run = True
    if first_run or autostart_enabled():
        set_autostart(True)  # also refreshes the path if the .exe was moved
    return first_run


def make_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((2, 2, 62, 62), fill=(124, 92, 255))
    draw.polygon([(25, 18), (25, 46), (47, 32)], fill="white")
    return img


def run_tray(ctl, worker, frozen):
    """Show the tray icon and run `worker` (the presence loop) in the background."""
    first_run = setup_autostart() if frozen else False

    def toggle_pause(icon, item):
        ctl.paused = not ctl.paused
        ctl.poke()

    def toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def quit_app(icon, item):
        icon.stop()  # Discord removes the status when the process exits

    icon = pystray.Icon(APP_NAME, make_icon(), TITLE, pystray.Menu(
        pystray.MenuItem("Pause sharing", toggle_pause, checked=lambda item: ctl.paused),
        pystray.MenuItem("Start with Windows", toggle_autostart,
                         checked=lambda item: autostart_enabled(), visible=frozen),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", quit_app),
    ))
    ctl.on_status = lambda text: setattr(icon, "title", f"{TITLE}\n{text}"[:127])

    def setup(icon):
        icon.visible = True
        if first_run:
            icon.notify("Running in the system tray. Right-click the icon to pause or quit.", TITLE)

        def guarded():
            try:
                worker()
            except Exception as e:
                ctl.say(f"Stopped because of an error: {e}")

        threading.Thread(target=guarded, daemon=True).start()

    icon.run(setup)
