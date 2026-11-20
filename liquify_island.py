from __future__ import annotations

import asyncio
import ctypes
import datetime as dt
import json
import math
import os
import random
import re
import struct
import subprocess
import sys
import threading
from array import array
import time
import traceback
import urllib.parse
import urllib.request
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from functools import partial
from pathlib import Path

if sys.platform != "win32":
    sys.exit("Liquify Island only runs on Windows 11.")

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
    from winsdk.windows.storage.streams import Buffer, DataReader, InputStreamOptions
    HAVE_MEDIA_SDK = True
except ImportError:  # pragma: no cover
    HAVE_MEDIA_SDK = False

try:                                            # Bluetooth is optional: media still works without it
    from winsdk.windows.devices.bluetooth import (
        BluetoothConnectionStatus, BluetoothDevice, BluetoothLEDevice)
    from winsdk.windows.devices.enumeration import DeviceInformation, DeviceInformationKind
    from winsdk.windows.foundation import IPropertyValue, PropertyType
    HAVE_BT = True
except ImportError:  # pragma: no cover
    HAVE_BT = False

try:                                            # Location is optional: weather falls back to IP
    from winsdk.windows.devices.geolocation import Geolocator, GeolocationAccessStatus
    HAVE_GEO = True
except ImportError:  # pragma: no cover
    HAVE_GEO = False

try:                                            # Microphone capture is optional
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
    HAVE_AUDIO_IN = True
except ImportError:  # pragma: no cover
    HAVE_AUDIO_IN = False

try:                                            # Hardware H.264/AAC MP4 writer (Qt 6.8+)
    from PySide6.QtMultimedia import (
        QAudioBuffer, QAudioBufferInput, QMediaCaptureSession, QMediaFormat,
        QMediaRecorder, QVideoFrame, QVideoFrameFormat, QVideoFrameInput,
    )
    HAVE_MP4 = True
except ImportError:  # pragma: no cover
    HAVE_MP4 = False

from PySide6.QtCore import (
    QBuffer, QDir, QEasingCurve, QEvent, QIODevice, QLockFile, QMetaObject, QObject, QPoint,
    QPointF, QRect, QRectF, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl,
    QVariantAnimation, Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QCursor, QFont, QFontDatabase, QGuiApplication,
    QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QRadialGradient, QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QGraphicsBlurEffect,
    QGraphicsPixmapItem, QGraphicsScene, QMenu,
    QSlider, QSystemTrayIcon, QWidget,
)

# --------------------------------------------------------------------------- #
# Geometry (all numbers measured from the Nook reference, 1:1)
# --------------------------------------------------------------------------- #
W, H = 642, 151                      # expanded notch
COLLAPSED = QSize(256, 40)           # resting pill (time, date, battery ring)
HIDDEN_Y = -48                       # tucked above the screen when top-center is covered / fullscreen
DOCK_Y = 0                           # flush with the top edge
BOTTOM_RADIUS = 32
TOP_TRIGGER_W = 260                  # top-edge hover zone when top-center is covered by a window
TOP_TRIGGER_H = 10
HIDE_GRACE_S = 0.2
ANIM_MS = 250

ART = 96                             # album art edge
ART_POS = QPoint(12, 44)
TEXT_X, TEXT_W = 119, 118            # title / controls / slider column
DIVIDER_1_X, DIVIDER_2_X = 249, 533
CAL_RECT = QRect(259, 44, 268, 96)
RING_RECT = QRect(543, 44, 90, 90)      # 9px clear of the right edge
# --- home layout: three glass cards (agent | now playing | system)
AGENT_CARD = QRect(14, 40, 148, 103)
MUSIC_CARD = QRect(170, 40, 282, 103)
SYS_CARD = QRect(460, 40, 168, 103)
HX, HW = 282, 160                       # text column of the now-playing card
HOME_RING = QPoint(464, 44)
DATE_RECT = QRect(556, 48, 68, 90)
SANS = ["Segoe UI Variable Text", "Segoe UI"]
DISPLAY = ["Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"]
PREFERRED_DISPLAY = ["Space Grotesk", "Sora", "Outfit", "Manrope", "Plus Jakarta Sans", "Inter Display", "Inter"]
_CUSTOM_FAMILIES: list[str] = []
DEFAULT_TINT = QColor(34, 40, 62)
DEFAULT_TINT_2 = QColor(52, 60, 96)

BASE_FILL_ALPHA = 10                 # fluid glass is clear: only a whisper of frost
BACKDROP_BLUR = 1.5                  # ...so the scene behind it stays crisp
BACKDROP_DIV = 2                     # backdrop is captured at 1/2 resolution

# --- Fluid Glass
FG_IOR, FG_THICKNESS, FG_CHROMA = 1.15, 5.0, 0.1
_FG_REFRACT = (FG_IOR - 1.0) * FG_THICKNESS          # 0.75
FG_CENTER_SHIFT = _FG_REFRACT * 1.5                  # px the flat middle magnifies the scene
FG_BANDS = ((4, _FG_REFRACT * 14.0),                 # (inset px, edge shift px): outer bevel
            (10, _FG_REFRACT * 7.0))                 #                            inner bevel
FG_CA = FG_CHROMA * 3.0                              # R / B are pushed +/- this much further than G

# --- Elastic Slider
ES_MAX_OVERFLOW = 22.0
ES_PAD = 22
MAX_DEVICES = 6
BT_REFRESH_S = 2.5
BT_BATTERY_KEY = "{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2"

# --- Screen capture
REC_FPS = 20
REC_QUALITY = 82
AUDIO_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_BLOCK = AUDIO_CHANNELS * 2           # 16-bit stereo frame
CAPTURE_CFG = Path(os.environ.get("APPDATA") or Path.home()) / "Liquify Island" / "capture.json"
FOCUS_CFG = Path(os.environ.get("APPDATA") or Path.home()) / "Liquify Island" / "focus.json"
LYRICS_CFG = Path(os.environ.get("APPDATA") or Path.home()) / "Liquify Island" / "lyrics.json"
DETAIL_TOP = 35                      # the three expandable views start under the nav trays
_MENU_QSS = ("QMenu { background-color: rgba(29, 33, 46, 247); color: white; padding: 7px;"
             " border: 1px solid rgba(255,255,255,72); border-radius: 16px; }"
             "QMenu::item { padding: 8px 20px 8px 30px; margin: 1px 3px; border-radius: 9px;"
             " background: transparent; }"
             "QMenu::item:selected { background-color: rgba(255,255,255,46); }"
             "QMenu::item:disabled { color: rgba(255,255,255,110); }"
             "QMenu::separator { height: 1px; background: rgba(255,255,255,34);"
             " margin: 6px 10px; }"
             "QMenu::icon { left: 12px; }")


def _menu_style(menu):
    """Make a native menu look like the glass capture card."""
    try:
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint
                            | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
    except Exception:
        pass
    menu.setFont(ui_font(11, QFont.Weight.Medium, False, 0.2))
    menu.setStyleSheet(_MENU_QSS)
    return menu
REC_MAX_S = 30 * 60                    # hard stop so a forgotten recording cannot fill the disk
REC_MAX_BYTES = 3_900_000_000          # classic AVI index is 32-bit

# --------------------------------------------------------------------------- #
# Win32 helpers + System Audio Mute (Core Audio COM + keybd_event fallback)
# --------------------------------------------------------------------------- #
_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32
_kernel32 = ctypes.windll.kernel32
_dwmapi = ctypes.windll.dwmapi
_ole32 = ctypes.windll.ole32

_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
_user32.keybd_event.restype = None
_user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_user32.MessageBoxW.restype = ctypes.c_int

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.ShowWindow.restype = wintypes.BOOL
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateProcess.restype = wintypes.BOOL
_kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, s: str) -> "_GUID":
        s = s.strip("{}")
        parts = s.split("-")
        b4 = bytes.fromhex(parts[3] + parts[4])
        return cls(
            int(parts[0], 16),
            int(parts[1], 16),
            int(parts[2], 16),
            (ctypes.c_ubyte * 8)(*b4),
        )


_CLSID_MMDeviceEnumerator = _GUID.from_str("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_IMMDeviceEnumerator = _GUID.from_str("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IID_IAudioEndpointVolume = _GUID.from_str("5CDF2C82-841E-4546-9722-0CF74078229A")

_ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_ole32.CoInitializeEx.restype = ctypes.HRESULT
_ole32.CoUninitialize.argtypes = []
_ole32.CoUninitialize.restype = None
_ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
]
_ole32.CoCreateInstance.restype = ctypes.HRESULT


def _com_method(ptr: ctypes.c_void_p, index: int, restype, *argtypes):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return fn_type(vtbl[index])


def _with_endpoint_volume(fn):
    """Run `fn(ep_vol_ptr)` against the default render audio endpoint, releasing COM pointers."""
    hr_init = _ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    did_init = hr_init in (0, 1)
    enum_ptr = ctypes.c_void_p()
    dev_ptr = ctypes.c_void_p()
    vol_ptr = ctypes.c_void_p()
    try:
        hr = _ole32.CoCreateInstance(
            ctypes.byref(_CLSID_MMDeviceEnumerator),
            None,
            1,  # CLSCTX_INPROC_SERVER
            ctypes.byref(_IID_IMMDeviceEnumerator),
            ctypes.byref(enum_ptr),
        )
        if hr != 0 or not enum_ptr.value:
            return None
        get_default = _com_method(
            enum_ptr, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)
        )
        if get_default(enum_ptr, 0, 0, ctypes.byref(dev_ptr)) != 0 or not dev_ptr.value:
            return None
        activate = _com_method(
            dev_ptr, 3, ctypes.HRESULT,
            ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        )
        if activate(dev_ptr, ctypes.byref(_IID_IAudioEndpointVolume), 1, None, ctypes.byref(vol_ptr)) != 0 or not vol_ptr.value:
            return None
        return fn(vol_ptr)
    finally:
        for p in (vol_ptr, dev_ptr, enum_ptr):
            if p.value:
                try:
                    _com_method(p, 2, wintypes.ULONG)(p)
                except Exception:
                    pass
        if did_init:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass


def get_system_mute() -> bool | None:
    try:
        def _read(vol_ptr):
            muted = wintypes.BOOL(0)
            get_mute = _com_method(vol_ptr, 15, ctypes.HRESULT, ctypes.POINTER(wintypes.BOOL))
            return bool(muted.value) if get_mute(vol_ptr, ctypes.byref(muted)) == 0 else None
        return _with_endpoint_volume(_read)
    except Exception:
        return None


def toggle_system_mute() -> bool:
    """Toggle system master mute and return the new muted state."""
    try:
        def _toggle(vol_ptr):
            muted = wintypes.BOOL(0)
            get_mute = _com_method(vol_ptr, 15, ctypes.HRESULT, ctypes.POINTER(wintypes.BOOL))
            set_mute = _com_method(vol_ptr, 14, ctypes.HRESULT, wintypes.BOOL, ctypes.c_void_p)
            if get_mute(vol_ptr, ctypes.byref(muted)) != 0:
                return None
            new_state = 0 if muted.value else 1
            if set_mute(vol_ptr, new_state, None) == 0:
                return bool(new_state)
            return None

        res = _with_endpoint_volume(_toggle)
        if res is not None:
            return res
    except Exception:
        pass

    # Fallback: synthetic media mute key
    _user32.keybd_event(0xAD, 0, 0, 0)
    _user32.keybd_event(0xAD, 0, 2, 0)
    cur = get_system_mute()
    return bool(cur) if cur is not None else False


def configure_native_window(hwnd: int) -> bool:
    """Hide from taskbar/Alt+Tab; exclude from capture (Win10 2004+)."""
    h = wintypes.HWND(hwnd)
    style = _user32.GetWindowLongPtrW(h, -20)                      # GWL_EXSTYLE
    _user32.SetWindowLongPtrW(h, -20, (style | 0x80) & ~0x40000)   # TOOLWINDOW, !APPWINDOW
    return bool(_user32.SetWindowDisplayAffinity(h, 0x11))         # EXCLUDEFROMCAPTURE


def left_button_down() -> bool:
    return bool(_user32.GetAsyncKeyState(0x01) & 0x8000)


_SHELL_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland",
    "ForegroundStaging", "ApplicationManager_DesktopShellWindow",
    "Windows.UI.Core.CoreWindow", "EdgeUiInputTopWndClass", "EdgeUiInputWndClass",
}


def _window_class(hwnd) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    n = _user32.GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def _is_cloaked(hwnd) -> bool:
    cloaked = wintypes.DWORD(0)
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return hr == 0 and cloaked.value != 0


def _window_rect(hwnd) -> QRect | None:
    rc = wintypes.RECT()
    hr = _dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rc), ctypes.sizeof(rc))
    if hr != 0:
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rc)):
            return None
    w, h = rc.right - rc.left, rc.bottom - rc.top
    if w <= 0 or h <= 0:
        return None
    return QRect(rc.left, rc.top, w, h)


def is_top_center_clear(pill_rect: QRect, own_hwnd: int = 0) -> bool:
    """True when the top-center pill area is unobstructed."""
    fg = _user32.GetForegroundWindow()
    if fg and int(fg) != own_hwnd and _window_class(fg) in ("Progman", "WorkerW"):
        return True

    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    phys_pill = QRect(
        int(round(pill_rect.x() * dpr)),
        int(round(pill_rect.y() * dpr)),
        int(round(pill_rect.width() * dpr)),
        int(round(pill_rect.height() * dpr)),
    ).adjusted(-4, 0, 4, 4)

    obstructed = False

    def cb(hwnd, _lparam):
        nonlocal obstructed
        try:
            if int(hwnd) == own_hwnd:
                return True
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            if _is_cloaked(hwnd):
                return True
            ex = _user32.GetWindowLongPtrW(hwnd, -20)
            if (ex & 0x80) and not (ex & 0x40000):
                return True
            if ex & 0x08000000:
                return True
            if _window_class(hwnd) in _SHELL_CLASSES:
                return True
            if _user32.GetWindowTextLengthW(hwnd) <= 0:
                return True
            wrect = _window_rect(hwnd)
            if wrect is not None and wrect.intersects(phys_pill):
                obstructed = True
                return False
            return True
        except Exception:
            return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return not obstructed


def _window_exe(hwnd) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = _kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(h)
    return ""


def _top_level_windows() -> list[tuple[int, str, str]]:
    out: list[tuple[int, str, str]] = []

    def cb(hwnd, _lparam):
        try:
            if _user32.IsWindowVisible(hwnd):
                n = _user32.GetWindowTextLengthW(hwnd)
                if n > 0:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    _user32.GetWindowTextW(hwnd, buf, n + 1)
                    out.append((int(hwnd), buf.value, _window_exe(hwnd)))
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out


def open_media_source(aumid: str, title: str, own_hwnd: int = 0) -> bool:
    aumid = (aumid or "").strip()
    track = (title or "").strip().lower()
    if track in ("", "unknown title"):
        track = ""

    hint = aumid.split("!")[0].lower()
    hint = hint[:-4] if hint.endswith(".exe") else hint
    hint = hint.split("_")[0].rsplit(".", 1)[-1]

    def exe_matches(exe: str) -> bool:
        stem = exe[:-4] if exe.endswith(".exe") else exe
        return len(stem) >= 3 and len(hint) >= 3 and (hint in stem or stem in hint)

    wins = [w for w in _top_level_windows() if w[0] != own_hwnd]
    by_exe = [w for w in wins if exe_matches(w[2])]
    pool = by_exe or wins
    pick = next((w for w in pool if track and track in w[1].lower()), None)
    if pick is None and by_exe:
        pick = by_exe[0]

    if pick is not None:
        hwnd = wintypes.HWND(pick[0])
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, 9)
        return bool(_user32.SetForegroundWindow(hwnd))

    if "!" in aumid:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{aumid}"])
        return True
    return False


def fullscreen_app_active() -> bool:
    state = ctypes.c_int()
    hr = _shell32.SHQueryUserNotificationState(ctypes.byref(state))
    return hr == 0 and state.value in (2, 3, 4)


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def read_battery():
    s = _PowerStatus()
    if not _kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None
    if s.BatteryFlag == 128 or s.BatteryLifePercent > 100:
        return None
    return int(s.BatteryLifePercent), s.ACLineStatus == 1


# --------------------------------------------------------------------------- #
# Graphics helpers
# --------------------------------------------------------------------------- #
def ui_font(px: int, weight=QFont.Weight.Normal, display: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies((_CUSTOM_FAMILIES + DISPLAY) if display else SANS)
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.PreferAntialias)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )


def blur_pixmap(pixmap: QPixmap, radius: float = 6.0) -> QPixmap:
    if pixmap.isNull():
        return pixmap
    src = QPixmap(pixmap)
    src.setDevicePixelRatio(1.0)
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(src)
    fx = QGraphicsBlurEffect()
    fx.setBlurRadius(radius)
    fx.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    scene.render(p, QRectF(out.rect()), QRectF(src.rect()))
    p.end()
    return out


def _finalize_tint(c: QColor) -> QColor:
    h, s, v, _ = c.getHsvF()
    return QColor.fromHsvF(max(h, 0.0), min(1.0, max(s, 0.40)), min(0.78, max(0.38, v)))


def extract_palette(pixmap: QPixmap) -> tuple[QColor, QColor]:
    default = (QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2))
    if pixmap.isNull():
        return default
    img = pixmap.toImage().scaled(40, 40, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    bins = 24
    wsum = [0.0] * bins
    rs, gs, bs = [0.0] * bins, [0.0] * bins, [0.0] * bins
    ar = ag = ab = 0.0
    for y in range(40):
        for x in range(40):
            c = img.pixelColor(x, y)
            ar += c.redF(); ag += c.greenF(); ab += c.blueF()
            h, s, v, _ = c.getHsvF()
            if h < 0 or s < 0.20 or v < 0.18:
                continue
            wt = s * (0.35 + 0.65 * v) * (1.5 if 10 <= x < 30 and 10 <= y < 30 else 1.0)
            b = int(h * bins) % bins
            wsum[b] += wt
            rs[b] += c.redF() * wt; gs[b] += c.greenF() * wt; bs[b] += c.blueF() * wt

    if sum(wsum) < 8.0:
        n = 1600.0
        g = QColor.fromRgbF(ar / n, ag / n, ab / n)
        h, s, v, _ = g.getHsvF()
        base = QColor.fromHsvF(max(h, 0.0), s, min(0.62, max(0.22, v)))
        return base, base.lighter(130)

    score = [wsum[i] + 0.5 * (wsum[i - 1] + wsum[(i + 1) % bins]) for i in range(bins)]

    def colour_at(i: int) -> QColor:
        idx = [(i - 1) % bins, i, (i + 1) % bins]
        tot = sum(wsum[k] for k in idx) or 1.0
        return QColor.fromRgbF(sum(rs[k] for k in idx) / tot,
                               sum(gs[k] for k in idx) / tot,
                               sum(bs[k] for k in idx) / tot)

    p_i = max(range(bins), key=score.__getitem__)
    primary = colour_at(p_i)

    far = [i for i in range(bins) if min((i - p_i) % bins, (p_i - i) % bins) >= 3]
    s_i = max(far, key=score.__getitem__)
    if score[s_i] >= 0.25 * score[p_i]:
        secondary = colour_at(s_i)
    else:
        h, s, v, _ = primary.getHsvF()
        secondary = QColor.fromHsvF((h + 0.05) % 1.0, s, min(1.0, v * 1.15))
    return _finalize_tint(primary), _finalize_tint(secondary)


def extract_dominant_color(pixmap: QPixmap) -> QColor:
    return extract_palette(pixmap)[0]


def with_alpha(c: QColor, a: int) -> QColor:
    return QColor(c.red(), c.green(), c.blue(), a)


def make_wash(pixmap: QPixmap) -> QPixmap:
    if pixmap.isNull():
        return QPixmap()
    small = pixmap.scaled(64, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return blur_pixmap(small, 3.0)


def split_channels(pm: QPixmap) -> list[QPixmap]:
    out = []
    for col in (QColor(255, 0, 0), QColor(0, 255, 0), QColor(0, 0, 255)):
        ch = QPixmap(pm)
        ch.setDevicePixelRatio(1.0)
        q = QPainter(ch)
        q.setCompositionMode(QPainter.CompositionMode_Multiply)
        q.fillRect(ch.rect(), col)
        q.end()
        out.append(ch)
    return out


def decay(value: float, limit: float) -> float:
    if limit == 0:
        return 0.0
    entry = value / limit
    return 2.0 * (1.0 / (1.0 + math.exp(-entry)) - 0.5) * limit


def round_pixmap(pixmap: QPixmap, size: int, radius: float, dpr: float = 1.0) -> QPixmap:
    px = max(1, int(size * dpr))
    out = QPixmap(px, px)
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    if pixmap.isNull():
        return out
    scaled = pixmap.scaled(px, px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    sx, sy = (scaled.width() - px) // 2, (scaled.height() - px) // 2
    p = QPainter(out)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.setClipPath(clip)
    p.drawPixmap(QRectF(0, 0, size, size), scaled, QRectF(sx, sy, px, px))
    p.end()
    return out


def placeholder_art(size: int, radius: float, dpr: float = 1.0) -> QPixmap:
    px = int(size * dpr)
    out = QPixmap(px, px)
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(255, 255, 255, 62))
    g.setColorAt(1, QColor(255, 255, 255, 18))
    p.setBrush(g)
    p.setPen(QPen(QColor(255, 255, 255, 55), 1))
    p.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), radius, radius)
    p.setPen(QColor(255, 255, 255, 190))
    p.setFont(ui_font(int(size * 0.46), QFont.Weight.DemiBold))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "♪")
    p.end()
    return out


def create_icon_pixmap(kind: str, size: int = 18, color: QColor | None = None) -> QIcon:
    color = color or QColor(255, 255, 255, 245)
    px = size * 4
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(px / 24.0, px / 24.0)

    def fill():
        p.setBrush(color)
        p.setPen(QPen(color, 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def stroke(w=1.8):
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    def tri(*pts):
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts]))

    def bar(x, w=3.0):
        p.drawRoundedRect(QRectF(x, 5.5, w, 13), 1.2, 1.2)

    if kind == "play":
        fill(); tri((8.5, 5.5), (18.5, 12), (8.5, 18.5))
    elif kind == "pause":
        fill(); bar(6.5, 3.4); bar(14.1, 3.4)
    elif kind == "next":
        fill(); tri((5, 5.5), (15, 12), (5, 18.5)); bar(17.0)
    elif kind == "prev":
        fill(); tri((19, 5.5), (9, 12), (19, 18.5)); bar(4.0)
    elif kind == "bolt":
        fill(); tri((13.5, 2.5), (5.5, 13.5), (11, 13.5)); tri((10.5, 21.5), (18.5, 10.5), (13, 10.5))
    elif kind == "gear":
        stroke(2.2)
        p.drawEllipse(QPointF(12, 12), 5.6, 5.6)
        p.setPen(QPen(color, 3.0, Qt.SolidLine, Qt.RoundCap))
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(12 + 7.4 * math.cos(a), 12 + 7.4 * math.sin(a)),
                       QPointF(12 + 9.4 * math.cos(a), 12 + 9.4 * math.sin(a)))
        stroke(1.8)
        p.drawEllipse(QPointF(12, 12), 2.2, 2.2)
    elif kind == "island":
        stroke(1.8)
        p.drawRoundedRect(QRectF(3, 7, 18, 10), 5, 5)
        fill(); p.drawEllipse(QPointF(8.2, 12), 1.5, 1.5)
    elif kind == "tray":
        stroke(1.8)
        path = QPainterPath()
        path.moveTo(3, 13); path.lineTo(6.2, 5.5); path.lineTo(17.8, 5.5); path.lineTo(21, 13)
        path.lineTo(21, 18.5); path.lineTo(3, 18.5); path.closeSubpath()
        p.drawPath(path)
        inner = QPainterPath()
        inner.moveTo(3, 13); inner.lineTo(8, 13); inner.lineTo(9.6, 15.6)
        inner.lineTo(14.4, 15.6); inner.lineTo(16, 13); inner.lineTo(21, 13)
        p.drawPath(inner)
    elif kind == "bluetooth":
        stroke(1.9)
        path = QPainterPath()
        path.moveTo(6.5, 7.5); path.lineTo(17, 16); path.lineTo(12, 20.5); path.lineTo(12, 3.5)
        path.lineTo(17, 8); path.lineTo(6.5, 16.5)
        p.drawPath(path)
    elif kind == "headphones":
        stroke(1.9)
        p.drawArc(QRectF(5.2, 4.2, 13.6, 17.0), 0, 180 * 16)
        fill()
        p.drawRoundedRect(QRectF(3.4, 12.6, 4.2, 7.4), 1.8, 1.8)
        p.drawRoundedRect(QRectF(16.4, 12.6, 4.2, 7.4), 1.8, 1.8)
    elif kind == "earbuds":
        stroke(2.0)
        for cx in (7.2, 16.8):
            p.drawEllipse(QPointF(cx, 8.2), 3.4, 3.4)
            p.drawLine(QPointF(cx + (2.6 if cx < 12 else -2.6), 10.6), QPointF(cx + (2.6 if cx < 12 else -2.6), 19.5))
    elif kind == "speaker":
        stroke(1.8)
        p.drawRoundedRect(QRectF(6, 3.2, 12, 17.6), 3.2, 3.2)
        p.drawEllipse(QPointF(12, 14.2), 3.4, 3.4)
        fill(); p.drawEllipse(QPointF(12, 7.4), 1.2, 1.2)
    elif kind == "screen":
        stroke(1.7)
        p.drawRoundedRect(QRectF(3.0, 4.5, 18.0, 12.5), 2.6, 2.6)
        p.drawLine(QPointF(9.0, 20.0), QPointF(15.0, 20.0))
        p.drawLine(QPointF(12.0, 17.0), QPointF(12.0, 20.0))
    elif kind == "window":
        stroke(1.7)
        p.drawRoundedRect(QRectF(3.5, 5.0, 17.0, 14.0), 2.6, 2.6)
        p.drawLine(QPointF(3.5, 9.0), QPointF(20.5, 9.0))
        fill()
        p.drawEllipse(QPointF(6.6, 7.0), 0.9, 0.9)
    elif kind == "portion":
        stroke(1.8)
        p.drawLine(QPointF(4.0, 8.0), QPointF(4.0, 4.5))
        p.drawLine(QPointF(4.0, 4.5), QPointF(8.0, 4.5))
        p.drawLine(QPointF(16.0, 4.5), QPointF(20.0, 4.5))
        p.drawLine(QPointF(20.0, 4.5), QPointF(20.0, 8.0))
        p.drawLine(QPointF(4.0, 16.0), QPointF(4.0, 19.5))
        p.drawLine(QPointF(4.0, 19.5), QPointF(8.0, 19.5))
        p.drawLine(QPointF(16.0, 19.5), QPointF(20.0, 19.5))
        p.drawLine(QPointF(20.0, 19.5), QPointF(20.0, 16.0))
    elif kind == "sliders":
        stroke(1.8)
        for yy in (7.5, 12.0, 16.5):
            p.drawLine(QPointF(4.0, yy), QPointF(20.0, yy))
        fill()
        p.drawEllipse(QPointF(9.5, 7.5), 2.2, 2.2)
        p.drawEllipse(QPointF(15.0, 12.0), 2.2, 2.2)
        p.drawEllipse(QPointF(8.0, 16.5), 2.2, 2.2)
    elif kind == "camera":
        stroke(1.7)
        p.drawRoundedRect(QRectF(3.0, 7.2, 18.0, 12.3), 3.2, 3.2)
        p.drawLine(QPointF(9.0, 7.2), QPointF(10.5, 4.7))
        p.drawLine(QPointF(10.5, 4.7), QPointF(13.5, 4.7))
        p.drawLine(QPointF(13.5, 4.7), QPointF(15.0, 7.2))
        p.drawEllipse(QPointF(12.0, 13.3), 4.1, 4.1)
        fill()
        p.drawEllipse(QPointF(12.0, 13.3), 2.0, 2.0)
    elif kind == "record":
        fill()
        p.drawEllipse(QPointF(12.0, 12.0), 5.6, 5.6)
    elif kind == "stop":
        fill()
        p.drawRoundedRect(QRectF(7.4, 7.4, 9.2, 9.2), 2.4, 2.4)
    elif kind == "calendar":
        stroke(1.7)
        p.drawRoundedRect(QRectF(3.5, 5, 17, 15), 3, 3)
        p.drawLine(QPointF(3.5, 10), QPointF(20.5, 10))
        p.drawLine(QPointF(8, 3), QPointF(8, 6.5))
        p.drawLine(QPointF(16, 3), QPointF(16, 6.5))
        fill(); p.drawEllipse(QPointF(12, 15), 1.3, 1.3)
    elif kind == "battery":
        stroke(1.8)
        p.drawRoundedRect(QRectF(3, 7.5, 16, 9), 2.5, 2.5)
        fill(); p.drawRoundedRect(QRectF(20, 10.5, 1.8, 3), 0.8, 0.8)
        p.drawRoundedRect(QRectF(5.2, 9.7, 9, 4.6), 1, 1)
    elif kind == "close":
        stroke(2.0)
        p.drawLine(QPointF(7.5, 7.5), QPointF(16.5, 16.5))
        p.drawLine(QPointF(16.5, 7.5), QPointF(7.5, 16.5))
    elif kind == "chevron":
        stroke(2.0)
        p.drawLine(QPointF(9.5, 6.5), QPointF(15.0, 12.0))
        p.drawLine(QPointF(15.0, 12.0), QPointF(9.5, 17.5))
    elif kind == "back":
        stroke(2.0)
        p.drawLine(QPointF(14.5, 6.5), QPointF(9.0, 12.0))
        p.drawLine(QPointF(9.0, 12.0), QPointF(14.5, 17.5))
    elif kind == "power":
        stroke(1.9)
        p.drawArc(QRectF(4.6, 4.6, 14.8, 14.8), -55 * 16, -250 * 16)
        p.drawLine(QPointF(12.0, 3.2), QPointF(12.0, 10.8))
    elif kind == "lyrics":
        stroke(1.8)
        p.drawLine(QPointF(4.5, 7.5), QPointF(19.5, 7.5))
        p.drawLine(QPointF(4.5, 12.0), QPointF(19.5, 12.0))
        p.drawLine(QPointF(4.5, 16.5), QPointF(13.5, 16.5))
    elif kind == "info":
        stroke(1.8)
        p.drawEllipse(QPointF(12.0, 12.0), 8.4, 8.4)
        p.drawLine(QPointF(12.0, 11.0), QPointF(12.0, 16.4))
        fill()
        p.drawEllipse(QPointF(12.0, 7.6), 1.15, 1.15)
    elif kind == "folder":
        stroke(1.7)
        path = QPainterPath()
        path.moveTo(3.4, 7.2); path.lineTo(9.6, 7.2); path.lineTo(11.6, 9.6)
        path.lineTo(20.6, 9.6); path.lineTo(20.6, 18.4); path.lineTo(3.4, 18.4)
        path.closeSubpath()
        p.drawPath(path)
    elif kind == "timer":
        stroke(1.8)
        p.drawEllipse(QPointF(12.0, 13.4), 7.0, 7.0)
        p.drawLine(QPointF(12.0, 13.4), QPointF(12.0, 9.4))
        p.drawLine(QPointF(12.0, 13.4), QPointF(14.9, 14.8))
        p.drawLine(QPointF(12.0, 3.4), QPointF(12.0, 5.6))
        p.drawLine(QPointF(9.2, 2.6), QPointF(12.0, 3.4))
    elif kind == "sparkle":
        fill()
        star = QPainterPath()
        star.moveTo(12.0, 3.2)
        star.cubicTo(13.4, 8.6, 15.4, 10.6, 20.8, 12.0)
        star.cubicTo(15.4, 13.4, 13.4, 15.4, 12.0, 20.8)
        star.cubicTo(10.6, 15.4, 8.6, 13.4, 3.2, 12.0)
        star.cubicTo(8.6, 10.6, 10.6, 8.6, 12.0, 3.2)
        p.drawPath(star)
    elif kind == "reset":
        stroke(1.9)
        p.drawArc(QRectF(4.6, 4.6, 14.8, 14.8), 46 * 16, 270 * 16)
        fill()
        tri((2.6, 9.4), (9.4, 8.0), (5.6, 12.8))
    elif kind == "check":
        stroke(2.2)
        p.drawLine(QPointF(5.0, 12.6), QPointF(10.0, 17.4))
        p.drawLine(QPointF(10.0, 17.4), QPointF(19.0, 6.6))
    elif kind == "terminal":
        stroke(1.7)
        p.drawRoundedRect(QRectF(3.0, 4.6, 18.0, 14.8), 3.0, 3.0)
        p.drawLine(QPointF(6.6, 9.4), QPointF(9.8, 12.0))
        p.drawLine(QPointF(9.8, 12.0), QPointF(6.6, 14.6))
        p.drawLine(QPointF(12.4, 14.8), QPointF(17.0, 14.8))
    elif kind in ("mic", "mic_off"):
        stroke(1.8)
        p.drawRoundedRect(QRectF(9.0, 3.0, 6.0, 10.5), 3.0, 3.0)
        p.drawArc(QRectF(5.6, 8.0, 12.8, 10.0), 200 * 16, 140 * 16)
        p.drawLine(QPointF(12.0, 18.0), QPointF(12.0, 21.0))
        p.drawLine(QPointF(8.6, 21.0), QPointF(15.4, 21.0))
        if kind == "mic_off":
            p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 255), 2.0,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(4.2, 4.2), QPointF(19.8, 19.8))
    p.end()
    return QIcon(pm)


def create_source_badge_icon(aumid: str, size: int = 16) -> QIcon:
    """Crisp 4x-supersampled vector app icon for the album art corner badge."""
    a = (aumid or "").lower()
    px = size * 4
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(px / 24.0, px / 24.0)

    def paint_logo(offset_y: float, col: QColor):
        p.save()
        p.translate(0.0, offset_y)
        if "spotify" in a:
            for y0, x_pad, bow, w in ((8.4, 4.2, -2.5, 2.35), (12.2, 5.4, -2.0, 2.1), (15.8, 6.6, -1.5, 1.85)):
                path = QPainterPath()
                path.moveTo(x_pad, y0)
                path.quadTo(12.0, y0 + bow, 24.0 - x_pad, y0 + 0.5)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col, w, Qt.SolidLine, Qt.RoundCap))
                p.drawPath(path)
        elif "youtube" in a:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawRoundedRect(QRectF(3.5, 6.0, 17.0, 12.0), 3.4, 3.4)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawPolygon(QPolygonF([QPointF(10.0, 8.8), QPointF(16.0, 12.0), QPointF(10.0, 15.2)]))
        elif "chrome" in a:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 2.0))
            p.drawEllipse(QPointF(12.0, 12.0), 7.5, 7.5)
            p.setBrush(col)
            p.drawEllipse(QPointF(12.0, 12.0), 3.1, 3.1)
            p.drawLine(QPointF(12.0, 8.9), QPointF(19.0, 8.9))
            p.drawLine(QPointF(9.3, 13.5), QPointF(5.8, 7.5))
            p.drawLine(QPointF(14.7, 13.5), QPointF(11.2, 19.3))
        elif "edge" in a:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 2.3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(4.8, 4.8, 14.4, 14.4), 30 * 16, 255 * 16)
            p.drawArc(QRectF(8.2, 8.2, 8.2, 8.2), -20 * 16, 210 * 16)
        elif "firefox" in a:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 2.3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(4.8, 4.8, 14.4, 14.4), 45 * 16, 275 * 16)
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(12.3, 12.3), 3.2, 3.2)
        elif "vlc" in a:
            p.setBrush(col)
            p.setPen(QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawPolygon(QPolygonF([QPointF(12.0, 4.2), QPointF(16.6, 17.5), QPointF(7.4, 17.5)]))
            p.drawRoundedRect(QRectF(5.0, 17.5, 14.0, 2.4), 1.0, 1.0)
        elif "tidal" in a:
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            for cx, cy in ((7.2, 9.5), (12.0, 9.5), (16.8, 9.5), (12.0, 14.3)):
                p.drawPolygon(QPolygonF([
                    QPointF(cx, cy - 2.3), QPointF(cx + 2.3, cy),
                    QPointF(cx, cy + 2.3), QPointF(cx - 2.3, cy),
                ]))
        elif "deezer" in a:
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            for idx, bh in enumerate((5.0, 9.0, 12.5, 7.5)):
                p.drawRoundedRect(QRectF(4.8 + idx * 3.9, 18.0 - bh, 2.7, bh), 1.0, 1.0)
        elif "brave" in a:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(col, 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            shield = QPainterPath()
            shield.moveTo(6.2, 5.2); shield.lineTo(17.8, 5.2); shield.lineTo(16.8, 14.2)
            shield.lineTo(12.0, 19.2); shield.lineTo(7.2, 14.2); shield.closeSubpath()
            p.drawPath(shield)
        else:
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(7.8, 16.5), 2.7, 2.1)
            p.drawEllipse(QPointF(15.6, 15.0), 2.7, 2.1)
            p.setPen(QPen(col, 2.1, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(10.0, 16.2), QPointF(10.0, 7.2))
            p.drawLine(QPointF(17.8, 14.7), QPointF(17.8, 5.8))
            p.setPen(QPen(col, 2.8, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(10.0, 7.2), QPointF(17.8, 5.8))
        p.restore()

    paint_logo(1.1, QColor(0, 0, 0, 130))
    paint_logo(0.0, QColor(255, 255, 255, 252))
    p.end()
    return QIcon(pm)


def notch_path(rect: QRectF, radius: float, closed: bool = True) -> QPainterPath:
    l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
    rb = max(0.0, min(radius, rect.height() / 2, rect.width() / 2))
    p = QPainterPath()
    p.moveTo(l, t)
    p.lineTo(l, b - rb)
    p.arcTo(QRectF(l, b - 2 * rb, 2 * rb, 2 * rb), 180, 90)
    p.lineTo(r - rb, b)
    p.arcTo(QRectF(r - 2 * rb, b - 2 * rb, 2 * rb, 2 * rb), 270, 90)
    p.lineTo(r, t)
    if closed:
        p.closeSubpath()
    return p


def source_style(aumid: str):
    a = (aumid or "").lower()
    table = [("spotify", (30, 215, 96)), ("apple", (252, 60, 68)), ("zune", (0, 120, 212)),
             ("youtube", (255, 40, 40)), ("chrome", (66, 133, 244)), ("msedge", (0, 164, 239)),
             ("edge", (0, 164, 239)), ("firefox", (255, 113, 57)), ("vlc", (255, 136, 0)),
             ("tidal", (30, 30, 30)), ("deezer", (162, 56, 255)), ("brave", (251, 84, 43))]
    for key, rgb in table:
        if key in a:
            return QColor(*rgb)
    return None


# --------------------------------------------------------------------------- #
# Capture: screenshots + a dependency-free MJPEG/AVI screen recorder
# --------------------------------------------------------------------------- #
def capture_folder(kind: str) -> Path:
    """Pictures\\Liquify Island for stills, Videos\\Liquify Island for clips."""
    loc = QStandardPaths.PicturesLocation if kind == "shot" else QStandardPaths.MoviesLocation
    base = QStandardPaths.writableLocation(loc)
    root = Path(base) if base else Path.home() / ("Pictures" if kind == "shot" else "Videos")
    folder = root / "Liquify Island"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path.home()
    return folder


def grab_display(screen) -> QPixmap:
    """Full-resolution grab of one display; the island itself is capture-excluded."""
    if screen is None:
        return QPixmap()
    geo = screen.geometry()
    shot = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
    if shot.isNull():
        shot = screen.grabWindow(0)
    if shot.isNull():
        return shot
    shot.setDevicePixelRatio(1.0)

    dpr = screen.devicePixelRatio() or 1.0
    want = QSize(max(1, int(round(geo.width() * dpr))), max(1, int(round(geo.height() * dpr))))
    if (shot.size() != want and want.width() < shot.width() and want.height() < shot.height()):
        vg = screen.virtualGeometry()
        dx = int(round((geo.x() - vg.x()) * dpr))
        dy = int(round((geo.y() - vg.y()) * dpr))
        if dx >= 0 and dy >= 0 and dx + want.width() <= shot.width() and dy + want.height() <= shot.height():
            shot = shot.copy(dx, dy, want.width(), want.height())
    return shot


_QUALITY_CHOICES = ((60, "Low"), (72, "Medium"), (82, "High"), (92, "Maximum"))
_FPS_CHOICES = (15, 20, 24, 30, 60)
_AREA_CHOICES = (("screen", "Entire Screen"), ("window", "Window"), ("portion", "Portion"))
_TIMER_CHOICES = (0, 3, 5, 10)


@dataclass
class CaptureOptions:
    """Everything the macOS-style capture Options menu can change."""

    area: str = "screen"
    quality: int = 82
    fps: int = 20
    mic: str = ""                      # "" off, "default" or a device description
    system_audio: bool = False
    pointer: bool = True
    timer: int = 0
    clipboard: bool = False

    def sanitized(self) -> "CaptureOptions":
        if self.area not in {key for key, _ in _AREA_CHOICES}:
            self.area = "screen"
        if self.quality not in {value for value, _ in _QUALITY_CHOICES}:
            self.quality = 82
        if self.fps not in _FPS_CHOICES:
            self.fps = 20
        if self.timer not in _TIMER_CHOICES:
            self.timer = 0
        return self

    @classmethod
    def load(cls) -> "CaptureOptions":
        opt = cls()
        try:
            raw = json.loads(CAPTURE_CFG.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return opt
        if not isinstance(raw, dict):
            return opt
        for key in ("area", "mic"):
            if isinstance(raw.get(key), str):
                setattr(opt, key, raw[key])
        for key in ("quality", "fps", "timer"):
            try:
                setattr(opt, key, int(raw.get(key, getattr(opt, key))))
            except (TypeError, ValueError):
                pass
        for key in ("system_audio", "pointer", "clipboard"):
            if isinstance(raw.get(key), bool):
                setattr(opt, key, raw[key])
        return opt.sanitized()

    def save(self):
        try:
            CAPTURE_CFG.parent.mkdir(parents=True, exist_ok=True)
            CAPTURE_CFG.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")
        except OSError:
            pass

    def quality_label(self) -> str:
        return next((label for value, label in _QUALITY_CHOICES if value == self.quality), "High")

    def area_label(self) -> str:
        return next((label for key, label in _AREA_CHOICES if key == self.area), "Entire Screen")

    def mic_label(self) -> str:
        if not self.mic:
            return "Off"
        return "Default" if self.mic == "default" else self.mic


# --------------------------------------------------------------------------- #
# PCM helpers (16-bit stereo 48 kHz is what the AVI audio stream carries)
# --------------------------------------------------------------------------- #
def pcm16_silence(frames: int) -> bytes:
    return bytes(max(0, int(frames)) * AUDIO_BLOCK)


def pcm16_resample(raw: bytes, src_rate: int, dst_rate: int = AUDIO_RATE) -> bytes:
    raw = raw[: len(raw) - len(raw) % AUDIO_BLOCK]
    if not raw or src_rate <= 0 or src_rate == dst_rate:
        return raw
    src = array("h")
    src.frombytes(raw)
    frames = len(src) // AUDIO_CHANNELS
    if frames <= 0:
        return b""
    dst_frames = max(1, int(round(frames * dst_rate / src_rate)))
    out = array("h", bytes(dst_frames * AUDIO_BLOCK))
    step = src_rate / dst_rate
    last = frames - 1
    for i in range(dst_frames):
        pos = i * step
        j = int(pos)
        frac = pos - j
        j2 = j + 1 if j < last else last
        for ch in range(AUDIO_CHANNELS):
            a = src[j * AUDIO_CHANNELS + ch]
            b = src[j2 * AUDIO_CHANNELS + ch]
            out[i * AUDIO_CHANNELS + ch] = a + int((b - a) * frac)
    return out.tobytes()


def pcm16_stereo(raw: bytes, channels: int) -> bytes:
    if channels <= 0 or channels == AUDIO_CHANNELS or not raw:
        return raw
    width = 2 * channels
    raw = raw[: len(raw) - len(raw) % width]
    src = array("h")
    src.frombytes(raw)
    frames = len(src) // channels
    out = array("h", bytes(frames * AUDIO_BLOCK))
    if channels == 1:
        for i in range(frames):
            sample = src[i]
            out[i * 2] = sample
            out[i * 2 + 1] = sample
    else:
        for i in range(frames):
            out[i * 2] = src[i * channels]
            out[i * 2 + 1] = src[i * channels + 1]
    return out.tobytes()


def pcm16_from_float32(raw: bytes) -> bytes:
    raw = raw[: len(raw) - len(raw) % 4]
    src = array("f")
    src.frombytes(raw)
    return array("h", (int(max(-1.0, min(1.0, value)) * 32767.0) for value in src)).tobytes()


def pcm16_from_int32(raw: bytes) -> bytes:
    raw = raw[: len(raw) - len(raw) % 4]
    src = array("i")
    src.frombytes(raw)
    return array("h", (value >> 16 for value in src)).tobytes()


def pcm16_mix(a: bytes, b: bytes) -> bytes:
    """Sum two PCM16 streams with headroom so a loud mix cannot clip (and distort)."""
    size = min(len(a), len(b))
    size -= size % 4
    if size <= 0:
        return b""
    left = array("h")
    left.frombytes(a[:size])
    right = array("h")
    right.frombytes(b[:size])
    return array("h", (max(-32768, min(32767, (x * 7 + y * 7) // 10))
                       for x, y in zip(left, right))).tobytes()


class _HighPass:
    """Two cascaded one-pole high-passes: removes DC and the boomy sub-bass rumble."""

    def __init__(self, cutoff: float = 90.0, rate: int = AUDIO_RATE):
        self._a = math.exp(-2.0 * math.pi * max(1.0, cutoff) / max(1, rate))
        self._stage_in = [[0.0] * AUDIO_CHANNELS, [0.0] * AUDIO_CHANNELS]
        self._stage_out = [[0.0] * AUDIO_CHANNELS, [0.0] * AUDIO_CHANNELS]

    def process(self, pcm: bytes) -> bytes:
        pcm = pcm[: len(pcm) - len(pcm) % AUDIO_BLOCK]
        if not pcm:
            return b""
        src = array("h")
        src.frombytes(pcm)
        a = self._a
        cur = src
        for stage in range(2):
            pin, pout = self._stage_in[stage], self._stage_out[stage]
            out = array("h", bytes(len(cur) * 2))
            for i in range(0, len(cur), AUDIO_CHANNELS):
                for ch in range(AUDIO_CHANNELS):
                    x = float(cur[i + ch])
                    y = a * (pout[ch] + x - pin[ch])
                    pin[ch], pout[ch] = x, y
                    out[i + ch] = max(-32768, min(32767, int(y)))
            cur = out
        return cur.tobytes()


# --------------------------------------------------------------------------- #
# System audio: WASAPI loopback of the default playback device
# --------------------------------------------------------------------------- #
class _WaveFormatEx(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class _WaveFormatExtensible(ctypes.Structure):
    _fields_ = [("Format", _WaveFormatEx), ("wValidBitsPerSample", wintypes.WORD),
                ("dwChannelMask", wintypes.DWORD), ("SubFormat", _GUID)]


_IID_IAudioClient = _GUID.from_str("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")
_IID_IAudioCaptureClient = _GUID.from_str("C8ADBD64-E71E-48A0-A4DE-185C395CD317")
_FLOAT_SUBFORMAT = _GUID.from_str("00000003-0000-0010-8000-00AA00389B71")
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None


def _guid_bytes(guid: _GUID) -> bytes:
    return ctypes.string_at(ctypes.byref(guid), ctypes.sizeof(_GUID))


class SystemAudioCapture:
    """Captures whatever the speakers play, using WASAPI loopback (no packages)."""

    def __init__(self):
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stop = False
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="liquify-loopback", daemon=True)

    @property
    def error(self):
        return self._error

    def start(self):
        self._thread.start()

    def read(self) -> bytes:
        with self._lock:
            if not self._buffer:
                return b""
            out = bytes(self._buffer)
            self._buffer.clear()
            return out

    def stop(self):
        self._stop = True

    def _run(self):
        enum_ptr = ctypes.c_void_p()
        dev_ptr = ctypes.c_void_p()
        client_ptr = ctypes.c_void_p()
        capture_ptr = ctypes.c_void_p()
        fmt_ptr = ctypes.c_void_p()
        started = False
        tag = bits = channels = rate = block = 0
        try:
            if _ole32.CoInitializeEx(None, 0) not in (0, 1):     # MTA
                raise OSError("audio init failed")
            if _ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDeviceEnumerator), None, 1,
                                       ctypes.byref(_IID_IMMDeviceEnumerator),
                                       ctypes.byref(enum_ptr)) != 0:
                raise OSError("audio endpoint unavailable")
            get_default = _com_method(enum_ptr, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int,
                                      ctypes.POINTER(ctypes.c_void_p))
            if get_default(enum_ptr, 0, 0, ctypes.byref(dev_ptr)) != 0 or not dev_ptr.value:
                raise OSError("no playback device")
            activate = _com_method(dev_ptr, 3, ctypes.HRESULT, ctypes.POINTER(_GUID),
                                   wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p))
            if activate(dev_ptr, ctypes.byref(_IID_IAudioClient), 1, None,
                        ctypes.byref(client_ptr)) != 0:
                raise OSError("audio client unavailable")
            get_format = _com_method(client_ptr, 8, ctypes.HRESULT,
                                     ctypes.POINTER(ctypes.c_void_p))
            if get_format(client_ptr, ctypes.byref(fmt_ptr)) != 0 or not fmt_ptr.value:
                raise OSError("audio format unavailable")
            wfx = ctypes.cast(fmt_ptr, ctypes.POINTER(_WaveFormatEx)).contents
            tag, bits = int(wfx.wFormatTag), int(wfx.wBitsPerSample)
            channels = int(wfx.nChannels) or AUDIO_CHANNELS
            rate = int(wfx.nSamplesPerSec) or AUDIO_RATE
            block = int(wfx.nBlockAlign) or max(1, channels * bits // 8)
            if tag == 0xFFFE:                                     # WAVE_FORMAT_EXTENSIBLE
                ext = ctypes.cast(fmt_ptr, ctypes.POINTER(_WaveFormatExtensible)).contents
                tag = 3 if _guid_bytes(ext.SubFormat) == _guid_bytes(_FLOAT_SUBFORMAT) else 1

            initialize = _com_method(client_ptr, 3, ctypes.HRESULT, ctypes.c_int, wintypes.DWORD,
                                     ctypes.c_longlong, ctypes.c_longlong, ctypes.c_void_p,
                                     ctypes.c_void_p)
            if initialize(client_ptr, 0, 0x00020000, 2_000_000, 0, fmt_ptr, None) != 0:
                raise OSError("audio stream unavailable")
            get_service = _com_method(client_ptr, 14, ctypes.HRESULT, ctypes.POINTER(_GUID),
                                      ctypes.POINTER(ctypes.c_void_p))
            if get_service(client_ptr, ctypes.byref(_IID_IAudioCaptureClient),
                           ctypes.byref(capture_ptr)) != 0:
                raise OSError("audio capture unavailable")
            if _com_method(client_ptr, 10, ctypes.HRESULT)(client_ptr) != 0:
                raise OSError("audio start failed")
            started = True

            next_size = _com_method(capture_ptr, 5, ctypes.HRESULT,
                                    ctypes.POINTER(ctypes.c_uint32))
            get_buffer = _com_method(capture_ptr, 3, ctypes.HRESULT,
                                     ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
                                     ctypes.POINTER(ctypes.c_uint32),
                                     ctypes.POINTER(wintypes.DWORD),
                                     ctypes.POINTER(ctypes.c_uint64),
                                     ctypes.POINTER(ctypes.c_uint64))
            release = _com_method(capture_ptr, 4, ctypes.HRESULT, ctypes.c_uint32)
            while not self._stop:
                packet = ctypes.c_uint32(0)
                if next_size(capture_ptr, ctypes.byref(packet)) != 0:
                    break
                if not packet.value:
                    time.sleep(0.01)
                    continue
                data_ptr = ctypes.POINTER(ctypes.c_ubyte)()
                frames = ctypes.c_uint32(0)
                flags = wintypes.DWORD(0)
                device_pos = ctypes.c_uint64(0)
                qpc = ctypes.c_uint64(0)
                if get_buffer(capture_ptr, ctypes.byref(data_ptr), ctypes.byref(frames),
                              ctypes.byref(flags), ctypes.byref(device_pos),
                              ctypes.byref(qpc)) != 0:
                    break
                count = int(frames.value)
                if count:
                    if flags.value & 0x2:                          # AUDCLNT_BUFFERFLAGS_SILENT
                        pcm = pcm16_silence(count)
                    else:
                        pcm = self._to_pcm16(ctypes.string_at(data_ptr, count * block),
                                             tag, bits, channels)
                    pcm = pcm16_resample(pcm16_stereo(pcm, channels), rate)
                    if pcm:
                        with self._lock:
                            self._buffer.extend(pcm)
                release(capture_ptr, count)
        except OSError as exc:
            self._error = str(exc)
        except Exception as exc:
            self._error = f"System audio unavailable ({type(exc).__name__}: {exc})"
        finally:
            if started and client_ptr.value:
                try:
                    _com_method(client_ptr, 11, ctypes.HRESULT)(client_ptr)
                except Exception:
                    pass
            for ptr in (capture_ptr, client_ptr, dev_ptr, enum_ptr):
                if ptr.value:
                    try:
                        _com_method(ptr, 2, wintypes.ULONG)(ptr)
                    except Exception:
                        pass
            if fmt_ptr.value:
                try:
                    _ole32.CoTaskMemFree(fmt_ptr)
                except Exception:
                    pass
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass

    @staticmethod
    def _to_pcm16(raw: bytes, tag: int, bits: int, channels: int) -> bytes:
        if tag == 3 and bits == 32:
            return pcm16_from_float32(raw)
        if tag == 1:
            if bits == 16:
                return raw
            if bits == 32:
                return pcm16_from_int32(raw)
            if bits == 24:
                return array("h", (int.from_bytes(raw[i:i + 3], "little", signed=True) >> 8
                                   for i in range(0, len(raw) - 2, 3))).tobytes()
            if bits == 8:
                return array("h", ((value - 128) << 8 for value in raw)).tobytes()
        return b""


# --------------------------------------------------------------------------- #
# Microphone: Qt Multimedia (device list comes free with it)
# --------------------------------------------------------------------------- #
def microphone_names() -> list[str]:
    if not HAVE_AUDIO_IN:
        return []
    try:
        return [device.description() for device in QMediaDevices.audioInputs()]
    except Exception:
        return []


class MicCapture(QObject):
    data = Signal(bytes)

    def __init__(self, device_name: str = "default", parent=None):
        super().__init__(parent)
        if not HAVE_AUDIO_IN:
            raise OSError("microphone capture unavailable")
        device = QMediaDevices.defaultAudioInput()
        if device_name and device_name not in ("default", ""):
            for candidate in QMediaDevices.audioInputs():
                if candidate.description() == device_name:
                    device = candidate
                    break
        if device.isNull():
            raise OSError("no microphone")
        fmt = QAudioFormat()
        fmt.setSampleRate(AUDIO_RATE)
        fmt.setChannelCount(AUDIO_CHANNELS)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not device.isFormatSupported(fmt):
            fmt = device.preferredFormat()
        self._rate = max(1, fmt.sampleRate())
        self._channels = max(1, fmt.channelCount())
        self._format = fmt.sampleFormat()
        self._source = QAudioSource(device, fmt, self)
        self._io = self._source.start()
        if self._io is None:
            raise OSError("microphone unavailable")
        self._io.readyRead.connect(self._read)

    def _read(self):
        try:
            data = bytes(self._io.readAll())
        except Exception:
            data = b""
        if data:
            self.data.emit(self._to_pcm16(data))

    def _to_pcm16(self, data: bytes) -> bytes:
        fmt = self._format
        if fmt == QAudioFormat.Int16:
            pcm = data
        elif fmt == QAudioFormat.Int32:
            pcm = pcm16_from_int32(data)
        elif fmt == QAudioFormat.Float:
            pcm = pcm16_from_float32(data)
        elif fmt == QAudioFormat.UInt8:
            pcm = array("h", ((value - 128) << 8 for value in data)).tobytes()
        else:
            pcm = b""
        return pcm16_resample(pcm16_stereo(pcm, self._channels), self._rate)

    def stop(self):
        try:
            self._source.stop()
        except Exception:
            pass


class AudioSession(QObject):
    """Mixes mic + system audio into one PCM stream and hands it to the writer."""

    def __init__(self, mic: str, system_audio: bool, sink, parent=None):
        super().__init__(parent)
        self.sink = sink
        self.error = None
        self._paused = False
        self._muted = False
        self._highpass = _HighPass()
        self._lock = threading.Lock()
        self._mic_buf = bytearray()
        self._sys_buf = bytearray()
        self._mic = None
        self._sys = None
        try:
            if mic:
                self._mic = MicCapture(mic, self)
                self._mic.data.connect(self._on_mic)
        except (OSError, RuntimeError):
            self._mic = None
            self.error = "Microphone unavailable"
        try:
            if system_audio:
                self._sys = SystemAudioCapture()
                self._sys.start()
        except Exception:
            self._sys = None
            self.error = self.error or "System audio unavailable"
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self.pump)
        self._timer.start()

    def set_paused(self, paused: bool):
        self._paused = bool(paused)

    def set_muted(self, muted: bool):
        self._muted = bool(muted)

    def _on_mic(self, chunk: bytes):
        with self._lock:
            self._mic_buf.extend(chunk)

    def pump(self):
        system = self._sys.read() if self._sys is not None else b""
        with self._lock:
            if system:
                self._sys_buf.extend(system)
            mic = bytes(self._mic_buf)
            sysb = bytes(self._sys_buf)
            self._mic_buf.clear()
            self._sys_buf.clear()
            if self._paused:
                return
            if self._muted:                      # keep A/V sync with silence, not dropped time
                size = max(len(mic), len(sysb))
                size -= size % AUDIO_BLOCK
                if size:
                    self.sink(pcm16_silence(size // AUDIO_BLOCK))
                return
            chunk = b""
            if mic and sysb:
                size = min(len(mic), len(sysb))
                size -= size % AUDIO_BLOCK
                if size:
                    chunk = pcm16_mix(mic[:size], sysb[:size])
                self._mic_buf.extend(mic[size:])
                self._sys_buf.extend(sysb[size:])
            elif mic:
                chunk = mic[: len(mic) - len(mic) % AUDIO_BLOCK]
            elif sysb:
                chunk = sysb[: len(sysb) - len(sysb) % AUDIO_BLOCK]
            if chunk:
                self.sink(self._highpass.process(chunk))

    def close(self):
        self._timer.stop()
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        if self._sys is not None:
            self._sys.stop()
            self._sys = None


class MjpegAviWriter:
    """Minimal AVI/MJPEG writer so recording needs no FFmpeg or extra wheels."""

    def __init__(self, path: Path, width: int, height: int, fps: int = REC_FPS,
                 quality: int = REC_QUALITY, audio: bool = False):
        self.path = Path(path)
        self.fps = max(1, int(fps))
        self.quality = max(40, min(95, int(quality)))
        self.width = max(2, int(width) - int(width) % 2)
        self.height = max(2, int(height) - int(height) % 2)
        self.audio = bool(audio)
        self.frames = 0
        self.audio_bytes = 0
        self._index: list[tuple[int, int, bytes]] = []
        self._closed = False
        self._movi_pos = 0
        self._audio_len_pos = 0
        self._lock = threading.RLock()
        self._fh = open(self.path, "wb")
        try:
            self._write_header()
        except Exception:
            self._fh.close()
            self._fh = None
            self._closed = True
            raise

    def _write_header(self):
        fh, w, h, fps = self._fh, self.width, self.height, self.fps
        audio_bps = AUDIO_RATE * AUDIO_BLOCK
        avih = struct.pack("<14I", int(1_000_000 / fps), audio_bps if self.audio else 0, 0, 0x10,
                           0, 0, 2 if self.audio else 1, 0, w, h, 0, 0, 0, 0)
        vstrh = (b"vids" + b"MJPG"
                 + struct.pack("<IHHIIIIIIII", 0, 0, 0, 0, 1, fps, 0, 0, 0, 0xFFFFFFFF, 0)
                 + struct.pack("<4h", 0, 0, w, h))
        vstrf = struct.pack("<IiiHH4sIiiII", 40, w, h, 1, 24, b"MJPG", w * h * 3, 0, 0, 0, 0)
        vstrl = b"strh" + struct.pack("<I", len(vstrh)) + vstrh
        vstrl += b"strf" + struct.pack("<I", len(vstrf)) + vstrf
        hdrl = b"avih" + struct.pack("<I", len(avih)) + avih
        hdrl += b"LIST" + struct.pack("<I", 4 + len(vstrl)) + b"strl" + vstrl

        astrh = astrf = astrl = b""
        if self.audio:
            astrh = (b"auds" + struct.pack("<I", 1)
                     + struct.pack("<IHHIIIIIIII", 0, 0, 0, 0, AUDIO_BLOCK, audio_bps,
                                   0, 0, 0, 0xFFFFFFFF, AUDIO_BLOCK)
                     + struct.pack("<4h", 0, 0, 0, 0))
            astrf = struct.pack("<HHIIHHH", 1, AUDIO_CHANNELS, AUDIO_RATE, audio_bps,
                                AUDIO_BLOCK, 16, 0)
            astrl = b"strh" + struct.pack("<I", len(astrh)) + astrh
            astrl += b"strf" + struct.pack("<I", len(astrf)) + astrf
            hdrl += b"LIST" + struct.pack("<I", 4 + len(astrl)) + b"strl" + astrl

        fh.write(b"RIFF")
        self._riff_size_pos = fh.tell()
        fh.write(struct.pack("<I", 0) + b"AVI ")
        fh.write(b"LIST" + struct.pack("<I", 4 + len(hdrl)) + b"hdrl")
        fh.write(b"avih" + struct.pack("<I", len(avih)))
        self._avih_frames_pos = fh.tell() + 16
        fh.write(avih)

        fh.write(b"LIST" + struct.pack("<I", 4 + len(vstrl)) + b"strl")
        fh.write(b"strh" + struct.pack("<I", len(vstrh)))
        self._strh_len_pos = fh.tell() + 32
        fh.write(vstrh)
        fh.write(b"strf" + struct.pack("<I", len(vstrf)) + vstrf)

        if self.audio:
            fh.write(b"LIST" + struct.pack("<I", 4 + len(astrl)) + b"strl")
            fh.write(b"strh" + struct.pack("<I", len(astrh)))
            self._audio_len_pos = fh.tell() + 32
            fh.write(astrh)
            fh.write(b"strf" + struct.pack("<I", len(astrf)) + astrf)

        fh.write(b"LIST")
        self._movi_size_pos = fh.tell()
        fh.write(struct.pack("<I", 0) + b"movi")
        self._movi_pos = fh.tell() - 4

    def size_locked(self) -> int:
        try:
            return self._fh.tell() if self._fh is not None else 0
        except (OSError, ValueError):
            return 0

    def size(self) -> int:
        with self._lock:
            return self.size_locked()

    def write_frame(self, image: QImage, elapsed: float | None = None) -> bool:
        """Encode one frame; duplicates it as needed so playback stays real-time."""
        if self._closed or self._fh is None:
            return False
        img = image
        if img.width() != self.width or img.height() != self.height:
            img = img.scaled(self.width, self.height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if img.format() != QImage.Format_RGB888:
            img = img.convertToFormat(QImage.Format_RGB888)
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        try:
            if not img.save(buf, "JPG", self.quality):
                return False
            data = bytes(buf.data())
        finally:
            buf.close()
        if not data:
            return False

        with self._lock:
            if self._closed or self._fh is None:
                return False
            repeat = 1
            if elapsed is not None:
                repeat = max(1, min(self.fps, int(elapsed * self.fps) + 1 - self.frames))
            for _ in range(repeat):
                if self.size_locked() + len(data) + 16 > REC_MAX_BYTES:
                    return False
                offset = self._fh.tell()
                self._fh.write(b"00dc" + struct.pack("<I", len(data)) + data)
                if len(data) & 1:
                    self._fh.write(b"\x00")
                self._index.append((offset - self._movi_pos, len(data), b"00dc"))
                self.frames += 1
        return True

    def write_audio(self, pcm: bytes) -> bool:
        """Append one PCM16 stereo block; called from the audio thread."""
        if not self.audio or not pcm:
            return False
        pcm = pcm[: len(pcm) - len(pcm) % AUDIO_BLOCK]
        if not pcm:
            return False
        with self._lock:
            if self._closed or self._fh is None:
                return False
            if self.size_locked() + len(pcm) + 16 > REC_MAX_BYTES:
                return False
            offset = self._fh.tell()
            self._fh.write(b"01wb" + struct.pack("<I", len(pcm)) + pcm)
            if len(pcm) & 1:
                self._fh.write(b"\x00")
            self._index.append((offset - self._movi_pos, len(pcm), b"01wb"))
            self.audio_bytes += len(pcm)
        return True

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fh, self._fh = self._fh, None
            if fh is None:
                return
            try:
                idx_pos = fh.tell()
                fh.write(b"idx1" + struct.pack("<I", 16 * len(self._index)))
                for offset, size, cid in self._index:
                    keyframe = 0x10 if cid == b"00dc" else 0
                    fh.write(cid + struct.pack("<III", keyframe, offset, size))
                end = fh.tell()
                fh.seek(self._movi_size_pos)
                fh.write(struct.pack("<I", idx_pos - self._movi_pos))
                fh.seek(self._riff_size_pos)
                fh.write(struct.pack("<I", end - 8))
                fh.seek(self._avih_frames_pos)
                fh.write(struct.pack("<I", self.frames))
                fh.seek(self._strh_len_pos)
                fh.write(struct.pack("<I", self.frames))
                if self.audio and self._audio_len_pos:
                    fh.seek(self._audio_len_pos)
                    fh.write(struct.pack("<I", self.audio_bytes // AUDIO_BLOCK))
                fh.flush()
            finally:
                fh.close()


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]


_gdi32 = ctypes.windll.gdi32
_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BitmapInfo), wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int

_SRCCOPY_CAPTUREBLT = 0x00CC0020 | 0x40000000


class _CursorInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


_user32.GetCursorInfo.argtypes = [ctypes.POINTER(_CursorInfo)]
_user32.GetCursorInfo.restype = wintypes.BOOL
_user32.DrawIconEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HANDLE,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.HANDLE,
                               wintypes.UINT]
_user32.DrawIconEx.restype = wintypes.BOOL


def draw_pointer(dc, origin_x: int, origin_y: int):
    """Paint the live cursor into a capture DC, macOS 'show pointer' style."""
    info = _CursorInfo()
    info.cbSize = ctypes.sizeof(_CursorInfo)
    if not _user32.GetCursorInfo(ctypes.byref(info)):
        return
    if not (info.flags & 0x0001) or not info.hCursor:
        return
    _user32.DrawIconEx(dc, info.ptScreenPos.x - int(origin_x), info.ptScreenPos.y - int(origin_y),
                       info.hCursor, 0, 0, 0, None, 0x0003)


def primary_screen_rect():
    """Physical (x, y, w, h) of the display the island lives on."""
    w = int(_user32.GetSystemMetrics(0))
    h = int(_user32.GetSystemMetrics(1))
    if w < 16 or h < 16:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        dpr = screen.devicePixelRatio() or 1.0
        geo = screen.geometry()
        w, h = int(round(geo.width() * dpr)), int(round(geo.height() * dpr))
    return (0, 0, w, h)


class _GdiScreen:
    """GDI desktop blit into a DIBSection: ~2.5x faster than QScreen.grabWindow."""

    def __init__(self, x: int, y: int, width: int, height: int):
        self.width = max(2, int(width) - int(width) % 2)
        self.height = max(2, int(height) - int(height) % 2)
        self._x, self._y = int(x), int(y)
        self._sdc = _user32.GetDC(None)
        self._mdc = None
        self._bmp = None
        self._old = None
        self._bits = ctypes.c_void_p(0)
        try:
            self._mdc = _gdi32.CreateCompatibleDC(self._sdc)
            bmi = _BitmapInfo()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            bmi.bmiHeader.biWidth = self.width
            bmi.bmiHeader.biHeight = -self.height          # top-down rows
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            self._bmp = _gdi32.CreateDIBSection(self._mdc, ctypes.byref(bmi), 0,
                                                ctypes.byref(self._bits), None, 0)
            if not self._sdc or not self._mdc or not self._bmp or not self._bits.value:
                raise OSError("screen capture unavailable")
            self._old = _gdi32.SelectObject(self._mdc, self._bmp)
        except Exception:
            self.close()
            raise

    def grab(self, pointer: bool = False):
        if not self._mdc or not self._bits.value:
            return None
        if not _gdi32.BitBlt(self._mdc, 0, 0, self.width, self.height,
                             self._sdc, self._x, self._y, _SRCCOPY_CAPTUREBLT):
            return None
        if pointer:
            draw_pointer(self._mdc, self._x, self._y)
        raw = ctypes.string_at(self._bits.value, self.width * self.height * 4)
        return QImage(raw, self.width, self.height, self.width * 4,
                      QImage.Format_RGB32).copy()

    def close(self):
        if self._mdc and self._bmp:
            if self._old:
                _gdi32.SelectObject(self._mdc, self._old)
            _gdi32.DeleteObject(self._bmp)
        if self._mdc:
            _gdi32.DeleteDC(self._mdc)
        if self._sdc:
            _user32.ReleaseDC(None, self._sdc)
        self._mdc = self._bmp = self._old = None
        self._bits = ctypes.c_void_p(0)


class Mp4Writer(QObject):
    """Writes H.264/AAC .mp4 through Qt's own media backend (no external tools).

    Frames and PCM arrive from the capture/audio threads but Qt's encoder objects live in
    the GUI thread, so the bytes are marshalled across with queued signals.
    """

    _frameReady = Signal(object, int)
    _audioReady = Signal(bytes, int)
    _stopRequested = Signal()

    def __init__(self, path: Path, width: int, height: int, fps: int = REC_FPS,
                 quality: int = REC_QUALITY, audio: bool = False):
        super().__init__()
        if not HAVE_MP4:
            raise OSError("mp4 writer unavailable")
        self.path = Path(path)
        self.fps = max(1, min(60, int(fps)))
        self.quality = max(40, min(95, int(quality)))
        self.width = max(2, int(width) - int(width) % 2)
        self.height = max(2, int(height) - int(height) % 2)
        self.audio = bool(audio)
        self.frames = 0
        self.audio_bytes = 0
        self._audio_us = 0
        self.error: str | None = None
        self._closed = False
        self._stopping = False
        self._lock = threading.Lock()
        self._inflight = 0
        self.dropped = 0
        self._size_check = 0
        self._too_big = False

        fmt = QMediaFormat()
        fmt.setFileFormat(QMediaFormat.FileFormat.MPEG4)
        fmt.setVideoCodec(QMediaFormat.VideoCodec.H264)
        fmt.setAudioCodec(QMediaFormat.AudioCodec.AAC if self.audio
                          else QMediaFormat.AudioCodec.Unspecified)
        if not fmt.isSupported(QMediaFormat.ConversionMode.Encode):
            raise OSError("mp4 encoding unsupported")

        self._video_format = QVideoFrameFormat(
            QSize(self.width, self.height), QVideoFrameFormat.PixelFormat.Format_RGBA8888)
        self._video_in = QVideoFrameInput(self._video_format)
        self._audio_format = QAudioFormat()
        self._audio_format.setSampleRate(AUDIO_RATE)
        self._audio_format.setChannelCount(AUDIO_CHANNELS)
        self._audio_format.setSampleFormat(QAudioFormat.Int16)
        self._audio_in = QAudioBufferInput() if self.audio else None

        self._recorder = QMediaRecorder()
        self._recorder.setMediaFormat(fmt)
        self._recorder.setVideoResolution(self.width, self.height)
        self._recorder.setVideoFrameRate(self.fps)
        self._recorder.setVideoBitRate(self._bitrate())
        self._recorder.setQuality(QMediaRecorder.Quality.NormalQuality)
        self._session = QMediaCaptureSession()
        self._session.setVideoFrameInput(self._video_in)
        if self._audio_in is not None:
            self._session.setAudioBufferInput(self._audio_in)
        self._session.setRecorder(self._recorder)
        self._recorder.errorOccurred.connect(self._on_recorder_error)

        self._frameReady.connect(self._push_frame)
        self._audioReady.connect(self._push_audio)
        self._stopRequested.connect(self._finish, Qt.BlockingQueuedConnection)

        self._recorder.setOutputLocation(QUrl.fromLocalFile(str(self.path)))
        self._recorder.record()

    # ------------------------------------------------------------- helpers #
    def _bitrate(self) -> int:
        raw = self.width * self.height * self.fps * 0.12 * (self.quality / 82.0)
        return int(max(2_000_000, min(40_000_000, raw)))

    def _on_recorder_error(self, _error, message):
        self.error = message or "encoder error"

    def size_locked(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def size(self) -> int:
        return self.size_locked()

    # -------------------------------------------------------------- writing #
    def write_frame(self, image: QImage, elapsed: float | None = None) -> bool:
        if self._closed or self._too_big or self.error:
            return False
        with self._lock:
            if self._inflight > 2:                 # encoder is behind: drop this frame
                self.dropped += 1
                return True
            self._inflight += 1
        img = image
        if img.width() != self.width or img.height() != self.height:
            img = img.scaled(self.width, self.height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if img.format() != QImage.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format_RGBA8888)
        self.frames += 1
        start_us = int(max(0.0, float(elapsed or 0.0)) * 1_000_000)
        self._frameReady.emit(img, start_us)
        if self.frames % 20 == 0 and self.size_locked() > REC_MAX_BYTES:
            self._too_big = True
        return True

    def write_audio(self, pcm: bytes) -> bool:
        if not self.audio or not pcm or self._closed:
            return False
        pcm = pcm[: len(pcm) - len(pcm) % AUDIO_BLOCK]
        if not pcm:
            return False
        self.audio_bytes += len(pcm)
        frames = len(pcm) // AUDIO_BLOCK
        start_us, self._audio_us = self._audio_us, self._audio_us + frames * 1_000_000 // AUDIO_RATE
        self._audioReady.emit(pcm, start_us)
        return True

    @Slot(object, int)
    def _push_frame(self, image, start_us: int):
        if self._closed or self._stopping:
            return
        try:
            frame = QVideoFrame(image)
            frame.setStartTime(int(start_us))
            frame.setEndTime(int(start_us) + int(1_000_000 / max(1, self.fps)))
            self._video_in.sendVideoFrame(frame)
        except Exception:
            pass
        finally:
            with self._lock:
                self._inflight = max(0, self._inflight - 1)

    @Slot(bytes, int)
    def _push_audio(self, pcm, start_us: int):
        if self._closed or self._stopping or self._audio_in is None:
            return
        try:
            self._audio_in.sendAudioBuffer(
                QAudioBuffer(pcm, self._audio_format, int(start_us)))
        except Exception:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if QThread.currentThread() is self.thread():
            self._finish()
        else:
            self._stopRequested.emit()

    @Slot()
    def _finish(self):
        if self._stopping:
            return
        self._stopping = True
        try:
            self._recorder.stop()
        except Exception:
            pass
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
            if self._recorder.recorderState() == QMediaRecorder.RecorderState.StoppedState:
                break
            time.sleep(0.01)
        if not self.error and self.size_locked() < 1024:
            self.error = "encoder produced no video"


class ScreenRecorder(QThread):
    """Records a display region to the supplied AVI writer, off the UI thread."""

    progress = Signal(float)

    def __init__(self, writer, area, fps: int = REC_FPS, pointer: bool = False, parent=None):
        super().__init__(parent)
        self.writer = writer
        self._area = tuple(int(value) for value in area)
        self.fps = max(5, min(60, int(fps)))
        self.pointer = bool(pointer)
        self.seconds = 0.0
        self.frames = 0
        self.error: str | None = None
        self.paused = False
        self._pause_req = False
        self._stopping = False
        self.saved_path: Path | None = None
        self.warn: str | None = None

    def request_stop(self):
        self._stopping = True

    def request_pause(self, paused: bool):
        self._pause_req = bool(paused)

    def run(self):
        cap = None
        try:
            cap = _GdiScreen(*self._area)
            period = 1.0 / self.fps
            last = time.monotonic()
            while not self._stopping:
                now = time.monotonic()
                self.paused = self._pause_req
                if self.paused:
                    last = now
                    self.msleep(30)
                    continue
                self.seconds += now - last
                last = now
                if self.seconds >= REC_MAX_S:
                    self.error = f"Reached the {REC_MAX_S // 60} minute limit"
                    break
                img = cap.grab(pointer=self.pointer)
                if img is None:
                    self.msleep(40)
                    continue
                if not self.writer.write_frame(img, self.seconds):
                    self.error = "Reached the video size limit"
                    break
                self.frames = self.writer.frames
                self.progress.emit(self.seconds)
                deadline = now + period
                while (not self._stopping and not self._pause_req
                       and time.monotonic() < deadline):
                    self.msleep(2)
        except OSError:
            self.error = self.error or "Could not write the video file"
        except Exception:
            self.error = self.error or "Screen capture failed"
        finally:
            if cap is not None:
                cap.close()
            try:
                self.writer.close()
                self.frames = self.writer.frames
            except OSError:
                self.error = self.error or "Could not finish the video file"
            self.saved_path = Path(self.writer.path)
            if getattr(self.writer, "error", None):
                self.error = self.error or self.writer.error


# --------------------------------------------------------------------------- #
# Bluetooth model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BluetoothAudioDevice:
    id: str
    name: str
    dev_type: str
    product: str
    battery: int | None
    mac: str = ""


_HEADPHONE_WORDS = ("headphone", "headset", "wh-", "xm4", "xm5", "xm3", "quietcomfort",
                    "studio", "over-ear", "px7", "px8", "momentum", "bose", "sennheiser",
                    "marshall", "beats")
_EARBUD_WORDS = ("buds", "pods", "earbud", "earphone", "tws", "ear (", "freebuds", "wf-",
                 "airdopes", "neckband", "rockerz", "bassheads", "soundcore", "jabra")
_SPEAKER_WORDS = ("speaker", "soundbar", "boom", "flip", "partybox", "homepod", "echo",
                  "sound", "charge ", "wonderboom", "megaboom")


def classify_audio(cod_raw: int, name: str) -> str | None:
    n = (name or "").lower()
    by_name = ("Earphones" if any(k in n for k in _EARBUD_WORDS) else
               "Headphones" if any(k in n for k in _HEADPHONE_WORDS) else
               "Speaker" if any(k in n for k in _SPEAKER_WORDS) else None)
    major, minor = (cod_raw >> 8) & 0x1F, (cod_raw >> 2) & 0x3F
    if major != 4:
        return by_name
    cod_type = {1: "Headset", 2: "Hands-free", 4: "Microphone", 5: "Speaker", 6: "Headphones",
                7: "Speaker", 8: "Car audio", 10: "Speaker"}.get(minor)
    if cod_type in ("Headset", "Hands-free"):
        return by_name or "Earphones"
    return cod_type or by_name or "Audio device"


def device_icon_kind(dev_type: str) -> str:
    return {"Earphones": "earbuds", "Hands-free": "earbuds", "Headphones": "headphones",
            "Headset": "headphones", "Speaker": "speaker", "Car audio": "speaker"}.get(
        dev_type, "headphones")


def product_name(manufacturer, model, name: str) -> str:
    generic = {"", "microsoft", "generic", "bluetooth", "bluetooth peripheral device",
               "(standard system devices)"}
    manu, model = str(manufacturer or "").strip(), str(model or "").strip()
    if model.lower() in generic:
        model = ""
    if model:
        if manu.lower() not in generic and manu.lower() not in model.lower():
            return f"{manu} {model}"
        return model
    if manu.lower() not in generic and manu.lower() not in name.lower():
        return f"{manu} {name}"
    return name


def _unbox_winrt(v):
    """Unwrap winsdk IPropertyValue / System.Object containers into native Python values."""
    if v is None or isinstance(v, (bool, int, float, str, bytes)):
        return v
    try:
        pv = IPropertyValue._from(v)
        t = pv.type
        if t == PropertyType.EMPTY:
            return None
        if t == PropertyType.BOOLEAN:
            return pv.get_boolean()
        if t == PropertyType.UINT8:
            return pv.get_uint8()
        if t == PropertyType.INT16:
            return pv.get_int16()
        if t == PropertyType.UINT16:
            return pv.get_uint16()
        if t == PropertyType.INT32:
            return pv.get_int32()
        if t == PropertyType.UINT32:
            return pv.get_uint32()
        if t == PropertyType.INT64:
            return pv.get_int64()
        if t == PropertyType.UINT64:
            return pv.get_uint64()
        if t == PropertyType.SINGLE:
            return pv.get_single()
        if t == PropertyType.DOUBLE:
            return pv.get_double()
        if t == PropertyType.CHAR16:
            return pv.get_char16()
        if t == PropertyType.STRING:
            return pv.get_string()
        if t == PropertyType.GUID:
            return str(pv.get_guid())
    except Exception:
        pass
    return v


def _prop(info, key, default=None):
    try:
        m = info.properties
        if hasattr(m, "has_key") and not m.has_key(key):
            return default
        v = m.lookup(key) if hasattr(m, "lookup") else m[key]
        v = _unbox_winrt(v)
        return default if v is None else v
    except Exception:
        return default


def _bool_prop(info, key, default=None):
    """Read a WinRT boolean without treating missing metadata as False."""
    value = _prop(info, key, default)
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def _is_paired(info) -> bool:
    """AEPs may omit IsPaired from the property map."""
    value = _bool_prop(info, "System.Devices.Aep.IsPaired")
    if value is not None:
        return value
    try:
        return bool(info.pairing and info.pairing.is_paired)
    except Exception:
        return False


def _norm_id(x) -> str:
    return str(x or "").strip("{}").lower()


# Audio profiles that make a Bluetooth device an audio output. Windows creates a
# separate BTHENUM child node per profile, and on some builds those nodes are the
# only place that records a paired device can play sound.
_BT_AUDIO_SERVICES = (
    "0000110b",   # A2DP sink
    "0000110c",   # AVRCP target
    "00001108",   # Headset (HSP)
    "0000111e",   # Hands-free (HFP)
)

_BT_MAC_RX = re.compile(r"(?:Dev_|BluetoothDevice_|&)([0-9A-Fa-f]{12})(?=[_\\]|$)")
_BT_NODE_SUFFIX_RX = re.compile(
    r"\s*[-,]?\s*(?:avrcp(?:\s+transport)?|hands[- ]?free(?:\s+ag)?"
    r"|a2dp(?:\s+(?:sink|source))?|hfp(?:\s+ag)?|hsp|audio\s+sink)$", re.IGNORECASE)


def bluetooth_mac(instance_id: str) -> str:
    """Bluetooth address of a BTHENUM/BTHHFENUM/BTHLE device node, or ""."""
    for match in _BT_MAC_RX.finditer(instance_id or ""):
        mac = match.group(1).upper()
        if mac.strip("0"):
            return mac
    return ""


_MAC_TEXT_RX = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}")


def bluetooth_mac_any(text: str) -> str:
    """Bluetooth address from any device id shape, including AEP ids that use
    colon-separated addresses (Bluetooth#Bluetooth90:A0:BE:...)."""
    mac = bluetooth_mac(text or "")
    if mac:
        return mac
    match = _MAC_TEXT_RX.search(text or "")
    return match.group(0).replace(":", "").replace("-", "").upper() if match else ""


class _BtAddress(ctypes.Structure):
    _fields_ = [("ullLong", ctypes.c_ulonglong)]


class _BtSystemTime(ctypes.Structure):
    _fields_ = [(name, wintypes.WORD) for name in
                ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
                 "wSecond", "wMilliseconds")]


class _BtDeviceInfo(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("Address", _BtAddress),
                ("ulClassofDevice", wintypes.ULONG),
                ("fConnected", wintypes.BOOL), ("fRemembered", wintypes.BOOL),
                ("fAuthenticated", wintypes.BOOL),
                ("stLastSeen", _BtSystemTime), ("stLastUsed", _BtSystemTime),
                ("szName", ctypes.c_wchar * 248)]


class _BtSearchParams(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("fReturnAuthenticated", wintypes.BOOL),
                ("fReturnRemembered", wintypes.BOOL), ("fReturnUnknown", wintypes.BOOL),
                ("fReturnConnected", wintypes.BOOL), ("fIssueInquiry", wintypes.BOOL),
                ("cTimeoutMultiplier", ctypes.c_ubyte), ("hRadio", wintypes.HANDLE)]


class _BtRadioParams(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD)]


# bthioctl.h: CTL_CODE(FILE_DEVICE_BLUETOOTH (0x41), 0x03, METHOD_BUFFERED,
# FILE_ANY_ACCESS).
_BTH_DISCONNECT_IOCTL = 0x41000C


def _close_bt_handle(handle):
    """Close a radio handle handed back by BluetoothFindFirstRadio."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(handle)
    except Exception:
        pass


def bluetooth_link_state(mac: str):
    """Live link state straight from the radio: True, False, or None if unseen.

    WinRT's BluetoothDevice queries can stall for seconds once the stack is
    holding a cached device, so the disconnect path asks the radio instead.
    """
    if not mac:
        return None
    try:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        dll = ctypes.WinDLL(os.path.join(root, "System32", "bthprops.cpl"))
        dll.BluetoothFindFirstRadio.argtypes = [ctypes.POINTER(_BtRadioParams),
                                               ctypes.POINTER(wintypes.HANDLE)]
        dll.BluetoothFindFirstRadio.restype = wintypes.HANDLE
        dll.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
        dll.BluetoothFindFirstDevice.argtypes = [ctypes.POINTER(_BtSearchParams),
                                                 ctypes.POINTER(_BtDeviceInfo)]
        dll.BluetoothFindFirstDevice.restype = wintypes.HANDLE
        dll.BluetoothFindNextDevice.argtypes = [wintypes.HANDLE,
                                                ctypes.POINTER(_BtDeviceInfo)]
        dll.BluetoothFindNextDevice.restype = wintypes.BOOL
        dll.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]

        params = _BtRadioParams()
        params.dwSize = ctypes.sizeof(params)
        radio = wintypes.HANDLE()
        find = dll.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(radio))
        if not find:
            return None
        device_find = None
        try:
            search = _BtSearchParams()
            search.dwSize = ctypes.sizeof(search)
            search.fReturnAuthenticated = True
            search.fReturnRemembered = True
            search.fReturnConnected = True
            search.hRadio = radio
            info = _BtDeviceInfo()
            info.dwSize = ctypes.sizeof(info)
            device_find = dll.BluetoothFindFirstDevice(ctypes.byref(search),
                                                       ctypes.byref(info))
            target = int(mac, 16)
            while device_find:
                if info.Address.ullLong == target:
                    return bool(info.fConnected)
                info = _BtDeviceInfo()
                info.dwSize = ctypes.sizeof(info)
                if not dll.BluetoothFindNextDevice(device_find, ctypes.byref(info)):
                    break
            return False
        finally:
            if device_find:
                dll.BluetoothFindDeviceClose(device_find)
            dll.BluetoothFindRadioClose(find)
            _close_bt_handle(radio)
    except Exception:
        return None


def bluetooth_disconnect_audio(mac: str) -> bool:
    """Drop the link to a paired device without disturbing the pairing.

    Windows has no public "disconnect" call, but the Bluetooth port driver
    accepts IOCTL_BTH_DISCONNECT_DEVICE on the radio handle and drops the ACL
    link. Flipping the device's audio services instead also stripped them from
    the paired record, so Windows' own Connect button then failed on it until
    the device had been paired again.
    """
    if not mac:
        return False
    try:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        dll = ctypes.WinDLL(os.path.join(root, "System32", "bthprops.cpl"))
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.BluetoothFindFirstRadio.argtypes = [ctypes.POINTER(_BtRadioParams),
                                               ctypes.POINTER(wintypes.HANDLE)]
        dll.BluetoothFindFirstRadio.restype = wintypes.HANDLE
        dll.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
        kernel32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.POINTER(wintypes.DWORD),
                                            ctypes.c_void_p]
        kernel32.DeviceIoControl.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        params = _BtRadioParams()
        params.dwSize = ctypes.sizeof(params)
        radio = wintypes.HANDLE()
        find = dll.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(radio))
        if not find:
            return False
        try:
            address = ctypes.c_ulonglong(int(mac, 16))
            returned = wintypes.DWORD()
            return bool(kernel32.DeviceIoControl(radio, _BTH_DISCONNECT_IOCTL,
                                                ctypes.byref(address), 8, None, 0,
                                                ctypes.byref(returned), None))
        finally:
            dll.BluetoothFindRadioClose(find)
            kernel32.CloseHandle(radio)
    except Exception:
        return False


def _bt_clean_name(name: str) -> str:
    """Drop the "… Avrcp Transport" / "… Hands-Free" suffixes Windows appends
    to the per-profile nodes of a Bluetooth device."""
    out = (name or "").strip()
    for _ in range(3):
        trimmed = _BT_NODE_SUFFIX_RX.sub("", out).strip(" -,–—")
        if not trimmed or trimmed == out:
            break
        out = trimmed
    return out


def _bt_node_name(names) -> str:
    """Friendly name from (priority, name) node labels: the device's own node
    beats a profile node, which beats anything else (serial ports and the like)."""
    for _, name in sorted(names, key=lambda item: item[0]):
        cleaned = _bt_clean_name(name)
        if cleaned:
            return cleaned
    return ""


# --------------------------------------------------------------------------- #
# Weather
# --------------------------------------------------------------------------- #
WEATHER_REFRESH_S = 900.0


@dataclass(frozen=True)
class WeatherState:
    city: str
    temp: float
    code: int
    is_day: bool = True


def weather_visual(code: int, is_day: bool = True) -> tuple[str, str]:
    """(icon kind, short label) for an Open-Meteo / WMO weather code."""
    if code in (0, 1):
        return ("sun" if is_day else "moon"), "Clear"
    if code == 2:
        return "partly", "Partly cloudy"
    if code == 3:
        return "cloud", "Overcast"
    if code in (45, 48):
        return "fog", "Fog"
    if 51 <= code <= 57:
        return "rain", "Drizzle"
    if 61 <= code <= 67 or 80 <= code <= 82:
        return "rain", "Rain"
    if 71 <= code <= 77 or 85 <= code <= 86:
        return "snow", "Snow"
    if code >= 95:
        return "storm", "Thunderstorm"
    return "cloud", "Cloudy"


def paint_weather_icon(p, rect: QRectF, kind: str, tint: QColor | None = None):
    """Small vector condition glyph, drawn into a 24x24 box."""
    warm = tint or QColor(255, 208, 122)
    cool = QColor(154, 202, 255, 235)
    p.save()
    p.translate(rect.left(), rect.top())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    p.setPen(Qt.NoPen)

    def sun(cx, cy, radius):
        p.setBrush(warm)
        p.drawEllipse(QPointF(cx, cy), radius, radius)
        p.setPen(QPen(warm, 1.7, Qt.SolidLine, Qt.RoundCap))
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(QPointF(cx + (radius + 2.2) * math.cos(a), cy + (radius + 2.2) * math.sin(a)),
                       QPointF(cx + (radius + 4.4) * math.cos(a), cy + (radius + 4.4) * math.sin(a)))
        p.setPen(Qt.NoPen)

    def cloud(dx=0.0, dy=0.0):
        # Opaque fills so the lobes merge into one clean silhouette.
        p.setBrush(QColor(238, 244, 255))
        p.drawEllipse(QPointF(8.7 + dx, 15.6 + dy), 3.5, 3.5)
        p.drawEllipse(QPointF(12.4 + dx, 13.4 + dy), 4.5, 4.5)
        p.drawEllipse(QPointF(16.2 + dx, 15.3 + dy), 3.4, 3.4)
        p.drawRoundedRect(QRectF(5.4 + dx, 14.0 + dy, 14.3, 5.2), 2.6, 2.6)

    if kind == "sun":
        sun(12, 12, 5.2)
    elif kind == "moon":
        disc = QPainterPath()
        disc.addEllipse(QPointF(12, 12), 6.6, 6.6)
        bite = QPainterPath()
        bite.addEllipse(QPointF(16.4, 8.4), 5.6, 5.6)
        p.setBrush(QColor(240, 246, 255, 240))
        p.drawPath(disc.subtracted(bite))
    elif kind == "partly":
        sun(9.4, 8.6, 3.4)
        cloud(1.4, 1.2)
    elif kind == "rain":
        cloud(0.0, -4.0)
        p.setPen(QPen(cool, 1.9, Qt.SolidLine, Qt.RoundCap))
        for x in (8.6, 12.2, 15.8):
            p.drawLine(QPointF(x + 0.8, 18.8), QPointF(x - 0.4, 22.2))
    elif kind == "snow":
        cloud(0.0, -4.0)
        p.setBrush(QColor(219, 240, 255, 240))
        for x in (8.8, 12.2, 15.6):
            p.drawEllipse(QPointF(x, 20.8), 1.2, 1.2)
    elif kind == "storm":
        cloud(0.0, -4.6)
        p.setBrush(warm)
        p.drawPolygon(QPolygonF([QPointF(13.2, 16.4), QPointF(9.4, 22.6), QPointF(12.2, 22.6),
                                 QPointF(10.8, 26.0), QPointF(15.8, 19.8),
                                 QPointF(12.8, 19.8)]))
    elif kind == "fog":
        p.setPen(QPen(QColor(255, 255, 255, 205), 1.9, Qt.SolidLine, Qt.RoundCap))
        for i, x0 in enumerate((5.0, 7.4, 5.8)):
            p.drawLine(QPointF(x0, 9.4 + i * 4.3), QPointF(24.0 - x0, 9.4 + i * 4.3))
    else:
        cloud()
    p.restore()


def _http_json(url: str, timeout: float = 7.0):
    req = urllib.request.Request(url, headers={"User-Agent": "LiquifyIsland/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _ip_place(timeout: float = 7.0):
    """(lat, lon, city) from a public IP lookup, or None when offline."""
    for url in ("https://ipwho.is/", "https://ipapi.co/json/"):
        try:
            data = _http_json(url, timeout)
            lat, lon = float(data["latitude"]), float(data["longitude"])
            city = str(data.get("city") or data.get("region") or "").strip()
            return lat, lon, city
        except Exception:
            continue
    return None


def _open_meteo(lat: float, lon: float, timeout: float = 7.0):
    """(temp_c, weather_code, is_day) for a coordinate, or None."""
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat:.4f}&longitude={lon:.4f}"
           "&current=temperature_2m,weather_code,is_day&timezone=auto")
    try:
        cur = _http_json(url, timeout).get("current") or {}
        return (float(cur["temperature_2m"]), int(cur["weather_code"]),
                bool(int(cur.get("is_day", 1))))
    except Exception:
        return None


def _reverse_place(lat: float, lon: float, timeout: float = 6.0) -> str:
    """City name for a coordinate, so a precise device fix is not labelled
    with the (often coarser) IP city."""
    try:
        data = _http_json("https://api.bigdatacloud.net/data/reverse-geocode-client"
                          f"?latitude={lat:.4f}&longitude={lon:.4f}&localityLanguage=en", timeout)
        return str(data.get("city") or data.get("locality")
                   or data.get("principalSubdivision") or "").strip()
    except Exception:
        return ""


async def _device_place():
    """(lat, lon) from the Windows location service, or None if not allowed."""
    if not HAVE_GEO:
        return None
    try:
        if await Geolocator.request_access_async() != GeolocationAccessStatus.ALLOWED:
            return None
        pos = await asyncio.wait_for(Geolocator().get_geoposition_async(), 8.0)
        if pos is None:
            return None
        return float(pos.coordinate.latitude), float(pos.coordinate.longitude)
    except Exception:
        return None


async def fetch_weather() -> WeatherState | None:
    """Current conditions for this machine's location (device GPS, else IP)."""
    device = await _device_place()
    ip = await asyncio.to_thread(_ip_place)
    if device:
        lat, lon = device
        city = await asyncio.to_thread(_reverse_place, lat, lon) or (ip[2] if ip else "")
    elif ip:
        lat, lon, city = ip
    else:
        return None
    wx = await asyncio.to_thread(_open_meteo, lat, lon)
    if wx is None:
        return None
    temp, code, is_day = wx
    return WeatherState(city=city or f"{lat:.2f}, {lon:.2f}", temp=temp, code=code, is_day=is_day)


# --------------------------------------------------------------------------- #
# Media state + worker thread
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MediaState:
    has_media: bool = False
    title: str = ""
    artist: str = ""
    album: str = ""
    source: str = ""
    playing: bool = False
    position: float = 0.0
    duration: float = 0.0
    art: bytes = b""
    art_key: str = ""


async def read_thumbnail(ref) -> bytes:
    stream = await ref.open_read_async()
    try:
        size = int(stream.size)
        if size <= 0:
            return b""
        buf = Buffer(size)
        await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        data = bytearray(buf.length)
        DataReader.from_buffer(buf).read_bytes(data)
        return bytes(data)
    finally:
        stream.close()


class MediaWorker(QThread):
    """Owns an asyncio loop; polls Windows media sessions off the UI thread."""

    state_changed = Signal(object)
    bluetooth_changed = Signal(object)
    weather_changed = Signal(object)
    bt_notice = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._stop = None
        self._manager = None
        self._art_key, self._art, self._art_tries = "", b"", 0
        self._bt_wake = None
        self._bt_stack_ok = False
        self._bt_devices = {}
        self._bt_tokens = {}
        self._bt_handler = self._on_bt_status_changed

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()
            self._loop = None

    async def _main(self):
        self._stop = asyncio.Event()
        self._bt_wake = asyncio.Event()
        bt_task = asyncio.create_task(self._bt_main())
        wx_task = asyncio.create_task(self._weather_main())
        try:
            if not HAVE_MEDIA_SDK:
                while not self._stop.is_set():
                    await self._sleep(2.0)
                return
            while self._manager is None and not self._stop.is_set():
                try:
                    self._manager = await SessionManager.request_async()
                except Exception:
                    await self._sleep(2.0)
            while not self._stop.is_set():
                await self._emit_snapshot()
                await self._sleep(1.0)
        finally:
            bt_task.cancel()
            wx_task.cancel()
            try:
                await bt_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await wx_task
            except (asyncio.CancelledError, Exception):
                pass
            self._close_bt_devices()

    async def _bt_main(self):
        while not self._stop.is_set():
            try:
                devices = await self._scan_bluetooth()
            except Exception:
                devices = []
            self.bluetooth_changed.emit(devices)
            try:
                await asyncio.wait_for(self._bt_wake.wait(), BT_REFRESH_S)
            except asyncio.TimeoutError:
                pass
            self._bt_wake.clear()

    def refresh_bluetooth(self):
        if self._loop and self._bt_wake:
            try:
                self._loop.call_soon_threadsafe(self._bt_wake.set)
            except RuntimeError:            # loop already closed during shutdown
                pass

    def disconnect_device(self, device):
        """Drop a Bluetooth device from the UI thread."""
        loop = self._loop
        if loop is None or device is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._disconnect_device(device), loop)
        except RuntimeError:            # loop already closed during shutdown
            pass

    async def _wait_bt_disconnected(self, mac: str) -> bool:
        """Poll the radio until the link really is down (or the device is gone)."""
        for attempt in range(10):
            state = await asyncio.to_thread(bluetooth_link_state, mac)
            if state is not True:
                return True
            if attempt == 3:            # some stacks reconnect on their own
                await asyncio.to_thread(bluetooth_disconnect_audio, mac)
            await asyncio.sleep(0.2)
        return False

    async def _disconnect_device(self, device):
        dev_id = getattr(device, "id", "") or (device if isinstance(device, str) else "")
        name = (getattr(device, "name", "") or "").strip() or "Device"
        mac = (getattr(device, "mac", "") or bluetooth_mac_any(dev_id)).upper()
        # Never let the driver call block the media/bluetooth event loop.
        ok = await asyncio.to_thread(bluetooth_disconnect_audio, mac)
        if ok and mac:
            ok = await self._wait_bt_disconnected(mac)
        elif mac:
            # A link that is already down counts as done.
            state = await asyncio.to_thread(bluetooth_link_state, mac)
            ok = state is False
        if ok:
            self._detach_bt(mac)
            self.bt_notice.emit(f"{name} disconnected \u00b7 still paired")
        else:
            self.bt_notice.emit(f"Couldn't disconnect {name}")
        self.refresh_bluetooth()

    async def _weather_main(self):
        while not self._stop.is_set():
            try:
                state = await fetch_weather()
            except Exception:
                state = None
            if state is not None:
                self.weather_changed.emit(state)
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       WEATHER_REFRESH_S if state else 120.0)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------- bluetooth live #
    def _on_bt_status_changed(self, *_args):
        """WinRT fires this on its own thread the instant a device connects."""
        self.refresh_bluetooth()

    async def _bt_device(self, mac: str):
        """Cached BluetoothDevice, so its connection event stays subscribed."""
        dev = self._bt_devices.get(mac)
        if dev is not None:
            return dev
        try:
            dev = await asyncio.wait_for(
                BluetoothDevice.from_bluetooth_address_async(int(mac, 16)), 3.0)
        except Exception:
            return None
        if dev is None:
            return None
        self._bt_devices[mac] = dev
        try:
            self._bt_tokens[mac] = dev.add_connection_status_changed(self._bt_handler)
        except Exception:
            pass
        return dev

    def _prune_bt_devices(self, keep: set):
        for mac in [m for m in self._bt_devices if m not in keep]:
            self._detach_bt(mac)

    def _detach_bt(self, mac: str):
        dev = self._bt_devices.pop(mac, None)
        token = self._bt_tokens.pop(mac, None)
        if dev is None:
            return
        try:
            if token is not None:
                dev.remove_connection_status_changed(token)
        except Exception:
            pass
        try:
            dev.close()
        except Exception:
            pass

    def _close_bt_devices(self):
        for mac in list(self._bt_devices):
            self._detach_bt(mac)

    async def _scan_bluetooth(self) -> list[BluetoothAudioDevice]:
        if not HAVE_BT:
            return []
        devices = await self._scan_bluetooth_aep()
        return devices or await self._scan_bluetooth_nodes()

    async def _scan_bluetooth_aep(self) -> list[BluetoothAudioDevice]:
        classic = "{e0cbf6c6-cd8a-464a-a9f8-0527e5477984}"
        le = "{bb7bb05e-5972-42b5-94fc-76eaa7084d49}"
        protocols = {_norm_id(classic), _norm_id(le)}
        selector = (f'(System.Devices.Aep.ProtocolId:="{classic}" OR '
                    f'System.Devices.Aep.ProtocolId:="{le}")')
        wanted = [
            "System.Devices.Aep.IsConnected",
            "System.Devices.Aep.IsPaired",
            "System.Devices.Aep.IsPresent",
            "System.Devices.Aep.ContainerId",
            "System.Devices.Aep.ProtocolId",
            "System.Devices.Aep.DeviceName",
            "System.Devices.Aep.Bluetooth.Cod.Major",
            "System.Devices.Aep.Bluetooth.Cod.Minor",
        ]
        infos = []
        broad_query = False
        # A few Windows 11 builds reject the protocol AQS on association
        # endpoints. Retry without the AQS and filter the protocol below.
        for query_index, query in enumerate((selector, "")):
            try:
                infos = await DeviceInformation.find_all_async(
                    query, wanted, DeviceInformationKind.ASSOCIATION_ENDPOINT)
            except Exception:
                infos = []
            if infos:
                broad_query = query_index == 1
                break

        le_id = _norm_id(le)
        candidates = []
        for info in infos:
            protocol = _norm_id(_prop(info, "System.Devices.Aep.ProtocolId", ""))
            # Depending on the Windows build this is either the Bluetooth
            # protocol GUID or a friendly value such as "Bluetooth" or
            # "BluetoothLE". The broad fallback query must accept both.
            friendly_bt = "bluetooth" in protocol
            if protocol and protocol not in protocols and not friendly_bt and not broad_query:
                continue
            if broad_query and protocol and protocol not in protocols and not friendly_bt:
                continue
            # Do not discard a device just because one optional AEP property
            # is absent; connected AEPs commonly omit IsPaired.
            paired = _is_paired(info)
            present = _bool_prop(info, "System.Devices.Aep.IsPresent", True)
            connected_hint = _bool_prop(info, "System.Devices.Aep.IsConnected", False)
            if present is False or (not paired and not connected_hint):
                continue
            name = ((info.name or "").strip()
                    or str(_prop(info, "System.Devices.Aep.DeviceName", "") or "").strip())
            if not name:
                continue

            major = _prop(info, "System.Devices.Aep.Bluetooth.Cod.Major")
            minor = _prop(info, "System.Devices.Aep.Bluetooth.Cod.Minor")
            if major is not None and int(major) != 4:
                continue
            cod_raw = (((int(major) & 0x1F) << 8) | ((int(minor or 0) & 0x3F) << 2)
                       if major is not None else 0)
            kind = classify_audio(cod_raw, name)
            if kind is None:
                continue

            cid = _norm_id(_prop(info, "System.Devices.Aep.ContainerId"))
            is_le = protocol == le_id
            candidates.append((info, name, kind, cid, is_le))

        if not candidates:
            return []

        async def connected(info, is_le: bool) -> bool:
            if _bool_prop(info, "System.Devices.Aep.IsConnected", False):
                return True
            dev = None
            try:
                cls = BluetoothLEDevice if is_le else BluetoothDevice
                dev = await asyncio.wait_for(cls.from_id_async(info.id), 3.0)
                return dev is not None and dev.connection_status == BluetoothConnectionStatus.CONNECTED
            except Exception:
                return False
            finally:
                try:
                    if dev is not None:
                        dev.close()
                except Exception:
                    pass

        flags = await asyncio.gather(*(connected(c[0], c[4]) for c in candidates))

        unique: dict[str, tuple] = {}
        for (info, name, kind, cid, is_le), ok in zip(candidates, flags):
            if not ok:
                continue
            key = cid or info.id
            if key not in unique or (unique[key][4] and not is_le):
                unique[key] = (info.id, name, kind, cid, is_le,
                               bluetooth_mac_any(info.id))
        if not unique:
            return []

        batteries = await self._read_battery_map()
        models = await self._read_model_map()

        out = []
        for dev_id, name, kind, cid, _, mac in list(unique.values())[:MAX_DEVICES]:
            manu, model = models.get(cid, ("", ""))
            out.append(BluetoothAudioDevice(
                id=dev_id, name=name, dev_type=kind,
                product=product_name(manu, model, name), battery=batteries.get(cid),
                mac=mac))
        return sorted(out, key=lambda d: d.name.lower())

    async def _scan_bluetooth_nodes(self) -> list[BluetoothAudioDevice]:
        """Fallback for builds whose association-endpoint store is empty (the AEP
        scan then returns nothing). Reads the paired Bluetooth device nodes,
        keeps the ones with an audio profile, and asks the radio whether each is
        connected right now."""
        try:
            nodes = await DeviceInformation.find_all_async(
                "", [], DeviceInformationKind.DEVICE)
        except Exception:
            return []
        self._bt_stack_ok = bool(nodes)

        found: dict[str, dict] = {}
        for node in nodes:
            upper = (node.id or "").upper()
            if not upper.startswith(("BTHENUM", "BTHHFENUM", "BTHLE")):
                continue
            mac = bluetooth_mac(node.id)
            if not mac:
                continue
            audio_node = upper.startswith("BTHHFENUM") or any(
                f"{{{sid}" in upper for sid in _BT_AUDIO_SERVICES)
            if "DEV_" in upper or "BLUETOOTHDEVICE_" in upper:
                priority = 0
            elif audio_node:
                priority = 1
            else:
                priority = 2
            entry = found.setdefault(mac, {"names": [], "audio": False, "cid": ""})
            name = (node.name or "").strip()
            if name:
                entry["names"].append((priority, name))
            if audio_node:
                entry["audio"] = True
            if not entry["cid"]:
                entry["cid"] = _norm_id(_prop(node, "System.Devices.ContainerId"))

        self._prune_bt_devices(set(found))
        if not found:
            return []

        async def probe(mac: str):
            """Connected class-of-device value, or None when not connected.

            The device object is cached: its ConnectionStatusChanged event is
            what makes the panel react the moment the radio connects."""
            dev = await self._bt_device(mac)
            if dev is None:
                return None
            try:
                if dev.connection_status != BluetoothConnectionStatus.CONNECTED:
                    return None
                try:
                    return int(dev.class_of_device.raw_value)
                except Exception:
                    return 0
            except Exception:
                return None

        macs = list(found)[:MAX_DEVICES * 3]
        cods = await asyncio.gather(*(probe(mac) for mac in macs))
        if all(cod is None for cod in cods):
            return []

        batteries = await self._read_battery_map()
        models = await self._read_model_map()

        out = []
        for mac, cod in zip(macs, cods):
            if cod is None:
                continue
            entry = found[mac]
            name = _bt_node_name(entry["names"])
            if not name:
                continue
            kind = classify_audio(cod, name)
            if kind is None:
                # A2DP/AVRCP/HFP nodes prove the device renders audio even when
                # its name carries no headphone/speaker keyword.
                if not entry["audio"]:
                    continue
                kind = "Audio device"
            cid = entry["cid"]
            manu, model = models.get(cid, ("", ""))
            out.append(BluetoothAudioDevice(
                id=cid or mac, name=name, dev_type=kind,
                product=product_name(manu, model, name), battery=batteries.get(cid),
                mac=mac))
        return sorted(out, key=lambda d: d.name.lower())[:MAX_DEVICES]

    async def _read_battery_map(self) -> dict:
        out = {}
        for aqs in ("System.Devices.Present:=System.StructuredQueryType.Boolean#True", ""):
            try:
                nodes = await DeviceInformation.find_all_async(
                    aqs, [BT_BATTERY_KEY, "System.Devices.ContainerId"], DeviceInformationKind.DEVICE)
            except Exception:
                continue
            for n in nodes:
                v = _prop(n, BT_BATTERY_KEY)
                if not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= int(v) <= 100:
                    cid = _norm_id(_prop(n, "System.Devices.ContainerId"))
                    if cid:
                        out[cid] = int(v)
            break
        return out

    async def _read_model_map(self) -> dict:
        out = {}
        try:
            conts = await DeviceInformation.find_all_async(
                "", ["System.Devices.ModelName", "System.Devices.Manufacturer"],
                DeviceInformationKind.DEVICE_CONTAINER)
        except Exception:
            return out
        for c in conts:
            out[_norm_id(c.id)] = (str(_prop(c, "System.Devices.Manufacturer", "") or ""),
                                   str(_prop(c, "System.Devices.ModelName", "") or ""))
        return out

    async def _sleep(self, seconds):
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    async def _emit_snapshot(self):
        try:
            state = await self._snapshot()
        except Exception:
            return
        self.state_changed.emit(state)

    async def _snapshot(self) -> MediaState:
        session = self._manager.get_current_session() if self._manager else None
        if session is None:
            self._art_key, self._art, self._art_tries = "", b"", 0
            return MediaState()

        props = await session.try_get_media_properties_async()
        title = (props.title or "").strip() if props else ""
        artist = (props.artist or "").strip() if props else ""
        album = (props.album_title or "").strip() if props else ""
        key = f"{title}\x00{artist}\x00{album}"
        if key != self._art_key:
            self._art_key, self._art, self._art_tries = key, b"", 0
        if props and props.thumbnail and not self._art and self._art_tries < 4:
            self._art_tries += 1
            try:
                self._art = await read_thumbnail(props.thumbnail)
            except Exception:
                pass

        info = session.get_playback_info()
        playing = bool(info) and info.playback_status == PlaybackStatus.PLAYING

        position = duration = 0.0
        tl = session.get_timeline_properties()
        if tl:
            start = tl.start_time.total_seconds()
            duration = max(0.0, tl.end_time.total_seconds() - start)
            position = tl.position.total_seconds() - start
            if playing:
                try:
                    stamp = tl.last_updated_time
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=dt.timezone.utc)
                    age = (dt.datetime.now(dt.timezone.utc) - stamp).total_seconds()
                    if 0 <= age < 6 * 3600:
                        position += age
                except Exception:
                    pass
            position = max(0.0, min(position, duration) if duration else position)

        return MediaState(
            has_media=True, title=title or "Unknown title", artist=artist, album=album,
            source=session.source_app_user_model_id or "", playing=playing,
            position=position, duration=duration, art=self._art, art_key=key,
        )

    def stop(self):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        self.wait(2500)

    def command(self, name: str, arg: float = 0.0):
        if self._loop and self._manager:
            asyncio.run_coroutine_threadsafe(self._run_command(name, arg), self._loop)

    async def _run_command(self, name, arg):
        session = self._manager.get_current_session()
        if session is None:
            return
        try:
            if name == "toggle":
                await session.try_toggle_play_pause_async()
            elif name == "next":
                await session.try_skip_next_async()
            elif name == "prev":
                await session.try_skip_previous_async()
            elif name == "seek":
                await session.try_change_playback_position_async(int(arg * 10_000_000))
        except Exception:
            pass
        await asyncio.sleep(0.2)
        await self._emit_snapshot()


# --------------------------------------------------------------------------- #
# Focus timer + AI agent progress
# --------------------------------------------------------------------------- #
def focus_clock(seconds: float) -> str:
    """mm:ss, or h:mm:ss once a session runs past an hour."""
    s = int(max(0.0, round(float(seconds))))
    m, s = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def play_chime() -> None:
    """System chime for a finished session or an agent that needs an answer."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


class FocusTimer(QObject):
    """One focus session at a time: presets, pause/resume, and a daily tally."""

    changed = Signal()
    finished = Signal()

    PRESETS = (15, 25, 45, 60)
    DEFAULT_MIN = 25

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset = self.DEFAULT_MIN
        self._total = float(self._preset * 60)
        self._left = self._total
        self._deadline = 0.0
        self._active = False
        self._paused = False
        self._done = 0
        self._seconds = 0
        self._load()
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._tick)

    # --- state ---------------------------------------------------------- #
    @property
    def preset(self) -> int:
        return self._preset

    @property
    def total(self) -> float:
        return max(1.0, self._total)

    @property
    def remaining(self) -> float:
        return max(0.0, self._left)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def progress(self) -> float:
        return clamp01(1.0 - self._left / self.total)

    @property
    def done(self) -> int:
        return self._done

    @property
    def done_seconds(self) -> int:
        return self._seconds

    def label(self) -> str:
        return focus_clock(self._left)

    # --- control -------------------------------------------------------- #
    def set_preset(self, minutes):
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            return
        if minutes not in self.PRESETS:
            return
        self._preset = minutes
        if not self._active:
            self._total = self._left = float(minutes * 60)
        self.changed.emit()

    def toggle(self) -> None:
        if self._paused:
            self.resume()
        elif self._active:
            self.pause()
        else:
            self.start()

    def start(self) -> None:
        self._total = float(self._preset * 60)
        self._left = self._total
        self._deadline = time.monotonic() + self._left
        self._active = True
        self._paused = False
        self._timer.start()
        self.changed.emit()

    def pause(self) -> None:
        if not self._active or self._paused:
            return
        self._left = max(0.0, self._deadline - time.monotonic())
        self._paused = True
        self._timer.stop()
        self.changed.emit()

    def resume(self) -> None:
        if not (self._active and self._paused):
            return
        self._deadline = time.monotonic() + self._left
        self._paused = False
        self._timer.start()
        self.changed.emit()

    def add_minutes(self, minutes: int = 5) -> None:
        if not self._active:
            return
        extra = float(minutes * 60)
        self._left += extra
        self._total += extra
        if not self._paused:
            self._deadline += extra
        self.changed.emit()

    def reset(self) -> None:
        self._timer.stop()
        self._active = self._paused = False
        self._total = self._left = float(self._preset * 60)
        self.changed.emit()

    def _tick(self) -> None:
        self._left = self._deadline - time.monotonic()
        if self._left <= 0.0:
            self._finish()
            return
        self.changed.emit()

    def _finish(self) -> None:
        self._timer.stop()
        self._active = self._paused = False
        self._left = 0.0
        self._done += 1
        self._seconds += int(round(self._total))
        self._save()
        self.changed.emit()
        self.finished.emit()

    # --- daily tally ---------------------------------------------------- #
    def _load(self) -> None:
        try:
            data = json.loads(FOCUS_CFG.read_text(encoding="utf-8"))
            if data.get("date") == dt.date.today().isoformat():
                self._done = max(0, int(data.get("sessions", 0)))
                self._seconds = max(0, int(data.get("seconds", 0)))
        except Exception:
            pass

    def _save(self) -> None:
        try:
            FOCUS_CFG.parent.mkdir(parents=True, exist_ok=True)
            FOCUS_CFG.write_text(json.dumps({
                "date": dt.date.today().isoformat(),
                "sessions": self._done,
                "seconds": self._seconds,
            }), encoding="utf-8")
        except OSError:
            pass


_TOOL_WORDS = {
    "Read": "Reading", "Write": "Writing", "Edit": "Editing", "MultiEdit": "Editing",
    "NotebookEdit": "Editing", "Bash": "Running", "BashOutput": "Checking",
    "Grep": "Searching", "Glob": "Scanning", "Task": "Delegating", "Agent": "Delegating",
    "WebFetch": "Fetching", "WebSearch": "Searching the web", "TodoWrite": "Planning",
    "AskUserQuestion": "Waiting for you", "shell": "Running", "apply_patch": "Editing",
}

_AGENT_ROOTS = (("Claude Code", (".claude", "projects")), ("Codex", (".codex", "sessions")))
_AGENT_APPS = (
    ("antigravity", "Antigravity"),
    ("freebuff", "Freebuff"),
    ("claude", "Claude Code"),
    ("codex", "Codex"),
    ("cursor", "Cursor"),
    ("windsurf", "Windsurf"),
    ("gemini", "Gemini"),
    ("copilot", "Copilot"),
)


def _running_agent() -> tuple[str, str, bool] | None:
    """(label, window title, is_foreground) for whichever agent app is open."""
    try:
        windows = _top_level_windows()
        foreground = int(_user32.GetForegroundWindow())
    except Exception:
        return None
    best = None
    for hwnd, title, exe in windows:
        stem = exe[:-4] if exe.endswith(".exe") else exe
        for key, label in _AGENT_APPS:
            if key in stem and (best is None or hwnd == foreground):
                best = (hwnd == foreground, label, title)
                break
        if best is not None and best[0]:
            break
    return (best[1], best[2], best[0]) if best is not None else None


def _agent_roots() -> list[tuple[str, Path]]:
    home = Path.home()
    return [(label, home.joinpath(*parts)) for label, parts in _AGENT_ROOTS]


def _ago_text(seconds: float) -> str:
    s = int(max(0.0, float(seconds)))
    if s < 5:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m ago"
    h, m = divmod(m, 60)
    return f"{h}h ago"


class AgentWatcher(QThread):
    """Follows the newest Claude Code / Codex session log so the island can show it working."""

    state = Signal(object)

    POLL_S = 1.0
    BUSY_S = 22.0
    RESCAN_S = 25.0
    APP_SCAN_S = 3.0
    MAX_FILES = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._files: list[tuple[str, Path]] = []
        self._scanned = 0.0
        self._last = None
        self._app: tuple[str, str, bool] | None = None
        self._app_at = 0.0
        self._busy_since = 0.0              # fallback run clock when the log has no timestamps

    def stop(self):
        self._stop.set()
        self.wait(2500)

    def run(self):
        while not self._stop.is_set():
            try:
                snap = self._scan()
            except Exception:
                snap = None
            if snap is not None and snap != self._last:
                self._last = snap
                self.state.emit(snap)
            self._stop.wait(self.POLL_S)

    def _agent_app(self):
        """Any agent app that is open (Antigravity, Freebuff, …), cached briefly."""
        now = time.monotonic()
        if now - self._app_at >= self.APP_SCAN_S:
            self._app_at = now
            self._app = _running_agent()
        return self._app

    # --- discovery ------------------------------------------------------ #
    def _collect(self, now: float):
        if self._files and now - self._scanned < self.RESCAN_S:
            return
        self._scanned = now
        found: list[tuple[str, Path]] = []
        for label, root in _agent_roots():
            if not root.is_dir():
                continue
            depth = len(root.parts)
            for dirpath, dirnames, filenames in os.walk(root):
                if len(found) >= self.MAX_FILES:
                    break
                here = Path(dirpath)
                if len(here.parts) - depth > 4:
                    dirnames[:] = []
                    continue
                for name in filenames:
                    if name.endswith(".jsonl"):
                        found.append((label, here / name))
        self._files = found

    def _scan(self):
        self._collect(time.monotonic())
        newest = None
        for label, path in self._files:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, label, path)
        fresh = newest is not None and time.time() - newest[0] < self.BUSY_S
        if not fresh and self._agent_app() is None:
            self._busy_since = 0.0
        if not fresh:
            app = self._agent_app()
            if app is not None:
                label, title, focused = app
                action = (title or "").strip()
                if not action or action.lower() == label.lower():
                    action = "Session open"
                self._busy_since = self._busy_since or time.time()
                return {"kind": label, "project": "", "branch": "", "action": action,
                        "detail": "", "busy": True, "waiting": False,
                        "age": 0.0, "steps": [], "app": True,
                        "foreground": bool(focused), "title": "", "file": "",
                        "diff": None, "phase": -1,
                        "elapsed": time.time() - self._busy_since}
        if newest is None:
            return {"kind": "", "project": "", "branch": "", "action": "", "detail": "",
                    "busy": False, "waiting": False, "age": 0.0, "steps": []}
        mtime, label, path = newest
        age = max(0.0, time.time() - mtime)
        info = self._parse(path)
        busy = age < self.BUSY_S
        now = time.time()
        started = float(info.pop("started", 0.0) or 0.0)
        if busy and not self._busy_since:
            self._busy_since = now
        elif not busy:
            self._busy_since = 0.0
        if not started and busy:
            started = self._busy_since
        end = now if busy else mtime
        elapsed = max(0.0, min(end - started, 99 * 3600.0)) if started else 0.0
        info.update({"kind": label, "age": age, "busy": busy, "elapsed": elapsed})
        return info

    # --- parsing -------------------------------------------------------- #
    @staticmethod
    def _tail(path: Path, limit: int = 90) -> list[dict]:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 120_000))
            raw = fh.read()
        out: list[dict] = []
        for line in raw.splitlines()[-limit:]:
            try:
                item = json.loads(line.decode("utf-8", "replace"))
            except Exception:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    @staticmethod
    def _blocks(entry: dict) -> list[dict]:
        blocks = []
        message = entry.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            blocks = message["content"]
        elif isinstance(entry.get("content"), list):
            blocks = entry["content"]
        return [b for b in blocks if isinstance(b, dict)]

    @classmethod
    def _actions(cls, entry: dict):
        for block in cls._blocks(entry):
            if block.get("type") not in ("tool_use", "function_call"):
                continue
            name = str(block.get("name") or block.get("tool_name") or "Tool")
            payload = block.get("input") or block.get("arguments") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {"value": payload}
            if not isinstance(payload, dict):
                payload = {}
            arg = (payload.get("file_path") or payload.get("path") or payload.get("command")
                   or payload.get("pattern") or payload.get("query") or payload.get("description")
                   or payload.get("prompt") or payload.get("value") or "")
            yield name, str(arg)

    _PHASE = {"Read": 0, "Grep": 0, "Glob": 0, "WebFetch": 0, "WebSearch": 0,
              "Task": 0, "Agent": 0, "TodoWrite": 0,
              "Edit": 1, "MultiEdit": 1, "Write": 1, "NotebookEdit": 1, "apply_patch": 1,
              "Bash": 2, "BashOutput": 2, "shell": 2}

    @staticmethod
    def _stamp(entry: dict) -> float:
        raw = entry.get("timestamp")
        try:
            if isinstance(raw, (int, float)):
                return float(raw) / (1000.0 if raw > 1e11 else 1.0)
            return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    @classmethod
    def _user_text(cls, entry: dict) -> str:
        """The prompt the person typed (tool results and system tags are skipped)."""
        message = entry.get("message")
        if not (entry.get("type") == "user"
                or (isinstance(message, dict) and message.get("role") == "user")):
            return ""
        content = message.get("content") if isinstance(message, dict) else entry.get("content")
        if isinstance(content, list):
            content = " ".join(str(b.get("text", "")) for b in content
                               if isinstance(b, dict) and b.get("type") in ("text", "input_text"))
        text = " ".join(str(content or "").split())
        return "" if (not text or text.startswith("<")) else text[:120]

    @classmethod
    def _edits(cls, entry: dict):
        """(file, old, new) for every edit / write / patch tool call in an entry."""
        for block in cls._blocks(entry):
            if block.get("type") not in ("tool_use", "function_call"):
                continue
            name = str(block.get("name") or block.get("tool_name") or "")
            payload = block.get("input") or block.get("arguments") or {}
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {"value": payload}
            if not isinstance(payload, dict):
                continue
            path = str(payload.get("file_path") or payload.get("path") or "")
            if name in ("Edit", "MultiEdit"):
                items = payload.get("edits") if name == "MultiEdit" else [payload]
                for item in (items or [])[:1]:
                    if isinstance(item, dict):
                        yield path, str(item.get("old_string") or ""), str(item.get("new_string") or "")
            elif name == "Write":
                yield path, "", str(payload.get("content") or "")
            elif name == "apply_patch":
                text = str(payload.get("input") or payload.get("value") or "")
                file = re.search(r"\*\*\* (?:Update|Add) File: (.+)", text)
                minus = [l[1:] for l in text.splitlines() if l.startswith("-") and not l.startswith("---")]
                plus = [l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++")]
                yield (file.group(1).strip() if file else path), "\n".join(minus), "\n".join(plus)

    @staticmethod
    def _first_diff(old: str, new: str) -> dict:
        ol, nl = old.splitlines(), new.splitlines()
        o = n = ""
        line = 1
        for i in range(max(len(ol), len(nl))):
            a = ol[i] if i < len(ol) else None
            b = nl[i] if i < len(nl) else None
            if a != b:
                o, n, line = (a or ""), (b or ""), i + 1
                break
        else:
            o, n = (ol[0] if ol else ""), (nl[0] if nl else "")
        clip = lambda s: s.expandtabs(2).rstrip()[:110]
        return {"old": clip(o) if old else "", "new": clip(n), "line": line,
                "minus": len(ol), "plus": len(nl)}

    @staticmethod
    def _short(arg: str) -> str:
        arg = (arg or "").strip().replace("\n", " ")
        if not arg:
            return ""
        if "/" in arg or "\\" in arg:
            arg = os.path.basename(arg.rstrip("/\\")) or arg
        return arg if len(arg) <= 46 else arg[:43].rstrip() + "…"

    @classmethod
    def _parse(cls, path: Path) -> dict:
        entries = cls._tail(path)
        project = branch = ""
        steps: list[tuple[str, str]] = []
        waiting = False
        title, started, phase, edit = "", 0.0, -1, None
        for entry in entries:
            prompt = cls._user_text(entry)
            if prompt:                                     # a new run starts here
                title, started, phase, edit = prompt, cls._stamp(entry), -1, None
            for file, old, new in cls._edits(entry):
                edit = (file, cls._first_diff(old, new))
            cwd = entry.get("cwd")
            if cwd and not project:
                project = Path(str(cwd)).name or str(cwd)
            if entry.get("gitBranch") and not branch:
                branch = str(entry["gitBranch"])
            for name, arg in cls._actions(entry):
                if name == "AskUserQuestion":
                    waiting = True
                phase = max(phase, cls._PHASE.get(name, -1))
                steps.append((name, arg))
        action, detail = "", ""
        if steps:
            name, arg = steps[-1]
            action = _TOOL_WORDS.get(name, name)
            detail = cls._short(arg)
        recent = []
        for name, arg in steps[-3:][::-1]:
            text = f"{_TOOL_WORDS.get(name, name)} {cls._short(arg)}".strip()
            if text and text not in recent:
                recent.append(text)
        return {"project": project, "branch": branch, "action": action, "detail": detail,
                "waiting": waiting, "steps": recent, "title": title, "started": started,
                "phase": phase, "file": os.path.basename(edit[0]) if edit else "",
                "diff": edit[1] if edit else None}


# --------------------------------------------------------------------------- #
# Motion helpers
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Lyrics (LRCLIB, cached locally)
# --------------------------------------------------------------------------- #
_LRC_TIME_RX = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """[(seconds, line)] from an LRC file, sorted and de-duplicated."""
    out: list[tuple[float, str]] = []
    for raw in (text or "").splitlines():
        stamps = list(_LRC_TIME_RX.finditer(raw))
        if not stamps:
            continue
        body = _LRC_TIME_RX.sub("", raw).strip()
        if not body:
            continue
        for match in stamps:
            seconds = int(match.group(1)) * 60 + float(match.group(2).replace(":", "."))
            out.append((seconds, body))
    out.sort(key=lambda item: item[0])
    return out


def lyrics_line_index(lines, position: float) -> int:
    """Index of the line that is playing at `position` (-1 before the first)."""
    lo, hi = 0, len(lines)
    while lo < hi:
        mid = (lo + hi) // 2
        if float(lines[mid][0]) <= position:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


def _lyrics_cache() -> dict:
    try:
        data = json.loads(LYRICS_CFG.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _lyrics_cache_store(key: str, payload: dict) -> None:
    try:
        data = _lyrics_cache()
        data[key] = {k: payload.get(k) for k in ("status", "lines", "plain", "source")}
        for old in list(data)[:-80]:
            data.pop(old, None)
        LYRICS_CFG.parent.mkdir(parents=True, exist_ok=True)
        LYRICS_CFG.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    except Exception:
        pass


class LyricsFetcher(QThread):
    """Looks a track up on LRCLIB once, off the UI thread, and caches the result."""

    ready = Signal(object)

    def __init__(self, key: str, title: str, artist: str, album: str = "",
                 duration: float = 0.0, parent=None):
        super().__init__(parent)
        self._key, self._title, self._artist = key, title, artist
        self._album, self._duration = album, duration

    def run(self):
        cached = _lyrics_cache().get(self._key)
        if isinstance(cached, dict) and cached.get("status") == "ok":
            cached = dict(cached)
            cached["key"] = self._key
            self.ready.emit(cached)
            return
        try:
            payload = self._lookup()
        except Exception:
            payload = {"status": "none", "lines": [], "plain": "", "source": "LRCLIB"}
        payload["key"] = self._key
        if payload.get("status") == "ok":
            _lyrics_cache_store(self._key, payload)
        self.ready.emit(payload)

    def _lookup(self) -> dict:
        if not self._title:
            return {"status": "none", "lines": [], "plain": "", "source": "LRCLIB"}
        query = urllib.parse.urlencode({
            "artist_name": self._artist or "", "track_name": self._title,
            "album_name": self._album or "", "duration": int(self._duration or 0),
        })
        candidates: list[dict] = []
        try:
            data = _http_json("https://lrclib.net/api/get?" + query, 3.0)
            if isinstance(data, dict):
                candidates.append(data)
        except Exception:
            pass
        if self.isInterruptionRequested():
            return {"status": "none", "lines": [], "plain": "", "source": "LRCLIB"}
        if not any((c.get("syncedLyrics") or c.get("plainLyrics")) for c in candidates):
            try:
                found = _http_json("https://lrclib.net/api/search?" + urllib.parse.urlencode(
                    {"track_name": self._title, "artist_name": self._artist or ""}), 3.0)
            except Exception:
                found = []
            if isinstance(found, list):
                candidates += [c for c in found[:5] if isinstance(c, dict)]
        for cand in candidates:
            synced = (cand.get("syncedLyrics") or "").strip()
            plain = (cand.get("plainLyrics") or "").strip()
            lines = parse_lrc(synced)
            if lines or plain:
                return {"status": "ok", "lines": [list(x) for x in lines],
                        "plain": plain, "source": "LRCLIB"}
        return {"status": "none", "lines": [], "plain": "", "source": "LRCLIB"}


# --------------------------------------------------------------------------- #
# System stats (for the expandable system panel)
# --------------------------------------------------------------------------- #
class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _system_stats() -> dict:
    stats: dict = {}
    try:
        mem = _MemoryStatusEx()
        mem.dwLength = ctypes.sizeof(mem)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            total = mem.ullTotalPhys / (1024 ** 3)
            used = (mem.ullTotalPhys - mem.ullAvailPhys) / (1024 ** 3)
            stats.update(ram_used=used, ram_total=total, ram_pct=int(mem.dwMemoryLoad))
    except Exception:
        pass
    try:
        fn = ctypes.windll.kernel32.GetTickCount64
        fn.restype = ctypes.c_ulonglong
        stats["uptime"] = fn() / 1000.0
    except Exception:
        pass
    return stats


def clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def ease_out_cubic(t: float) -> float:
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t: float, s: float = 1.4) -> float:
    t = clamp01(t)
    u = t - 1.0
    return 1.0 + u * u * ((s + 1.0) * u + s)


def seg(t: float, delay: float, dur: float = 0.45) -> float:
    return ease_out_cubic((t - delay) / dur)


def pastel(c: QColor, sat: float = 0.55, val: float = 1.0) -> QColor:
    h, s, _v, _ = c.getHsvF()
    return QColor.fromHsvF(max(h, 0.0), min(1.0, s * sat), val)


class _Reveal:
    _rv = 1.0

    def set_reveal(self, v: float):
        v = clamp01(v)
        if v != self._rv:
            self._rv = v
            self.update()


# --------------------------------------------------------------------------- #
# Widgets
# --------------------------------------------------------------------------- #
_SCRAMBLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789#%&*+=?"


class GlassText(QWidget, _Reveal):
    GAP = 28

    def __init__(self, px: int, weight, color: QColor, *, align=Qt.AlignLeft, display=False,
                 spacing: float = 0.0, scramble=False, marquee=False, shine=False, shadow=True):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFont(ui_font(px, weight, display, spacing))
        self._color = QColor(color)
        self._align = align | Qt.AlignVCenter
        self._scramble, self._marquee, self._shine, self._shadow = scramble, marquee, shine, shadow
        self._full = self._shown = ""
        self._scr_t, self._scr_dur, self._scr_acc = 1.0, 0.6, 0.0
        self._off, self._hold = 0.0, 1.5
        self._shine_on, self._shine_t = False, 0.0

    def set_full_text(self, text: str):
        if text == self._full:
            return
        self._full = text
        self._off, self._hold = 0.0, 1.5
        if self._scramble and text:
            self._start_scramble()
        else:
            self._shown = text
        self.update()

    def full_text(self) -> str:
        return self._full

    def replay(self):
        if self._scramble and self._full:
            self._start_scramble()
            self.update()

    def set_shine(self, on: bool):
        self._shine_on = bool(on)
        if not on:
            self._shine_t = 0.0
            self.update()

    def _start_scramble(self):
        self._scr_t = 0.0
        self._scr_acc = 0.0
        self._scr_dur = min(1.0, 0.35 + 0.03 * len(self._full))
        self._rebuild_shown()

    def _rebuild_shown(self):
        n = len(self._full)
        k = int(self._scr_t * n)
        self._shown = "".join(ch if (i < k or ch == " ") else random.choice(_SCRAMBLE)
                              for i, ch in enumerate(self._full))

    def tick(self, dt: float):
        dirty = False
        if self._scr_t < 1.0:
            self._scr_t = min(1.0, self._scr_t + dt / self._scr_dur)
            self._scr_acc += dt
            if self._scr_acc >= 0.045 or self._scr_t >= 1.0:
                self._scr_acc = 0.0
                self._rebuild_shown()
            dirty = True
        if self._marquee and self.isVisible():
            tw = self.fontMetrics().horizontalAdvance(self._full)
            if tw > self.width():
                if self._hold > 0:
                    self._hold -= dt
                else:
                    self._off += 30.0 * dt
                    if self._off >= tw + self.GAP:
                        self._off, self._hold = 0.0, 1.6
                dirty = True
            elif self._off:
                self._off, dirty = 0.0, True
        if self._shine_on and self.isVisible():
            self._shine_t = (self._shine_t + dt / 3.6) % 1.0
            dirty = True
        if dirty:
            self.update()

    def paintEvent(self, _e):
        if self._rv <= 0.004 or not self._shown:
            return
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        img = QImage(max(1, int(w * dpr)), max(1, int(h * dpr)), QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        q = QPainter(img)
        q.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        q.setFont(self.font())
        fm = q.fontMetrics()
        text = self._shown
        tw = fm.horizontalAdvance(text)
        scrolling = self._marquee and tw > w
        if tw > w and not self._marquee:
            text = fm.elidedText(text, Qt.ElideRight, w)
        slide = (1.0 - self._rv) * 12.0

        def put(ox: float, oy: float, col: QColor):
            q.setPen(col)
            if scrolling:
                span = tw + self.GAP
                for k in (0, 1):
                    q.drawText(QRectF(ox - self._off + k * span, oy, tw + 2, h),
                               Qt.AlignLeft | Qt.AlignVCenter, text)
            else:
                q.drawText(QRectF(ox, oy, w, h), self._align, text)

        if self._shadow:
            put(slide, 1.0, QColor(0, 0, 0, 120))
        put(slide, 0.0, self._color)

        if self._shine_on and self._shine_t < 0.38:
            s = self._shine_t / 0.38
            bw = max(44.0, w * 0.5)
            x0 = -bw + (w + bw) * s
            g = QLinearGradient(x0, 0, x0 + bw, 0)
            g.setColorAt(0.0, QColor(255, 255, 255, 0))
            g.setColorAt(0.5, QColor(255, 255, 255, 255))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            q.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            q.fillRect(QRectF(0, 0, w, h), g)

        if scrolling:
            lead = min(1.0, self._off / 10.0)
            fade = QLinearGradient(0, 0, w, 0)
            fade.setColorAt(0.0, QColor(0, 0, 0, int(255 * (1.0 - lead))))
            fade.setColorAt(min(0.4, 10.0 / w), QColor(0, 0, 0, 255))
            fade.setColorAt(max(0.6, 1.0 - 14.0 / w), QColor(0, 0, 0, 255))
            fade.setColorAt(1.0, QColor(0, 0, 0, 0))
            q.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            q.fillRect(QRectF(0, 0, w, h), fade)
        q.end()

        p = QPainter(self)
        p.setOpacity(self._rv)
        p.drawImage(0, 0, img)


class _Fader:
    def _init_fader(self):
        self._hover = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(160)
        self._fade.valueChanged.connect(self._on_fade)

    def _on_fade(self, v):
        self._hover = float(v)
        self.update()

    def _fade_to(self, t):
        self._fade.stop()
        self._fade.setStartValue(self._hover)
        self._fade.setEndValue(t)
        self._fade.start()


class GhostButton(QAbstractButton, _Fader, _Reveal):
    def __init__(self, kind: str, size: QSize, icon_px: int, solid: bool = False):
        super().__init__()
        self.setFixedSize(size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._kind = self._prev = kind
        self._icon_px, self._solid = icon_px, solid
        self._icons: dict[str, QIcon] = {}
        self._init_fader()

        self._morph = 1.0
        self._morph_anim = QVariantAnimation(self)
        self._morph_anim.setDuration(300)
        self._morph_anim.setStartValue(0.0)
        self._morph_anim.setEndValue(1.0)
        self._morph_anim.valueChanged.connect(self._on_morph)

    def _on_morph(self, v):
        self._morph = float(v)
        self.update()

    def set_kind(self, kind):
        if kind != self._kind:
            self._prev, self._kind = self._kind, kind
            self._morph = 0.0
            self._morph_anim.stop()
            self._morph_anim.start()
            self.update()

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def _icon(self, kind: str) -> QIcon:
        return self._icons.get(kind) or self._icons.setdefault(
            kind, create_icon_pixmap(kind, self._icon_px))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        rv = self._rv
        p.setOpacity(rv)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        a = int((34 if self._solid else 0) + self._hover * 44)
        if a:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor(255, 255, 255, min(255, a + 22)))
            g.setColorAt(1, QColor(255, 255, 255, a))
            p.setBrush(g)
            edge = 40 + int(self._hover * 70) if self._solid else int(self._hover * 90)
            p.setPen(QPen(QColor(255, 255, 255, edge), 1))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        c = r.center()
        s = self._icon_px

        def draw_icon(kind, scale, rot, op):
            if op <= 0.01:
                return
            p.save()
            p.translate(c.x(), c.y())
            p.rotate(rot)
            p.scale(scale, scale)
            p.setOpacity(rv * min(1.0, op))
            self._icon(kind).paint(p, QRect(-s // 2, -s // 2, s, s))
            p.restore()

        m = self._morph
        if m < 1.0 and self._prev != self._kind:
            draw_icon(self._prev, 1.0 - 0.5 * m, 80.0 * m, 1.0 - m * 1.4)
            draw_icon(self._kind, 0.5 + 0.5 * ease_out_back(m, 1.6), -80.0 * (1.0 - m), m * 1.5)
        else:
            draw_icon(self._kind, 1.0, 0.0, 1.0)


class TabButton(QAbstractButton, _Fader):
    def __init__(self, kind: str, label: str):
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._kind, self._label = kind, label
        self._font = ui_font(12, QFont.Weight.DemiBold, True, 0.35)
        self._icon = create_icon_pixmap(kind, 14)
        self._icon_dim = create_icon_pixmap(kind, 14, QColor(255, 255, 255, 170))
        self.setFont(self._font)
        self.setFixedSize(self.fontMetrics().horizontalAdvance(label) + 38, 25)
        self._init_fader()

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        on = self.isChecked()
        fill = 40 if on else int(self._hover * 22)
        if fill:
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor(255, 255, 255, fill + 16))
            g.setColorAt(1, QColor(255, 255, 255, fill))
            p.setBrush(g)
            p.setPen(QPen(QColor(255, 255, 255, 70 if on else 35), 1))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        (self._icon if on else self._icon_dim).paint(p, QRect(12, 5, 14, 14))
        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 245 if on else 175))
        p.drawText(QRectF(31, 0, r.width() - 31, r.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   self._label)


class RecIndicator(QWidget, _Reveal):
    """Small glass pill in the top bar: pulsing red dot + elapsed time."""

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedSize(72, 22)
        self._font = ui_font(10, QFont.Weight.Bold, True, 0.6)
        self._text = "0:00"
        self._t = 0.0
        self._pulse = QTimer(self)
        self._pulse.setInterval(60)
        self._pulse.timeout.connect(self._tick)

    def start(self):
        self._t = 0.0
        self._text = "0:00"
        self._pulse.start()
        self.update()

    def stop(self):
        self._pulse.stop()

    def set_elapsed(self, seconds: float):
        s = int(max(0.0, seconds))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        text = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
        if text != self._text:
            self._text = text
            self.update()

    def _tick(self):
        self._t += 0.06
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        p.setOpacity(self._rv)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setBrush(QColor(255, 255, 255, 26))
        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        pulse = 0.5 + 0.5 * math.sin(self._t * 4.4)
        center = QPointF(12.0, r.center().y())
        halo = QRadialGradient(center, 9.0)
        halo.setColorAt(0.0, QColor(255, 92, 92, int(70 + 90 * pulse)))
        halo.setColorAt(1.0, QColor(255, 92, 92, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(halo)
        p.drawEllipse(center, 9.0, 9.0)
        p.setBrush(QColor(255, 92, 92, 238))
        p.drawEllipse(center, 3.1, 3.1)

        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 240))
        p.drawText(QRectF(20.0, 0.0, r.width() - 24.0, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._text)


class SeekSlider(QSlider, _Reveal):
    stretched = Signal(float, float)

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.setRange(0, 1000)
        self.setFixedHeight(12)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self._scale = self._scale_t = 1.0
        self._overflow = self._vel = 0.0
        self._spring = False
        self._region = "middle"
        self._origin_right = False
        self._c1, self._c2 = QColor(255, 255, 255), QColor(255, 255, 255)
        self._playing, self._shim = False, 0.0
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(16)
        self._tick_timer.timeout.connect(self._tick)

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = pastel(c1, 0.75), pastel(c2, 0.45)
        self.update()

    def set_playing(self, on: bool):
        self._playing = bool(on)

    def tick(self, dt: float):
        if self._playing and self.isVisible() and self.isEnabled():
            self._shim = (self._shim + dt / 2.4) % 1.0
            self.update()

    def _track(self):
        return float(ES_PAD), float(max(1, self.width() - 2 * ES_PAD))

    def _value_at(self, x: float) -> int:
        left, tw = self._track()
        f = min(max((x - left) / tw, 0.0), 1.0)
        return int(round(self.minimum() + f * (self.maximum() - self.minimum())))

    def _drag_to(self, x: float):
        left, tw = self._track()
        if x < left:
            self._region, raw = "left", left - x
        elif x > left + tw:
            self._region, raw = "right", x - (left + tw)
        else:
            self._region, raw = "middle", 0.0
        self._overflow = decay(raw, ES_MAX_OVERFLOW)
        self._origin_right = x < left + tw / 2
        self.setSliderPosition(self._value_at(x))
        self._emit_stretch()
        self.update()

    def _emit_stretch(self):
        s = self._scale or 1.0
        self.stretched.emit(-self._overflow / s if self._region == "left" else 0.0,
                            self._overflow / s if self._region == "right" else 0.0)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or not self.isEnabled():
            return
        self._spring = False
        self.setSliderDown(True)
        self._drag_to(e.position().x())
        self._start()
        e.accept()

    def mouseMoveEvent(self, e):
        if self.isSliderDown():
            self._drag_to(e.position().x())
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            self._spring, self._vel = True, 0.0
            self._start()
        e.accept()

    def enterEvent(self, e):
        self._scale_t = 1.2
        self._start()

    def leaveEvent(self, e):
        self._scale_t = 1.0
        self._start()

    def _start(self):
        if not self._tick_timer.isActive():
            self._tick_timer.start()

    def _tick(self):
        dt, moving = 0.016, False
        if abs(self._scale - self._scale_t) > 0.002:
            self._scale += (self._scale_t - self._scale) * 0.35
            moving = True
        else:
            self._scale = self._scale_t
        if self._spring:
            w0, damping = 17.3, 0.5
            self._vel += (-(w0 * w0) * self._overflow - 2 * damping * w0 * self._vel) * dt
            self._overflow += self._vel * dt
            moving = True
            if abs(self._overflow) < 0.02 and abs(self._vel) < 0.3:
                self._overflow = self._vel = 0.0
                self._spring = False
        if moving:
            self._emit_stretch()
            self.update()
        elif not self.isSliderDown():
            self._tick_timer.stop()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left, tw = self._track()
        k = min(1.0, max(0.0, (self._scale - 1.0) / 0.2))
        th = 3.0 + 3.0 * k
        cy = self.height() / 2.0
        sx = 1.0 + self._overflow / tw
        sy = 1.0 - 0.2 * min(1.0, max(0.0, self._overflow / ES_MAX_OVERFLOW))
        ox = left + tw if self._origin_right else left
        p.translate(ox, cy)
        p.scale(sx, sy)
        p.translate(-ox, -cy)
        p.setOpacity((0.75 + 0.25 * k) * (1.0 if self.isEnabled() else 0.5) * self._rv)
        track = QRectF(left, cy - th / 2, tw, th)
        clip = QPainterPath()
        clip.addRoundedRect(track, th / 2, th / 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 52))
        p.drawPath(clip)
        span = self.maximum() - self.minimum()
        frac = (self.value() - self.minimum()) / span if span else 0.0
        fw = tw * frac
        if frac > 0:
            fill = QRectF(left, track.y(), fw, th)
            p.save()
            p.setClipPath(clip)
            g = QLinearGradient(left, 0, left + max(fw, 1.0), 0)
            g.setColorAt(0.0, self._c1)
            g.setColorAt(1.0, QColor(255, 255, 255, 250))
            p.setBrush(g)
            p.drawRoundedRect(fill, th / 2, th / 2)
            if self._playing and fw > 6:
                bw = 36.0
                x0 = left - bw + (fw + bw) * self._shim
                sg = QLinearGradient(x0, 0, x0 + bw, 0)
                sg.setColorAt(0.0, QColor(255, 255, 255, 0))
                sg.setColorAt(0.5, QColor(255, 255, 255, 130))
                sg.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setClipRect(fill, Qt.IntersectClip)
                p.setBrush(sg)
                p.drawRect(fill)
            p.restore()
        kn = max(k, 1.0 if self.isSliderDown() else 0.0)
        if kn > 0.02 and self.isEnabled():
            kr = 1.5 + 3.6 * kn
            p.setBrush(QColor(255, 255, 255, 255))
            p.setPen(QPen(with_alpha(self._c2, 170), 1.2))
            p.drawEllipse(QPointF(left + fw, cy), kr, kr)


class Equalizer(QWidget, _Fader, _Reveal):
    """Five mirrored bars that dance while music plays; clicking toggles Windows system mute."""
    N = 5
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(33, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Mute volume")
        self._init_fader()
        self._h = [0.15] * self.N
        self._amp, self._t, self._playing = 0.0, 0.0, False
        self._muted = False
        self._down = False
        self._c1, self._c2 = QColor(255, 255, 255), QColor(255, 255, 255)

    def set_playing(self, on: bool):
        self._playing = bool(on)

    def set_muted(self, muted: bool):
        muted = bool(muted)
        if muted != self._muted:
            self._muted = muted
            self.setToolTip("Unmute volume" if muted else "Mute volume")
            self.update()

    def is_muted(self) -> bool:
        return self._muted

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = pastel(c1, 0.7), pastel(c2, 0.7)

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._down = True
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._down and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        self._down = False

    def tick(self, dt: float):
        self._t += dt
        active = self._playing and not self._muted
        self._amp += ((1.0 if active else 0.0) - self._amp) * min(1.0, dt * 6.0)
        t = self._t
        for i in range(self.N):
            a = 0.5 + 0.5 * math.sin(t * (3.1 + i * 1.7) + i * 1.9)
            b = 0.5 + 0.5 * math.sin(t * (6.3 + i * 0.9) + i * 0.7)
            tgt = 0.16 + 0.84 * (0.55 * a + 0.45 * b) * self._amp
            self._h[i] += (tgt - self._h[i]) * min(1.0, dt * 18.0)
        if self.isVisible():
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(self._rv * 0.95)

        if self._hover > 0.01 or self._muted:
            bg = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
            fill_a = int(self._hover * 34) + (18 if self._muted else 0)
            p.setBrush(QColor(255, 255, 255, fill_a))
            p.setPen(QPen(QColor(255, 255, 255, int(self._hover * 70) + (35 if self._muted else 0)), 1))
            p.drawRoundedRect(bg, bg.height() / 2.0, bg.height() / 2.0)

        p.setPen(Qt.NoPen)
        mid = self.height() / 2.0
        bar_area_h = 16.0
        x0 = (self.width() - 27.0) / 2.0
        for i, hv in enumerate(self._h):
            bh = 3.0 + (bar_area_h - 3.0) * hv
            r = QRectF(x0 + i * 6.0, mid - bh / 2.0, 3.0, bh)
            if self._muted:
                p.setBrush(QColor(255, 255, 255, 110))
            else:
                g = QLinearGradient(0, r.top(), 0, r.bottom())
                g.setColorAt(0.0, self._c1)
                g.setColorAt(1.0, self._c2)
                p.setBrush(g)
            p.drawRoundedRect(r, 1.5, 1.5)

        if self._muted:
            p.setPen(QPen(QColor(0, 0, 0, 150), 2.8, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(6.0, self.height() - 4.5), QPointF(self.width() - 6.0, 4.5))
            p.setPen(QPen(QColor(255, 110, 105, 240), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(6.0, self.height() - 4.5), QPointF(self.width() - 6.0, 4.5))


class ClickArea(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self._down = False

    def mousePressEvent(self, e):
        self._down = e.button() == Qt.LeftButton
        if self._down:
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._down and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        self._down = False


class Divider(QWidget, _Reveal):
    def __init__(self):
        super().__init__()
        self.setFixedSize(1, 92)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setOpacity(self._rv)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, QColor(255, 255, 255, 0))
        g.setColorAt(0.5, QColor(255, 255, 255, 62))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), g)


ART_M = 10


class ArtWidget(QWidget, _Reveal):
    """Artwork card with 3D tilt, spotlight, and crisp vector source-app badge icon."""
    clicked = Signal()
    TILT = 10.0

    def __init__(self):
        super().__init__()
        side = ART + 3 + 2 * ART_M
        self.setFixedSize(side, side)
        self.setMouseTracking(True)
        self._pix = QPixmap()
        self._glow = QPixmap()
        self._badge = None
        self._source_aumid = ""
        self._badge_icon = create_source_badge_icon("", 15)
        self._dim = False
        self._playing = False
        self._interactive = False
        self._hov, self._down = False, False
        self._tx = self._ty = self._rx = self._ry = 0.0
        self._cur = QPointF(ART / 2, ART / 2)
        self._s, self._spot, self._t = 0.97, 0.0, 0.0
        self.set_art(QPixmap())
        self.set_glow(QColor(DEFAULT_TINT_2))

    def _art_rect(self) -> QRectF:
        return QRectF(ART_M, ART_M, ART, ART)

    def set_art(self, raw: QPixmap):
        dpr = self.devicePixelRatioF()
        self._pix = round_pixmap(raw, ART, 12, dpr) if not raw.isNull() \
            else placeholder_art(ART, 12, dpr)
        self.update()

    def set_glow(self, color: QColor):
        side = ART + 2 * ART_M
        base = QPixmap(side, side)
        base.fill(Qt.transparent)
        q = QPainter(base)
        q.setRenderHint(QPainter.Antialiasing)
        q.setPen(Qt.NoPen)
        q.setBrush(QColor(color.red(), color.green(), color.blue(), 235))
        q.drawRoundedRect(QRectF(ART_M + 4, ART_M + 4, ART - 8, ART - 8), 16, 16)
        q.end()
        self._glow = blur_pixmap(base, 9.0)
        self.update()

    def set_badge(self, color: QColor | None, dim: bool, source: str = ""):
        self._badge, self._dim = color, dim
        if source != self._source_aumid:
            self._source_aumid = source
            self._badge_icon = create_source_badge_icon(source, 15)
        self.update()

    def set_playing(self, on: bool):
        self._playing = bool(on)

    def set_interactive(self, on: bool):
        self._interactive = bool(on)
        self.setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)
        if not on:
            self._hov = False

    def _track(self, pos: QPointF):
        r = self._art_rect()
        self._cur = QPointF(pos.x() - ART_M, pos.y() - ART_M)
        self._tx = max(-1.0, min(1.0, (pos.x() - r.center().x()) / (ART / 2)))
        self._ty = max(-1.0, min(1.0, (pos.y() - r.center().y()) / (ART / 2)))

    def enterEvent(self, e):
        if self._interactive:
            self._hov = True
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hov = False
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        if self._interactive:
            self._hov = self._art_rect().contains(e.position())
            self._track(e.position())
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        self._down = (e.button() == Qt.LeftButton and self._interactive
                      and self._art_rect().contains(e.position()))
        if self._down:
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if self._down and e.button() == Qt.LeftButton and self._art_rect().contains(e.position()):
            self.clicked.emit()
        self._down = False

    def tick(self, dt: float):
        self._t += dt
        k = 1.0 - math.exp(-dt * 12.0)
        tx, ty = (self._tx, self._ty) if self._hov else (0.0, 0.0)
        self._ry += (tx - self._ry) * k
        self._rx += (ty - self._rx) * k
        self._spot += ((1.0 if self._hov else 0.0) - self._spot) * k
        goal = 1.04 if self._hov else 0.97
        self._s += (goal - self._s) * k
        if self.isVisible():
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        rv = self._rv
        if rv <= 0.004:
            return

        if not self._dim:
            pulse = (0.50 + 0.22 * math.sin(self._t * 2.4)) if self._playing else 0.42
            p.setOpacity(rv * pulse)
            p.drawPixmap(0, 5, self._glow)

        c = self._art_rect().center()
        s = self._s * (0.82 + 0.18 * ease_out_back(rv, 1.2))
        tr = QTransform()
        tr.translate(c.x(), c.y())
        tr.rotate(-self._ry * self.TILT, Qt.YAxis)
        tr.rotate(self._rx * -self.TILT, Qt.XAxis)
        tr.scale(s, s)
        tr.translate(-c.x(), -c.y())
        p.setTransform(tr)
        p.translate(ART_M, ART_M)

        p.setOpacity(rv * (0.55 if self._dim else 1.0))
        p.drawPixmap(0, 0, self._pix)
        p.setOpacity(rv)

        if self._spot > 0.01 and not self._dim:
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, ART, ART), 12, 12)
            g = QRadialGradient(self._cur, 62)
            g.setColorAt(0.0, QColor(255, 255, 255, int(78 * self._spot)))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.save()
            p.setClipPath(clip)
            p.fillRect(QRectF(0, 0, ART, ART), g)
            p.restore()

        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, ART - 1, ART - 1), 12, 12)
        if self._badge is not None and not self._dim:
            b = QRectF(ART - 21, ART - 21, 24, 24)
            g = QLinearGradient(0, b.top(), 0, b.bottom())
            g.setColorAt(0, self._badge.lighter(118))
            g.setColorAt(1, self._badge.darker(120))
            p.setBrush(g)
            p.setPen(QPen(QColor(0, 0, 0, 110), 1))
            p.drawRoundedRect(b, 7, 7)
            p.setPen(QPen(QColor(255, 255, 255, 115), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(b.adjusted(1, 1, -1, -1), 6, 6)
            self._badge_icon.paint(p, QRect(int(b.x() + 4.5), int(b.y() + 4.5), 15, 15))


class WeatherChip(QWidget, _Reveal):
    """Current-location conditions under the calendar, tinted like the island."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(CAL_RECT.width(), 43)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._state = None
        self._c1 = QColor(DEFAULT_TINT)
        self._appear = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(460)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.valueChanged.connect(self._on_fade)

    def _on_fade(self, v):
        self._appear = float(v)
        self.update()

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1 = QColor(c1)
        self.update()

    def _tint(self, alpha: int = 255) -> QColor:
        c = self._c1
        return QColor(c.red(), c.green(), c.blue(), alpha)

    def set_state(self, state):
        if state == self._state:
            return
        self._state = state
        if self._appear <= 0.02:
            self._fade.start()
        self.update()

    def paintEvent(self, _e):
        st = self._state
        rv = self._rv * ease_out_cubic(self._appear)
        if st is None or rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        p.setOpacity(rv)
        w, hgt = self.width(), self.height()
        card = QRectF(0.5, 0.5, w - 1, hgt - 1)
        radius = 14.0

        body = QLinearGradient(0, card.top(), 0, card.bottom())
        body.setColorAt(0.0, QColor(255, 255, 255, 32))
        body.setColorAt(1.0, QColor(255, 255, 255, 10))
        p.setBrush(body)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(card, radius, radius)

        p.save()                                            # theme wash
        clip = QPainterPath()
        clip.addRoundedRect(card, radius, radius)
        p.setClipPath(clip)
        wash = QLinearGradient(card.topLeft(), card.bottomRight())
        wash.setColorAt(0.0, self._tint(62))
        wash.setColorAt(0.6, self._tint(8))
        wash.setColorAt(1.0, self._tint(0))
        p.setBrush(wash)
        p.drawRect(card)
        p.restore()

        rim = QLinearGradient(card.topLeft(), card.bottomRight())
        rim.setColorAt(0.0, QColor(255, 255, 255, 70))
        rim.setColorAt(1.0, QColor(255, 255, 255, 22))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(rim), 1.0))
        p.drawRoundedRect(card, radius, radius)

        icon_kind, label = weather_visual(st.code, st.is_day)
        accent = pastel(self._c1, 0.85, 1.0)
        mid = hgt / 2
        glow = QRadialGradient(QPointF(30, mid), 33)
        glow.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 78))
        glow.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(30, mid), 33, 33)
        paint_weather_icon(p, QRectF(16, mid - 14, 28, 28), icon_kind, accent)

        temp = f"{round(st.temp)}°"
        p.setFont(ui_font(25, QFont.Weight.Bold, True))
        p.setPen(QColor(0, 0, 0, 95))
        p.drawText(QRectF(51, 4, 100, 30), Qt.AlignLeft | Qt.AlignVCenter, temp)
        p.setPen(QColor(255, 255, 255, 246))
        p.drawText(QRectF(51, 3, 100, 30), Qt.AlignLeft | Qt.AlignVCenter, temp)

        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 0.4))
        p.setPen(QColor(255, 255, 255, 154))
        p.drawText(QRectF(52, 29, 100, 12), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(label, Qt.ElideRight, 100))

        hair = QLinearGradient(0, 8, 0, 35)
        hair.setColorAt(0.0, QColor(255, 255, 255, 0))
        hair.setColorAt(0.5, QColor(255, 255, 255, 56))
        hair.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hair), 1.0))
        p.drawLine(QPointF(w - 122, 8), QPointF(w - 122, 35))

        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 0.2))
        p.setPen(QColor(255, 255, 255, 210))
        city = p.fontMetrics().elidedText(st.city or "Current location", Qt.ElideRight, 96)
        p.drawText(QRectF(w - 110, 9, 96, 14), Qt.AlignRight | Qt.AlignVCenter, city)
        today = dt.date.today()
        p.setFont(ui_font(8, QFont.Weight.Medium, True, 0.9))
        p.setPen(QColor(255, 255, 255, 118))
        p.drawText(QRectF(w - 110, 23, 96, 12), Qt.AlignRight | Qt.AlignVCenter,
                   f"{today:%a} · {today:%b} {today.day}".upper())


class CalendarColumn(QWidget, _Reveal):
    """Big month + year, week strip, and today in a frosted glass capsule tinted to match the
    island's active theme."""

    PITCH = 30.6
    STRIP_X = 52

    def __init__(self):
        super().__init__()
        self.setFixedSize(CAL_RECT.size())
        self._today = dt.date.today()
        self._icon = create_icon_pixmap("calendar", 15, QColor(255, 255, 255, 150))
        self._c1 = QColor(DEFAULT_TINT)
        self._c2 = QColor(DEFAULT_TINT_2)

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = QColor(c1), QColor(c2)
        self.update()

    def set_date(self, d: dt.date):
        if d != self._today:
            self._today = d
            self.update()

    def paintEvent(self, _e):
        rv = self._rv
        if rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        dpr = self.devicePixelRatioF()
        t = self._today
        mon = t.strftime("%b")

        p.setOpacity(rv)
        p.setFont(ui_font(25, QFont.Weight.Bold, True))
        p.setPen(QColor(0, 0, 0, 110))
        p.drawText(QRectF(0, 3, 56, 34), Qt.AlignLeft | Qt.AlignVCenter, mon)
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRectF(0, 2, 56, 34), Qt.AlignLeft | Qt.AlignVCenter, mon)
        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 1.6))
        p.setPen(QColor(255, 255, 255, 120))
        p.drawText(QRectF(1, 33, 56, 12), Qt.AlignLeft | Qt.AlignVCenter, str(t.year))

        sw = self.width() - self.STRIP_X
        layer = QImage(int(sw * dpr), int(50 * dpr), QImage.Format_ARGB32_Premultiplied)
        layer.setDevicePixelRatio(dpr)
        layer.fill(Qt.transparent)
        lp = QPainter(layer)
        lp.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        accent_top = pastel(self._c1, 0.72, 0.88)
        accent_bot = self._c2.darker(125)
        rim_col = pastel(self._c1, 0.50, 1.0)

        for i in range(8):
            local = clamp01(rv * 1.7 - i * 0.09)
            if local <= 0.0:
                continue
            e = ease_out_cubic(local)
            d = t + dt.timedelta(days=i - 3)
            cx = 8 + self.PITCH * i
            weekend = d.weekday() >= 5
            lp.save()
            lp.setOpacity(e)
            lp.translate(0, (1.0 - e) * 7.0)
            if i == 3:
                pill = QRectF(cx - 17, 1, 34, 44)
                lp.setPen(Qt.NoPen)
                lp.setBrush(QColor(14, 18, 26, 115))
                lp.drawRoundedRect(pill, 13, 13)

                g = QLinearGradient(0, pill.top(), 0, pill.bottom())
                g.setColorAt(0.0, with_alpha(accent_top, 165))
                g.setColorAt(0.55, with_alpha(self._c1, 145))
                g.setColorAt(1.0, with_alpha(accent_bot, 135))
                lp.setBrush(g)
                lp.setPen(QPen(with_alpha(rim_col, 165), 1.0))
                lp.drawRoundedRect(pill, 13, 13)

                lp.setBrush(Qt.NoBrush)
                lp.setPen(QPen(QColor(255, 255, 255, 65), 1.0))
                lp.drawRoundedRect(pill.adjusted(1.0, 1.0, -1.0, -1.0), 12, 12)

                lp.setFont(ui_font(8, QFont.Weight.Bold, True, 0.8))
                lp.setPen(QColor(0, 0, 0, 120))
                lp.drawText(QRectF(cx - 20, 5, 40, 11), Qt.AlignCenter, d.strftime("%a").upper())
                lp.setPen(QColor(255, 255, 255, 242))
                lp.drawText(QRectF(cx - 20, 4, 40, 11), Qt.AlignCenter, d.strftime("%a").upper())

                lp.setFont(ui_font(21, QFont.Weight.Bold, True))
                lp.setPen(QColor(0, 0, 0, 130))
                lp.drawText(QRectF(cx - 18, 14, 36, 28), Qt.AlignCenter, f"{d.day:02d}")
                lp.setPen(QColor(255, 255, 255))
                lp.drawText(QRectF(cx - 18, 13, 36, 28), Qt.AlignCenter, f"{d.day:02d}")
            else:
                lp.setFont(ui_font(8, QFont.Weight.Medium, True, 0.6))
                lp.setPen(QColor(255, 130, 125, 130) if weekend else QColor(255, 255, 255, 110))
                lp.drawText(QRectF(cx - 15, 4, 30, 11), Qt.AlignCenter, "MTWTFSS"[d.weekday()])
                lp.setFont(ui_font(13, QFont.Weight.DemiBold, True))
                lp.setPen(QColor(255, 140, 135, 170) if weekend else QColor(255, 255, 255, 180))
                lp.drawText(QRectF(cx - 18, 13, 36, 28), Qt.AlignCenter, f"{d.day:02d}")
            lp.restore()
        fade = QLinearGradient(0, 0, sw, 0)
        fade.setColorAt(0.0, QColor(0, 0, 0, 255))
        fade.setColorAt(0.72, QColor(0, 0, 0, 255))
        fade.setColorAt(1.0, QColor(0, 0, 0, 0))
        lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        lp.fillRect(QRectF(0, 0, sw, 50), fade)
        lp.end()
        p.drawImage(QPointF(self.STRIP_X, 0), layer)


class RingGauge(QWidget, _Reveal):
    """Round glass dial with darkened contrast backing so clock and battery % remain crisp."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(RING_RECT.size())
        self._time = self._prev_time = ""
        self._ampm = ""
        self._pct, self._charging = None, False
        self._disp = 0.0
        self._t = 0.0
        self._c1, self._c2 = QColor(255, 255, 255), QColor(255, 255, 255)
        self._bolt = create_icon_pixmap("bolt", 11, QColor(255, 255, 255, 250))
        self._batt = create_icon_pixmap("battery", 12, QColor(255, 255, 255, 230))

        self._roll = 1.0
        self._roll_anim = QVariantAnimation(self)
        self._roll_anim.setDuration(420)
        self._roll_anim.setStartValue(0.0)
        self._roll_anim.setEndValue(1.0)
        self._roll_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._roll_anim.valueChanged.connect(self._on_roll)

        self._glide = QVariantAnimation(self)
        self._glide.setDuration(900)
        self._glide.setEasingCurve(QEasingCurve.OutCubic)
        self._glide.valueChanged.connect(self._on_glide)

    def _on_roll(self, v):
        self._roll = float(v)
        self.update()

    def _on_glide(self, v):
        self._disp = float(v)
        self.update()

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = pastel(c1, 0.6), pastel(c2, 0.6)
        self.update()

    def set_data(self, time_text, ampm, battery):
        pct, chg = battery if battery else (None, False)
        if time_text != self._time:
            self._prev_time, self._time = self._time, time_text
            if self._prev_time:
                self._roll_anim.stop()
                self._roll = 0.0
                self._roll_anim.start()
        if pct != self._pct and pct is not None:
            self._glide.stop()
            self._glide.setStartValue(self._disp)
            self._glide.setEndValue(float(pct))
            self._glide.start()
        self._ampm, self._pct, self._charging = ampm, pct, chg
        self.update()

    def tick(self, dt: float):
        self._t += dt
        if self._charging and self.isVisible():
            self.update()

    def paintEvent(self, _e):
        rv = self._rv
        if rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        s = 0.86 + 0.14 * ease_out_back(rv, 1.1)
        p.translate(44, 44)
        p.scale(s, s)
        p.translate(-44, -44)
        p.setOpacity(rv)
        disc = QRectF(2, 2, 84, 84)
        arc = disc.adjusted(5, 5, -5, -5)

        g = QRadialGradient(disc.center().x() - 10, disc.top() + 12, 68)
        g.setColorAt(0.0, QColor(14, 18, 28, 155))
        g.setColorAt(0.75, QColor(10, 13, 22, 185))
        g.setColorAt(1.0, QColor(8, 10, 18, 205))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 58), 1))
        p.drawEllipse(disc)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 44), 4.5))
        p.drawEllipse(arc)

        if self._pct is not None:
            low = self._pct <= 20 and not self._charging
            col = QColor(255, 105, 95) if low else QColor(80, 220, 130) if self._charging \
                else self._c2
            frac = max(0.01, self._disp / 100.0) * ease_out_cubic(rv * 1.15)
            span = int(-360 * 16 * frac)
            if self._charging:
                halo = QColor(col)
                halo.setAlphaF(0.16 + 0.12 * math.sin(self._t * 3.0))
                p.setPen(QPen(halo, 10, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(arc, 90 * 16, span)
            p.setPen(QPen(col, 4.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, 90 * 16, span)
            if 0.02 < frac < 0.995:
                ang = math.radians(90.0 - 360.0 * frac)
                tip = QPointF(arc.center().x() + arc.width() / 2 * math.cos(ang),
                              arc.center().y() - arc.height() / 2 * math.sin(ang))
                gl = QRadialGradient(tip, 8)
                gl.setColorAt(0, QColor(col.red(), col.green(), col.blue(), 170))
                gl.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
                p.setPen(Qt.NoPen)
                p.setBrush(gl)
                p.drawEllipse(tip, 8, 8)
                p.setBrush(QColor(255, 255, 255, 255))
                p.drawEllipse(tip, 2.3, 2.3)

        self._paint_clock(p)

        sub = f"{self._ampm} · {self._pct}%" if self._pct is not None else self._ampm
        sub_font = ui_font(9, QFont.Weight.Bold, True, 0.8)
        p.setFont(sub_font)
        sw = min(64.0, p.fontMetrics().horizontalAdvance(sub) + 12.0)
        sub_pill = QRectF((88.0 - sw) / 2.0, 48.0, sw, 14.0)
        p.setBrush(QColor(6, 8, 14, 175))
        p.setPen(QPen(QColor(255, 255, 255, 34), 0.8))
        p.drawRoundedRect(sub_pill, 7.0, 7.0)

        p.setPen(QColor(0, 0, 0, 210))
        p.drawText(sub_pill.translated(0, 0.8), Qt.AlignCenter, sub)
        p.setPen(QColor(245, 248, 255, 248))
        p.drawText(sub_pill, Qt.AlignCenter, sub)

        if self._pct is not None:
            badge = QRectF(70, 70, 18, 18)
            p.setBrush(QColor(18, 22, 34, 235))
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            p.drawEllipse(badge)
            (self._bolt if self._charging else self._batt).paint(
                p, QRect(int(badge.x() + 3.5), int(badge.y() + 3.5), 11, 11))

    def _paint_clock(self, p: QPainter):
        font = ui_font(21, QFont.Weight.Bold, True)
        p.setFont(font)
        fm = p.fontMetrics()
        n = max(len(self._time), len(self._prev_time))
        new, old = self._time.rjust(n), self._prev_time.rjust(n)
        widths = [fm.horizontalAdvance(ch) for ch in new]
        x = (88 - sum(widths)) / 2.0
        roll = self._roll
        for i, ch in enumerate(new):
            slot = QRectF(x, 23, widths[i], 24)
            changed = roll < 1.0 and old[i] != ch
            if not changed:
                self._glyph(p, slot, ch, 1.0, 0.0)
            else:
                self._glyph(p, slot, old[i], 1.0 - roll, -10.0 * roll)
                self._glyph(p, slot, ch, roll, 10.0 * (1.0 - roll))
            x += widths[i]

    @staticmethod
    def _glyph(p: QPainter, slot: QRectF, ch: str, alpha: float, dy: float):
        if alpha <= 0.01 or ch == " ":
            return
        r = slot.translated(0, dy)
        p.setPen(QColor(0, 0, 0, int(150 * alpha)))
        p.drawText(r.translated(0, 1), Qt.AlignCenter, ch)
        p.setPen(QColor(255, 255, 255, int(255 * alpha)))
        p.drawText(r, Qt.AlignCenter, ch)


def _page_card(p: QPainter, rect: QRectF, alpha: int = 26) -> None:
    """The frosted sub-card every island page sits on."""
    p.save()
    p.setPen(Qt.NoPen)
    for i in range(6, 0, -1):
        p.setBrush(QColor(0, 0, 0, 4 + (6 - i) * 2))
        p.drawRoundedRect(rect.adjusted(-i * 0.5, i * 0.2, i * 0.5, i * 1.1), 20 + i, 20 + i)
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0.0, QColor(255, 255, 255, alpha + 12))
    grad.setColorAt(1.0, QColor(255, 255, 255, max(6, alpha - 8)))
    p.setBrush(grad)
    p.setPen(QPen(QColor(255, 255, 255, 46), 1.0))
    p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)
    p.restore()


class FocusPage(QWidget, _Reveal):
    """Focus timer: countdown ring, presets and today's tally."""

    preset_changed = Signal(object)
    primary_clicked = Signal()
    reset_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(W, H)
        self._c1, self._c2 = QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2)
        self._state = {"total": 1500.0, "left": 1500.0, "active": False, "paused": False,
                       "done": 0, "seconds": 0, "preset": FocusTimer.DEFAULT_MIN}
        self._sweep = 1.0
        self._t = 0.0

        self.presets = SegmentedControl([(m, f"{m}m") for m in FocusTimer.PRESETS], 176, 24)
        self.presets.setParent(self)
        self.presets.move(136, 109)
        self.presets.changed.connect(self.preset_changed)

        self.primary = ActionButton("play", "Start focus", "primary")
        self.primary.setFixedSize(136, 28)
        self.primary.setParent(self)
        self.primary.move(330, 56)
        self.primary.clicked.connect(self.primary_clicked)

        self.reset = ActionButton("reset", "Reset", "plain")
        self.reset.setFixedSize(96, 28)
        self.reset.setParent(self)
        self.reset.move(330, 94)
        self.reset.clicked.connect(self.reset_clicked)

        self._pulse = QTimer(self)
        self._pulse.setInterval(33)
        self._pulse.timeout.connect(self._tick)

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = QColor(c1), QColor(c2)
        self.update()

    def set_state(self, focus):
        self._state = {"total": focus.total, "left": focus.remaining,
                       "active": focus.active, "paused": focus.paused,
                       "done": focus.done, "seconds": focus.done_seconds,
                       "preset": focus.preset}
        running = focus.active and not focus.paused
        self.primary.set_label("Pause" if running else
                               ("Resume" if focus.paused else "Start focus"))
        self.primary.set_icon("pause" if running else "play")
        self.presets.set_value(focus.preset)
        self.update()

    def celebrate(self):
        self._sweep = 0.0
        self.update()

    def showEvent(self, e):
        self._pulse.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._pulse.stop()
        super().hideEvent(e)

    def _tick(self):
        self._t += 0.033
        if self._sweep < 1.0:
            self._sweep = min(1.0, self._sweep + 0.033 / 0.9)
        self.update()

    def paintEvent(self, _e):
        rv = self._rv
        if rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        st = self._state
        p.setOpacity(rv)
        _page_card(p, QRectF(12, 42, 618, 100))

        total = max(1.0, float(st["total"]))
        left = max(0.0, float(st["left"]))
        frac = clamp01(left / total)
        paused = bool(st["paused"])
        accent = QColor(255, 178, 92) if paused else pastel(self._c2, 0.8)

        disc = QRectF(24, 44, 96, 96)
        arc = disc.adjusted(7.0, 7.0, -7.0, -7.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 13, 22, 150))
        p.drawEllipse(disc)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 40), 3.2))
        p.drawEllipse(arc)
        if frac > 0.002:
            p.setPen(QPen(accent, 3.2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, 90 * 16, int(-360 * 16 * frac))

        p.setFont(ui_font(25, QFont.Weight.Bold, True))
        p.setPen(QColor(0, 0, 0, 150))
        p.drawText(disc.translated(0, -6.0), Qt.AlignCenter, str(st["preset"]))
        p.setPen(QColor(255, 255, 255, 246))
        p.drawText(disc.translated(0, -7.0), Qt.AlignCenter, str(st["preset"]))
        p.setFont(ui_font(7, QFont.Weight.Bold, True, 1.4))
        p.setPen(QColor(255, 255, 255, 142))
        p.drawText(disc.translated(0, 22.0), Qt.AlignCenter, "MIN")

        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 1.7))
        p.setPen(QColor(255, 255, 255, 196))
        p.drawText(QRectF(136, 47, 210, 14), Qt.AlignLeft | Qt.AlignVCenter, "FOCUS SESSION")

        big = focus_clock(left)
        p.setFont(ui_font(30, QFont.Weight.Bold, True))
        p.setPen(QColor(0, 0, 0, 120))
        p.drawText(QRectF(136, 58, 200, 38).translated(0, 1.5),
                   Qt.AlignLeft | Qt.AlignVCenter, big)
        p.setPen(QColor(255, 255, 255, 248))
        p.drawText(QRectF(136, 58, 200, 38), Qt.AlignLeft | Qt.AlignVCenter, big)

        p.setFont(ui_font(9, QFont.Weight.Medium, False, 0.2))
        p.setPen(QColor(255, 255, 255, 156))
        note = ("Paused · pick up where you left off" if paused else
                "Running · the pill keeps the countdown" if st["active"] else
                "Pick a length, then start")
        p.drawText(QRectF(136, 92, 320, 13), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(note, Qt.ElideRight, 318))

        box = QRectF(478, 50, 140, 86)
        p.setBrush(QColor(255, 255, 255, 14))
        p.setPen(QPen(QColor(255, 255, 255, 38), 1.0))
        p.drawRoundedRect(box, 16, 16)
        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 1.7))
        p.setPen(QColor(255, 255, 255, 190))
        p.drawText(QRectF(box.x() + 14, box.y() + 9, box.width() - 28, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, "TODAY")
        p.setFont(ui_font(22, QFont.Weight.Bold, True))
        p.setPen(QColor(0, 0, 0, 120))
        p.drawText(QRectF(box.x() + 14, box.y() + 21, 60, 30).translated(0, 1.0),
                   Qt.AlignLeft | Qt.AlignVCenter, str(st["done"]))
        p.setPen(QColor(255, 255, 255, 246))
        p.drawText(QRectF(box.x() + 14, box.y() + 21, 60, 30),
                   Qt.AlignLeft | Qt.AlignVCenter, str(st["done"]))
        p.setFont(ui_font(9, QFont.Weight.Medium, False, 0.3))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(box.x() + 46, box.y() + 30, 80, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, "sessions")
        mins = int(st["seconds"]) // 60
        focused = f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins} min"
        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 0.4))
        p.setPen(with_alpha(pastel(self._c2, 0.9), 235))
        p.drawText(QRectF(box.x() + 14, box.y() + 54, box.width() - 28, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, focused)

        if 0.0 < self._sweep < 1.0:
            s = ease_out_cubic(self._sweep)
            x0 = 12 + 606 * s
            band = QLinearGradient(x0 - 60, 0, x0 + 60, 0)
            band.setColorAt(0.0, QColor(255, 255, 255, 0))
            band.setColorAt(0.5, QColor(255, 255, 255, int(48 * math.sin(math.pi * self._sweep))))
            band.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(band)
            p.drawRoundedRect(QRectF(12, 42, 618, 100), 20, 20)


def mono_font(px: int, weight=QFont.Weight.Medium, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies(["Cascadia Mono", "Cascadia Code", "Consolas", "Courier New"])
    f.setPixelSize(px)
    f.setWeight(weight)
    f.setStyleStrategy(QFont.PreferAntialias)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    return f


class AgentPage(QWidget, _Reveal):
    """Live agent view: eyes + run clock, the task, the latest diff, and a phase bar."""

    PHASES = ("READ", "EDIT", "TEST", "DONE")
    LEFT_CX = 86.0
    COL_X, COL_W = 168.0, 456.0
    _STATUS = {"Editing": "EDITING", "Writing": "EDITING", "Reading": "READING",
               "Scanning": "READING", "Searching": "READING", "Running": "TESTING",
               "Checking": "TESTING", "Planning": "PLANNING",
               "Waiting for you": "NEEDS YOU"}

    def __init__(self):
        super().__init__()
        self.setFixedSize(W, H)
        self._c1, self._c2 = QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2)
        self._state = {"kind": "", "project": "", "branch": "", "action": "", "detail": "",
                       "busy": False, "waiting": False, "age": 0.0, "steps": [],
                       "title": "", "file": "", "diff": None, "phase": -1, "elapsed": 0.0}
        self._got = time.monotonic()
        self._prev = self._got
        self._t = 0.0
        self._seg = [0.0] * 4
        self._diff_key, self._diff_t = None, 1.0
        self._title, self._title_old, self._title_t = "", "", 1.0
        self._pulse = QTimer(self)
        self._pulse.setInterval(16)
        self._pulse.timeout.connect(self._tick)

    # ------------------------------------------------------------------ api #
    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = QColor(c1), QColor(c2)
        self.update()

    def set_state(self, state: dict):
        self._state = dict(state or {})
        self._got = time.monotonic()
        title = self._headline()
        if title != self._title:
            self._title_old, self._title, self._title_t = self._title, title, 0.0
        diff = self._state.get("diff")
        key = (self._state.get("file"), diff.get("old"), diff.get("new")) if diff else None
        if key != self._diff_key:
            self._diff_key = key
            self._diff_t = 0.0 if key else 1.0
        self.update()

    def showEvent(self, e):
        self._prev = time.monotonic()
        self._pulse.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._pulse.stop()
        super().hideEvent(e)

    # --------------------------------------------------------------- motion #
    def _headline(self) -> str:
        st = self._state
        if not st.get("kind"):
            return "No agent detected"
        return (str(st.get("title") or "").strip()
                or str(st.get("project") or "").strip() or "Waiting for the next task")

    def _targets(self) -> list[float]:
        st = self._state
        if not st.get("kind"):
            return [0.0] * 4
        if not st.get("busy"):
            return [1.0] * 4
        if st.get("app"):
            return [0.0] * 4
        phase = max(0, min(2, int(st.get("phase", -1))))
        return [1.0 if i < phase else (0.5 if i == phase else 0.0) for i in range(3)] + [0.0]

    def _tick(self):
        now = time.monotonic()
        dt_ = min(0.05, max(0.0, now - self._prev))
        self._prev = now
        self._t += dt_
        k = 1.0 - math.exp(-dt_ * 5.5)
        for i, goal in enumerate(self._targets()):
            self._seg[i] += (goal - self._seg[i]) * k
        self._diff_t = min(1.0, self._diff_t + dt_ / 0.5)
        self._title_t = min(1.0, self._title_t + dt_ / 0.42)
        self.update()

    def _elapsed(self) -> float:
        st = self._state
        extra = (time.monotonic() - self._got) if st.get("busy") else 0.0
        return float(st.get("elapsed") or 0.0) + extra

    # ------------------------------------------------------------- painting #
    def _accent(self, kind: str, waiting: bool) -> QColor:
        if waiting:
            return QColor(255, 178, 92)
        if "claude" in kind.lower():
            return QColor(238, 134, 74)
        return pastel(self._c2, 0.85, 1.0) if kind else pastel(self._c1, 0.55, 1.0)

    @staticmethod
    def _shadowed(p, rect, flags, text, color, lift: float = 1.0):
        p.setPen(QColor(0, 0, 0, 110))
        p.drawText(rect.translated(0, lift), flags, text)
        p.setPen(color)
        p.drawText(rect, flags, text)

    def paintEvent(self, _e):
        rv = self._rv
        if rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        st = self._state
        kind = str(st.get("kind") or "")
        busy = bool(st.get("busy"))
        waiting = bool(st.get("waiting")) and busy
        accent = self._accent(kind, waiting)
        a_left, a_mid, a_low = seg(rv, 0.0, 0.55), seg(rv, 0.12, 0.55), seg(rv, 0.28, 0.55)

        # warm wash across the whole island, like the reference
        p.save()
        p.setClipPath(notch_path(QRectF(0, 0, W, H), BOTTOM_RADIUS))
        wash = QLinearGradient(0, H, W * 0.85, 0)
        wash.setColorAt(0.0, with_alpha(accent, int(78 * rv)))
        wash.setColorAt(0.55, with_alpha(accent, int(22 * rv)))
        wash.setColorAt(1.0, with_alpha(accent, 0))
        p.fillRect(QRectF(0, 0, W, H), wash)
        p.restore()

        self._paint_left(p, kind, busy, waiting, accent, a_left)
        self._paint_task(p, kind, accent, a_mid)
        self._paint_phases(p, kind, busy, accent, a_low)

    # --- left column: eyes, clock, status ------------------------------------ #
    def _paint_left(self, p, kind, busy, waiting, accent, a):
        st, t, cx = self._state, self._t, self.LEFT_CX
        p.save()
        p.setOpacity(self._rv * a)
        p.translate(0, (1.0 - a) * 8.0)

        cyc = t % 4.4
        blink = math.sin(math.pi * cyc / 0.2) if cyc < 0.2 else 0.0
        asleep = not kind
        height = 5.0 if asleep else 38.0 * (1.0 - 0.88 * blink)
        gaze = 0.0 if asleep else (3.2 * math.sin(t * 0.8) if busy else 1.2 * math.sin(t * 0.5))
        bob = -2.5 * abs(math.sin(t * 3.2)) if waiting else 0.0
        mid_y = 64.0 + bob

        glow = QRadialGradient(QPointF(cx, mid_y), 54)
        glow.setColorAt(0.0, with_alpha(accent, 96 if busy else 44))
        glow.setColorAt(1.0, with_alpha(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, mid_y), 54, 54)

        for sign in (-1, 1):
            r = QRectF(cx + sign * 19 - 13 + gaze, mid_y - height / 2, 26, height)
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0.0, QColor(255, 238, 226, 128 if asleep else 238))
            g.setColorAt(1.0, QColor(255, 214, 190, 90 if asleep else 196))
            p.setBrush(g)
            p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
            p.drawRoundedRect(r, 13, min(13.0, height / 2))

        clock = focus_clock(self._elapsed())
        p.setFont(ui_font(24, QFont.Weight.Bold, True))
        self._shadowed(p, QRectF(cx - 64, 88, 128, 28), Qt.AlignCenter, clock,
                       QColor(255, 255, 255, 250), 1.5)

        if not kind:
            label = "NO AGENT"
        elif waiting:
            label = "NEEDS YOU"
        elif not busy:
            label = "DONE"
        elif st.get("app"):
            label = "OPEN"
        else:
            label = self._STATUS.get(str(st.get("action") or ""),
                                    str(st.get("action") or "WORKING").upper())
        p.setFont(ui_font(8, QFont.Weight.Bold, False, 1.5))
        tw = p.fontMetrics().horizontalAdvance(label)
        x0 = cx - (7 + 6 + tw) / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(accent, 235 if busy else 120))
        p.drawRoundedRect(QRectF(x0, 124.5, 6, 6), 1.5, 1.5)
        p.setPen(QColor(255, 255, 255, 196))
        p.drawText(QRectF(x0 + 13, 120, tw + 4, 14), Qt.AlignLeft | Qt.AlignVCenter, label)
        p.restore()

    # --- right column: task + diff card -------------------------------------- #
    def _paint_task(self, p, kind, accent, a):
        st = self._state
        x, w = self.COL_X, self.COL_W
        p.save()
        p.setOpacity(self._rv * a)
        p.translate(0, (1.0 - a) * 7.0)

        e = ease_out_cubic(self._title_t)
        p.setFont(ui_font(13, QFont.Weight.DemiBold, True, 0.1))
        fm = p.fontMetrics()
        for text, alpha, dy in ((self._title_old, 1.0 - e, -6.0 * e),
                                (self._title, e, 6.0 * (1.0 - e))):
            if alpha <= 0.01 or not text:
                continue
            p.setOpacity(self._rv * a * alpha)
            p.setPen(QColor(255, 255, 255, 238))
            p.drawText(QRectF(x, 40 + dy, w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(text, Qt.ElideRight, int(w)))
        p.setOpacity(self._rv * a)

        card = QRectF(x, 61.5, w, 52)
        p.setBrush(QColor(8, 10, 16, 118))
        p.setPen(QPen(QColor(255, 255, 255, 36), 1.0))
        p.drawRoundedRect(card, 13, 13)

        diff = st.get("diff")
        file = str(st.get("file") or "")
        if diff:
            head = (file or "CHANGE").upper()
            rows = []
            if diff.get("old"):
                rows.append(("del", diff.get("line", 1), diff["old"]))
            if diff.get("new"):
                rows.append(("add", diff.get("line", 1), diff["new"]))
            plus, minus = int(diff.get("plus", 0)), int(diff.get("minus", 0))
        else:
            steps = [str(s) for s in (st.get("steps") or [])][:2]
            head = "ACTIVITY" if steps else ("STANDBY" if kind else "AGENT")
            rows = [("info", 0, s) for s in steps] or [("info", 0,
                    "Waiting for the first change" if kind
                    else "Start Claude Code or Codex to see edits here")]
            plus = minus = 0

        p.setFont(mono_font(8, QFont.Weight.Bold, 0.9))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(x + 13, 63, w * 0.6, 15), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(head, Qt.ElideRight, int(w * 0.6)))
        if diff:
            p.setFont(mono_font(8, QFont.Weight.Bold))
            sub_w = p.fontMetrics().horizontalAdvance(f"-{minus}")
            p.setPen(QColor(255, 118, 118, 235))
            p.drawText(QRectF(card.right() - 13 - sub_w, 63, sub_w + 2, 15),
                       Qt.AlignRight | Qt.AlignVCenter, f"-{minus}")
            add = f"+{plus}"
            add_w = p.fontMetrics().horizontalAdvance(add)
            p.setPen(QColor(112, 222, 150, 240))
            p.drawText(QRectF(card.right() - 13 - sub_w - 7 - add_w, 63, add_w + 2, 15),
                       Qt.AlignRight | Qt.AlignVCenter, add)

        d = ease_out_cubic(self._diff_t)
        clip = QPainterPath()
        clip.addRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 12.5, 12.5)
        p.save()
        p.setClipPath(clip)
        for i, (kind_, line, text) in enumerate(rows[:2]):
            ry = 79.0 + i * 17.0 + (1.0 - d) * 7.0
            p.setOpacity(self._rv * a * d)
            row = QRectF(card.left() + 1, ry, w - 2, 17)
            if kind_ == "del":
                p.fillRect(row, QColor(214, 70, 70, 82))
            elif kind_ == "add":
                p.fillRect(row, QColor(58, 170, 96, 78))
            col = (QColor(255, 218, 214, 240) if kind_ == "del" else
                   QColor(214, 255, 226, 240) if kind_ == "add" else QColor(255, 255, 255, 190))
            p.setFont(mono_font(9))
            p.setPen(QColor(255, 255, 255, 112))
            p.drawText(QRectF(x + 13, ry, 20, 17), Qt.AlignLeft | Qt.AlignVCenter,
                       str(line) if line else "›")
            p.setPen(col)
            p.drawText(QRectF(x + 40, ry, w - 54, 17), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(text, Qt.ElideRight, int(w - 56)))
        p.restore()
        p.restore()

    # --- phase bar ------------------------------------------------------------ #
    def _paint_phases(self, p, kind, busy, accent, a):
        x, w = self.COL_X, self.COL_W
        gap = 6.0
        sw = (w - gap * 3) / 4.0
        p.save()
        p.setOpacity(self._rv * a)
        p.translate(0, (1.0 - a) * 6.0)
        for i, name in enumerate(self.PHASES):
            sx = x + i * (sw + gap)
            fill = clamp01(self._seg[i])
            track = QRectF(sx, 121, sw, 3)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(track, 1.5, 1.5)
            if fill > 0.004:
                bar = QRectF(sx, 121, max(3.0, sw * fill), 3)
                p.setBrush(QColor(255, 246, 238, 238))
                p.drawRoundedRect(bar, 1.5, 1.5)
                if busy and 0.05 < fill < 0.98:               # shimmer on the live segment
                    p.save()
                    p.setClipRect(bar)
                    bw = 26.0
                    x0 = bar.left() - bw + (bar.width() + bw) * ((self._t / 1.6) % 1.0)
                    g = QLinearGradient(x0, 0, x0 + bw, 0)
                    g.setColorAt(0.0, QColor(255, 255, 255, 0))
                    g.setColorAt(0.5, with_alpha(accent, 200))
                    g.setColorAt(1.0, QColor(255, 255, 255, 0))
                    p.setBrush(g)
                    p.drawRect(bar)
                    p.restore()
            p.setFont(ui_font(8, QFont.Weight.Bold, False, 1.4))
            p.setPen(QColor(255, 255, 255, int(112 + 124 * clamp01(fill * 1.6))))
            p.drawText(QRectF(sx, 127, sw, 12), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.restore()


class AgentCard(QWidget, _Reveal):
    """Home-page agent card: a small character whose mood mirrors what the agent is doing.

    sleep   · no agent running          focus   · working, eyes scan the work
    curious · the agent needs an answer happy   · just finished
    idle    · session open but quiet    (eyes also follow the cursor)
    """

    clicked = Signal()
    focus_clicked = Signal()
    bt_clicked = Signal()
    bt_disconnect = Signal(object)
    VIEWS = 3                    # 0 agent  ·  1 focus timer  ·  2 bluetooth (scroll to switch)
    PHASES = AgentPage.PHASES
    # mood -> (eye height, smile, gaze sweep, bob, asymmetry)
    MOODS = {"sleep": (0.13, 0.0, 0.0, 0.0, 0.0), "idle": (1.0, 0.0, 1.0, 0.0, 0.0),
             "focus": (0.80, 0.0, 3.6, 0.0, 0.0), "curious": (1.16, 0.0, 1.6, 2.6, 0.2),
             "happy": (0.62, 1.0, 0.0, 1.8, 0.0)}

    def __init__(self):
        super().__init__()
        self.setFixedSize(AGENT_CARD.size())
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self._c1, self._c2 = QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2)
        self._state = {"kind": "", "busy": False, "waiting": False, "age": 0.0, "action": "",
                       "detail": "", "phase": -1, "elapsed": 0.0}
        self._got = self._prev = time.monotonic()
        self._t = 0.0
        self._hover, self._hov, self._down = 0.0, False, False
        self._look, self._look_t = QPointF(0, 0), QPointF(0, 0)
        self._par = list(self.MOODS["sleep"])
        self._seg = [0.0] * 4
        self._text, self._text_old, self._text_t = "", "", 1.0
        self._vpos, self._vtarget = 0.0, 0.0        # continuous, endless (wraps every VIEWS)
        self._settle = QTimer(self)                 # after the wheel stops, ease to a whole view
        self._settle.setSingleShot(True)
        self._settle.setInterval(130)
        self._settle.timeout.connect(self._snap)
        self._focus = None
        self._devices: list = []
        self._bt_hot = False
        self._pulse = QTimer(self)
        self._pulse.setInterval(16)
        self._pulse.timeout.connect(self._tick)

    # ------------------------------------------------------------------ api #
    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = QColor(c1), QColor(c2)
        self.update()

    def set_focus(self, focus):
        self._focus = focus

    def set_devices(self, devices):
        self._devices = list(devices or [])
        self.update()

    def _cur(self) -> int:
        return int(round(self._vpos)) % self.VIEWS

    def _snap(self):
        self._vtarget = float(round(self._vtarget))

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if not dy:
            e.ignore()
            return
        # follow the wheel continuously: one notch = one view, a touchpad moves it fractionally
        step = max(-1.0, min(1.0, dy / 120.0))
        self._vtarget += step                       # scroll up = next, and it never runs out
        self._vtarget = max(self._vpos - 1.5, min(self._vpos + 1.5, self._vtarget))
        self._settle.start()
        e.accept()

    def set_state(self, state: dict):
        self._state = dict(state or {})
        self._got = time.monotonic()
        line = self._headline()
        if line != self._text:
            self._text_old, self._text, self._text_t = self._text, line, 0.0
        self.update()

    def showEvent(self, e):
        self._prev = time.monotonic()
        self._pulse.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._pulse.stop()
        super().hideEvent(e)

    # ---------------------------------------------------------------- state #
    def mood(self) -> str:
        st = self._state
        if not st.get("kind"):
            return "sleep"
        if st.get("busy"):
            return "curious" if st.get("waiting") else "focus"
        return "happy" if float(st.get("age") or 0.0) < 30.0 else "idle"

    def _headline(self) -> str:
        st, mood = self._state, self.mood()
        if mood == "sleep":
            return "No agent running"
        if mood == "curious":
            return "Needs your answer"
        if mood == "happy":
            return "All done"
        if mood == "idle":
            return "Session idle"
        text = str(st.get("action") or "Working")
        if st.get("app"):
            return text
        return f"{text} {st.get('detail') or ''}".strip()

    def _sub(self) -> str:
        st, mood = self._state, self.mood()
        clock = focus_clock(self._elapsed())
        if mood == "sleep":
            return "Start Claude Code or Codex"
        if mood == "happy":
            return f"Finished in {clock}"
        if mood == "idle":
            return "Click to open the agent"
        phase = max(0, min(2, int(st.get("phase", -1))))
        return f"{clock}  ·  {self.PHASES[phase]}" if not st.get("app") else f"{clock}  ·  OPEN"

    def _elapsed(self) -> float:
        extra = (time.monotonic() - self._got) if self._state.get("busy") else 0.0
        return float(self._state.get("elapsed") or 0.0) + extra

    def _targets(self) -> list[float]:
        st = self._state
        if not st.get("kind"):
            return [0.0] * 4
        if not st.get("busy"):
            return [1.0] * 4
        if st.get("app"):
            return [0.0] * 4
        phase = max(0, min(2, int(st.get("phase", -1))))
        return [1.0 if i < phase else (0.5 if i == phase else 0.0) for i in range(3)] + [0.0]

    def _accent(self) -> QColor:
        return {"sleep": QColor(150, 158, 190), "curious": QColor(255, 178, 92),
                "happy": QColor(112, 222, 150), "idle": pastel(self._c2, 0.7, 1.0)}.get(
            self.mood(), QColor(238, 134, 74))

    # --------------------------------------------------------------- motion #
    def _tick(self):
        now = time.monotonic()
        dt_ = min(0.05, max(0.0, now - self._prev))
        self._prev = now
        self._t += dt_
        k = 1.0 - math.exp(-dt_ * 7.0)
        goal = self.MOODS[self.mood()]
        for i, g in enumerate(goal):
            self._par[i] += (g - self._par[i]) * k
        kk = 1.0 - math.exp(-dt_ * 10.0)
        self._look.setX(self._look.x() + (self._look_t.x() - self._look.x()) * kk)
        self._look.setY(self._look.y() + (self._look_t.y() - self._look.y()) * kk)
        for i, g in enumerate(self._targets()):
            self._seg[i] += (g - self._seg[i]) * (1.0 - math.exp(-dt_ * 5.5))
        self._hover += ((1.0 if self._hov else 0.0) - self._hover) * (1.0 - math.exp(-dt_ * 12.0))
        self._text_t = min(1.0, self._text_t + dt_ / 0.4)
        self._vpos += (self._vtarget - self._vpos) * (1.0 - math.exp(-dt_ * 10.0))
        if abs(self._vtarget - self._vpos) < 0.003 and not self._settle.isActive():
            wrap = math.floor(self._vtarget / self.VIEWS) * self.VIEWS
            self._vtarget -= wrap
            self._vpos = float(self._vtarget)
        tip = ("Open the agent view", "Start or pause focus", "Open Bluetooth")[self._cur()]
        if tip != self.toolTip():
            self.setToolTip(tip)
        self.update()

    # --------------------------------------------------------------- events #
    def enterEvent(self, e):
        self._hov = True
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hov, self._down = False, False
        self._look_t = QPointF(0, 0)
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        c = QPointF(self.width() / 2.0, 40.0)
        d = e.position() - c
        self._look_t = QPointF(max(-1.0, min(1.0, d.x() / 60.0)) * 4.0,
                               max(-1.0, min(1.0, d.y() / 40.0)) * 3.0)
        self._bt_hot = self._cur() == 2 and self._bt_btn().contains(e.position())
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        self._down = e.button() == Qt.LeftButton
        if self._down:
            e.accept()
        else:
            super().mousePressEvent(e)

    def _bt_btn(self) -> QRectF:
        return QRectF(12, 77, self.width() - 24, 20)

    def mouseReleaseEvent(self, e):
        if self._down and e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            cur = self._cur()
            if cur == 1:
                self.focus_clicked.emit()
            elif cur == 2:
                if self._devices and self._bt_btn().contains(e.position()):
                    self.bt_disconnect.emit(self._devices[0])
                else:
                    self.bt_clicked.emit()
            else:
                self.clicked.emit()
        self._down = False

    # ------------------------------------------------------------- painting #
    def paintEvent(self, _e):
        vp = self._vpos
        if abs(vp - round(vp)) < 0.004 and int(round(vp)) % self.VIEWS == 0:
            self._paint_agent_card(self)
            return
        rv = self._rv
        if rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        accent = self._focus_accent()
        card = QRectF(0.5, 0.5, w - 1, h - 1)
        body = QLinearGradient(0, 0, 0, h)
        body.setColorAt(0.0, QColor(255, 255, 255, 34 + int(18 * self._hover)))
        body.setColorAt(1.0, QColor(255, 255, 255, 12 + int(10 * self._hover)))
        p.setOpacity(rv)
        p.setBrush(body)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(card, 18, 18)
        clip = QPainterPath()
        clip.addRoundedRect(card, 18, 18)
        p.save()
        p.setClipPath(clip)
        wash = QRadialGradient(QPointF(w * 0.5, 40), w * 0.95)
        wash.setColorAt(0.0, with_alpha(accent, 66))
        wash.setColorAt(1.0, with_alpha(accent, 0))
        p.setBrush(wash)
        p.drawRect(card)
        agent_pm = None
        for idx in range(self.VIEWS):
            d = ((idx - vp + 1.5) % self.VIEWS) - 1.5        # shortest signed distance on the loop
            amount = clamp01(1.0 - abs(d))
            if amount <= 0.01:
                continue
            p.save()
            p.setOpacity(rv * amount)
            p.translate(0, d * 26.0)
            if idx == 0:
                dpr = self.devicePixelRatioF()
                agent_pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
                agent_pm.setDevicePixelRatio(dpr)
                agent_pm.fill(Qt.transparent)
                self._paint_agent_card(agent_pm)
                p.drawPixmap(QPointF(0, 0), agent_pm)
            elif idx == 1:
                self._paint_focus_view(p, w, h)
            else:
                self._paint_bt_view(p, w, h)
            p.restore()
        p.restore()
        rim = QLinearGradient(card.topLeft(), card.bottomRight())
        rim.setColorAt(0.0, QColor(255, 255, 255, 78 + int(40 * self._hover)))
        rim.setColorAt(1.0, with_alpha(accent, 40 + int(40 * self._hover)))
        p.setOpacity(rv)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(rim), 1.0))
        p.drawRoundedRect(card, 18, 18)
        p.end()

    def _focus_accent(self) -> QColor:
        f = self._focus
        if f is not None and f.active and f.paused:
            return QColor(255, 178, 92)
        return pastel(self._c2, 0.78)

    def _eyebrow(self, p, w, text: str, accent: QColor, live: bool):
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.4))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(13, 8, w - 40, 11), Qt.AlignLeft | Qt.AlignVCenter, text)
        beat = 0.5 + 0.5 * math.sin(self._t * 4.0) if live else 0.0
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(accent, int(46 + 60 * beat)))
        p.drawEllipse(QPointF(w - 15, 13.5), 5.5 + 1.5 * beat, 5.5 + 1.5 * beat)
        p.setBrush(with_alpha(accent, 240))
        p.drawEllipse(QPointF(w - 15, 13.5), 2.8, 2.8)

    def _paint_focus_view(self, p, w, h):
        f = self._focus
        accent = self._focus_accent()
        active = bool(f is not None and f.active)
        paused = bool(active and f.paused)
        self._eyebrow(p, w, "FOCUS", accent, active and not paused)
        ring = QRectF(w / 2.0 - 22, 24, 44, 44)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 40), 4.0))
        p.drawEllipse(ring)
        frac = clamp01(f.remaining / max(1.0, f.total)) if f is not None else 1.0
        p.setPen(QPen(accent, 4.0, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(ring, 90 * 16, int(-360 * 16 * max(0.003, frac)))
        left = f.remaining if f is not None else 1500.0
        p.setFont(ui_font(11, QFont.Weight.Bold, True))
        p.setPen(QColor(255, 255, 255, 245))
        p.drawText(ring, Qt.AlignCenter, focus_clock(left))
        p.setFont(ui_font(11, QFont.Weight.Bold, True, 0.1))
        title = "Paused" if paused else ("Focusing" if active else "Start focus")
        p.drawText(QRectF(10, 70, w - 20, 16), Qt.AlignCenter, title)
        p.setFont(ui_font(8, QFont.Weight.Medium, False, 0.5))
        p.setPen(QColor(255, 255, 255, 150))
        sub = (f"{f.preset} min  ·  {f.done} done today" if f is not None else "")
        p.drawText(QRectF(10, 85, w - 20, 12), Qt.AlignCenter, sub)

    def _paint_bt_view(self, p, w, h):
        accent = self._focus_accent()                    # follows the island theme
        devs = self._devices
        n = len(devs)
        self._eyebrow(p, w, "BLUETOOTH" + (f"  ·  {n}" if n > 1 else ""), accent, n > 0)
        if not devs:
            disc = QRectF(w / 2.0 - 19, 27, 38, 38)
            p.setPen(QPen(QColor(255, 255, 255, 46), 1))
            p.setBrush(QColor(255, 255, 255, 22))
            p.drawEllipse(disc)
            create_icon_pixmap("bluetooth", 18, QColor(255, 255, 255, 220)).paint(
                p, QRect(int(w / 2) - 9, 37, 18, 18))
            p.setFont(ui_font(11, QFont.Weight.Bold, True, 0.1))
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(QRectF(10, 70, w - 20, 16), Qt.AlignCenter, "No device")
            p.setFont(ui_font(8, QFont.Weight.Medium, False, 0.5))
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(10, 85, w - 20, 12), Qt.AlignCenter, "Click for settings")
            return
        d = devs[0]
        disc = QRectF(13, 26, 32, 32)
        p.setPen(QPen(with_alpha(accent, 90), 1.2))
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawEllipse(disc)
        paint_device_art(p, disc.adjusted(6.5, 6.5, -6.5, -6.5), device_icon_kind(d.dev_type))

        p.setFont(ui_font(11, QFont.Weight.Bold, True, 0.1))
        p.setPen(QColor(255, 255, 255, 245))
        name = p.fontMetrics().elidedText(d.name or d.product or "Device", Qt.ElideRight, w - 66)
        p.drawText(QRectF(53, 25, w - 60, 16), Qt.AlignLeft | Qt.AlignVCenter, name)

        # battery: slim glyph in the theme accent, percentage beside it
        pct = d.battery
        bx, by = 54.0, 46.0
        low = pct is not None and pct <= 20
        fill = QColor(255, 178, 92) if low else accent
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.0))
        p.drawRoundedRect(QRectF(bx, by, 20, 9), 2.6, 2.6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 110))
        p.drawRoundedRect(QRectF(bx + 20.8, by + 2.6, 1.6, 3.8), 0.8, 0.8)
        if pct is not None:
            p.setBrush(with_alpha(fill, 235))
            p.drawRoundedRect(QRectF(bx + 1.6, by + 1.6, 16.8 * clamp01(pct / 100.0), 5.8),
                              1.6, 1.6)
        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 0.2))
        p.setPen(QColor(255, 255, 255, 215))
        p.drawText(QRectF(bx + 28, by - 4, 44, 17), Qt.AlignLeft | Qt.AlignVCenter,
                   "—" if pct is None else f"{int(pct)}%")

        # disconnect: quiet glass pill, no alarm colours
        btn = self._bt_btn()
        hot = 1.0 if self._bt_hot else 0.0
        g = QLinearGradient(btn.topLeft(), btn.bottomLeft())
        g.setColorAt(0.0, QColor(255, 255, 255, int(34 + 30 * hot)))
        g.setColorAt(1.0, QColor(255, 255, 255, int(14 + 22 * hot)))
        p.setPen(QPen(QColor(255, 255, 255, int(56 + 60 * hot)), 1))
        p.setBrush(g)
        p.drawRoundedRect(btn, btn.height() / 2, btn.height() / 2)
        p.setFont(ui_font(8, QFont.Weight.Bold, False, 1.2))
        p.setPen(QColor(255, 255, 255, int(200 + 45 * hot)))
        p.drawText(btn, Qt.AlignCenter, "DISCONNECT")

    def _paint_agent_card(self, target):
        rv = self._rv
        if rv <= 0.004:
            return
        p = QPainter(target)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        st, mood, t = self._state, self.mood(), self._t
        accent = self._accent()
        a_card, a_eyes, a_text = seg(rv, 0.0, 0.5), seg(rv, 0.1, 0.55), seg(rv, 0.2, 0.55)
        p.setOpacity(rv)
        if self._down:
            p.translate(w / 2.0, h / 2.0)
            p.scale(0.985, 0.985)
            p.translate(-w / 2.0, -h / 2.0)

        card = QRectF(0.5, 0.5, w - 1, h - 1)
        body = QLinearGradient(0, 0, 0, h)
        body.setColorAt(0.0, QColor(255, 255, 255, 34 + int(18 * self._hover)))
        body.setColorAt(1.0, QColor(255, 255, 255, 12 + int(10 * self._hover)))
        p.setBrush(body)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(card, 18, 18)
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(card, 18, 18)
        p.setClipPath(clip)
        wash = QRadialGradient(QPointF(w * 0.5, 36), w * 0.95)
        wash.setColorAt(0.0, with_alpha(accent, int((86 if mood == "focus" else 62) * a_card)))
        wash.setColorAt(1.0, with_alpha(accent, 0))
        p.setBrush(wash)
        p.drawRect(card)
        p.restore()
        rim = QLinearGradient(card.topLeft(), card.bottomRight())
        rim.setColorAt(0.0, QColor(255, 255, 255, 78 + int(40 * self._hover)))
        rim.setColorAt(1.0, with_alpha(accent, 40 + int(40 * self._hover)))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(rim), 1.0))
        p.drawRoundedRect(card, 18, 18)

        # eyebrow + live dot
        kind = str(st.get("kind") or "AGENT")
        p.setOpacity(rv * a_text)
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.4))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(13, 8, w - 40, 11), Qt.AlignLeft | Qt.AlignVCenter, kind.upper())
        beat = 0.5 + 0.5 * math.sin(t * 4.0) if st.get("busy") else 0.0
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(accent, int(46 + 60 * beat)))
        p.drawEllipse(QPointF(w - 15, 13.5), 5.5 + 1.5 * beat, 5.5 + 1.5 * beat)
        p.setBrush(with_alpha(accent, 240 if st.get("kind") else 110))
        p.drawEllipse(QPointF(w - 15, 13.5), 2.8, 2.8)

        # character
        p.setOpacity(rv * a_eyes)
        self._paint_eyes(p, w / 2.0, 41.0 + (1.0 - a_eyes) * 6.0, accent, mood)

        # text
        p.setOpacity(rv * a_text)
        e_ = ease_out_cubic(self._text_t)
        p.setFont(ui_font(11, QFont.Weight.Bold, True, 0.1))
        fm = p.fontMetrics()
        for text, alpha, dy in ((self._text_old, 1.0 - e_, -5.0 * e_),
                                (self._text, e_, 5.0 * (1.0 - e_))):
            if alpha > 0.01 and text:
                p.setOpacity(rv * a_text * alpha)
                p.setPen(QColor(255, 255, 255, 242))
                p.drawText(QRectF(10, 63 + dy, w - 20, 16), Qt.AlignCenter,
                           fm.elidedText(text, Qt.ElideRight, w - 22))
        p.setOpacity(rv * a_text)
        p.setFont(ui_font(8, QFont.Weight.Medium, False, 0.5))
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(10, 79, w - 20, 12), Qt.AlignCenter, self._sub())

        # phase bar
        gap, bx = 4.0, 14.0
        sw = (w - 2 * bx - gap * 3) / 4.0
        for i in range(4):
            x = bx + i * (sw + gap)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 38))
            p.drawRoundedRect(QRectF(x, 94, sw, 3), 1.5, 1.5)
            fill = clamp01(self._seg[i])
            if fill > 0.004:
                p.setBrush(QColor(255, 246, 238, 236))
                p.drawRoundedRect(QRectF(x, 94, max(3.0, sw * fill), 3), 1.5, 1.5)

    def _paint_eyes(self, p, cx: float, cy: float, accent: QColor, mood: str):
        t = self._t
        hs, smile, sweep, bob, asym = self._par
        cyc = t % 4.6
        blink = math.sin(math.pi * cyc / 0.18) if (cyc < 0.18 and mood not in ("sleep", "happy")) else 0.0
        gx = sweep * math.sin(t * (1.5 if mood == "focus" else 0.6)) + self._look.x() * self._hover
        gy = self._look.y() * self._hover
        by = -bob * abs(math.sin(t * (3.2 if mood == "curious" else 2.6)))
        glow = QRadialGradient(QPointF(cx, cy), 34)
        glow.setColorAt(0.0, with_alpha(accent, 84 if mood in ("focus", "curious", "happy") else 36))
        glow.setColorAt(1.0, with_alpha(accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, cy), 34, 34)
        for sign in (-1, 1):
            grow = 1.0 + (asym if sign > 0 else -asym * 0.4)
            eh = max(3.5, 27.0 * hs * grow * (1.0 - 0.88 * blink))
            ew = 18.0
            r = QRectF(cx + sign * 14 - ew / 2 + gx, cy + by + gy - eh / 2, ew, eh)
            if smile < 0.98:
                p.save()
                p.setOpacity(p.opacity() * (1.0 - smile))
                g = QLinearGradient(0, r.top(), 0, r.bottom())
                g.setColorAt(0.0, QColor(255, 238, 226, 242))
                g.setColorAt(1.0, QColor(255, 214, 190, 200))
                p.setBrush(g)
                p.setPen(QPen(QColor(255, 255, 255, 70), 1.0))
                p.drawRoundedRect(r, ew / 2, min(ew / 2, eh / 2))
                p.restore()
            if smile > 0.02:                                     # happy ^ ^ eyes
                p.save()
                p.setOpacity(p.opacity() * smile)
                path = QPainterPath()
                mx, my = cx + sign * 14 + gx, cy + by + gy + 3.0
                path.moveTo(mx - 7.5, my)
                path.quadTo(mx, my - 12.0, mx + 7.5, my)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 238, 226, 250), 4.2, Qt.SolidLine, Qt.RoundCap))
                p.drawPath(path)
                p.restore()
        if mood == "curious":                                    # the question bubble
            s = 1.0 + 0.12 * math.sin(t * 5.0)
            p.save()
            p.translate(cx + 30, cy - 22)
            p.scale(s, s)
            p.setBrush(with_alpha(accent, 235))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(0, 0), 8, 8)
            p.setFont(ui_font(11, QFont.Weight.Bold, True))
            p.setPen(QColor(40, 26, 16))
            p.drawText(QRectF(-8, -8, 16, 16), Qt.AlignCenter, "?")
            p.restore()
        elif mood == "happy":                                    # little celebration sparkles
            for i, (dx, dy, ph) in enumerate(((30, -22, 0.0), (-31, -18, 1.7), (34, 4, 3.1))):
                s = 0.5 + 0.5 * math.sin(t * 3.0 + ph)
                if s < 0.05:
                    continue
                r0 = 2.2 + 2.4 * s
                p.setPen(QPen(with_alpha(accent, int(70 + 170 * s)), 1.4, Qt.SolidLine, Qt.RoundCap))
                x, y = cx + dx, cy + dy
                p.drawLine(QPointF(x - r0, y), QPointF(x + r0, y))
                p.drawLine(QPointF(x, y - r0), QPointF(x, y + r0))


class DetailPage(QWidget):
    """The expanded view of one island section, as three borderless columns.

    media  · player · synced lyrics · track info
    info   · date · weather · at a glance
    clock  · local time · power · system
    """

    command = Signal(object)

    def __init__(self):
        super().__init__()
        self.setFixedSize(W, H - DETAIL_TOP)
        self.setMouseTracking(True)
        self._kind = "media"
        self._c1, self._c2 = QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2)
        self._media = {"title": "Nothing playing", "artist": "", "album": "",
                       "source": "", "playing": False, "pos": 0.0, "duration": 0.0}
        self._lyrics = {"status": "idle", "lines": [], "plain": "", "source": ""}
        self._art = QPixmap()
        self._weather = None
        self._battery = None
        self._muted = False
        self._stats: dict = {}
        self._now = dt.datetime.now()
        self._hits: dict[str, QRectF] = {}
        self._hover = ""
        self._t = 0.0
        self._rv = 0.0
        self._icons: dict = {}
        self._lyric_to = 0
        self._scroll = 0.0
        self._open_t = 9.0
        self._lyric_hits: list[tuple[QRectF, int]] = []
        self._lyric_hover = -1
        self._pb_pos = 0.0
        self._pb_t0 = time.monotonic()
        self._pb_playing = False
        self._t_prev = time.monotonic()

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_show)

        self._pulse = QTimer(self)
        self._pulse.setInterval(16)
        self._pulse.timeout.connect(self._tick)

        self._build_media_strip()

    # ------------------------------------------------------------------ api #
    def start(self):
        self._t = 0.0
        self._t_prev = time.monotonic()
        self._open_t = 0.0
        self._apply_strip_reveal()
        if self._kind == "media":
            for lbl in (self._m_title, self._m_artist):
                lbl.replay()
        self._anim.stop()
        self._anim.start()

    def set_kind(self, kind: str):
        self._kind = kind if kind in ("media", "info", "clock") else "media"
        self._show_media_strip(self._kind == "media")
        self._hits = {}
        self._lyric_hits = []
        self._lyric_hover = -1
        self._pulse.setInterval(16 if self._kind == "media" else 33)
        self.update()

    def set_tints(self, c1: QColor, c2: QColor):
        a, b = pastel(QColor(c2), 0.8), pastel(QColor(c1), 0.8)
        if a != self._c1 or b != self._c2:
            self._c1, self._c2 = a, b
            self._m_slider.set_tints(c1, c2)
            self._m_eq.set_tints(c1, c2)
            self._m_art.set_glow(pastel(QColor(c1), 0.9, 0.95))
            self.update()

    def set_media(self, title: str, artist: str, album: str, source: str,
                  playing: bool, duration: float):
        before = (self._media["title"], self._media["artist"])
        self._media.update(title=title or "Nothing playing", artist=artist or "",
                           album=album or "", source=source or "",
                           playing=bool(playing), duration=float(duration or 0.0))
        if (title or "Nothing playing", artist or "") != before:
            self._pb_pos, self._pb_t0 = 0.0, time.monotonic()
            self._pb_playing = bool(playing)
        has = bool(title) and title != "Nothing playing"
        live = bool(playing) and has
        self._m_title.set_full_text(title or "Nothing playing")
        self._m_album.set_full_text(album or artist or "")
        self._m_artist.set_full_text((artist or "").upper() if album else "")
        self._m_play.set_kind("pause" if live else "play")
        self._m_slider.setEnabled(float(duration or 0.0) > 0)
        for w in (self._m_eq, self._m_slider, self._m_art):
            w.set_playing(live)
        self._m_title.set_shine(live)
        self._m_art.set_badge(source_style(source) or self._c1.lighter(160),
                              not has, source or "")
        self.update()

    def set_playback(self, position: float, duration: float, playing: bool):
        target = float(position or 0.0)
        live = self._position()
        if self._pb_playing and playing and 0.0 < live - target < 1.0:
            target = live          # a slightly stale report must not rewind the clock
        self._pb_pos, self._pb_t0 = target, time.monotonic()
        self._pb_playing = bool(playing)
        self._media["pos"] = target
        if duration:
            self._media["duration"] = float(duration)
        self._media["playing"] = bool(playing)
        span = float(self._media.get("duration") or 0.0)
        if not self._m_slider.isSliderDown():
            self._m_slider.setSliderPosition(
                int(round(clamp01(target / span) * 1000)) if span > 0 else 0)
            self._m_cur.set_full_text(self._fmt(target))
        self._m_tot.set_full_text(self._fmt(span) if span > 0 else "—:—")
        self._sync_lyric_index()
        self.update()

    def _position(self) -> float:
        """Playback time, extrapolated from the last report so every frame moves."""
        pos = self._pb_pos + ((time.monotonic() - self._pb_t0) if self._pb_playing else 0.0)
        duration = float(self._media.get("duration") or 0.0)
        if duration > 0:
            pos = min(pos, duration)
        return max(0.0, pos)

    def _sync_lyric_index(self):
        lines = self._lyrics.get("lines") or []
        if self._lyrics.get("status") != "ok" or not lines:
            return
        index = max(0, min(len(lines) - 1, lyrics_line_index(lines, self._position())))
        if index == self._lyric_to:
            return
        if index < self._lyric_to and self._pb_playing:
            # a report that lands a hair behind must not bounce the highlight back
            if float(lines[self._lyric_to][0]) - float(lines[index][0]) < 1.0:
                return
        self._lyric_to = index

    def _seek_lyric(self, index: int):
        """Jump to a pressed line; the column glides there on its own clock."""
        lines = self._lyrics.get("lines") or []
        if not (0 <= index < len(lines)):
            return
        target = max(0.0, float(lines[index][0]))
        self._pb_pos, self._pb_t0 = target, time.monotonic()
        self._media["pos"] = target
        self._lyric_to = index
        self.update()
        self.command.emit(("seek_at", target))

    def set_art(self, pixmap: QPixmap):
        self._art = QPixmap(pixmap) if pixmap is not None else QPixmap()
        self._m_art.set_art(self._art)
        self.update()

    def set_lyrics(self, payload: dict):
        if not isinstance(payload, dict):
            return
        lines = []
        for item in payload.get("lines") or []:
            try:
                lines.append((float(item[0]), str(item[1])))
            except Exception:
                continue
        self._lyrics = {"status": str(payload.get("status") or "none"),
                        "lines": lines, "plain": str(payload.get("plain") or ""),
                        "source": str(payload.get("source") or "")}
        self._lyric_to = 0
        self._scroll = 0.0
        self._lyric_hover = -1
        self._sync_lyric_index()
        self.update()

    def set_lyrics_loading(self):
        self._lyrics = {"status": "loading", "lines": [], "plain": "", "source": ""}
        self.update()

    def set_muted(self, muted: bool):
        muted = bool(muted)
        if muted != self._muted:
            self._muted = muted
            self._m_eq.set_muted(muted)
            self.update()

    def set_panel(self, now, weather, battery, stats: dict):
        self._now = now or dt.datetime.now()
        self._weather = weather
        self._battery = battery
        self._stats = dict(stats or {})
        self.update()

    # --------------------------------------------------------------- motion #
    def showEvent(self, e):
        self._t_prev = time.monotonic()
        self._pulse.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._pulse.stop()
        super().hideEvent(e)

    def _tick(self):
        now = time.monotonic()
        dt = min(0.25, max(0.0, now - self._t_prev))
        self._t += dt
        self._t_prev = now
        self._open_t += dt
        if self._kind == "media":
            self._advance_lyric_scroll(dt)
            self._tick_media_strip(dt)
        self._sync_lyric_index()
        self.update()

    def _on_show(self, v):
        self._rv = float(v)
        self.update()

    # --------------------------------------------------------------- events #
    def _hit_at(self, pos):
        for name, rect in self._hits.items():
            if rect.contains(pos):
                return name
        return ""

    def _lyric_at(self, pos) -> int:
        for rect, index in self._lyric_hits:
            if rect.contains(pos):
                return index
        return -1

    def mouseMoveEvent(self, e):
        pos = e.position()
        hot = self._lyric_at(pos)
        if hot != self._lyric_hover:
            self._lyric_hover = hot
            self.update()
        hit = self._hit_at(pos)
        if hot >= 0:
            hit = "lyric"
        if hit != self._hover:
            self._hover = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover = ""
        self._lyric_hover = -1
        self.setCursor(Qt.ArrowCursor)
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position()
        index = self._lyric_at(pos)
        if index >= 0:
            self._seek_lyric(index)
            return
        seek = self._hits.get("seek")
        if seek is not None and seek.contains(pos):
            frac = clamp01((pos.x() - seek.left()) / max(1.0, seek.width()))
            self.command.emit(("seek", frac))
            return
        hit = self._hit_at(pos)
        if hit:
            self.command.emit((hit, None))

    # -------------------------------------------------------------- helpers #
    def _accent(self) -> QColor:
        return pastel(self._c2, 0.85, 1.0)

    def _elide(self, p, text, width: float) -> str:
        return p.fontMetrics().elidedText(str(text), Qt.ElideRight, int(max(8, width)))

    def _text(self, p, rect, flags, text, color: QColor):
        p.setPen(QColor(0, 0, 0, 105))
        p.drawText(rect.translated(0, 1), flags, text)
        p.setPen(color)
        p.drawText(rect, flags, text)

    def _hdr(self, p, card: QRectF, text: str):
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.8))
        p.setPen(QColor(255, 255, 255, 142))
        p.drawText(QRectF(card.left(), card.top() + 7, card.width(), 10),
                   Qt.AlignLeft | Qt.AlignVCenter, text)

    def _icon(self, p, kind: str, rect: QRectF, color: QColor | None = None, px: int = 14):
        key = (kind, px)
        icon = self._icons.get(key)
        if icon is None:
            icon = self._icons[key] = create_icon_pixmap(
                kind, px, color or QColor(255, 255, 255, 238))
        icon.paint(p, QRect(int(round(rect.x())), int(round(rect.y())),
                            int(round(rect.width())), int(round(rect.height()))))

    def _chip(self, p, rect: QRectF, label: str, name: str, accent: bool = False):
        hot = self._hover == name
        col = self._accent() if accent else QColor(255, 255, 255)
        p.setBrush(QColor(255, 255, 255, 46 if hot else 24))
        p.setPen(QPen(with_alpha(col, 130 if accent else (92 if hot else 58)), 1.0))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.3))
        p.setPen(with_alpha(col, 238 if hot or accent else 196))
        p.drawText(rect, Qt.AlignCenter, label)

    @staticmethod
    def _source_label(source: str) -> str:
        raw = (source or "").strip()
        low = raw.lower()
        for key, label in (("spotify", "Spotify"), ("zune", "Groove Music"),
                           ("msedge", "Edge"), ("chrome", "Chrome"),
                           ("firefox", "Firefox"), ("tidal", "TIDAL"),
                           ("deezer", "Deezer"), ("vlc", "VLC"),
                           ("musicbee", "MusicBee"), ("foobar", "foobar2000")):
            if key in low:
                return label
        if "!" in raw:
            return raw.rsplit("!", 1)[-1] or "Media"
        base = raw.rsplit(".", 1)[0]
        return base or "Media"

    def _fmt(self, seconds: float) -> str:
        s = int(max(0, seconds))
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _row(self, p, card: QRectF, y: float, key: str, value: str, wide: bool = True):
        p.setFont(ui_font(7, QFont.Weight.DemiBold, False, 1.15))
        p.setPen(QColor(255, 255, 255, 116))
        p.drawText(QRectF(card.left(), y, 76, 11), Qt.AlignLeft | Qt.AlignVCenter, key)
        p.setFont(ui_font(9, QFont.Weight.DemiBold))
        va = (Qt.AlignRight if wide else Qt.AlignLeft) | Qt.AlignVCenter
        rect = (QRectF(card.left() + 80, y, card.width() - 80, 12) if wide
                else QRectF(card.left(), y + 10, card.width(), 12))
        p.setPen(QColor(255, 255, 255, 226))
        p.drawText(rect, va, self._elide(p, value, rect.width()))

    def paintEvent(self, _e):
        if self._rv <= 0.004:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        self._hits = {}
        self._lyric_hits = []
        p.setOpacity(self._rv)
        if self._kind == "media":
            cols = (QRectF(18, 6, 226, 102), QRectF(250, 6, 374, 102))
            painters = (None, self._media_lyrics)
            dividers = (241.0,)
        else:
            cols = (QRectF(18, 6, 224, 102), QRectF(262, 6, 168, 102),
                    QRectF(450, 6, 174, 102))
            painters = ((self._info_date, self._info_weather, self._info_glance)
                        if self._kind == "info" else
                        (self._clock_time, self._clock_power, self._clock_system))
            dividers = (250.0, 438.0)
        for x in dividers:
            edge = QLinearGradient(x, 14, x, 102)
            edge.setColorAt(0.0, QColor(255, 255, 255, 0))
            edge.setColorAt(0.45, QColor(255, 255, 255, 34))
            edge.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setPen(QPen(QBrush(edge), 1.0))
            p.drawLine(QPointF(x, 14), QPointF(x, 102))
        for index, (col, paint) in enumerate(zip(cols, painters)):
            if paint is None:
                continue
            p.save()
            p.setOpacity(clamp01(self._rv * 1.35 - index * 0.12))
            paint(p, col)
            p.restore()

    # ---------------------------------------------------------------- media #
    def _build_media_strip(self):
        """The island's own player widgets, so this page animates identically."""
        mute = QColor(255, 255, 255, 170)
        self._m_art = ArtWidget()
        self._m_art.setParent(self)
        self._m_art.move(2, -1)
        self._m_art.clicked.connect(lambda: self.command.emit(("open", None)))

        self._m_title = GlassText(15, QFont.Weight.Bold, QColor(226, 230, 244),
                                  display=True, scramble=True, marquee=True, shine=True)
        self._m_album = GlassText(11, QFont.Weight.Medium, QColor(255, 255, 255, 208),
                                  marquee=True)
        self._m_artist = GlassText(9, QFont.Weight.DemiBold, QColor(255, 255, 255, 150),
                                   spacing=1.2, scramble=True, marquee=True)
        for lbl, (y, hh) in zip((self._m_title, self._m_album, self._m_artist),
                                ((10, 19), (29, 15), (44, 13))):
            lbl.setParent(self)
            lbl.setGeometry(TEXT_X, y, TEXT_W, hh)

        self._m_prev = GhostButton("prev", QSize(26, 26), 13)
        self._m_play = GhostButton("play", QSize(34, 26), 15, solid=True)
        self._m_next = GhostButton("next", QSize(26, 26), 13)
        for btn, x in ((self._m_prev, TEXT_X - 2), (self._m_play, TEXT_X + 25),
                       (self._m_next, TEXT_X + 60)):
            btn.setParent(self)
            btn.move(x, 56)
        self._m_prev.clicked.connect(lambda: self.command.emit(("prev", None)))
        self._m_play.clicked.connect(lambda: self.command.emit(("toggle", None)))
        self._m_next.clicked.connect(lambda: self.command.emit(("next", None)))

        self._m_eq = Equalizer()
        self._m_eq.setParent(self)
        self._m_eq.move(TEXT_X + TEXT_W - self._m_eq.width() + 3, 58)
        self._m_eq.clicked.connect(lambda: self.command.emit(("mute", None)))

        self._m_slider = SeekSlider()
        self._m_slider.setParent(self)
        self._m_slider.setGeometry(TEXT_X - ES_PAD, 84, TEXT_W + 2 * ES_PAD, 12)
        self._m_slider.setEnabled(False)
        self._m_slider.sliderMoved.connect(self._on_media_scrub)
        self._m_slider.sliderReleased.connect(self._on_media_release)

        self._m_cur = GlassText(9, QFont.Weight.Medium, mute, display=True, spacing=0.4)
        self._m_tot = GlassText(9, QFont.Weight.Medium, mute, display=True, spacing=0.4,
                                align=Qt.AlignRight)
        for lbl, x in ((self._m_cur, TEXT_X), (self._m_tot, TEXT_X + TEXT_W - 50)):
            lbl.setParent(self)
            lbl.setGeometry(x, 95, 50, 12)
        self._m_cur.set_full_text("0:00")
        self._m_tot.set_full_text("0:00")

        self._media_strip = (self._m_art, self._m_title, self._m_album, self._m_artist,
                             self._m_prev, self._m_play, self._m_next, self._m_eq,
                             self._m_slider, self._m_cur, self._m_tot)
        # the same stagger the island home page uses when it opens
        self._media_reveal = ((0.00, 0.55), (0.06, 0.45), (0.10, 0.45), (0.14, 0.45),
                              (0.16, 0.40), (0.20, 0.40), (0.24, 0.40), (0.26, 0.40),
                              (0.28, 0.45), (0.30, 0.40), (0.30, 0.40))
        self._show_media_strip(False)

    def _show_media_strip(self, on: bool):
        for w in self._media_strip:
            w.setVisible(on)
            w.set_reveal(0.0 if on else 1.0)
        self._m_art.set_interactive(on)
        if on:
            for w in self._media_strip:
                w.raise_()

    def _apply_strip_reveal(self):
        for w, (delay, dur) in zip(self._media_strip, self._media_reveal):
            w.set_reveal(seg(self._open_t, delay, dur))

    def _tick_media_strip(self, dt: float):
        if self._open_t < 1.6:
            self._apply_strip_reveal()
        self._m_art.tick(dt)
        self._m_title.tick(dt)
        self._m_album.tick(dt)
        self._m_artist.tick(dt)
        self._m_eq.tick(dt)
        self._m_slider.tick(dt)
        span = float(self._media.get("duration") or 0.0)
        pos = self._position()
        if not self._m_slider.isSliderDown():
            if span > 0:
                self._m_slider.setSliderPosition(int(round(clamp01(pos / span) * 1000)))
            self._m_cur.set_full_text(self._fmt(pos))
        self._m_tot.set_full_text(self._fmt(span) if span > 0 else "—:—")

    def _on_media_scrub(self, value: int):
        span = float(self._media.get("duration") or 0.0)
        self._m_cur.set_full_text(self._fmt(value / 1000.0 * span))

    def _on_media_release(self):
        span = float(self._media.get("duration") or 0.0)
        frac = clamp01(self._m_slider.value() / 1000.0)
        self._m_cur.set_full_text(self._fmt(frac * span))
        self.command.emit(("seek", frac))

    # --------------------------------------------------------------- lyrics #
    PITCH = 30.0

    def _advance_lyric_scroll(self, dt: float):
        """Glide the column toward the playing line, even across a long jump."""
        target = float(self._lyric_to)
        delta = target - self._scroll
        if abs(delta) < 0.0015:
            self._scroll = target
            return
        rate = 5.0 if abs(delta) > 2.0 else 7.5
        self._scroll += delta * (1.0 - math.exp(-dt * rate))

    @staticmethod
    def _wrap(fm, text: str, room: float, rows: int = 2):
        """Break a lyric into at most `rows` lines so the hero line stays whole."""
        words = str(text).split()
        if len(words) < 2:
            return [str(text)]
        out, cur = [], words[0]
        for i, word in enumerate(words[1:], 1):
            trial = cur + " " + word
            if fm.horizontalAdvance(trial) <= room:
                cur = trial
                continue
            out.append(cur)
            if len(out) >= rows - 1:
                cur = " ".join(words[i:])
                break
            cur = word
        out.append(cur)
        return out[:rows]

    def _media_lyrics(self, p, b):
        accent = self._accent()
        p.save()
        opening = seg(self._open_t, 0.10, 0.5)
        p.setOpacity(p.opacity() * (0.18 + 0.82 * opening))
        p.translate(0, (1.0 - opening) * 7.0)
        self._hdr(p, b, "LYRICS")
        lines = self._lyrics.get("lines") or []
        status = self._lyrics.get("status")

        if status == "loading":
            p.setFont(ui_font(11, QFont.Weight.DemiBold, True, 0.2))
            p.setPen(QColor(255, 255, 255, 172))
            p.drawText(QRectF(b.left(), b.top() + 32, b.width(), 20),
                       Qt.AlignLeft | Qt.AlignVCenter, "Listening for lyrics")
            for i in range(3):
                alpha = int(80 + 120 * (0.5 + 0.5 * math.sin(self._t * 2.6 - i * 0.6)))
                p.setBrush(with_alpha(accent, alpha))
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(b.left() + 4 + i * 11, b.top() + 68), 2.6, 2.6)
            p.restore()
            return

        if lines:
            target = max(0, min(len(lines) - 1, self._lyric_to))
            p.setFont(ui_font(8, QFont.Weight.Medium, True, 0.6))
            p.setPen(QColor(255, 255, 255, 122))
            p.drawText(QRectF(b.right() - 56, b.top() + 6, 56, 10),
                       Qt.AlignRight | Qt.AlignVCenter,
                       self._fmt(max(0.0, float(lines[target][0]))))

            area = QRectF(b.left(), b.top() + 19, b.width(), 90)
            pitch = self.PITCH
            centre = self._scroll
            base = area.center().y() + 1.0
            room = b.width() - 26

            dpr = self.devicePixelRatioF()
            layer = QImage(max(1, int(area.width() * dpr)), max(1, int(area.height() * dpr)),
                           QImage.Format_ARGB32_Premultiplied)
            layer.setDevicePixelRatio(dpr)
            layer.fill(Qt.transparent)
            q = QPainter(layer)
            q.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            q.translate(-area.left(), -area.top())

            first = max(0, int(math.floor(centre)) - 3)
            last = min(len(lines), int(math.ceil(centre)) + 4)
            for i in range(first, last):
                d = i - centre
                dist = abs(d)
                if dist > 2.7:
                    continue
                glow = clamp01(1.0 - dist)
                size = 10.0 + 5.0 * glow
                alpha = 96.0 + 150.0 * glow
                hero = glow > 0.45
                weight = QFont.Weight.Bold if hero else QFont.Weight.Medium
                y = base + d * pitch
                line = str(lines[i][1])
                q.setFont(ui_font(int(round(size)), weight, True, 0.1))
                rows = self._wrap(q.fontMetrics(), line, room) if hero else [line]
                while (size > 8.6
                       and max(q.fontMetrics().horizontalAdvance(r) for r in rows) > room):
                    size -= 0.6
                    q.setFont(ui_font(max(8, int(round(size))), weight, True, 0.1))
                    rows = self._wrap(q.fontMetrics(), line, room) if hero else [line]
                rows = [r if q.fontMetrics().horizontalAdvance(r) <= room
                        else self._elide(q, r, room) for r in rows]
                lh = max(13.0, size * 1.32)
                top = y - (len(rows) - 1) * lh / 2.0
                if i == self._lyric_hover:
                    # the hover plate wraps every row of the line, so a 2-line lyric keeps its box
                    box_h = max(26.0, len(rows) * lh + 10.0)
                    q.setPen(Qt.NoPen)
                    q.setBrush(QColor(255, 255, 255, 18))
                    q.drawRoundedRect(QRectF(b.left() - 2, y - box_h / 2.0, b.width() - 4, box_h),
                                      9, 9)
                if glow > 0.05:
                    q.setPen(Qt.NoPen)
                    q.setBrush(with_alpha(accent, int(240 * glow)))
                    q.drawRoundedRect(QRectF(b.left(), y - 11.0 * glow - 1.0, 2.0,
                                             22.0 * glow + 2.0), 1.0, 1.0)
                for k, row in enumerate(rows):
                    rect = QRectF(b.left() + 12, top + k * lh - lh / 2, b.width() - 18, lh)
                    if alpha > 214:
                        q.setPen(QColor(0, 0, 0, 118))
                        q.drawText(rect.translated(0, 1.3), Qt.AlignLeft | Qt.AlignVCenter, row)
                    q.setPen(QColor(255, 255, 255, int(alpha)))
                    q.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, row)
                self._lyric_hits.append((QRectF(b.left(), y - pitch / 2, b.width(), pitch), i))
            q.end()

            fade = QLinearGradient(0, 0, 0, area.height())
            fade.setColorAt(0.0, QColor(0, 0, 0, 0))
            fade.setColorAt(0.18, QColor(0, 0, 0, 255))
            fade.setColorAt(0.80, QColor(0, 0, 0, 255))
            fade.setColorAt(1.0, QColor(0, 0, 0, 0))
            mask = QPainter(layer)
            mask.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            mask.fillRect(layer.rect(), fade)
            mask.end()

            p.drawImage(area.topLeft(), layer)
            p.restore()
            return

        if self._lyrics.get("plain"):
            p.setFont(ui_font(10, QFont.Weight.Medium, True, 0.1))
            p.setPen(QColor(255, 255, 255, 196))
            p.drawText(QRectF(b.left() + 12, b.top() + 24, b.width() - 18, 74),
                       Qt.AlignLeft | Qt.TextWordWrap, self._lyrics["plain"][:260])
            p.restore()
            return

        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawEllipse(QPointF(b.center().x(), b.top() + 40), 18, 18)
        self._icon(p, "lyrics", QRectF(b.center().x() - 9, b.top() + 31, 18, 18),
                   QColor(255, 255, 255, 140), px=18)
        p.setFont(ui_font(11, QFont.Weight.DemiBold, True, 0.2))
        p.setPen(QColor(255, 255, 255, 208))
        p.drawText(QRectF(b.left(), b.top() + 66, b.width(), 15), Qt.AlignCenter,
                   "No lyrics found")
        p.setFont(ui_font(7, QFont.Weight.Bold, True, 1.3))
        p.setPen(QColor(255, 255, 255, 110))
        p.drawText(QRectF(b.left(), b.top() + 84, b.width(), 10), Qt.AlignCenter,
                   "LRCLIB  ·  SYNCED WHEN AVAILABLE")
        p.restore()

    # ----------------------------------------------------------------- info #
    def _info_date(self, p, a):
        accent = self._accent()
        now = self._now
        day = now.date()
        leap = day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0)
        days_in_year = 366 if leap else 365
        doy = day.timetuple().tm_yday
        month_end = (dt.date(day.year + (day.month == 12), day.month % 12 + 1, 1)
                     - dt.timedelta(days=1)).day
        week = day.isocalendar().week

        self._hdr(p, a, "TODAY")
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.4))
        p.setPen(QColor(255, 255, 255, 130))
        p.drawText(QRectF(a.right() - 80, a.top() + 7, 80, 10),
                   Qt.AlignRight | Qt.AlignVCenter, f"WEEK {week}")
        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 0.5))
        p.setPen(QColor(255, 255, 255, 215))
        p.drawText(QRectF(a.left(), a.top() + 24, 120, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, day.strftime("%A").upper())
        p.setFont(ui_font(44, QFont.Weight.Bold, True))
        self._text(p, QRectF(a.left() - 2, a.top() + 34, 88, 50),
                   Qt.AlignLeft | Qt.AlignVCenter, str(day.day), QColor(255, 255, 255, 250))
        p.setFont(ui_font(12, QFont.Weight.DemiBold, True, 0.4))
        p.setPen(QColor(255, 255, 255, 226))
        p.drawText(QRectF(a.left() + 92, a.top() + 40, a.width() - 92, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, day.strftime("%B"))
        p.setFont(ui_font(9, QFont.Weight.Medium, False, 0.5))
        p.setPen(with_alpha(accent, 220))
        p.drawText(QRectF(a.left() + 92, a.top() + 56, a.width() - 92, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, str(day.year))
        p.setFont(ui_font(7, QFont.Weight.Medium, False, 0.4))
        p.setPen(QColor(255, 255, 255, 140))
        p.drawText(QRectF(a.left() + 92, a.top() + 70, a.width() - 92, 10),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{doy} of {days_in_year} days")

        frac = clamp01(day.day / max(1, month_end))
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.3))
        p.setPen(QColor(255, 255, 255, 132))
        p.drawText(QRectF(a.left(), a.top() + 86, 150, 10),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   f"MONTH  ·  {int(round(frac * 100))}%")
        bar = QRectF(a.left(), a.top() + 99, a.width(), 2.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawRoundedRect(bar, 1.2, 1.2)
        p.setBrush(with_alpha(accent, 220))
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(3.0, bar.width() * frac), 2.5),
                          1.2, 1.2)

    def _info_weather(self, p, b):
        self._hdr(p, b, "WEATHER")
        weather = self._weather
        if weather is None:
            p.setFont(ui_font(9, QFont.Weight.Medium))
            p.setPen(QColor(255, 255, 255, 150))
            p.drawText(QRectF(b.left(), b.top() + 32, b.width(), 44),
                       Qt.AlignLeft | Qt.TextWordWrap, "Waiting for the local forecast…")
            return
        kind, label = weather_visual(int(weather.code), bool(weather.is_day))
        paint_weather_icon(p, QRectF(b.left(), b.top() + 24, 38, 38), kind, self._accent())
        p.setFont(ui_font(30, QFont.Weight.Bold, True))
        self._text(p, QRectF(b.left() + 46, b.top() + 19, 110, 38),
                   Qt.AlignLeft | Qt.AlignVCenter, f"{round(weather.temp)}°",
                   QColor(255, 255, 255, 248))
        p.setFont(ui_font(9, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255, 214))
        p.drawText(QRectF(b.left(), b.top() + 68, b.width(), 12),
                   Qt.AlignLeft | Qt.AlignVCenter, label)
        p.setFont(ui_font(8, QFont.Weight.Medium))
        p.setPen(QColor(255, 255, 255, 148))
        p.drawText(QRectF(b.left(), b.top() + 84, b.width(), 11),
                   Qt.AlignLeft | Qt.AlignVCenter,
                   self._elide(p, weather.city or "Local", b.width()))

    def _info_glance(self, p, c):
        day = self._now.date()
        leap = day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0)
        days_in_year = 366 if leap else 365
        doy = day.timetuple().tm_yday
        month_end = (dt.date(day.year + (day.month == 12), day.month % 12 + 1, 1)
                     - dt.timedelta(days=1)).day
        battery = self._battery
        rows = (("WEEK", str(day.isocalendar().week)),
                ("DAY", f"{doy} / {days_in_year}"),
                ("LEFT THIS YEAR", f"{days_in_year - doy} d"),
                ("MONTH", f"{month_end - day.day} d left"),
                ("BATTERY", f"{battery[0]}%" if battery else "—"))
        self._hdr(p, c, "AT A GLANCE")
        for i, (key, value) in enumerate(rows):
            self._row(p, c, c.top() + 27 + i * 16, key, value)

    # ---------------------------------------------------------------- clock #
    def _clock_time(self, p, a):
        now = self._now
        self._hdr(p, a, "LOCAL TIME")
        p.setFont(ui_font(38, QFont.Weight.Bold, True))
        self._text(p, QRectF(a.left() - 2, a.top() + 16, 150, 44),
                   Qt.AlignLeft | Qt.AlignVCenter, now.strftime("%I:%M").lstrip("0"),
                   QColor(255, 255, 255, 250))
        p.setFont(ui_font(9, QFont.Weight.Bold, False, 1.2))
        p.setPen(with_alpha(self._accent(), 224))
        p.drawText(QRectF(a.left() + 108, a.top() + 34, 60, 12),
                   Qt.AlignLeft | Qt.AlignVCenter, now.strftime("%p"))
        p.setFont(ui_font(9, QFont.Weight.Medium, False, 0.2))
        p.setPen(QColor(255, 255, 255, 196))
        p.drawText(QRectF(a.left(), a.top() + 68, a.width(), 13),
                   Qt.AlignLeft | Qt.AlignVCenter, now.strftime("%A, %d %B %Y"))
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.2))
        p.setPen(QColor(255, 255, 255, 116))
        p.drawText(QRectF(a.left(), a.top() + 86, a.width(), 10),
                   Qt.AlignLeft | Qt.AlignVCenter, time.tzname[0].upper())

    def _clock_power(self, p, b):
        self._hdr(p, b, "POWER")
        battery = self._battery
        pct = battery[0] if battery else None
        charge = bool(battery and battery[1])
        cx, cy, radius = b.center().x(), b.top() + 58, 28
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 13, 22, 120))
        p.drawEllipse(QPointF(cx, cy), radius + 3, radius + 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 38), 3.0, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(QPointF(cx, cy), radius, radius)
        frac = clamp01((pct or 0) / 100)
        if frac > 0.002:
            tone = (QColor(255, 176, 92) if pct is not None and pct <= 20
                    else self._accent())
            p.setPen(QPen(tone, 3.0, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(cx - radius, cy - radius, radius * 2, radius * 2),
                      90 * 16, int(-360 * 16 * frac))
        p.setFont(ui_font(15, QFont.Weight.Bold, True))
        p.setPen(QColor(255, 255, 255, 244))
        p.drawText(QRectF(cx - 30, cy - 10, 60, 20), Qt.AlignCenter,
                   f"{pct}%" if pct is not None else "—")
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.2))
        p.setPen(QColor(255, 255, 255, 176) if pct is not None
                 else QColor(255, 255, 255, 120))
        p.drawText(QRectF(b.left(), b.top() + 92, b.width(), 10), Qt.AlignCenter,
                   ("PLUGGED IN" if charge else "ON BATTERY") if pct is not None
                   else "NO BATTERY")

    def _clock_system(self, p, c):
        stats = self._stats
        ram = (f"{stats['ram_used']:.1f} / {stats['ram_total']:.0f} GB"
               if "ram_used" in stats else "—")
        up = stats.get("uptime")
        if up is None:
            uptime = "—"
        else:
            days, rem = divmod(int(up), 86400)
            hours, rem = divmod(rem, 3600)
            uptime = f"{days}d {hours:02d}h" if days else f"{hours}h {rem // 60:02d}m"
        try:
            win = f"Win 11 · {sys.getwindowsversion().build}"
        except Exception:
            win = "Windows"
        rows = (("MEMORY", ram),
                ("UPTIME", uptime),
                ("DEVICE", os.environ.get("COMPUTERNAME") or "This PC"),
                ("BUILD", win),
                ("MEDIA", "Playing" if self._media["playing"] else "Idle"))
        self._hdr(p, c, "SYSTEM")
        for i, (key, value) in enumerate(rows):
            self._row(p, c, c.top() + 27 + i * 16, key, value)


def paint_device_art(p, rect: QRectF, kind: str):
    """Detailed vector art for a device type, drawn into a 24x24 box."""
    ink = QColor(255, 255, 255, 242)
    soft = QColor(255, 255, 255, 96)
    p.save()
    p.translate(rect.left(), rect.top())
    p.scale(rect.width() / 24.0, rect.height() / 24.0)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(ink, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))

    if kind == "earbuds":
        for sign in (-1, 1):
            cx = 12 + sign * 5.1
            p.setBrush(QColor(255, 255, 255, 20))
            p.drawEllipse(QPointF(cx, 8.0), 3.2, 3.2)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(cx - 1.65, 9.4, 3.3, 9.4), 1.65, 1.65)
            p.setPen(Qt.NoPen)                       # speaker mesh
            p.setBrush(QColor(255, 255, 255, 132))
            p.drawEllipse(QPointF(cx, 7.9), 1.15, 1.15)
            p.setPen(QPen(soft, 1.1, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(cx - 2.2, 5.8, 4.4, 4.4), 40 * 16, 96 * 16)
            p.setPen(QPen(ink, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    elif kind == "headphones":
        p.drawArc(QRectF(3.4, 3.4, 17.2, 17.6), 24 * 16, 132 * 16)
        for cx in (4.9, 19.1):
            p.setBrush(QColor(255, 255, 255, 26))
            p.drawRoundedRect(QRectF(cx - 2.9, 11.6, 5.8, 9.2), 2.7, 2.7)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(soft, 1.15, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.drawRoundedRect(QRectF(cx - 1.35, 13.2, 2.7, 6.0), 1.35, 1.35)
            p.setPen(QPen(ink, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    elif kind == "car":
        p.setPen(QPen(ink, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        path.moveTo(2.6, 15.2); path.lineTo(4.4, 9.0); path.lineTo(9.0, 6.4)
        path.lineTo(15.0, 6.4); path.lineTo(19.6, 9.0); path.lineTo(21.4, 15.2)
        p.drawPath(path)
        p.drawLine(QPointF(3.2, 15.2), QPointF(20.8, 15.2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 150))
        p.drawEllipse(QPointF(6.6, 18.0), 2.0, 2.0)
        p.drawEllipse(QPointF(17.4, 18.0), 2.0, 2.0)
    else:                                            # speaker
        p.setBrush(QColor(255, 255, 255, 22))
        p.drawRoundedRect(QRectF(6.0, 2.4, 12.0, 19.2), 4.4, 4.4)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(12, 14.6), 3.6, 3.6)
        p.setPen(QPen(soft, 1.15))
        p.drawEllipse(QPointF(12, 14.6), 1.45, 1.45)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 150))
        p.drawEllipse(QPointF(12, 7.1), 1.5, 1.5)
    p.restore()


class DeviceTile(QWidget, _Fader):
    """One connected Bluetooth audio device, as a live-feeling glass card."""

    RADIUS = 18
    disconnect_requested = Signal(object)

    def __init__(self, dev: BluetoothAudioDevice):
        super().__init__()
        self.dev = dev
        self.setMouseTracking(True)
        self._confirm_until = 0.0
        self._removing_until = 0.0
        self._power_icon = create_icon_pixmap("power", 10, QColor(255, 255, 255, 220))
        self._power_hot = create_icon_pixmap("power", 10, QColor(255, 255, 255, 255))

        self._appear = 0.0
        self._grow = QVariantAnimation(self)
        self._grow.setDuration(430)
        self._grow.setEasingCurve(QEasingCurve.OutCubic)
        self._grow.setStartValue(0.0)
        self._grow.setEndValue(1.0)
        self._grow.valueChanged.connect(self._on_grow)

        self._t = 0.0
        self._anim = QTimer(self)
        self._anim.setInterval(45)
        self._anim.timeout.connect(self._tick)

        self._spot = QPointF(-999.0, -999.0)
        self._c1 = pastel(QColor(DEFAULT_TINT), 0.85, 1.0)
        self._c2 = pastel(QColor(DEFAULT_TINT_2), 0.85, 1.0)
        self._init_fader()

    # ------------------------------------------------------------------ api #
    def play_in(self):
        self._grow.stop()
        self._grow.start()

    def set_device(self, dev: BluetoothAudioDevice):
        if dev != self.dev:
            self.dev = dev
            self.update()

    def set_tints(self, c1: QColor, c2: QColor):
        accent = pastel(QColor(c1), 0.85, 1.0)
        second = pastel(QColor(c2), 0.85, 1.0)
        if accent != self._c1 or second != self._c2:
            self._c1, self._c2 = accent, second
            self.update()

    def _tint(self, alpha: int = 255) -> QColor:
        c = self._c1
        return QColor(c.red(), c.green(), c.blue(), alpha)

    # --------------------------------------------------------------- events #
    def _on_grow(self, v):
        self._appear = float(v)
        self.update()

    def _tick(self):
        self._t += 0.045
        self.update()

    def showEvent(self, e):
        self._anim.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._anim.stop()
        super().hideEvent(e)

    def mouseMoveEvent(self, e):
        self._spot = QPointF(e.position())
        over = self._power_rect().adjusted(-3, -3, 3, 3).contains(self._spot)
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        self.update()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._power_rect().adjusted(
                -3, -3, 3, 3).contains(QPointF(e.position())):
            if time.monotonic() < self._removing_until:
                return
            if time.monotonic() < self._confirm_until:
                self._confirm_until = 0.0
                self._removing_until = time.monotonic() + 25.0
                self.setCursor(Qt.ArrowCursor)
                self.disconnect_requested.emit(self.dev)
            else:
                self._confirm_until = time.monotonic() + 3.0
            self.update()
            return
        super().mousePressEvent(e)

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        self._spot = QPointF(-999.0, -999.0)
        super().leaveEvent(e)

    # -------------------------------------------------------------- helpers #
    @staticmethod
    def _text(p, rect, flags, text, color):
        p.setPen(QColor(0, 0, 0, 105))
        p.drawText(rect.translated(0, 1), flags, text)
        p.setPen(color)
        p.drawText(rect, flags, text)

    def _paint_eq(self, p, x: float, y: float):
        """Breathing bars, so a connected device reads as live."""
        p.setPen(Qt.NoPen)
        for i in range(3):
            hgt = 3.0 + 5.6 * (0.5 + 0.5 * math.sin(self._t * 3.6 + i * 1.35))
            p.setBrush(self._tint(150 + 80 * (hgt / 8.6)))
            p.drawRoundedRect(QRectF(x + i * 4.1, y + 8.4 - hgt, 2.5, hgt), 1.25, 1.25)

    def _paint_battery(self, p, box: QRectF, pct):
        """Segmented meter in the theme tint, with a slow travelling shimmer."""
        cells = 10
        gap = 2.0
        cw = max(2.0, (box.width() - gap * (cells - 1)) / cells)
        filled = 0 if pct is None else int(round(max(0, min(100, int(pct))) / 100.0 * cells))
        p.setPen(Qt.NoPen)
        for i in range(cells):
            r = QRectF(box.left() + i * (cw + gap), box.top(), cw, box.height())
            if i < filled:
                wave = math.sin(self._t * 2.4 - i * 0.55)
                p.setBrush(self._tint(int(196 + 44 * wave)))
            else:
                p.setBrush(QColor(255, 255, 255, 26 + int(14 * self._hover)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

    def _power_rect(self) -> QRectF:
        """Where the disconnect control lives: a chip when wide, a glyph when narrow."""
        w = self.width()
        if w >= 260:
            return QRectF(w - 108, 74, 92, 20)
        return QRectF(w - 30, 72, 22, 22)

    def _paint_disconnect(self, p):
        rect = self._power_rect()
        now = time.monotonic()
        confirming = now < self._confirm_until
        removing = now < self._removing_until
        hot = (self._hover > 0.4 and not removing
               and rect.adjusted(-4, -4, 4, 4).contains(self._spot))
        amber = QColor(255, 176, 92, 235)
        if self.width() >= 260:
            p.setBrush(amber if (confirming or removing) else
                       QColor(255, 255, 255, 20 + int(18 * hot)))
            p.setPen(QPen(amber if (confirming or removing) else
                          QColor(255, 255, 255, 62 + int(46 * hot)), 1.0))
            p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            icon = self._power_hot if (hot or confirming) else self._power_icon
            icon.paint(p, QRect(int(rect.left()) + 9, int(rect.top()) + 5, 10, 10))
            p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.15))
            p.setPen(QColor(40, 26, 16) if (confirming or removing)
                     else QColor(255, 255, 255, 234))
            p.drawText(rect.adjusted(22, 0, -4, 0), Qt.AlignCenter,
                       "REMOVING…" if removing else
                       "CONFIRM?" if confirming else "DISCONNECT")
            return
        p.setBrush(amber if (confirming or removing) else
                   QColor(255, 255, 255, 20 + int(18 * hot)))
        p.setPen(QPen(amber if (confirming or removing) else
                      QColor(255, 255, 255, 68 + int(48 * hot)), 1.0))
        p.drawEllipse(rect)
        if confirming or removing:
            p.setFont(ui_font(11 if removing else 12, QFont.Weight.Bold, True))
            p.setPen(QColor(40, 26, 16))
            p.drawText(rect, Qt.AlignCenter, "…" if removing else "!")
        else:
            icon = self._power_hot if hot else self._power_icon
            icon.paint(p, QRect(int(rect.center().x()) - 5, int(rect.center().y()) - 5,
                                10, 10))

    def _paint_pill(self, p, rect: QRectF):
        p.setPen(QPen(self._tint(120), 1))
        p.setBrush(self._tint(34))
        p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.3))
        p.setPen(QColor(240, 245, 255, 235))
        p.drawText(rect, Qt.AlignCenter, "CONNECTED")

    def _disc(self, p, rect: QRectF, art: float):
        c = rect.center()
        glow = QRadialGradient(c, rect.width() * 0.92)
        glow.setColorAt(0.0, self._tint(64))
        glow.setColorAt(1.0, self._tint(0))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(c, rect.width() * 0.92, rect.width() * 0.92)

        body = QRadialGradient(c.x() - rect.width() * 0.22, rect.top() + rect.height() * 0.16,
                               rect.width() * 1.05)
        body.setColorAt(0.0, QColor(255, 255, 255, 78))
        body.setColorAt(1.0, QColor(255, 255, 255, 14))
        p.setBrush(body)
        p.setPen(QPen(QColor(255, 255, 255, 74 + int(30 * self._hover)), 1))
        p.drawEllipse(rect)

        pulse = 0.5 + 0.5 * math.sin(self._t * 2.0)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self._tint(int(96 + 74 * pulse)), 1.6, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect.adjusted(1.8, 1.8, -1.8, -1.8), 118 * 16, -128 * 16)

        paint_device_art(p, QRectF(c.x() - art / 2, c.y() - art / 2, art, art),
                         device_icon_kind(self.dev.dev_type))

    # ------------------------------------------------------------- painting #
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        e = ease_out_cubic(self._appear)
        if e <= 0.004:
            return

        w, h = self.width(), self.height()
        p.setOpacity(e)
        p.translate(0, (1.0 - e) * 9.0 - 2.0 * self._hover)
        card = QRectF(0.5, 0.5, w - 1, h - 1)

        body = QLinearGradient(0, card.top(), 0, card.bottom())
        body.setColorAt(0.0, QColor(255, 255, 255, 34 + int(16 * self._hover)))
        body.setColorAt(1.0, QColor(255, 255, 255, 12 + int(10 * self._hover)))
        p.setBrush(body)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(card, self.RADIUS, self.RADIUS)

        p.save()                                            # theme wash
        clip = QPainterPath()
        clip.addRoundedRect(card, self.RADIUS, self.RADIUS)
        p.setClipPath(clip)
        wash = QLinearGradient(card.topLeft(), card.bottomRight())
        wash.setColorAt(0.0, self._tint(52 + int(18 * self._hover)))
        wash.setColorAt(0.42, self._tint(10))
        wash.setColorAt(1.0, self._tint(0))
        p.setBrush(wash)
        p.drawRect(card)
        if self._hover > 0.01:                              # neutral cursor sheen
            g = QRadialGradient(self._spot, 86)
            g.setColorAt(0.0, QColor(255, 255, 255, int(38 * self._hover)))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(g)
            p.drawRect(card)
        p.restore()

        rim = QLinearGradient(card.topLeft(), card.bottomRight())
        rim.setColorAt(0.0, QColor(255, 255, 255, 78 + int(50 * self._hover)))
        rim.setColorAt(0.5, QColor(255, 255, 255, 26))
        rim.setColorAt(1.0, self._tint(30 + int(66 * self._hover)))
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QBrush(rim), 1.0))
        p.drawRoundedRect(card, self.RADIUS, self.RADIUS)

        hl = QLinearGradient(card.left(), 0.0, card.right(), 0.0)
        hl.setColorAt(0.0, QColor(255, 255, 255, 0))
        hl.setColorAt(0.5, QColor(255, 255, 255, 70))
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hl), 1.0))
        p.drawLine(QPointF(card.left() + self.RADIUS, card.top() + 1.5),
                   QPointF(card.right() - self.RADIUS, card.top() + 1.5))

        self._paint_body(p, w, h)

    def _paint_body(self, p, w, h):
        d = self.dev
        wide = w >= 260
        if wide:
            big = w >= 500
            size = 68 if big else 58
            art = size * 0.68
            self._disc(p, QRectF(16, (h - size) // 2, size, size), art)
            nx = 16 + size + (18 if big else 14)
            p.setFont(ui_font(17 if big else 15, QFont.Weight.Bold))
            name_rect = QRectF(nx, 20 if big else 21, w - nx - 150, 26 if big else 23)
        else:
            self._disc(p, QRectF(10, 13, 40, 40), 28)
            nx = 60
            p.setFont(ui_font(12, QFont.Weight.Bold))
            name_rect = QRectF(nx, 12, max(24, w - nx - 12), 19)

        name = p.fontMetrics().elidedText(d.name, Qt.ElideRight, int(name_rect.width()))
        self._text(p, name_rect, Qt.AlignLeft | Qt.AlignVCenter, name, QColor(255, 255, 255))

        type_y = (name_rect.bottom() + 4) if wide else 31
        p.setFont(ui_font(8, QFont.Weight.Bold, False, 1.2))
        self._text(p, QRectF(nx, type_y, w - nx - 16, 11), Qt.AlignLeft | Qt.AlignVCenter,
                   d.dev_type.upper(), QColor(255, 255, 255, 146))
        eq_x = nx + p.fontMetrics().horizontalAdvance(d.dev_type.upper()) + 9
        if eq_x + 13 <= w - 12:
            self._paint_eq(p, eq_x, type_y - 1)

        value = "—" if d.battery is None else f"{d.battery}%"
        if wide:
            bx = w - 150
            p.setFont(ui_font(30 if big else 26, QFont.Weight.Bold))
            self._text(p, QRectF(bx, 20, 136, 36), Qt.AlignRight | Qt.AlignVCenter, value,
                       QColor(255, 255, 255))
            self._paint_battery(p, QRectF(bx, 66, 136, 6), d.battery)
            if w >= 420:
                self._paint_pill(p, QRectF(nx, 62, 104, 18))
            self._paint_disconnect(p)
            return

        if w - nx - 52 >= 46:
            p.setFont(ui_font(7, QFont.Weight.Bold, False, 1.5))
            self._text(p, QRectF(nx, 48, w - nx - 52, 10), Qt.AlignLeft | Qt.AlignVCenter,
                       "BATTERY", QColor(255, 255, 255, 124))
        p.setFont(ui_font(12, QFont.Weight.Bold))
        self._text(p, QRectF(w - 56, 46, 45, 14), Qt.AlignRight | Qt.AlignVCenter, value,
                   QColor(255, 255, 255, 235))
        self._paint_battery(p, QRectF(12, 64, w - 24, 5), d.battery)
        self._paint_disconnect(p)


class BluetoothPage(QWidget):
    """Connected audio devices as a row of live glass cards."""

    disconnect_requested = Signal(object)
    GAP = 10
    TOP = 42
    CARD_H = 100
    SLOTS = 4                     # the widest row this panel can hold

    def __init__(self):
        super().__init__()
        self.setFixedSize(W, H)
        self.tiles: dict[str, DeviceTile] = {}
        self._scanned = False
        self._pulse = 0.0
        self._c1 = QColor(DEFAULT_TINT)
        self._c2 = QColor(DEFAULT_TINT_2)
        self._icon = create_icon_pixmap("bluetooth", 22, QColor(255, 255, 255, 230))
        self._glow = QTimer(self)
        self._glow.setInterval(33)
        self._glow.timeout.connect(self._tick)
        self.setCursor(Qt.PointingHandCursor)

    def _tick(self):
        self._pulse += 0.033
        self.update()

    def set_tints(self, c1: QColor, c2: QColor):
        self._c1, self._c2 = QColor(c1), QColor(c2)
        for tile in self.tiles.values():
            tile.set_tints(c1, c2)
        self.update()

    def showEvent(self, e):
        if not self.tiles:
            self._glow.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._glow.stop()
        super().hideEvent(e)

    def mousePressEvent(self, e):
        # Settings only make sense while nothing is connected; with cards on
        # screen a click is just a click.
        if e.button() == Qt.LeftButton and not self.tiles:
            try:
                subprocess.Popen(["cmd", "/c", "start", "ms-settings:bluetooth"])
            except Exception:
                pass
        super().mousePressEvent(e)

    def set_devices(self, devices: list[BluetoothAudioDevice]):
        self._scanned = True
        devices = devices[: self.SLOTS]
        ids = {d.id for d in devices}
        for gone in [k for k in self.tiles if k not in ids]:
            stale = self.tiles.pop(gone)
            stale.hide()
            stale.deleteLater()

        fresh = set()
        for d in devices:
            tile = self.tiles.get(d.id)
            if tile is None:
                tile = self.tiles[d.id] = DeviceTile(d)
                tile.setParent(self)
                tile.set_tints(self._c1, self._c2)
                tile.disconnect_requested.connect(self.disconnect_requested)
                fresh.add(d.id)
            else:
                tile.set_device(d)

        n = len(devices)
        self.setCursor(Qt.ArrowCursor if n else Qt.PointingHandCursor)
        if n:
            self._glow.stop()
            tw = (W - 24 - (n - 1) * self.GAP) // n
            total = n * tw + (n - 1) * self.GAP
            x0 = (W - total) // 2
            for i, d in enumerate(devices):
                t = self.tiles[d.id]
                t.setGeometry(x0 + i * (tw + self.GAP), self.TOP, tw, self.CARD_H)
                t.show()
                t.raise_()
                if d.id in fresh:
                    QTimer.singleShot(45 * i, lambda tile=t: self._play(tile))
        elif self.isVisible():
            self._glow.start()
        self.update()

    @staticmethod
    def _play(tile):
        try:
            tile.play_in()
        except RuntimeError:              # tile was replaced between frames
            pass

    def paintEvent(self, _e):
        if self.tiles:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        c = QPointF(W / 2, 62)
        breath = 0.5 + 0.5 * math.sin(self._pulse * 1.7)
        accent = pastel(self._c1, 0.9, 1.0)
        for radius, alpha in ((24.0, 14), (18.0, 24), (13.0, 40)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(accent.red(), accent.green(), accent.blue(),
                              int(alpha * (0.6 + 0.4 * breath))))
            r = radius + 1.6 * breath
            p.drawEllipse(c, r, r)
        g = QRadialGradient(c.x(), c.y() - 4, 30)
        g.setColorAt(0.0, QColor(accent.red(), accent.green(), accent.blue(), 96))
        g.setColorAt(1.0, QColor(accent.red(), accent.green(), accent.blue(), 0))
        p.setBrush(g)
        p.drawEllipse(c, 30, 30)
        self._icon.paint(p, QRect(int(c.x()) - 11, int(c.y()) - 11, 22, 22))

        p.setFont(ui_font(12, QFont.Weight.DemiBold))
        p.setPen(QColor(255, 255, 255, 230))
        p.drawText(QRectF(0, 90, W, 17), Qt.AlignCenter,
                   "No audio device connected" if self._scanned else "Looking for devices…")
        p.setFont(ui_font(9, QFont.Weight.Medium))
        p.setPen(QColor(255, 255, 255, 138))
        p.drawText(QRectF(0, 106, W, 13), Qt.AlignCenter,
                   "Turn on your headphones or speaker")

        pill = QRectF(W / 2 - 88, 122, 176, 18)
        p.setPen(QPen(QColor(255, 255, 255, 62), 1))
        p.setBrush(QColor(255, 255, 255, 20))
        p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        p.setFont(ui_font(8, QFont.Weight.DemiBold, False, 1.2))
        p.setPen(QColor(255, 255, 255, 205))
        p.drawText(pill, Qt.AlignCenter, "OPEN BLUETOOTH SETTINGS")

class _WindowPoint(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL


def window_rect_at(x: int, y: int, dpr: float = 1.0, own_hwnd: int = 0):
    """Logical rect of the top-most real window under a physical screen point.

    WindowFromPoint() always returns the full-screen selector overlay, so walk the
    z-ordered top-level windows instead and skip our own.
    """
    found: list[QRect] = []

    def cb(hwnd, _lparam):
        try:
            if own_hwnd and int(hwnd) == int(own_hwnd):
                return True
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or _is_cloaked(hwnd):
                return True
            ex = _user32.GetWindowLongPtrW(hwnd, -20)
            if ex & 0x20:                                   # WS_EX_TRANSPARENT (click-through overlays)
                return True
            if _window_class(hwnd) in _SHELL_CLASSES:
                return True
            if (ex & 0x80) and _user32.GetWindowTextLengthW(hwnd) <= 0:
                return True                                 # untitled tool windows (our island etc.)
            wrect = _window_rect(hwnd)
            if wrect is None or wrect.width() < 24 or wrect.height() < 24:
                return True
            if wrect.contains(int(x), int(y)):
                found.append(wrect)
                return False
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(cb), 0)
    if not found:
        return None
    r, scale = found[0], (dpr or 1.0)
    return QRect(int(round(r.x() / scale)), int(round(r.y() / scale)),
                 int(round(r.width() / scale)), int(round(r.height() / scale)))


# --------------------------------------------------------------------------- #
# macOS-style capture UI
# --------------------------------------------------------------------------- #
class CaptureTile(QAbstractButton, _Fader, _Reveal):
    def __init__(self, kind: str, title: str, subtitle: str):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(200, 58)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._icon = create_icon_pixmap(kind, 20)
        self._title, self._subtitle = title, subtitle
        self._font_title = ui_font(11, QFont.Weight.DemiBold, True, 0.2)
        self._font_sub = ui_font(8, QFont.Weight.Medium, False, 0.3)
        self._accent = QColor(255, 255, 255)
        self._init_fader()

    def set_tints(self, c1: QColor, _c2: QColor):
        self._accent = pastel(c1, 0.85, 1.0)
        self.update()

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        p.setOpacity(self._rv)
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        on = self.isChecked()
        fill = (16 if not on else 30) + int(self._hover * 12)
        p.setBrush(QColor(255, 255, 255, fill))
        p.setPen(QPen(QColor(255, 255, 255, 46 if not on else 104), 1.0))
        p.drawRoundedRect(r, 16, 16)

        if on:
            glow = QRadialGradient(QPointF(32, r.top() + 10), 70)
            glow.setColorAt(0.0, with_alpha(self._accent, 46))
            glow.setColorAt(1.0, with_alpha(self._accent, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawRoundedRect(r, 16, 16)

        self._icon.paint(p, QRect(14, 19, 20, 20))
        p.setFont(self._font_title)
        p.setPen(QColor(255, 255, 255, 242 if on else 220))
        p.drawText(QRectF(43, 11, r.width() - 62, 18), Qt.AlignLeft | Qt.AlignVCenter, self._title)
        p.setFont(self._font_sub)
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(QRectF(43, 29, r.width() - 62, 14), Qt.AlignLeft | Qt.AlignVCenter, self._subtitle)

        if on:
            p.setBrush(self._accent)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(r.right() - 16, r.top() + 16), 2.8, 2.8)


class ActionButton(QAbstractButton, _Fader, _Reveal):
    def __init__(self, kind: str, label: str, variant: str = "plain"):
        super().__init__()
        self.setFixedSize(180, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._icon = create_icon_pixmap(kind, 13)
        self._font = ui_font(10, QFont.Weight.DemiBold, True, 0.3)
        self._label, self._variant = label, variant
        self._init_fader()

    def set_icon(self, kind: str):
        icon = create_icon_pixmap(kind, 13)
        if icon.cacheKey() != self._icon.cacheKey():
            self._icon = icon
            self.update()

    def set_label(self, label: str):
        if label != self._label:
            self._label = label
            self.update()

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        p.setOpacity(self._rv)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._variant == "record":
            p.setBrush(QColor(255, 92, 92, 34 + int(self._hover * 24)))
            p.setPen(QPen(QColor(255, 92, 92, 108 + int(self._hover * 40)), 1.0))
            text = QColor(255, 238, 238, 250)
        elif self._variant == "primary":
            p.setBrush(QColor(255, 255, 255, 28 + int(self._hover * 26)))
            p.setPen(QPen(QColor(255, 255, 255, 74 + int(self._hover * 40)), 1.0))
            text = QColor(255, 255, 255, 250)
        else:
            p.setBrush(QColor(255, 255, 255, 12 + int(self._hover * 16)))
            p.setPen(QPen(QColor(255, 255, 255, 42 + int(self._hover * 30)), 1.0))
            text = QColor(255, 255, 255, 225)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        p.setFont(self._font)
        text_w = p.fontMetrics().horizontalAdvance(self._label)
        total = 13 + 6 + text_w
        x0 = r.center().x() - total / 2.0
        self._icon.paint(p, QRect(int(x0), int(r.center().y() - 6), 13, 13))
        p.setPen(text)
        p.drawText(QRectF(x0 + 19, r.top(), text_w + 2, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._label)


class CapturePage(QWidget):
    area_changed = Signal(str)
    shoot_clicked = Signal()
    record_clicked = Signal()
    options_clicked = Signal()

    TILE_Y, TILE_STEP = 38, 208
    BUTTON_Y = 116

    def __init__(self):
        super().__init__()
        self.setFixedSize(W, H)
        self._area = "screen"
        self._status = ""
        self._status_font = ui_font(8, QFont.Weight.DemiBold, False, 1.1)
        specs = (("screen", "Entire Screen", "Everything you see", "screen"),
                 ("window", "Window", "Click a window", "window"),
                 ("portion", "Portion", "Drag an area", "portion"))
        self.tiles: dict[str, CaptureTile] = {}
        for index, (key, title, sub, icon) in enumerate(specs):
            tile = CaptureTile(icon, title, sub)
            tile.setParent(self)
            tile.move(12 + index * self.TILE_STEP, self.TILE_Y)
            tile.clicked.connect(partial(self._pick, key))
            self.tiles[key] = tile

        self.shot_btn = ActionButton("camera", "Screenshot", "primary")
        self.rec_btn = ActionButton("record", "Record", "record")
        self.opt_btn = ActionButton("sliders", "Options", "plain")
        for button, x, signal in ((self.shot_btn, 43, self.shoot_clicked),
                                  (self.rec_btn, 231, self.record_clicked),
                                  (self.opt_btn, 419, self.options_clicked)):
            button.setParent(self)
            button.move(x, self.BUTTON_Y)
            button.clicked.connect(signal)
        self.set_area("screen")

    def set_tints(self, c1: QColor, c2: QColor):
        for tile in self.tiles.values():
            tile.set_tints(c1, c2)

    def _pick(self, key: str):
        self.set_area(key)
        self.area_changed.emit(key)

    def set_area(self, key: str):
        self._area = key
        for tile_key, tile in self.tiles.items():
            tile.setChecked(tile_key == key)
        self.update()

    def set_status(self, text: str):
        self._status = text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setFont(self._status_font)
        p.setPen(QColor(255, 255, 255, 152))
        p.drawText(QRectF(14, 99, W - 28, 12), Qt.AlignLeft | Qt.AlignVCenter, self._status)


class RecHud(QWidget):
    """The collapsed island turns into a recording indicator with pause + stop."""

    pause_clicked = Signal()
    stop_clicked = Signal()
    mute_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFixedSize(COLLAPSED.width(), COLLAPSED.height())
        self._elapsed = 0.0
        self._paused = False
        self._muted = False
        self._t = 0.0
        self._font = ui_font(13, QFont.Weight.Bold, True, 0.4)
        self._font_small = ui_font(7, QFont.Weight.Bold, True, 1.2)
        self.mute_btn = GhostButton("mic", QSize(24, 24), 12)
        self.pause_btn = GhostButton("pause", QSize(24, 24), 12)
        self.stop_btn = GhostButton("stop", QSize(24, 24), 12)
        for button, x in ((self.mute_btn, 144), (self.pause_btn, 180), (self.stop_btn, 212)):
            button.setParent(self)
            button.move(x, 8)
        self.mute_btn.setToolTip("Mute recording audio")
        self.pause_btn.setToolTip("Pause recording")
        self.stop_btn.setToolTip("Stop recording")
        self.mute_btn.clicked.connect(self.mute_clicked)
        self.pause_btn.clicked.connect(self.pause_clicked)
        self.stop_btn.clicked.connect(self.stop_clicked)
        self._pulse = QTimer(self)
        self._pulse.setInterval(50)
        self._pulse.timeout.connect(self._tick)

    def start(self):
        self._elapsed = 0.0
        self._paused = False
        self.mute_btn.set_kind("mic")
        self.mute_btn.setToolTip("Mute recording audio")
        self.pause_btn.set_kind("pause")
        self._pulse.start()
        self.show()
        self.raise_()

    def set_muted(self, muted: bool):
        self._muted = bool(muted)
        self.mute_btn.set_kind("mic_off" if muted else "mic")
        self.mute_btn.setToolTip("Unmute recording audio" if muted else "Mute recording audio")

    def stop(self):
        self._pulse.stop()
        self.hide()

    def set_elapsed(self, seconds: float):
        self._elapsed = max(0.0, float(seconds))
        self.update()

    def set_paused(self, paused: bool):
        self._paused = bool(paused)
        self.pause_btn.set_kind("play" if paused else "pause")
        self.update()

    def _tick(self):
        self._t += 0.05
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        seconds = int(self._elapsed)
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        text = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

        centre = QPointF(19.0, self.height() / 2.0)
        accent = QColor(255, 178, 92) if self._paused else QColor(255, 92, 92)
        if not self._paused:
            pulse = 0.5 + 0.5 * math.sin(self._t * 4.4)
            halo = QRadialGradient(centre, 11.0)
            halo.setColorAt(0.0, with_alpha(accent, int(50 + 80 * pulse)))
            halo.setColorAt(1.0, with_alpha(accent, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(centre, 11.0, 11.0)
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(centre, 4.2, 4.2)

        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 200 if self._paused else 248))
        p.drawText(QRectF(32, 0, 140, self.height()), Qt.AlignLeft | Qt.AlignVCenter, text)
        if self._paused:
            p.setFont(self._font_small)
            p.setPen(QColor(255, 178, 92, 220))
            p.drawText(QRectF(32, 0, 140, self.height()),
                       Qt.AlignRight | Qt.AlignVCenter, "PAUSED")


class ScreenSelector(QWidget):
    """macOS-style dimmed screen with window snapping or drag-to-select."""

    picked = Signal(object)          # QRect in logical screen coords, or None

    def __init__(self, mode: str = "portion"):
        super().__init__(None)
        self.mode = mode
        self._screen = QGuiApplication.primaryScreen()
        self._dpr = self._screen.devicePixelRatio() or 1.0
        self._shot = grab_display(self._screen)
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._hover: QRect | None = None
        self._drag = False
        self._done = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(self._screen.geometry())

    def showEvent(self, e):
        super().showEvent(e)
        try:
            configure_native_window(int(self.winId()))
        except Exception:
            pass
        self.raise_()
        self.activateWindow()

    def _selection(self) -> QRect | None:
        if self.mode == "window":
            return self._hover
        if self._origin is not None and self._current is not None:
            a, b = self._origin, self._current
            return QRect(min(a.x(), b.x()), min(a.y(), b.y()),
                         abs(a.x() - b.x()), abs(a.y() - b.y()))
        return None

    def _finish(self, rect: QRect | None):
        if self._done:
            return
        self._done = True
        self.close()
        self.picked.emit(rect)
        self.deleteLater()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._finish(None)
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            self._finish(None)
            return
        if self.mode == "window":
            self._finish(self._hover)
            return
        self._drag = True
        self._origin = e.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self.mode == "window":
            rect = window_rect_at(int(pos.x() * self._dpr), int(pos.y() * self._dpr),
                                  self._dpr, int(self.winId()))
            if rect is not None:
                rect = rect.intersected(self.rect())
            if rect != self._hover:
                self._hover = rect
                self.update()
            return
        if self._drag:
            self._current = pos
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.mode == "window":
            return
        self._drag = False
        rect = self._selection()
        if rect is not None and rect.width() > 4 and rect.height() > 4:
            self._finish(rect)
        else:
            self._origin = self._current = None
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform
                         | QPainter.TextAntialiasing)
        widget_rect = QRectF(self.rect())
        if not self._shot.isNull():
            p.drawPixmap(widget_rect, self._shot, QRectF(self._shot.rect()))
        p.fillRect(widget_rect, QColor(6, 8, 14, 128))

        sel = self._selection()
        if sel is not None and sel.width() > 1 and sel.height() > 1:
            p.save()
            p.setClipRect(sel)
            if not self._shot.isNull():
                p.drawPixmap(widget_rect, self._shot, QRectF(self._shot.rect()))
            p.restore()
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 236), 1.4))
            p.drawRect(sel.adjusted(0, 0, -1, -1))
            p.setBrush(QColor(255, 255, 255, 236))
            p.setPen(Qt.NoPen)
            for point in (sel.topLeft(), sel.topRight(), sel.bottomLeft(), sel.bottomRight()):
                p.drawEllipse(QPointF(point.x(), point.y()), 2.4, 2.4)

            label = f"{int(round(sel.width() * self._dpr))} × {int(round(sel.height() * self._dpr))}"
            p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 0.4))
            metrics = p.fontMetrics()
            badge = QRectF(sel.center().x() - (metrics.horizontalAdvance(label) + 18) / 2.0,
                           sel.bottom() + 8, metrics.horizontalAdvance(label) + 18, 20)
            if badge.bottom() > self.height() - 6:
                badge.moveBottom(sel.top() - 8)
            p.setBrush(QColor(22, 25, 36, 216))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.drawRoundedRect(badge, 10, 10)
            p.setPen(QColor(255, 255, 255, 235))
            p.drawText(badge, Qt.AlignCenter, label)

        hint = ("Click a window to capture it" if self.mode == "window"
                else "Drag to select  ·  Esc to cancel")
        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 0.5))
        metrics = p.fontMetrics()
        box = QRectF(self.width() / 2 - (metrics.horizontalAdvance(hint) + 28) / 2.0,
                     28, metrics.horizontalAdvance(hint) + 28, 26)
        p.setBrush(QColor(22, 25, 36, 210))
        p.setPen(QPen(QColor(255, 255, 255, 52), 1))
        p.drawRoundedRect(box, 13, 13)
        p.setPen(QColor(255, 255, 255, 225))
        p.drawText(box, Qt.AlignCenter, hint)


class CaptureFlash(QWidget):
    """The macOS shutter flash after a screenshot."""

    def __init__(self):
        super().__init__(None)
        self._opacity = 0.0
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_fade)
        self._anim.finished.connect(self.hide)

    def _on_fade(self, value):
        self._opacity = float(value)
        self.update()

    def flash(self):
        self.show()
        self.raise_()
        try:
            configure_native_window(int(self.winId()))
        except Exception:
            pass
        self._anim.stop()
        self._anim.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(255, 255, 255, int(150 * self._opacity)))


class CountdownBadge(QWidget):
    """The macOS timer: a big number counting down before capture."""

    done = Signal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(120, 84)
        self._left = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._number_font = ui_font(40, QFont.Weight.Bold, True)
        self._label_font = ui_font(8, QFont.Weight.DemiBold, False, 1.6)

    def start(self, seconds: int):
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.geometry()
            self.move(geo.center().x() - self.width() // 2, geo.y() + 64)
        self._left = max(1, int(seconds))
        self.show()
        self.raise_()
        try:
            configure_native_window(int(self.winId()))
        except Exception:
            pass
        self._timer.start()
        self.update()

    def _tick(self):
        self._left -= 1
        if self._left <= 0:
            self._timer.stop()
            self.hide()
            self.done.emit()
            return
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        p.setBrush(QColor(20, 23, 34, 232))
        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        p.drawRoundedRect(r, 24, 24)
        p.setFont(self._number_font)
        p.setPen(QColor(255, 255, 255, 245))
        p.drawText(r.adjusted(0, -8, 0, -10), Qt.AlignCenter, str(self._left))
        p.setFont(self._label_font)
        p.setPen(QColor(255, 255, 255, 150))
        p.drawText(r.adjusted(0, 0, 0, 8), Qt.AlignBottom | Qt.AlignHCenter, "CAPTURING")


# --------------------------------------------------------------------------- #
# Capture settings card (macOS-style glass panel, replaces the old menu)
# --------------------------------------------------------------------------- #
class SegmentedControl(QWidget):
    """macOS segmented picker with a sliding selection pill."""

    changed = Signal(object)

    def __init__(self, options, width: int = 188, height: int = 26, parent=None):
        super().__init__(parent)
        self._options = [(value, label) for value, label in options]
        self._index = 0
        self._pos = 0.0
        self._hover = -1
        self._font = ui_font(9, QFont.Weight.DemiBold, True, 0.15)
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_slide)

    def _on_slide(self, value):
        self._pos = float(value)
        self.update()

    def value(self):
        return self._options[self._index][0]

    def set_value(self, value):
        for index, (option, _label) in enumerate(self._options):
            if option == value:
                self._set_index(index, emit=False)
                return

    def _segment_at(self, x: float) -> int:
        step = self.width() / max(1, len(self._options))
        return max(0, min(len(self._options) - 1, int(x // step)))

    def _set_index(self, index: int, emit: bool = True):
        index = max(0, min(len(self._options) - 1, index))
        if index == self._index:
            return
        self._index = index
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(float(index))
        self._anim.start()
        self.update()
        if emit:
            self.changed.emit(self._options[index][0])

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._set_index(self._segment_at(e.position().x()))

    def mouseMoveEvent(self, e):
        hover = self._segment_at(e.position().x())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        p.setBrush(QColor(255, 255, 255, 24))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        step = r.width() / max(1, len(self._options))
        pill = QRectF(r.left() + self._pos * step + 2, r.top() + 2, step - 4, r.height() - 4)
        g = QLinearGradient(0, pill.top(), 0, pill.bottom())
        g.setColorAt(0.0, QColor(255, 255, 255, 78))
        g.setColorAt(1.0, QColor(255, 255, 255, 44))
        p.setPen(QPen(QColor(255, 255, 255, 96), 1))
        p.setBrush(g)
        p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)

        p.setFont(self._font)
        for index, (_value, label) in enumerate(self._options):
            cell = QRectF(r.left() + index * step, r.top(), step, r.height())
            on = index == self._index
            if on:
                p.setPen(QColor(255, 255, 255, 248))
            elif index == self._hover:
                p.setPen(QColor(255, 255, 255, 208))
            else:
                p.setPen(QColor(255, 255, 255, 146))
            p.drawText(cell, Qt.AlignCenter, label)


class SettingSwitch(QWidget):
    """Small glowing pill switch."""

    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = False
        self._t = 0.0
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_slide)

    def _on_slide(self, value):
        self._t = float(value)
        self.update()

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool):
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.setChecked(not self._on)
            self.toggled.emit(self._on)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        t = self._t
        p.setPen(QPen(QColor(255, 255, 255, 54), 1))
        p.setBrush(QColor(255, 255, 255, 34))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        if t > 0.01:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(104, 178, 244, int(238 * t)))
            p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        knob = QRectF(r.left() + 3 + (r.width() - d - 6) * t, r.top() + 3, d, d)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(6, 10, 18, int(90 * t)))
        p.drawEllipse(knob.adjusted(0, 1.6, 0, 1.6))
        p.setBrush(QColor(255, 255, 255, 246))
        p.drawEllipse(knob)


class PickerButton(QAbstractButton, _Fader):
    """Value row that opens a menu when clicked (microphone picker)."""

    def __init__(self, label: str, width: int = 188, height: int = 26):
        super().__init__()
        self.setFixedSize(width, height)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._label = label
        self._font = ui_font(9, QFont.Weight.Medium, False, 0.1)
        self._chevron = create_icon_pixmap("chevron", 11, QColor(255, 255, 255, 150))
        self._init_fader()

    def set_label(self, label: str):
        if label != self._label:
            self._label = label
            self.update()

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(255, 255, 255, 44 + int(self._hover * 32)), 1))
        p.setBrush(QColor(255, 255, 255, 14 + int(self._hover * 18)))
        p.drawRoundedRect(r, 9, 9)
        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 206))
        text = p.fontMetrics().elidedText(self._label, Qt.ElideRight, int(r.width()) - 32)
        p.drawText(QRectF(r.left() + 10, r.top(), r.width() - 32, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, text)
        self._chevron.paint(p, QRect(int(r.right()) - 18, int(r.center().y()) - 5, 11, 11))


class LinkRow(QAbstractButton, _Fader):
    """Footer action row inside the settings card."""

    def __init__(self, kind: str, label: str, width: int = 328):
        super().__init__()
        self.setFixedSize(width, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self._icon = create_icon_pixmap(kind, 13)
        self._label = label
        self._font = ui_font(9, QFont.Weight.DemiBold, True, 0.2)
        self._init_fader()

    def enterEvent(self, e):
        self._fade_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._fade_to(0.0)
        super().leaveEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover > 0.01:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, int(20 * self._hover)))
            p.drawRoundedRect(r, 9, 9)
        self._icon.paint(p, QRect(10, int(r.center().y()) - 7, 14, 14))
        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 224))
        p.drawText(QRectF(32, r.top(), r.width() - 42, r.height()),
                   Qt.AlignLeft | Qt.AlignVCenter, self._label)


class CaptureSettings(QWidget):
    """Floating glass card with every capture option laid out at once."""

    changed = Signal(str, object)
    toggled = Signal(str)
    folder_requested = Signal()
    closed = Signal()

    CARD_W, CARD_H = 352, 368
    PAD = 14
    LABEL_X = 20
    CTRL_X = CARD_W - 18 - 188
    ROW_Y = (76, 110, 144, 178, 212, 246, 280)
    LABELS = ("Quality", "Frame rate", "Microphone", "System audio",
              "Show pointer", "Timer", "Copy to clipboard")

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.CARD_W + self.PAD * 2, self.CARD_H + self.PAD * 2)
        self.setFocusPolicy(Qt.StrongFocus)
        self._summary = ""
        self._menu_busy = False
        self._mic_names: list[str] = []

        self.quality = SegmentedControl(_QUALITY_CHOICES)
        self.fps = SegmentedControl([(value, str(value)) for value in _FPS_CHOICES])
        self.mic = PickerButton("Off")
        self.switch_audio = SettingSwitch()
        self.switch_pointer = SettingSwitch()
        self.timer = SegmentedControl(
            [(value, "Off" if not value else f"{value}s") for value in _TIMER_CHOICES])
        self.switch_clipboard = SettingSwitch()
        self.close_btn = GhostButton("close", QSize(24, 24), 11)
        self.folder = LinkRow("folder", "Open captures folder")

        controls = (self.quality, self.fps, self.mic, self.switch_audio,
                    self.switch_pointer, self.timer, self.switch_clipboard)
        for widget, y in zip(controls, self.ROW_Y):
            widget.setParent(self)
            offset = (26 - widget.height()) // 2
            widget.move(self.PAD + self.CTRL_X, self.PAD + y + offset)
        self.close_btn.setParent(self)
        self.close_btn.move(self.PAD + self.CARD_W - 18 - 24, self.PAD + 14)
        self.folder.setParent(self)
        self.folder.move(self.PAD + 12, self.PAD + 324)

        self.quality.changed.connect(partial(self.changed.emit, "quality"))
        self.fps.changed.connect(partial(self.changed.emit, "fps"))
        self.timer.changed.connect(partial(self.changed.emit, "timer"))
        self.mic.clicked.connect(self._pick_mic)
        self.switch_audio.toggled.connect(lambda _on: self.toggled.emit("system_audio"))
        self.switch_pointer.toggled.connect(lambda _on: self.toggled.emit("pointer"))
        self.switch_clipboard.toggled.connect(lambda _on: self.toggled.emit("clipboard"))
        self.close_btn.clicked.connect(self.close_panel)
        self.folder.clicked.connect(self.folder_requested)

    # --------------------------------------------------------------- state #
    def set_mic_names(self, names):
        self._mic_names = list(names)

    def set_options(self, opt):
        self.quality.set_value(opt.quality)
        self.fps.set_value(opt.fps)
        self.timer.set_value(opt.timer)
        self.switch_audio.setChecked(opt.system_audio)
        self.switch_pointer.setChecked(opt.pointer)
        self.switch_clipboard.setChecked(opt.clipboard)
        self.mic.set_label(opt.mic_label())
        self._summary = f"{opt.area_label()} · {opt.fps} fps · {opt.quality_label()}"
        self.update()

    def _pick_mic(self):
        menu = _menu_style(QMenu(self))
        current = self.mic._label
        for label, value in (("Off", ""), ("System default", "default")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current == label)
            action.triggered.connect(partial(self._choose_mic, label, value))
        if self._mic_names:
            menu.addSeparator()
        for name in self._mic_names:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(current == name)
            action.triggered.connect(partial(self._choose_mic, name, name))
        self._menu_busy = True
        menu.exec(self.mic.mapToGlobal(QPoint(self.mic.width() - 8, self.mic.height())))
        self._menu_busy = False

    def _choose_mic(self, label: str, value: str):
        self.mic.set_label(label)
        self.changed.emit("mic", value)

    # ------------------------------------------------------------ show/hide #
    def position_near(self, anchor: QRect):
        screen = QGuiApplication.primaryScreen().geometry()
        w, h = self.width(), self.height()
        x = max(screen.left() + 12, min(anchor.center().x() - w // 2, screen.right() - w - 12))
        y = anchor.bottom() + 10
        if y + h > screen.bottom() - 12:
            y = max(screen.top() + 12, anchor.top() - h - 10)
        self.move(x, y)

    def show_panel(self):
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        try:
            configure_native_window(int(self.winId()))
        except Exception:
            pass

    def close_panel(self):
        if not self.isVisible():
            return
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def event(self, e):
        if e.type() == QEvent.WindowDeactivate and self.isVisible():
            QTimer.singleShot(0, self._maybe_close)
        return super().event(e)

    def _maybe_close(self):
        if not self.isVisible() or self._menu_busy:
            return
        if QApplication.activePopupWidget() is not None:
            return
        if QApplication.activeWindow() is self:
            return
        self.close_panel()

    # ---------------------------------------------------------------- paint #
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        pad = self.PAD
        card = QRectF(pad, pad, self.CARD_W, self.CARD_H)

        p.setPen(Qt.NoPen)
        for i in range(9, 0, -1):
            p.setBrush(QColor(0, 0, 0, 5 + (9 - i) * 2))
            p.drawRoundedRect(card.adjusted(-i * 0.7, -i * 0.25, i * 0.7, i * 1.0),
                              20 + i, 20 + i)

        g = QLinearGradient(card.topLeft(), card.bottomLeft())
        g.setColorAt(0.0, QColor(35, 39, 53, 246))
        g.setColorAt(0.55, QColor(23, 26, 37, 246))
        g.setColorAt(1.0, QColor(18, 21, 31, 248))
        p.setBrush(g)
        p.setPen(QPen(QColor(255, 255, 255, 64), 1))
        p.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 20, 20)

        sheen = QLinearGradient(card.topLeft(), QPointF(card.left(), card.top() + 96))
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(sheen)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(card.adjusted(1, 1, -1, -1), 19, 19)

        p.setFont(ui_font(13, QFont.Weight.Bold, True, 0.2))
        p.setPen(QColor(255, 255, 255, 245))
        p.drawText(QRectF(card.left() + self.LABEL_X, card.top() + 14, 230, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, "Capture settings")
        p.setFont(ui_font(8, QFont.Weight.DemiBold, False, 0.9))
        p.setPen(QColor(255, 255, 255, 128))
        p.drawText(QRectF(card.left() + self.LABEL_X, card.top() + 35, self.CARD_W - 70, 14),
                   Qt.AlignLeft | Qt.AlignVCenter, self._summary.upper())

        p.setPen(QPen(QColor(255, 255, 255, 26), 1))
        p.drawLine(QPointF(card.left() + 14, card.top() + 62),
                   QPointF(card.right() - 14, card.top() + 62))

        p.setFont(ui_font(10, QFont.Weight.Medium, False, 0.2))
        p.setPen(QColor(255, 255, 255, 214))
        for text, y in zip(self.LABELS, self.ROW_Y):
            p.drawText(QRectF(card.left() + self.LABEL_X, card.top() + y,
                              self.CTRL_X - self.LABEL_X - 6, 26),
                       Qt.AlignLeft | Qt.AlignVCenter, text)

        p.setPen(QPen(QColor(255, 255, 255, 24), 1))
        p.drawLine(QPointF(card.left() + 14, card.top() + 318),
                   QPointF(card.right() - 14, card.top() + 318))


class InnerSettings(QWidget):
    """In-island settings. Each section (Focus / Agent / Bluetooth / Capture) gets its own
    compact grid of glass rows inside the notch, so nothing opens in a floating window."""

    SECTION_TABS = {"focus": 1, "agent": 2, "bluetooth": 3, "capture": 4}

    changed = Signal(str, object)
    toggled = Signal(str)
    folder_requested = Signal()
    focus_preset = Signal(object)
    pin_toggled = Signal(bool)
    startup_toggled = Signal(bool)
    menu_busy = Signal(bool)

    ROW_W, ROW_H, CTRL_W, CTRL_H = 282, 24, 150, 20      # keeps 30px side / 10px bottom margins
    COL_X = (30, 330)                                  # clear of the notch's rounded corners
    ROW_Y = (4, 30, 56, 82)

    def __init__(self):
        super().__init__()
        self.setFixedSize(W, H - DETAIL_TOP)
        self._section = "capture"
        self._mic_names: list[str] = []
        cw, ch = self.CTRL_W, self.CTRL_H
        self.quality = SegmentedControl(
            [(v, "Max" if lab == "Maximum" else lab) for v, lab in _QUALITY_CHOICES], cw, ch)
        self.fps = SegmentedControl([(v, str(v)) for v in _FPS_CHOICES], cw, ch)
        self.timer = SegmentedControl(
            [(v, "Off" if not v else f"{v}s") for v in _TIMER_CHOICES], cw, ch)
        self.mic = PickerButton("Off", cw, ch)
        self.switch_audio = SettingSwitch()
        self.switch_pointer = SettingSwitch()
        self.switch_clipboard = SettingSwitch()
        self.folder = LinkRow("folder", "Open captures folder", self.ROW_W)
        self.folder.setFixedSize(self.ROW_W, self.ROW_H)
        self.preset = SegmentedControl([(m, f"{m}m") for m in FocusTimer.PRESETS], cw, ch)
        self.switch_pin = SettingSwitch()
        self.switch_startup = SettingSwitch()

        # section -> [(slot, label, widget)]; slots run down the left column, then the right
        self._layout = {
            "focus": [(0, "Session length", self.preset), (4, "Keep island open", self.switch_pin),
                      (5, "Start with Windows", self.switch_startup)],
            "agent": [(0, "Keep island open", self.switch_pin),
                      (1, "Start with Windows", self.switch_startup)],
            "bluetooth": [(0, "Keep island open", self.switch_pin),
                          (1, "Start with Windows", self.switch_startup)],
            "capture": [(0, "Quality", self.quality), (1, "Frame rate", self.fps),
                        (2, "Timer", self.timer), (3, "Microphone", self.mic),
                        (4, "System audio", self.switch_audio),
                        (5, "Show pointer", self.switch_pointer),
                        (6, "Copy to clipboard", self.switch_clipboard),
                        (7, "", self.folder)],
        }
        for rows in self._layout.values():
            for slot, _label, widget in rows:
                widget.setParent(self)
                x, y = self._slot_pos(slot)
                if widget is self.folder:
                    widget.move(x, y)
                else:
                    widget.move(x + self.ROW_W - 8 - widget.width(),
                                y + (self.ROW_H - widget.height()) // 2)
                widget.hide()

        self.quality.changed.connect(partial(self.changed.emit, "quality"))
        self.fps.changed.connect(partial(self.changed.emit, "fps"))
        self.timer.changed.connect(partial(self.changed.emit, "timer"))
        self.mic.clicked.connect(self._pick_mic)
        self.switch_audio.toggled.connect(lambda _on: self.toggled.emit("system_audio"))
        self.switch_pointer.toggled.connect(lambda _on: self.toggled.emit("pointer"))
        self.switch_clipboard.toggled.connect(lambda _on: self.toggled.emit("clipboard"))
        self.folder.clicked.connect(self.folder_requested)
        self.preset.changed.connect(self.focus_preset)
        self.switch_pin.toggled.connect(self.pin_toggled)
        self.switch_startup.toggled.connect(self.startup_toggled)

    def _slot_pos(self, slot: int):
        return self.COL_X[slot // 4], self.ROW_Y[slot % 4]

    # --------------------------------------------------------------- state #
    def set_section(self, name: str):
        self._section = name if name in self._layout else "capture"
        for rows in self._layout.values():
            for _slot, _label, widget in rows:
                widget.hide()
        for _slot, _label, widget in self._layout[self._section]:
            widget.show()
        self.update()

    def set_mic_names(self, names):
        self._mic_names = list(names)

    def set_options(self, opt):
        self.quality.set_value(opt.quality)
        self.fps.set_value(opt.fps)
        self.timer.set_value(opt.timer)
        self.switch_audio.setChecked(opt.system_audio)
        self.switch_pointer.setChecked(opt.pointer)
        self.switch_clipboard.setChecked(opt.clipboard)
        self.mic.set_label(opt.mic_label())

    def set_preset(self, minutes):
        self.preset.set_value(minutes)

    def set_general(self, pinned: bool, startup: bool):
        self.switch_pin.setChecked(pinned)
        self.switch_startup.setChecked(startup)

    def _pick_mic(self):
        menu = _menu_style(QMenu(self))
        current = self.mic._label
        for label, value in (("Off", ""), ("System default", "default")):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current == label)
            action.triggered.connect(partial(self._choose_mic, label, value))
        if self._mic_names:
            menu.addSeparator()
        for name in self._mic_names:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(current == name)
            action.triggered.connect(partial(self._choose_mic, name, name))
        self.menu_busy.emit(True)
        menu.exec(self.mic.mapToGlobal(QPoint(self.mic.width() - 8, self.mic.height())))
        self.menu_busy.emit(False)

    def _choose_mic(self, label: str, value: str):
        self.mic.set_label(label)
        self.changed.emit("mic", value)

    # ---------------------------------------------------------------- paint #
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        p.setFont(ui_font(10, QFont.Weight.Medium, False, 0.2))
        for slot, label, widget in self._layout[self._section]:
            if widget is self.folder:
                continue
            x, y = self._slot_pos(slot)
            row = QRectF(x, y, self.ROW_W, self.ROW_H).adjusted(0.5, 0.5, -0.5, -0.5)
            g = QLinearGradient(row.topLeft(), row.bottomLeft())
            g.setColorAt(0.0, QColor(255, 255, 255, 30))
            g.setColorAt(1.0, QColor(255, 255, 255, 12))
            p.setPen(QPen(QColor(255, 255, 255, 40), 1))
            p.setBrush(g)
            p.drawRoundedRect(row, 12, 12)
            p.setPen(QColor(255, 255, 255, 220))
            p.drawText(QRectF(x + 14, y, self.ROW_W - widget.width() - 30, self.ROW_H),
                       Qt.AlignLeft | Qt.AlignVCenter, label)


# --------------------------------------------------------------------------- #
# The island
# --------------------------------------------------------------------------- #
class LiquidIsland(QWidget):
    FX_MS = 16

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._expanded = False
        self._blocked = False
        self._pill_clear = False
        self._pinned = False
        self._menu_open = False
        self._capture_ok = False
        self._leave_since = None
        self._startup_peek_until = time.monotonic() + 2.5

        self._has_media = False
        self._source = ""
        self._playing = False
        self._duration = 0.0
        self._pos_base = 0.0
        self._pos_t0 = time.monotonic()
        self._seek_hold = 0.0
        self._title = self._artist = self._album = ""
        self._art_sig = None
        self._dragging = False
        self._battery = None
        self._last_time_text = ("", "")

        self._recorder = None
        self._rec_path = None
        self._options = CaptureOptions.load()
        self._capture_busy = False
        self._selector = None
        self._settings = None
        self._badge = None
        self._flash = None
        self._writer = None
        self._audio = None
        self._hud_paused = False
        self._rec_muted = False

        self.focus = FocusTimer(self)
        self.focus.changed.connect(self._refresh_focus)
        self.focus.finished.connect(self._on_focus_finished)
        self._agent: dict = {}
        self._agent_busy = False
        self._detail_kind = ""
        self._lyrics_key = ""
        self._lyrics_threads = set()
        self._art_raw = QPixmap()
        self._weather_state = None

        self._backdrop = QPixmap()
        self._backdrop_rect = QRect()
        self._backdrop_ch = []
        self._backdrop_serial = 0
        self._glass_key = None
        self._glass_cache = QPixmap()
        self._tint_from = (QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2))
        self._tint_to = (QColor(DEFAULT_TINT), QColor(DEFAULT_TINT_2))
        self._mix = 1.0
        self._wash_prev = QPixmap()
        self._wash = QPixmap()

        self._t = 0.0
        self._reveal_t = 9.0
        self._sweep = 1.0
        self._energy = 0.45
        self._fx_last = time.monotonic()
        self._geo_from = self._geo_to = QRect()
        self._opening = True

        self._build_ui()
        self._build_animations()
        self._build_tray()

        try:
            self._blocked = fullscreen_app_active()
            self._pill_clear = False if self._blocked else is_top_center_clear(self._collapsed_rect())
        except Exception:
            self._blocked = False
            self._pill_clear = True

        self.setGeometry(self._pill_rect(COLLAPSED.width(), COLLAPSED.height(), self._resting_y()))

        self.smooth_timer = QTimer(self)
        self.smooth_timer.timeout.connect(self.tick_smooth_progress)
        self.smooth_timer.start(100)

        self.blur_timer = QTimer(self)
        self.blur_timer.setInterval(1000)
        self.blur_timer.timeout.connect(self.refresh_live_backdrop)
        if self._pill_visible():
            self.blur_timer.start()

        self.edge_timer = QTimer(self)
        self.edge_timer.timeout.connect(self.poll_cursor)
        self.edge_timer.start(40)

        self.fx_timer = QTimer(self)
        self.fx_timer.setInterval(self.FX_MS)
        self.fx_timer.timeout.connect(self._tick_fx)

        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)

        self.battery_timer = QTimer(self)
        self.battery_timer.timeout.connect(self.update_battery)
        self.battery_timer.start(20_000)

        self.env_timer = QTimer(self)
        self.env_timer.timeout.connect(self.check_environment)
        self.env_timer.start(250)

        self.hud_timer = QTimer(self)
        self.hud_timer.setInterval(150)
        self.hud_timer.timeout.connect(self._refresh_hud)
        self.hud_timer.start()
        self.pill_timer = QTimer(self)               # drives Robo + the glowing edge when collapsed
        self.pill_timer.setInterval(50)
        self.pill_timer.timeout.connect(self._pill_tick)
        self.pill_timer.start()

        self.update_battery()
        self.update_clock()
        self._sync_mute_state()
        self._set_media_visible(False)
        self._push_tints()
        self.content.hide()

        self.worker = MediaWorker(self)
        self.worker.state_changed.connect(self.apply_state)
        self.worker.bluetooth_changed.connect(self.bluetooth_page.set_devices)
        self.worker.bluetooth_changed.connect(self.agent_card.set_devices)
        self.agent_card.bt_disconnect.connect(self.worker.disconnect_device)
        self.worker.weather_changed.connect(self.weather.set_state)
        self.worker.weather_changed.connect(self._on_weather)
        self.worker.bt_notice.connect(lambda msg: self._notify("Bluetooth", msg))
        self.bluetooth_page.disconnect_requested.connect(self.worker.disconnect_device)
        self.worker.start()

        self.agent_watch = AgentWatcher(self)
        self.agent_watch.state.connect(self._apply_agent)
        self.agent_watch.start()

    # ------------------------------------------------------------------ UI #
    def _build_ui(self):
        self.content = QWidget(self)
        self.content.setFixedSize(W, H)
        self._cue = create_icon_pixmap("chevron", 9, QColor(255, 255, 255, 112))

        self.tab_island = TabButton("island", "Island")
        self.tab_focus = TabButton("timer", "Focus")
        self.tab_agent = TabButton("sparkle", "Agent")
        self.tab_bt = TabButton("bluetooth", "Bluetooth")
        self.tab_capture = TabButton("camera", "Capture")
        self.tabs = (self.tab_island, self.tab_focus, self.tab_agent,
                     self.tab_bt, self.tab_capture)
        nav_x = 12
        for tab in self.tabs:
            tab.setParent(self.content)
            tab.move(nav_x, 6)
            nav_x += tab.width() + 4
        self._nav_rect = QRectF(8, 3, nav_x - 8, 31)      # 4px of tray after the last tab, like the left side
        self.tab_island.setChecked(True)
        for tab_index, tab in enumerate(self.tabs):
            tab.clicked.connect(partial(self.show_tab, tab_index))

        self.gear = GhostButton("gear", QSize(26, 26), 16)
        self.gear.setParent(self.content)
        self.gear.move(W - 12 - 26, 5)
        self.gear.clicked.connect(self.open_menu)

        self.shot_btn = GhostButton("camera", QSize(26, 26), 15)
        self.rec_btn = GhostButton("record", QSize(26, 26), 15)
        for b, tip in ((self.shot_btn, "Screenshot"), (self.rec_btn, "Record screen")):
            b.setParent(self.content)
            b.setToolTip(tip)
        self.rec_btn.move(W - 12 - 26 - 32, 5)
        self.shot_btn.move(self.rec_btn.x() - 32, 5)
        self.shot_btn.clicked.connect(self.take_screenshot)
        self.rec_btn.clicked.connect(self.toggle_recording)
        self._cap_rect = QRectF(self.shot_btn.x() - 4, 3,
                                self.gear.x() + self.gear.width() + 8 - self.shot_btn.x(), 31)

        self.rec_indicator = RecIndicator()
        self.rec_indicator.setParent(self.content)
        self.rec_indicator.move(self.shot_btn.x() - 6 - self.rec_indicator.width(), 6)
        self.rec_indicator.hide()

        self.home_page = QWidget(self.content)
        self.home_page.setGeometry(0, 0, W, H)

        self.art_widget = ArtWidget()
        self.art_widget.setParent(self.home_page)
        self.art_widget.move(QPoint(MUSIC_CARD.x() + 4 - ART_M, MUSIC_CARD.y() + 4 - ART_M))
        self.art_widget.clicked.connect(self.open_media_app)

        self.title_label = GlassText(15, QFont.Weight.Bold, QColor(226, 230, 244), display=True,
                                     scramble=True, marquee=True, shine=True)
        self.album_label = GlassText(11, QFont.Weight.Medium, QColor(255, 255, 255, 208),
                                     marquee=True)
        self.artist_label = GlassText(9, QFont.Weight.DemiBold, QColor(255, 255, 255, 150),
                                      spacing=1.2, scramble=True, marquee=True)
        for lbl, (y, hh) in zip((self.title_label, self.album_label, self.artist_label),
                                ((45, 19), (64, 15), (79, 13))):
            lbl.setParent(self.home_page)
            lbl.setGeometry(HX, y, HW, hh)

        self.media_hit = ClickArea(self.home_page)
        self.media_hit.setGeometry(HX, 45, HW, 46)
        self.media_hit.setToolTip("Open lyrics")
        self.media_hit.clicked.connect(lambda: self._open_detail("media"))

        # The eyebrow above the track still opens the lyrics / player page.
        self.media_head_hit = ClickArea(self.home_page)
        self.media_head_hit.setGeometry(0, 0, 0, 0)
        self.media_head_hit.hide()
        self.media_head_hit.clicked.connect(lambda: self._open_detail("media"))

        self.prev_btn = GhostButton("prev", QSize(26, 26), 13)
        self.play_btn = GhostButton("play", QSize(34, 26), 15, solid=True)
        self.next_btn = GhostButton("next", QSize(26, 26), 13)
        for b, x in ((self.prev_btn, HX - 2), (self.play_btn, HX + 25),
                     (self.next_btn, HX + 60)):
            b.setParent(self.home_page)
            b.move(x, 91)
        self.prev_btn.clicked.connect(lambda: self.worker.command("prev"))
        self.play_btn.clicked.connect(lambda: self.worker.command("toggle"))
        self.next_btn.clicked.connect(lambda: self.worker.command("next"))

        self.eq = Equalizer()
        self.eq.setParent(self.home_page)
        self.eq.move(HX + HW - self.eq.width() + 3, 93)
        self.eq.clicked.connect(self._on_toggle_mute)

        self.slider = SeekSlider()
        self.slider.setParent(self.home_page)
        self.slider.setGeometry(HX - ES_PAD, 119, HW + 2 * ES_PAD, 12)
        self.slider.stretched.connect(self._on_slider_stretch)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self.slider.sliderMoved.connect(
            lambda v: self.current_time_label.set_full_text(self.format_time(v / 1000 * self._duration)))
        self.slider.sliderReleased.connect(self._on_slider_released)

        mute = QColor(255, 255, 255, 170)
        self.current_time_label = GlassText(9, QFont.Weight.Medium, mute, display=True, spacing=0.4)
        self.total_time_label = GlassText(9, QFont.Weight.Medium, mute, display=True, spacing=0.4,
                                          align=Qt.AlignRight)
        self.current_time_label.setParent(self.home_page)
        self.total_time_label.setParent(self.home_page)
        self.current_time_label.setGeometry(HX, 130, 50, 12)
        self.total_time_label.setGeometry(HX + HW - 50, 130, 50, 12)
        self.current_time_label.set_full_text("0:00")
        self.total_time_label.set_full_text("0:00")

        self.dividers = []
        for x in (DIVIDER_1_X, DIVIDER_2_X):
            d = Divider()
            d.setParent(self.home_page)
            d.move(x, 46)
            d.hide()
            self.dividers.append(d)

        self.calendar = CalendarColumn()
        self.calendar.setParent(self.home_page)
        self.calendar.move(CAL_RECT.topLeft())
        self.calendar.hide()

        self.weather = WeatherChip()
        self.weather.setParent(self.home_page)
        self.weather.move(CAL_RECT.x(), CAL_RECT.y() + 53)
        self.weather.hide()

        self.ring = RingGauge()
        self.ring.setParent(self.home_page)
        self.ring.move(HOME_RING)

        self.calendar_hit = ClickArea(self.home_page)
        self.calendar_hit.setGeometry(DATE_RECT)
        self.calendar_hit.setCursor(Qt.PointingHandCursor)
        self.calendar_hit.setToolTip("Expand today")
        self.calendar_hit.clicked.connect(lambda: self._open_detail("info"))
        self.ring_hit = ClickArea(self.home_page)
        self.ring_hit.setGeometry(QRect(HOME_RING, RING_RECT.size()))
        self.ring_hit.setCursor(Qt.PointingHandCursor)
        self.ring_hit.setToolTip("Expand system")
        self.ring_hit.clicked.connect(lambda: self._open_detail("clock"))

        self.agent_card = AgentCard()
        self.agent_card.setParent(self.home_page)
        self.agent_card.move(AGENT_CARD.topLeft())
        self.agent_card.setToolTip("Open the agent view")
        self.agent_card.clicked.connect(lambda: self.show_tab(2))
        self.agent_card.set_focus(self.focus)
        self.agent_card.focus_clicked.connect(self.focus.toggle)
        self.agent_card.bt_clicked.connect(lambda: self.show_tab(3))

        self.bluetooth_page = BluetoothPage()
        self.bluetooth_page.setParent(self.content)
        self.bluetooth_page.move(0, 0)
        self.bluetooth_page.hide()

        self.focus_page = FocusPage()
        self.focus_page.setParent(self.content)
        self.focus_page.move(0, 0)
        self.focus_page.hide()
        self.focus_page.preset_changed.connect(self.focus.set_preset)
        self.focus_page.primary_clicked.connect(self.toggle_focus)
        self.focus_page.reset_clicked.connect(self.focus.reset)

        self.agent_page = AgentPage()
        self.agent_page.setParent(self.content)
        self.agent_page.move(0, 0)
        self.agent_page.hide()

        self.capture_page = CapturePage()
        self.capture_page.setParent(self.content)
        self.capture_page.move(0, 0)
        self.capture_page.hide()
        self.capture_page.area_changed.connect(self._on_area_changed)
        self.capture_page.shoot_clicked.connect(self.take_screenshot)
        self.capture_page.record_clicked.connect(self.toggle_recording)
        self.capture_page.options_clicked.connect(self.open_capture_options)

        self.detail = DetailPage()
        self.detail.setParent(self.content)
        self.detail.move(0, DETAIL_TOP)
        self.detail.hide()
        self.detail.command.connect(self._on_detail_command)

        self.back_btn = GhostButton("back", QSize(26, 26), 13)
        self.back_btn.setParent(self.content)
        self.back_btn.move(int(self._nav_rect.right()) + 3, 5)   # sits in the nav tray's end
        self.back_btn.setToolTip("Back")
        self.back_btn.clicked.connect(self._on_back)
        self.back_btn.hide()

        # inner settings: one compact view per section, opened from the button in the nav gap
        self._tab_index = 0
        self.inner = InnerSettings()
        self.inner.setParent(self.content)
        self.inner.move(0, DETAIL_TOP)
        self.inner.hide()
        self.inner.changed.connect(self._set_option)
        self.inner.toggled.connect(self._toggle_option)
        self.inner.folder_requested.connect(self._open_capture_folder)
        self.inner.focus_preset.connect(self.focus.set_preset)
        self.inner.pin_toggled.connect(lambda on: setattr(self, "_pinned", bool(on)))
        self.inner.startup_toggled.connect(self._inner_startup)
        self.inner.menu_busy.connect(lambda busy: setattr(self, "_menu_open", bool(busy)))
        self.inner_btn = GhostButton("sliders", QSize(26, 26), 13)
        self.inner_btn.setParent(self.content)
        self.inner_btn.move(self.back_btn.pos())
        self.inner_btn.setToolTip("Settings for this section")
        self.inner_btn.clicked.connect(
            lambda: self.open_inner(self.INNER_KEYS[self._tab_index]))
        self.inner_btn.hide()

        self.rec_hud = RecHud()
        self.rec_hud.setParent(self)
        self.rec_hud.move(0, 0)
        self.rec_hud.hide()
        self.rec_hud.pause_clicked.connect(self.toggle_pause)
        self.rec_hud.stop_clicked.connect(self.stop_recording)
        self.rec_hud.mute_clicked.connect(self.toggle_recording_mute)

        self.title_label.set_full_text("Nothing playing")
        for page in (self.home_page, self.focus_page, self.agent_page,
                     self.bluetooth_page, self.capture_page):
            page.lower()

        for tab in self.tabs:
            tab.raise_()
        self.gear.raise_()
        self.shot_btn.raise_()
        self.rec_btn.raise_()
        self.rec_indicator.raise_()
        self.back_btn.raise_()
        self._refresh_capture_status()
        self._refresh_focus()

        self._fx_widgets = (self.title_label, self.album_label, self.artist_label,
                            self.art_widget, self.eq, self.slider, self.ring)

    def _build_animations(self):
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.valueChanged.connect(self._on_geo)
        self.anim.finished.connect(self._on_anim_finished)

        self.tint_anim = QVariantAnimation(self)
        self.tint_anim.setDuration(700)
        self.tint_anim.setStartValue(0.0)
        self.tint_anim.setEndValue(1.0)
        self.tint_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.tint_anim.valueChanged.connect(self._on_mix)

    def _build_tray(self):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        path = notch_path(QRectF(3, 6, 26, 14), 7)
        p.setBrush(QColor(255, 255, 255, 80))
        p.setPen(QPen(QColor(255, 255, 255, 235), 1.6))
        p.drawPath(path)
        p.end()
        self.tray = QSystemTrayIcon(QIcon(pm), self)
        self.tray.setToolTip("Liquify Island")
        self.tray_menu = QMenu()
        self.tray_shot = QAction("Take screenshot", self.tray_menu)
        self.tray_shot.triggered.connect(self.take_screenshot)
        self.tray_rec = QAction("Record screen", self.tray_menu)
        self.tray_rec.triggered.connect(self.toggle_recording)
        self.tray_focus = QAction(f"Focus {self.focus.preset} min", self.tray_menu)
        self.tray_focus.triggered.connect(self.toggle_focus)
        self.tray_pause = QAction("Pause recording", self.tray_menu)
        self.tray_pause.triggered.connect(self.toggle_pause)
        self.tray_mute = QAction("Mute recording audio", self.tray_menu)
        self.tray_mute.setCheckable(True)
        self.tray_mute.toggled.connect(self._apply_recording_mute)
        self.tray_options = QAction("Capture options…", self.tray_menu)
        self.tray_options.triggered.connect(self.open_capture_options)
        self.tray_startup = QAction("Start with Windows", self.tray_menu)
        self.tray_startup.setCheckable(True)
        self.tray_startup.toggled.connect(self._on_startup_toggled)
        q = QAction("Quit Liquify Island", self.tray_menu)
        q.triggered.connect(QApplication.quit)
        self.tray_menu.addAction(self.tray_shot)
        self.tray_menu.addAction(self.tray_rec)
        self.tray_menu.addAction(self.tray_focus)
        self.tray_menu.addAction(self.tray_pause)
        self.tray_menu.addAction(self.tray_mute)
        self.tray_menu.addAction(self.tray_options)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.tray_startup)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(q)
        self.tray_menu.aboutToShow.connect(self._sync_tray_menu)
        _menu_style(self.tray_menu)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

    PAGE_ORDER = ("home_page", "focus_page", "agent_page", "bluetooth_page", "capture_page")

    INNER_KEYS = ("", "focus", "agent", "bluetooth", "capture")

    def show_tab(self, index: int):
        if not self.detail.isHidden():
            self.detail.hide()
            self.back_btn.hide()
            self._detail_kind = ""
        if not self.inner.isHidden():
            self.inner.hide()
            self.back_btn.hide()
        index = max(0, min(len(self.PAGE_ORDER) - 1, index))
        self._tab_index = index
        for tab_index, tab in enumerate(self.tabs):
            tab.setChecked(tab_index == index)
        for page_index, name in enumerate(self.PAGE_ORDER):
            page = getattr(self, name)
            page.setVisible(page_index == index)
            page.lower()
        for tab in self.tabs:
            tab.raise_()
        self.gear.raise_()
        self.shot_btn.raise_()
        self.rec_btn.raise_()
        self.rec_indicator.raise_()
        self.inner_btn.setVisible(index > 0)
        self.inner_btn.raise_()

        if index in (0, 1, 2):
            self._reveal_t = 0.0
            self._apply_reveal()
        if index == 1:
            self._refresh_focus()
        if index == 3:
            self.worker.refresh_bluetooth()
        if index == 4:
            self._refresh_capture_status()

    def open_menu(self):
        self._menu_open = True
        menu = _menu_style(QMenu(self))
        pin = menu.addAction("Keep open")
        pin.setCheckable(True)
        pin.setChecked(self._pinned)

        shot = menu.addAction("Take screenshot")
        rec = menu.addAction("Stop recording" if self._recorder is not None else "Record screen")
        options = menu.addAction("Capture options…")

        menu.addSeparator()
        startup = menu.addAction("Start with Windows")
        startup.setCheckable(True)
        startup.setChecked(is_autostart_enabled())

        menu.addSeparator()
        quit_act = menu.addAction("Quit Liquify Island")
        chosen = menu.exec(self.gear.mapToGlobal(QPoint(0, self.gear.height() + 4)))
        self._menu_open = False
        if chosen is pin:
            self._pinned = pin.isChecked()
        elif chosen is shot:
            self.take_screenshot()
        elif chosen is rec:
            self.toggle_recording()
        elif chosen is options:
            self.open_capture_options()
        elif chosen is startup:
            if not set_autostart(startup.isChecked()):
                self._notify("Autostart failed", "Windows blocked the startup entry")
        elif chosen is quit_act:
            QApplication.quit()
            
    # ---------------------------------------------------------- capturing --#
    def _notify(self, title: str, body: str):
        try:
            if self.tray.supportsMessages():
                self.tray.showMessage(title, body, QSystemTrayIcon.Information, 2600)
        except Exception:
            pass

    def _sync_tray_menu(self):
        self.tray_rec.setText("Stop recording" if self._recorder is not None else "Record screen")
        self.tray_focus.setText("Stop focus session" if self.focus.active
                                else f"Focus {self.focus.preset} min")
        self.tray_shot.setEnabled(not self._capture_busy)
        self.tray_pause.setVisible(self._recorder is not None)
        self.tray_pause.setText("Resume recording" if self._hud_paused else "Pause recording")
        self.tray_mute.setVisible(self._recorder is not None)
        self.tray_mute.blockSignals(True)
        self.tray_mute.setChecked(self._rec_muted)
        self.tray_mute.blockSignals(False)
        self.tray_startup.blockSignals(True)
        self.tray_startup.setChecked(is_autostart_enabled())
        self.tray_startup.blockSignals(False)

    def _on_startup_toggled(self, checked: bool):
        if set_autostart(bool(checked)):
            self._notify("Autostart on" if checked else "Autostart off",
                         "Liquify Island will start with Windows" if checked
                         else "Liquify Island will no longer start with Windows")
            return
        self.tray_startup.blockSignals(True)
        self.tray_startup.setChecked(not checked)
        self.tray_startup.blockSignals(False)
        self._notify("Autostart failed", "Windows blocked the startup entry")

    def _refresh_capture_status(self):
        opt = self._options
        parts = [opt.area_label(), f"{opt.fps} fps", opt.quality_label()]
        if opt.timer:
            parts.append(f"{opt.timer} s timer")
        parts.append("Mic on" if opt.mic else "Mic off")
        if opt.system_audio:
            parts.append("System audio")
        if opt.clipboard:
            parts.append("Clipboard")
        self.capture_page.set_area(opt.area)
        self.capture_page.set_status("   ·   ".join(parts).upper())

    def _set_option(self, key: str, value):
        setattr(self._options, key, value)
        self._options.save()
        self._refresh_capture_status()
        if self._settings is not None:
            self._settings.set_options(self._options)
        self.inner.set_options(self._options)

    def _toggle_option(self, key: str):
        self._set_option(key, not bool(getattr(self._options, key)))

    def _on_area_changed(self, key: str):
        self._set_option("area", key)

    def _open_capture_folder(self):
        try:
            subprocess.Popen(["explorer.exe", str(capture_folder("video"))])
        except Exception:
            pass

    def open_capture_options(self):
        """Capture options now live inside the island (inner settings, Capture section)."""
        if not self.inner.isHidden() and self.inner._section == "capture":
            self._close_inner()
            return
        self.open_inner("capture")

    def open_inner(self, section: str):
        if section not in InnerSettings.SECTION_TABS:
            return
        if not self._expanded:
            self.expand()
        if not self.detail.isHidden():
            self.detail.hide()
            self._detail_kind = ""
        self._tab_index = InnerSettings.SECTION_TABS[section]
        self.inner.set_section(section)
        if section == "capture":
            self.inner.set_mic_names(microphone_names())
        self.inner.set_options(self._options)
        self.inner.set_preset(self.focus.preset)
        self.inner.set_general(self._pinned, is_autostart_enabled())
        for name in self.PAGE_ORDER:
            getattr(self, name).setVisible(False)
        for tab_index, tab in enumerate(self.tabs):
            tab.setChecked(tab_index == self._tab_index)
        self.inner.show()
        self.inner.raise_()
        self.inner_btn.hide()
        self.back_btn.show()
        self.back_btn.raise_()
        for tab in self.tabs:
            tab.raise_()
        self.update()

    def _close_inner(self):
        if self.inner.isHidden():
            return
        self.inner.hide()
        self.back_btn.hide()
        self.show_tab(self._tab_index)

    def _on_back(self):
        if not self.inner.isHidden():
            self._close_inner()
        else:
            self._close_detail()

    def _inner_startup(self, on: bool):
        if not set_autostart(bool(on)):
            self._notify("Autostart failed", "Windows blocked the startup entry")
            self.inner.set_general(self._pinned, is_autostart_enabled())

    def _on_settings_closed(self):
        self._menu_open = False

    # ---------------------------------------------------------- capturing --#
    def _request_capture(self, kind: str):
        if self._recorder is not None or self._capture_busy:
            return
        action = partial(self._pick_capture_area, kind, self._options.area)
        if self._options.timer > 0:
            self._capture_busy = True
            badge = CountdownBadge()
            badge.done.connect(partial(self._countdown_done, badge, action))
            self._badge = badge
            badge.start(self._options.timer)
        else:
            action()

    def _countdown_done(self, badge, action):
        self._capture_busy = False
        self._badge = None
        badge.deleteLater()
        action()

    def _pick_capture_area(self, kind: str, mode: str):
        if mode == "screen":
            self._capture_now(kind, None)
            return
        self._capture_busy = True
        try:
            selector = ScreenSelector(mode)
        except Exception:
            self._capture_busy = False
            self._notify("Capture failed", "Could not open the selector")
            return
        self._selector = selector
        selector.picked.connect(partial(self._selector_done, kind, selector))
        selector.show()

    def _selector_done(self, kind: str, selector, rect):
        self._selector = None
        self._capture_busy = False
        if rect is None:
            try:
                selector.deleteLater()
            except RuntimeError:
                pass
            return
        self._capture_now(kind, rect)

    def _capture_now(self, kind: str, rect):
        self._capture_busy = False
        if kind == "shot":
            self._save_screenshot(rect)
        else:
            self._start_recording(rect)

    def take_screenshot(self):
        self._request_capture("shot")

    def toggle_recording(self):
        if self._recorder is not None:
            self.stop_recording()
        else:
            self._request_capture("record")

    def _save_screenshot(self, rect):
        try:
            screen = QGuiApplication.primaryScreen()
            shot = grab_display(screen)
            if shot.isNull():
                raise RuntimeError("empty grab")
            if rect is not None:
                dpr = screen.devicePixelRatio() or 1.0
                area = QRect(int(round(rect.x() * dpr)), int(round(rect.y() * dpr)),
                             int(round(rect.width() * dpr)), int(round(rect.height() * dpr)))
                shot = shot.copy(area.intersected(QRect(0, 0, shot.width(), shot.height())))
            target = capture_folder("shot") / f"Liquify {dt.datetime.now():%Y-%m-%d %H-%M-%S}.png"
            if not shot.save(str(target), "PNG"):
                raise RuntimeError("png save failed")
            if self._options.clipboard:
                QGuiApplication.clipboard().setPixmap(shot)
            self._flash_capture()
            self._notify("Screenshot saved", target.name)
        except Exception:
            self._notify("Screenshot failed", "Could not capture the screen")
        finally:
            self._sweep = 0.0
            self.update()

    def _flash_capture(self):
        if self._flash is None:
            self._flash = CaptureFlash()
        self._flash.flash()

    def _physical_area(self, rect):
        if rect is None:
            return primary_screen_rect()
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return None
        dpr = screen.devicePixelRatio() or 1.0
        area = (int(round(rect.x() * dpr)), int(round(rect.y() * dpr)),
                int(round(rect.width() * dpr)), int(round(rect.height() * dpr)))
        return area if area[2] >= 16 and area[3] >= 16 else None

    def _start_recording(self, rect=None):
        if self._recorder is not None:
            return
        area = self._physical_area(rect)
        if area is None:
            self._notify("Recording failed", "No usable capture area")
            return
        opt = self._options
        wants_audio = bool(opt.mic or opt.system_audio)
        stamp = f"Liquify {dt.datetime.now():%Y-%m-%d %H-%M-%S}"
        folder = capture_folder("video")
        path = folder / f"{stamp}.mp4"
        writer = None
        if HAVE_MP4:
            try:
                writer = Mp4Writer(path, area[2], area[3], opt.fps, opt.quality, audio=wants_audio)
            except OSError:
                writer = None
        if writer is None:                       # no H.264 encoder: keep the MJPEG fallback
            path = folder / f"{stamp}.avi"
            try:
                writer = MjpegAviWriter(path, area[2], area[3], opt.fps, opt.quality,
                                        audio=wants_audio)
            except OSError:
                self._notify("Recording failed", "Could not create the video file")
                return
        audio = None
        if wants_audio:
            audio = AudioSession(opt.mic, opt.system_audio, writer.write_audio, self)
            if audio.error:
                self._notify("Audio unavailable", audio.error)
        recorder = ScreenRecorder(writer, area, opt.fps, opt.pointer, parent=self)
        recorder.finished.connect(self._on_recorder_finished)
        self._writer, self._audio, self._recorder = writer, audio, recorder
        self._rec_path = path
        self._hud_paused = False
        self._rec_muted = False
        self.rec_btn.set_kind("stop")
        self.rec_hud.start()
        self.rec_hud.move((self.width() - COLLAPSED.width()) // 2, 0)
        self.rec_indicator.set_elapsed(0.0)
        self.rec_indicator.start()
        self.rec_indicator.show()
        try:
            self.tray.setToolTip("Liquify Island · recording")
        except Exception:
            pass
        self._notify("Recording started", "Saving to Videos\\Liquify Island")
        self._expanded = False
        self._leave_since = None
        self.animate_to(COLLAPSED.width(), COLLAPSED.height(), DOCK_Y)
        recorder.start()

    def stop_recording(self):
        if self._recorder is not None:
            self._recorder.request_stop()

    def toggle_pause(self):
        if self._recorder is None:
            return
        self._hud_paused = not self._hud_paused
        self._recorder.request_pause(self._hud_paused)
        if self._audio is not None:
            self._audio.set_paused(self._hud_paused)
        self.rec_hud.set_paused(self._hud_paused)

    def toggle_recording_mute(self):
        self._apply_recording_mute(not self._rec_muted)

    def _apply_recording_mute(self, muted: bool):
        self._rec_muted = bool(muted) and self._recorder is not None
        if self._audio is not None:
            self._audio.set_muted(self._rec_muted)
        self.rec_hud.set_muted(self._rec_muted)
        try:
            self.tray_mute.blockSignals(True)
            self.tray_mute.setChecked(self._rec_muted)
            self.tray_mute.blockSignals(False)
        except Exception:
            pass

    def _pill_tick(self):
        if (not self._expanded and self._pill_clear and self.isVisible()
                and not self.rec_hud.isVisible()):
            self.update()

    def _refresh_hud(self):
        if self._recorder is None:
            return
        self.rec_hud.set_elapsed(self._recorder.seconds)
        self.rec_indicator.set_elapsed(self._recorder.seconds)

    def _on_recorder_finished(self):
        recorder, self._recorder = self._recorder, None
        if recorder is None:
            return
        audio, self._audio = self._audio, None
        if audio is not None:
            audio.close()
            audio.deleteLater()
        self._writer = None
        path, self._rec_path = self._rec_path, None
        path = recorder.saved_path or path
        self.rec_btn.set_kind("record")
        self.rec_indicator.stop()
        self.rec_indicator.hide()
        self.rec_hud.stop()
        self._hud_paused = False
        self._rec_muted = False
        try:
            self.tray.setToolTip("Liquify Island")
        except Exception:
            pass
        try:
            size = path.stat().st_size if path is not None else 0
        except OSError:
            size = 0
        if recorder.error:
            self._notify("Recording stopped", recorder.error)
        elif path is not None and size > 4096:
            self._notify("Recording saved", recorder.warn or f"{path.name} · {self.format_time(recorder.seconds)}")
        else:
            self._notify("Recording failed", "No video was written")
        recorder.deleteLater()
        self.check_environment(force=True)

    def _on_toggle_mute(self):
        muted = toggle_system_mute()
        self.eq.set_muted(muted)
        detail = getattr(self, "detail", None)
        if detail is not None:
            detail.set_muted(muted)

    def _sync_mute_state(self):
        muted = get_system_mute()
        if muted is not None:
            self.eq.set_muted(muted)
            detail = getattr(self, "detail", None)
            if detail is not None:
                detail.set_muted(muted)

    # ------------------------------------------------------- focus + agent --#
    def toggle_focus(self):
        self.focus.toggle()
        if self.focus.active:
            self._peek(5.0)
        self._refresh_focus()

    def _refresh_focus(self):
        page = getattr(self, "focus_page", None)
        if page is not None:
            page.set_state(self.focus)
        try:
            self.tray_focus.setText("Stop focus session" if self.focus.active
                                    else f"Focus {self.focus.preset} min")
        except Exception:
            pass
        self.update()

    def _on_focus_finished(self):
        play_chime()
        self._notify("Focus complete", f"{self.focus.preset} minutes done — take a break")
        page = getattr(self, "focus_page", None)
        if page is not None:
            page.celebrate()
        self._peek(9.0)

    def _peek(self, seconds: float):
        """Let the pill sit at the top edge for a moment so a change is noticed."""
        if self._recorder is not None:
            return
        self._startup_peek_until = time.monotonic() + max(0.0, float(seconds))
        self.check_environment(force=True)
        self.update()

    def _apply_agent(self, snap):
        self._agent = dict(snap or {})
        busy = bool(self._agent.get("busy"))
        page = getattr(self, "agent_page", None)
        if page is not None:
            page.set_state(self._agent)
        card = getattr(self, "agent_card", None)
        if card is not None:
            card.set_state(self._agent)
        if busy and not self._agent_busy:
            self._peek(6.0)
        elif not busy and self._agent_busy and self._agent.get("kind"):
            self._notify(f"{self._agent.get('kind')} finished", "The session went quiet")
        self._agent_busy = busy
        self.update()

    # ------------------------------------------------- expandable views --#
    def _lyrics_cache_key(self) -> str:
        return (f"{self._artist.strip().lower()}|{self._title.strip().lower()}"
                f"|{int(self._duration)}")

    def _request_lyrics(self):
        if not (self._has_media and self._title):
            return
        key = self._lyrics_cache_key()
        if key == self._lyrics_key:
            return
        self._lyrics_key = key
        self.detail.set_lyrics_loading()
        thread = LyricsFetcher(key, self._title, self._artist, self._album,
                               self._duration, self)
        thread.ready.connect(self._on_lyrics)
        thread.finished.connect(partial(self._lyrics_done, thread))
        self._lyrics_threads.add(thread)
        thread.start()

    def _lyrics_done(self, thread):
        self._lyrics_threads.discard(thread)
        try:
            thread.deleteLater()
        except RuntimeError:
            pass

    def _on_lyrics(self, payload):
        if isinstance(payload, dict) and payload.get("key") == self._lyrics_key:
            self.detail.set_lyrics(payload)

    def _on_weather(self, state):
        self._weather_state = state
        if self.detail.isVisible():
            self._refresh_detail_panel()

    def _refresh_detail_panel(self, now=None):
        self.detail.set_panel(now or dt.datetime.now(), self._weather_state,
                              self._battery, _system_stats())

    def _open_detail(self, kind: str):
        """Swap the active island page for one of the three expanded views."""
        if not self._expanded:
            return
        self._detail_kind = kind
        self.detail.set_kind(kind)
        self.detail.set_tints(*self.current_tints())
        self.detail.set_media(self._title, self._artist, self._album, self._source,
                              self._playing, self._duration)
        self.detail.set_playback(self.estimated_position(), self._duration, self._playing)
        self.detail.set_art(self._art_raw)
        self._refresh_detail_panel()
        if kind == "media" and self._has_media and self._lyrics_cache_key() != self._lyrics_key:
            self._request_lyrics()
        for name in self.PAGE_ORDER:
            getattr(self, name).setVisible(False)
        self.detail.show()
        self.detail.raise_()
        self.back_btn.show()
        self.back_btn.raise_()
        self.detail.start()
        self.update()

    def _close_detail(self):
        if self.detail.isHidden():
            return
        self.detail.hide()
        self.back_btn.hide()
        self._detail_kind = ""
        self.show_tab(0)

    def _on_detail_command(self, command):
        name, arg = command
        if name == "back":
            self._close_detail()
        elif name == "toggle":
            self.worker.command("toggle")
        elif name in ("prev", "next"):
            self.worker.command(name)
        elif name == "seek":
            target = clamp01(float(arg or 0.0)) * self._duration
            self._pos_base, self._pos_t0 = target, time.monotonic()
            self._dragging = False
            self._seek_hold = time.monotonic() + 0.9
            self.worker.command("seek", target)
            self.detail.set_playback(target, self._duration, self._playing)
            self.tick_smooth_progress(force=True)
        elif name == "seek_at":
            target = max(0.0, float(arg or 0.0))
            if self._duration > 0:
                target = min(target, self._duration)
            self._pos_base, self._pos_t0 = target, time.monotonic()
            self._dragging = False
            self._seek_hold = time.monotonic() + 0.9
            self.worker.command("seek", target)
            self.detail.set_playback(target, self._duration, self._playing)
            self.tick_smooth_progress(force=True)
        elif name == "mute":
            self._on_toggle_mute()
        elif name == "open":
            self.open_media_app()

    # ---------------------------------------------------------- geometry --#
    @staticmethod
    def _screen_rect() -> QRect:
        return QGuiApplication.primaryScreen().geometry()

    def _pill_rect(self, w, h, y) -> QRect:
        sg = self._screen_rect()
        return QRect(sg.x() + (sg.width() - w) // 2, sg.y() + y, w, h)

    def _collapsed_rect(self) -> QRect:
        return self._pill_rect(COLLAPSED.width(), COLLAPSED.height(), DOCK_Y)

    def _expanded_rect(self) -> QRect:
        return self._pill_rect(W, H, DOCK_Y)

    def _pill_visible(self) -> bool:
        if self._recorder is not None:
            return True
        if self._blocked:
            return False
        return self._pill_clear or (time.monotonic() < self._startup_peek_until)

    def _resting_y(self) -> int:
        return DOCK_Y if self._pill_visible() else HIDDEN_Y

    def animate_to(self, width, height, y):
        self.anim.stop()
        self._geo_from = self.geometry()
        self._geo_to = self._pill_rect(width, height, y)
        self._opening = self._expanded
        if self._opening:
            curve = QEasingCurve(QEasingCurve.OutBack)
            curve.setOvershoot(0.55)                # a softer, more liquid settle
            self.anim.setDuration(520)
        else:
            curve = QEasingCurve(QEasingCurve.InOutQuart)
            self.anim.setDuration(320)
        self.anim.setEasingCurve(curve)
        self.anim.start()

    def _on_geo(self, v):
        t = float(v)
        a, b = self._geo_from, self._geo_to
        w = int(round(a.width() + (b.width() - a.width()) * t))
        h = int(round(a.height() + (b.height() - a.height()) * t))
        cx = a.center().x() + (b.center().x() - a.center().x()) * t
        y = a.y() + (b.y() - a.y()) * t
        if self._opening:
            y = min(y, float(b.y()))
        self.setGeometry(int(round(cx - w / 2.0)), int(round(y)), max(1, w), max(1, h))
        open_frac = clamp01((w - COLLAPSED.width()) / max(1, W - COLLAPSED.width()))
        self.content.setVisible(open_frac >= 0.18)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.content.move((self.width() - W) // 2, 0)
        self.rec_hud.move((self.width() - COLLAPSED.width()) // 2, 0)

    # -------------------------------------------------------- native bits --#
    def showEvent(self, e):
        super().showEvent(e)
        try:
            self._capture_ok = configure_native_window(int(self.winId()))
        except Exception:
            self._capture_ok = False
        self.check_environment(force=True)

    # ------------------------------------------------- show / hide logic --#
    def expand(self):
        self._expanded = True
        self._leave_since = None
        self._reveal_t, self._sweep = 0.0, 0.0
        self._apply_reveal()
        for lbl in (self.title_label, self.artist_label):
            lbl.replay()
        self._sync_mute_state()
        self._fx_last = time.monotonic()
        self.fx_timer.start()
        target = self._expanded_rect()
        self.capture_backdrop(target)
        self.blur_timer.setInterval(250)
        self.blur_timer.start()
        self.worker.refresh_bluetooth()
        self.animate_to(W, H, DOCK_Y)

    def collapse(self):
        self._expanded = False
        self._leave_since = None
        if not self.detail.isHidden():
            self.detail.hide()
            self.back_btn.hide()
            self._detail_kind = ""
            self.show_tab(0)         # the home page is still there for the next expand
        if not self.inner.isHidden():
            self.inner.hide()
            self.back_btn.hide()
            self.show_tab(0)
        self.animate_to(COLLAPSED.width(), COLLAPSED.height(), self._resting_y())

    def _on_anim_finished(self):
        if not self._expanded:
            self.fx_timer.stop()
            if self._pill_visible():
                self.blur_timer.setInterval(1000)
                self.capture_backdrop(self._collapsed_rect())
            else:
                self.blur_timer.stop()

    def poll_cursor(self):
        if self._blocked or self._capture_busy or self._recorder is not None:
            return

        sg = self._screen_rect()
        pos = QCursor.pos()

        if not self._expanded:
            if self._pill_visible():
                hit = self._collapsed_rect().contains(pos)
            else:
                trigger = QRect(
                    sg.x() + (sg.width() - TOP_TRIGGER_W) // 2,
                    sg.y(),
                    TOP_TRIGGER_W,
                    TOP_TRIGGER_H,
                )
                hit = trigger.contains(pos)
            if hit and not left_button_down():
                self.expand()
            return

        exp = self._expanded_rect()
        keep = QRect(
            exp.x() - 25,
            sg.y(),
            exp.width() + 50,
            exp.bottom() - sg.y() + 30
        )

        if (
            keep.contains(pos)
            or self.slider.isSliderDown()
            or self._pinned
            or self._menu_open
            or any(tab.underMouse() for tab in self.tabs)
            or self.gear.underMouse()
            or self.bluetooth_page.underMouse()
                    or self.focus_page.underMouse()
        or self.agent_page.underMouse()
        or self.detail.underMouse()
        or self.back_btn.underMouse()
        or self.inner.underMouse()
        or self.inner_btn.underMouse()
        ):
            self._leave_since = None
        elif self._leave_since is None:
            self._leave_since = time.monotonic()
        elif time.monotonic() - self._leave_since > HIDE_GRACE_S:
            self.collapse()

    def check_environment(self, force: bool = False):
        if self._capture_busy:
            return
        if self._recorder is not None:
            self._blocked = False
            self._pill_clear = True
        if self._expanded:
            self._sync_mute_state()

        try:
            blocked = fullscreen_app_active()
        except Exception:
            blocked = self._blocked
        try:
            raw_clear = False if blocked else is_top_center_clear(self._collapsed_rect(), int(self.winId()))
        except Exception:
            raw_clear = self._pill_clear

        effective_clear = False if blocked else (raw_clear or time.monotonic() < self._startup_peek_until)

        if not force and blocked == self._blocked and effective_clear == self._pill_clear:
            return

        self._blocked = blocked
        self._pill_clear = effective_clear

        if self._blocked and self._expanded:
            self.collapse()
            return

        if not self._expanded:
            if self._pill_visible():
                self.blur_timer.setInterval(1000)
                self.blur_timer.start()
                self.capture_backdrop(self._collapsed_rect())
                self.animate_to(COLLAPSED.width(), COLLAPSED.height(), DOCK_Y)
            else:
                self.blur_timer.stop()
                self.animate_to(COLLAPSED.width(), COLLAPSED.height(), HIDDEN_Y)

    # ------------------------------------------------------------ backdrop #
    def capture_backdrop(self, target: QRect):
        if not self._capture_ok:
            return
        screen = QGuiApplication.primaryScreen()
        grab = target.adjusted(-28, -28, 28, 28).intersected(screen.geometry())
        if grab.width() <= 0 or grab.height() <= 0:
            return
        shot = screen.grabWindow(0, grab.x(), grab.y(), grab.width(), grab.height())
        if shot.isNull():
            return
        shot.setDevicePixelRatio(1.0)
        sw, sh = max(1, grab.width() // BACKDROP_DIV), max(1, grab.height() // BACKDROP_DIV)
        small = shot.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        blurred = blur_pixmap(small, BACKDROP_BLUR)
        solid = QPixmap(small)
        q = QPainter(solid)
        q.drawPixmap(0, 0, blurred)
        q.end()
        self._backdrop = solid
        self._backdrop_ch = split_channels(self._backdrop)
        self._backdrop_rect = grab
        self._backdrop_serial += 1
        self.update()

    def refresh_live_backdrop(self):
        if self._blocked or (not self._expanded and not self._pill_clear):
            return
        self.capture_backdrop(self._expanded_rect() if self._expanded else self._collapsed_rect())

    # --------------------------------------------------------- system info #
    def update_clock(self):
        now = dt.datetime.now()
        self.calendar.set_date(now.date())
        self._refresh_ring(now)
        if self.detail.isVisible():
            self._refresh_detail_panel(now)

    def update_battery(self):
        try:
            self._battery = read_battery()
        except Exception:
            self._battery = None
        self._refresh_ring()
        if self.detail.isVisible():
            self._refresh_detail_panel()

    def _refresh_ring(self, now=None):
        now = now or dt.datetime.now()
        self.ring.set_data(now.strftime("%I:%M").lstrip("0"), now.strftime("%p"), self._battery)
        if not self._expanded and self._pill_clear:
            self.update()

    # --------------------------------------------------------------- media #
    def _set_media_visible(self, on: bool):
        for w in (self.album_label, self.artist_label, self.prev_btn, self.play_btn,
                  self.next_btn, self.slider, self.current_time_label, self.total_time_label,
                  self.eq):
            w.setVisible(on)
        self.art_widget.set_badge(None, not on, "")
        self.art_widget.set_interactive(on)
        self.title_label.setGeometry(HX, 45 if on else 68, HW, 19)
        self.media_hit.setVisible(on)

    def open_media_app(self):
        if not self._has_media:
            return
        try:
            open_media_source(self._source, self._title, int(self.winId()))
        except Exception:
            pass

    def apply_state(self, st: MediaState):
        was_playing = self._playing
        estimate = self.estimated_position()
        self._has_media = st.has_media
        self._source = st.source
        self._playing = st.playing
        self._duration = st.duration

        if st.has_media:
            changed = (st.title, st.artist, st.album) != (self._title, self._artist, self._album)
            holding = time.monotonic() < self._seek_hold
            if not self._dragging and (changed or st.playing != was_playing or not st.playing
                                       or (abs(st.position - estimate) > 0.8 and not holding)):
                self._pos_base, self._pos_t0 = st.position, time.monotonic()
            self._title, self._artist, self._album = st.title, st.artist, st.album

            self.title_label.set_full_text(st.title)
            self.album_label.set_full_text(st.album or st.artist)
            self.artist_label.set_full_text(st.artist.upper() if st.album else "")
            self._set_media_visible(True)
            self.art_widget.set_badge(
                source_style(st.source) or self._tint_to[0].lighter(160), False, st.source
            )
            self.play_btn.set_kind("pause" if st.playing else "play")
            self.slider.setEnabled(st.duration > 0)
            self._apply_art(st)
        else:
            self._title = self._artist = self._album = ""
            self.title_label.set_full_text("Nothing playing")
            self._set_media_visible(False)
            self._apply_art(MediaState())
        for w in (self.eq, self.slider, self.art_widget):
            w.set_playing(st.has_media and st.playing)
        self.title_label.set_shine(st.has_media and st.playing)
        self.detail.set_media(self._title, self._artist, self._album, self._source,
                              self._playing, self._duration)
        if self._has_media:
            self._request_lyrics()
        else:
            self._lyrics_key = ""
            self.detail.set_lyrics({"status": "none", "lines": [], "plain": ""})
        self.tick_smooth_progress(force=True)

    def _apply_art(self, st: MediaState):
        sig = (st.art_key, bool(st.art))
        if sig == self._art_sig:
            return
        self._art_sig = sig
        raw = QPixmap()
        if st.art:
            raw.loadFromData(st.art)
        self._art_raw = QPixmap(raw)
        self.detail.set_art(raw)
        self.art_widget.set_art(raw)
        self._tint_from = self.current_tints()
        self._wash_prev, self._wash = self._wash, make_wash(raw)
        self._tint_to = extract_palette(raw)
        self.art_widget.set_glow(pastel(self._tint_to[0], 0.9, 0.95))
        self._mix = 0.0
        self.tint_anim.stop()
        self.tint_anim.start()

    def current_tints(self) -> tuple[QColor, QColor]:
        return (lerp_color(self._tint_from[0], self._tint_to[0], self._mix),
                lerp_color(self._tint_from[1], self._tint_to[1], self._mix))

    def _push_tints(self):
        c1, c2 = self.current_tints()
        self.slider.set_tints(c1, c2)
        self.eq.set_tints(c1, c2)
        self.calendar.set_tints(c1, c2)
        self.ring.set_tints(c1, c2)
        self.weather.set_tints(c1, c2)
        if getattr(self, "agent_card", None) is not None:
            self.agent_card.set_tints(c1, c2)
        self.bluetooth_page.set_tints(c1, c2)
        self.capture_page.set_tints(c1, c2)
        self.focus_page.set_tints(c1, c2)
        self.agent_page.set_tints(c1, c2)
        self.detail.set_tints(c1, c2)

    def _on_mix(self, v):
        self._mix = float(v)
        self._push_tints()
        self.update()

    # ---------------------------------------------------- progress / slider #
    def estimated_position(self) -> float:
        pos = self._pos_base + ((time.monotonic() - self._pos_t0) if self._playing else 0.0)
        if self._duration > 0:
            pos = min(pos, self._duration)
        return max(0.0, pos)

    def tick_smooth_progress(self, force: bool = False):
        if not (self._expanded or force) or self._dragging:
            return
        pos = self.estimated_position()
        detail = getattr(self, "detail", None)
        if detail is not None and not detail.isHidden():
            detail.set_playback(pos, self._duration, self._playing)
        if self._duration > 0:
            self.slider.setValue(int(pos / self._duration * 1000))
        cur = self.format_time(pos)
        tot = self.format_time(self._duration) if self._duration > 0 else "—:—"
        if (cur, tot) != self._last_time_text:
            self._last_time_text = (cur, tot)
            self.current_time_label.set_full_text(cur)
            self.total_time_label.set_full_text(tot)

    def _on_slider_stretch(self, left_dx: float, right_dx: float):
        self.current_time_label.move(HX + int(round(left_dx)), 130)
        self.total_time_label.move(HX + HW - 50 + int(round(right_dx)), 130)

    def _on_slider_released(self):
        target = self.slider.value() / 1000 * self._duration
        self._dragging = False
        self._pos_base, self._pos_t0 = target, time.monotonic()
        self.worker.command("seek", target)

    @staticmethod
    def format_time(seconds: float) -> str:
        s = int(max(0, seconds))
        h, rem = divmod(s, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # ------------------------------------------------------------- motion #
    def _apply_reveal(self):
        R = self._reveal_t
        plan = ((self.art_widget, 0.00, 0.55), (self.title_label, 0.06, 0.45),
                (self.album_label, 0.10, 0.45), (self.artist_label, 0.14, 0.45),
                (self.prev_btn, 0.16, 0.40), (self.play_btn, 0.20, 0.40),
                (self.next_btn, 0.24, 0.40), (self.eq, 0.26, 0.40), (self.slider, 0.28, 0.45),
                (self.current_time_label, 0.30, 0.40), (self.total_time_label, 0.30, 0.40),
                (self.calendar, 0.10, 0.90), (self.ring, 0.22, 0.80),
                (self.weather, 0.26, 0.50), (self.agent_card, 0.02, 0.60),
                (self.focus_page, 0.04, 0.95), (self.agent_page, 0.04, 0.95),
                (self.dividers[0], 0.12, 0.5), (self.dividers[1], 0.20, 0.5))
        for w, delay, dur in plan:
            w.set_reveal(seg(R, delay, dur))

    def _tick_fx(self):
        now = time.monotonic()
        dt_ = min(0.05, now - self._fx_last)
        self._fx_last = now
        self._t += dt_
        if self._reveal_t < 1.5:
            self._reveal_t += dt_
            self._apply_reveal()
        if self._sweep < 1.0:
            self._sweep = min(1.0, self._sweep + dt_ / 0.95)
        self._energy += ((1.0 if self._playing else 0.45) - self._energy) * min(1.0, dt_ * 2.5)
        for w in self._fx_widgets:
            w.tick(dt_)
        if self.detail.isVisible() and self._detail_kind == "media":
            self.detail.set_playback(self.estimated_position(), self._duration, self._playing)
        self.update()

    # ------------------------------------------------------------- painting #
    def _layer(self, w: int, h: int) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(max(1, int(w * dpr)), max(1, int(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        return pm

    def _build_glass(self, w: int, h: int, r: QRectF, radius: float, shape: QPainterPath) -> QPixmap:
        pm = self._layer(w, h)
        q = QPainter(pm)
        q.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        q.setClipPath(shape)

        q.fillRect(QRect(0, 0, w, h), QColor(255, 255, 255, BASE_FILL_ALPHA))

        if not self._backdrop.isNull():
            cx, cy = max(w / 2.0, 1.0), max(h / 2.0, 1.0)
            bd = QRectF(self._backdrop_rect.translated(-self.geometry().topLeft()))

            def view(shift: float) -> QRectF:
                zx, zy = 1.0 + shift / cx, 1.0 + shift / cy
                return QRectF(cx + (bd.x() - cx) * zx, cy + (bd.y() - cy) * zy,
                              bd.width() * zx, bd.height() * zy)

            q.drawPixmap(view(FG_CENTER_SHIFT), self._backdrop, QRectF(self._backdrop.rect()))

            if self._backdrop_ch:
                insets = [0.0] + [float(b[0]) for b in FG_BANDS]
                paths = [notch_path(r.adjusted(i, 0, -i, -i), max(0.0, radius - i)) for i in insets]
                bands = [(paths[k].subtracted(paths[k + 1]), FG_BANDS[k][1])
                         for k in range(len(FG_BANDS))]
                lens = self._layer(w, h)
                lp = QPainter(lens)
                lp.setRenderHint(QPainter.SmoothPixmapTransform)
                lp.setCompositionMode(QPainter.CompositionMode_Plus)
                for path, shift in bands:
                    lp.save()
                    lp.setClipPath(path)
                    for ch, k in zip(self._backdrop_ch, (1.0 + FG_CA, 1.0, 1.0 - FG_CA)):
                        lp.drawPixmap(view(shift * k), ch, QRectF(ch.rect()))
                    lp.restore()
                lp.end()
                q.drawPixmap(0, 0, lens)
        q.end()
        return pm

    def _paint_aurora(self, p: QPainter, w: int, h: int, c1: QColor, c2: QColor):
        t, e = self._t, self._energy
        a, b = pastel(c1, 0.85, 0.95), pastel(c2, 0.85, 0.95)
        mid = lerp_color(a, b, 0.5)
        blobs = ((a, 0.20 + 0.16 * math.sin(t * 0.55), 0.45 + 0.30 * math.cos(t * 0.40), 0.52),
                 (b, 0.78 + 0.14 * math.cos(t * 0.47), 0.55 + 0.30 * math.sin(t * 0.62), 0.56),
                 (mid, 0.50 + 0.30 * math.sin(t * 0.33 + 1.3), 0.30 + 0.25 * math.cos(t * 0.71), 0.42))
        for col, fx, fy, fr in blobs:
            rad = w * fr * 0.5
            g = QRadialGradient(0, 0, rad)
            g.setColorAt(0.0, QColor(col.red(), col.green(), col.blue(), int(78 * e)))
            g.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            p.save()
            p.translate(w * fx, h * fy)
            p.scale(1.0, 0.55)
            p.fillRect(QRectF(-rad, -rad, 2 * rad, 2 * rad), g)
            p.restore()

    def _paint_collapsed_pill(self, p: QPainter, w: int, h: int, c2: QColor, alpha: float):
        if alpha <= 0.01:
            return
        if self.rec_hud.isVisible():
            return
        p.save()
        p.setOpacity(alpha)

        now = dt.datetime.now()
        time_str = now.strftime("%I:%M").lstrip("0")
        ampm_str = now.strftime("%p")
        date_str = f"{now:%a} · {now:%b} {now.day}".upper()
        focus = getattr(self, "focus", None)
        focus_on = bool(focus is not None and focus.active)
        agent = getattr(self, "_agent", None) or {}
        agent_busy = bool(agent.get("busy"))
        waiting = bool(agent.get("waiting")) and agent_busy
        dot_col = None
        if agent_busy:
            ap_kind = str(agent.get("kind") or "AGENT").upper()
            if waiting:
                status_str = f"{ap_kind} · NEEDS YOU"
            elif agent.get("app") and not agent.get("foreground", True):
                status_str = f"{ap_kind} · WORKING"
            else:
                word = (agent.get("action") or "WORKING").upper()
                short = (agent.get("detail") or "").strip()
                status_str = f"{ap_kind} · {word}"
                if short:
                    status_str = f"{status_str} {short}"
            dot_col = QColor(255, 186, 92) if waiting else QColor(178, 146, 255)
        elif focus_on:
            status_str = "FOCUS · PAUSED" if focus.paused else "FOCUS SESSION"
            dot_col = QColor(255, 186, 92) if focus.paused else pastel(c2, 0.78)
        else:
            status_str = date_str
        beat = 0.5 + 0.5 * math.sin(time.monotonic() * 4.2)

        p.setFont(ui_font(14, QFont.Weight.Bold, True))
        tw = p.fontMetrics().horizontalAdvance(time_str)
        p.setPen(QColor(0, 0, 0, 120))
        p.drawText(QRectF(16, 1, tw + 4, h), Qt.AlignLeft | Qt.AlignVCenter, time_str)
        p.setPen(QColor(255, 255, 255, 248))
        p.drawText(QRectF(16, 0, tw + 4, h), Qt.AlignLeft | Qt.AlignVCenter, time_str)

        p.setFont(ui_font(9, QFont.Weight.DemiBold, True, 0.5))
        p.setPen(QColor(255, 255, 255, 175))
        p.drawText(QRectF(16 + tw + 3, 1, 24, h), Qt.AlignLeft | Qt.AlignVCenter, ampm_str)

        d = 28.0
        disc = QRectF(w - 12 - d, (h - d) / 2.0, d, d)
        left_x = 16 + tw + 26
        right_x = disc.x() - 8

        p.setPen(QPen(QColor(255, 255, 255, 45), 1))
        p.drawLine(QPointF(left_x - 3, 12), QPointF(left_x - 3, h - 12))

        # Robo lives on the pill: happy when idle, focused at work, wide-eyed when you are needed
        idle = dot_col is None
        robo = pastel(c2, 0.7) if idle else dot_col
        now_s = time.monotonic()
        cx = left_x + 9.0 + (math.sin(now_s * 22.0) * 0.9 if waiting else 0.0)
        cy = h / 2.0 + 0.5 + math.sin(now_s * 2.4) * (0.8 if idle else 0.4)
        blink = (now_s % 4.2) < 0.13
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(robo, int((20 if idle else 34) + (30 if idle else 60) * beat)))
        p.drawRoundedRect(QRectF(cx - 10.5, cy - 8.5, 21, 17), 7, 7)
        p.setBrush(QColor(12, 15, 24, 238))
        p.setPen(QPen(with_alpha(robo, 235), 1.2))
        p.drawRoundedRect(QRectF(cx - 7.5, cy - 5.5, 15, 11), 4.5, 4.5)
        p.drawLine(QPointF(cx, cy - 5.5), QPointF(cx, cy - 7.8))
        p.setPen(Qt.NoPen)
        p.setBrush(with_alpha(robo, 255))
        p.drawEllipse(QPointF(cx, cy - 8.2), 1.1, 1.1)
        if idle and not blink:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(robo, 1.3, Qt.SolidLine, Qt.RoundCap))
            for dx in (-3.0, 3.0):
                p.drawArc(QRectF(cx + dx - 1.8, cy - 1.0, 3.6, 3.4), 0, 180 * 16)
        else:
            p.setBrush(robo)
            eye_h = 1.6 if blink else (5.0 if waiting else 3.4)
            for dx, lift in ((-3.0, 0.0), (3.0, -1.1 if waiting else 0.0)):
                p.drawRoundedRect(QRectF(cx + dx - 1.4, cy - eye_h / 2.0 + lift, 2.8, eye_h),
                                  1.4, 1.4)
        pad = 20
        region = QRectF(left_x + pad, 0, max(24.0, right_x - left_x - pad - 2), h)
        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 0.9))
        fitted = p.fontMetrics().elidedText(status_str, Qt.ElideRight, int(region.width()))
        head, sep, tail = fitted.partition(" · ")
        if sep and head and tail:
            fm = p.fontMetrics()
            head_w = fm.horizontalAdvance(head + sep)
            x0 = region.center().x() - (head_w + fm.horizontalAdvance(tail)) / 2.0
            head_col = QColor(robo) if not idle else pastel(c2, 0.85)
            for txt, x, col in ((head + sep, x0, head_col),
                                (tail, x0 + head_w, QColor(255, 255, 255, 215))):
                cell = QRectF(x, 0, fm.horizontalAdvance(txt) + 2, h)
                p.setPen(QColor(0, 0, 0, 110))
                p.drawText(cell.translated(0, 1), Qt.AlignLeft | Qt.AlignVCenter, txt)
                p.setPen(col)
                p.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter, txt)
        else:
            p.setPen(QColor(0, 0, 0, 110))
            p.drawText(region.translated(0, 1), Qt.AlignCenter, fitted)
            p.setPen(QColor(255, 255, 255, 215))
            p.drawText(region, Qt.AlignCenter, fitted)

        p.setBrush(QColor(12, 15, 24, 155))
        p.setPen(QPen(QColor(255, 255, 255, 55), 1))
        p.drawEllipse(disc)

        arc = disc.adjusted(2.5, 2.5, -2.5, -2.5)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 40), 2.5))
        p.drawEllipse(arc)

        if focus_on:
            frac = clamp01(focus.remaining / max(1.0, focus.total))
            col = QColor(255, 178, 92) if focus.paused else pastel(c2, 0.78)
            p.setPen(QPen(col, 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, 90 * 16, int(-360 * 16 * frac))
            left = focus.remaining
            label = f"{int(left // 60)}" if left >= 60 else focus_clock(left)
            p.setFont(ui_font(9 if len(label) <= 3 else 7, QFont.Weight.Bold, True))
            p.setPen(QColor(0, 0, 0, 190))
            p.drawText(disc.translated(0, 1), Qt.AlignCenter, label)
            p.setPen(QColor(255, 255, 255, 250))
            p.drawText(disc, Qt.AlignCenter, label)
        elif self._battery is not None:
            pct, charging = self._battery
            low = pct <= 20 and not charging
            ring_col = (QColor(255, 105, 95) if low else
                        QColor(80, 220, 130) if charging else
                        pastel(c2, 0.7))
            span = int(-360 * 16 * max(0.02, pct / 100.0))
            p.setPen(QPen(with_alpha(ring_col, 46 + int(30 * beat)), 5.0, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, 90 * 16, span)
            p.setPen(QPen(ring_col, 2.5, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, 90 * 16, span)

            p.setFont(ui_font(8 if pct >= 100 else 9, QFont.Weight.Bold, True))
            label = f"{pct}%"
            p.setPen(QColor(0, 0, 0, 180))
            p.drawText(disc.translated(0, 1), Qt.AlignCenter, label)
            p.setPen(QColor(255, 255, 255, 250))
            p.drawText(disc, Qt.AlignCenter, label)
        else:
            p.setFont(ui_font(9, QFont.Weight.Bold, True))
            p.setPen(QColor(255, 255, 255, 180))
            p.drawText(disc, Qt.AlignCenter, "—")

        if focus_on and focus.progress > 0.004:
            bar_w = max(0.0, (w - 24.0) * clamp01(focus.progress))
            p.setPen(Qt.NoPen)
            p.setBrush(with_alpha(pastel(c2, 0.85), 190))
            p.drawRoundedRect(QRectF(12, h - 3.2, bar_w, 1.7), 0.85, 0.85)

        # living edge: a soft light glides along the bottom lip (faster while an agent works)
        sweep_x = ((now_s * (0.55 if agent_busy else 0.22)) % 1.6 - 0.3) * w
        lip = QLinearGradient(sweep_x - 70, 0, sweep_x + 70, 0)
        lip.setColorAt(0.0, with_alpha(robo, 0))
        lip.setColorAt(0.5, with_alpha(robo, 210 if agent_busy else 120))
        lip.setColorAt(1.0, with_alpha(robo, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(lip)
        p.drawRoundedRect(QRectF(16, h - 2.4, w - 32, 1.4), 0.7, 0.7)
        if waiting:                                  # the pill breathes when it needs you
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(with_alpha(robo, int(40 + 100 * beat)), 1.4))
            p.drawPath(notch_path(QRectF(1.2, 0, w - 2.4, h - 1.5), min(BOTTOM_RADIUS, h / 2) - 1,
                                  closed=False))

        p.restore()

    def _paint_section_labels(self, p: QPainter):
        """Home page cards: the now-playing and system panes (the agent card paints itself)."""
        if not self.detail.isHidden() or self.home_page.isHidden():
            return
        c1, c2 = self.current_tints()
        accent = pastel(c2, 0.85, 1.0)
        p.save()
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                         | QPainter.SmoothPixmapTransform)
        offset = self.content.x()
        for rect, tint in ((MUSIC_CARD, c1), (SYS_CARD, c2)):
            card = QRectF(rect).translated(offset, 0).adjusted(0.5, 0.5, -0.5, -0.5)
            body = QLinearGradient(card.topLeft(), card.bottomLeft())
            body.setColorAt(0.0, QColor(255, 255, 255, 34))
            body.setColorAt(1.0, QColor(255, 255, 255, 12))
            p.setPen(Qt.NoPen)
            p.setBrush(body)
            p.drawRoundedRect(card, 18, 18)
            p.save()
            clip = QPainterPath()
            clip.addRoundedRect(card, 18, 18)
            p.setClipPath(clip)
            wash = QLinearGradient(card.topLeft(), card.bottomRight())
            wash.setColorAt(0.0, with_alpha(tint, 58))
            wash.setColorAt(0.5, with_alpha(tint, 10))
            wash.setColorAt(1.0, with_alpha(tint, 0))
            p.setBrush(wash)
            p.drawRect(card)
            p.restore()
            rim = QLinearGradient(card.topLeft(), card.bottomRight())
            rim.setColorAt(0.0, QColor(255, 255, 255, 74))
            rim.setColorAt(1.0, with_alpha(tint, 40))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QBrush(rim), 1.0))
            p.drawRoundedRect(card, 18, 18)

        # date + weather column of the system card
        today = dt.date.today()
        d = DATE_RECT.translated(offset, 0)
        hair = QLinearGradient(0, d.top(), 0, d.bottom())
        hair.setColorAt(0.0, QColor(255, 255, 255, 0))
        hair.setColorAt(0.5, QColor(255, 255, 255, 46))
        hair.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(QPen(QBrush(hair), 1.0))
        p.drawLine(QPointF(d.left() - 4, d.top() + 6), QPointF(d.left() - 4, d.bottom() - 6))

        def shadowed(rect, flags, text, col):
            p.setPen(QColor(0, 0, 0, 110))
            p.drawText(rect.translated(0, 1.2), flags, text)
            p.setPen(col)
            p.drawText(rect, flags, text)

        p.setFont(ui_font(8, QFont.Weight.Bold, True, 1.6))
        p.setPen(with_alpha(accent, 235))
        p.drawText(QRectF(d.left(), d.top() + 1, 50, 11), Qt.AlignLeft | Qt.AlignVCenter,
                   today.strftime("%a").upper())
        self._cue.paint(p, QRect(int(d.right()) - 9, int(d.top()) + 2, 9, 9))
        p.setFont(ui_font(36, QFont.Weight.Bold, True))
        shadowed(QRectF(d.left() - 1, d.top() + 12, 72, 44), Qt.AlignLeft | Qt.AlignVCenter,
                 str(today.day), QColor(255, 255, 255, 250))
        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 0.4))
        p.setPen(QColor(255, 255, 255, 214))
        p.drawText(QRectF(d.left(), d.top() + 56, 70, 13), Qt.AlignLeft | Qt.AlignVCenter,
                   today.strftime("%B"))
        state = getattr(self.weather, "_state", None)
        if state is not None:
            kind, _label = weather_visual(int(state.code), bool(state.is_day))
            paint_weather_icon(p, QRectF(d.left() - 1, d.top() + 72, 17, 17), kind, accent)
            p.setFont(ui_font(11, QFont.Weight.Bold, True))
            p.setPen(QColor(255, 255, 255, 238))
            p.drawText(QRectF(d.left() + 19, d.top() + 71, 48, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{round(state.temp)}°")
        else:
            p.setFont(ui_font(9, QFont.Weight.Medium, True, 0.6))
            p.setPen(QColor(255, 255, 255, 130))
            p.drawText(QRectF(d.left(), d.top() + 72, 68, 14), Qt.AlignLeft | Qt.AlignVCenter,
                       str(today.year))
        p.restore()

    def _paint_top_trays(self, p: QPainter, w: int, h: int):
        """Two faint glass trays hold the nav and the capture buttons."""
        if self.rec_hud.isVisible():
            return
        open_frac = clamp01((w - COLLAPSED.width()) / max(1, W - COLLAPSED.width()))
        if open_frac < 0.55:
            return
        alpha = int(52 * clamp01((open_frac - 0.55) / 0.45))
        if alpha <= 2:
            return
        p.save()
        p.setPen(QPen(QColor(255, 255, 255, alpha // 2), 1.0))
        grad = QLinearGradient(0, 3, 0, 34)
        grad.setColorAt(0.0, QColor(255, 255, 255, alpha))
        grad.setColorAt(1.0, QColor(255, 255, 255, max(4, alpha - 22)))
        p.setBrush(grad)
        offset = self.content.x()
        nav = QRectF(self._nav_rect).translated(offset, 0)
        if self.back_btn.isVisible() or self.inner_btn.isVisible():
            nav.setRight(nav.right() + 31)                      # tray grows to hold the button
            p.drawRoundedRect(nav, nav.height() / 2, nav.height() / 2)
            p.setPen(QPen(QColor(255, 255, 255, alpha), 1.0))
            x = self._nav_rect.right() + offset + 1.0
            p.drawLine(QPointF(x, 10), QPointF(x, 27))          # hairline before the button
            p.setPen(QPen(QColor(255, 255, 255, alpha // 2), 1.0))
        else:
            p.drawRoundedRect(nav, nav.height() / 2, nav.height() / 2)
        cap = QRectF(self._cap_rect).translated(offset, 0)
        p.drawRoundedRect(cap, cap.height() / 2, cap.height() / 2)
        p.restore()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        w, h = self.width(), self.height()
        r = QRectF(0.75, 0, w - 1.5, h - 0.75)
        radius = min(BOTTOM_RADIUS, h / 2)
        shape = notch_path(r, radius)
        rim = notch_path(r, radius, closed=False)
        c1, c2 = self.current_tints()

        key = (w, h, self.x(), self.y(), self._backdrop_serial, self.devicePixelRatioF())
        if key != self._glass_key:
            self._glass_key = key
            self._glass_cache = self._build_glass(w, h, r, radius, shape)
        p.drawPixmap(0, 0, self._glass_cache)

        p.save()
        p.setClipPath(shape)

        for pm, op in ((self._wash_prev, (1 - self._mix) * 0.20), (self._wash, self._mix * 0.20)):
            if not pm.isNull() and op > 0.01:
                p.setOpacity(op)
                p.drawPixmap(QRectF(0, 0, w, h), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
        tint = QLinearGradient(0, 0, w, 0)
        tint.setColorAt(0.0, with_alpha(c1, 26))
        tint.setColorAt(1.0, with_alpha(c2, 26))
        p.fillRect(self.rect(), tint)

        self._paint_aurora(p, w, h, c1, c2)

        veil = QLinearGradient(0, 0, 0, h)
        veil.setColorAt(0.0, QColor(0, 0, 0, 24))
        veil.setColorAt(1.0, QColor(0, 0, 0, 54))
        p.fillRect(self.rect(), veil)

        glare = QRadialGradient(w * 0.10, h * 0.08, w * 0.45)
        glare.setColorAt(0.0, QColor(255, 255, 255, 40))
        glare.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), glare)

        if 0.0 < self._sweep < 1.0:
            s = ease_out_cubic(self._sweep)
            x0 = -0.25 * w + 1.5 * w * s
            band = QLinearGradient(x0 - 70, 0, x0 + 70, 46)
            peak = int(58 * math.sin(math.pi * self._sweep))
            band.setColorAt(0.0, QColor(255, 255, 255, 0))
            band.setColorAt(0.5, QColor(255, 255, 255, peak))
            band.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.fillRect(self.rect(), band)

        pill_alpha = 1.0 - clamp01(((w - COLLAPSED.width()) / max(1, W - COLLAPSED.width())) * 3.0)
        self._paint_collapsed_pill(p, w, h, c2, pill_alpha)
        self._paint_top_trays(p, w, h)
        if w > W - 40:
            self._paint_section_labels(p)
        p.restore()

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 46), 1.0))
        p.drawPath(rim)
        inner = notch_path(r.adjusted(1.5, 0, -1.5, -1.5), max(0.0, radius - 1.5), closed=False)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        p.drawPath(inner)

    # ------------------------------------------------------------ shutdown #
    def shutdown(self):
        self.edge_timer.stop()
        self.env_timer.stop()
        self.fx_timer.stop()
        self.hud_timer.stop()
        self.pill_timer.stop()
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            recorder.request_stop()
            recorder.wait(4000)
        audio, self._audio = self._audio, None
        if audio is not None:
            audio.close()
        if self._settings is not None:
            self._settings.hide()
            self._settings.deleteLater()
            self._settings = None
        try:
            self.agent_watch.stop()
        except Exception:
            pass
        for thread in list(self._lyrics_threads):
            try:
                thread.requestInterruption()
                thread.wait(4500)
            except Exception:
                pass
        self._lyrics_threads.clear()
        self.worker.stop()
        self.tray.hide()


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_REG_NAME = "LiquifyIsland"


def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    py_exe = Path(sys.executable).resolve()
    pythonw = py_exe.with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else py_exe
    return f'"{runner}" "{Path(__file__).resolve()}"'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, _APP_REG_NAME)
            return bool(val)
    except OSError:
        return False


def set_autostart(enable: bool) -> bool:
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, _APP_REG_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(key, _APP_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def refresh_autostart_path() -> None:
    """Point an existing Run entry at the current .exe after a rebuild or move."""
    try:
        if not is_autostart_enabled():
            return
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            current, _ = winreg.QueryValueEx(key, _APP_REG_NAME)
        if str(current) != _startup_command():
            set_autostart(True)
    except OSError:
        pass


def load_custom_fonts():
    global _CUSTOM_FAMILIES
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    folder = base / "fonts"
    if not folder.is_dir():
        folder = Path(sys.executable).resolve().parent / "fonts"
    if not folder.is_dir():
        return
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in (".ttf", ".otf"):
            QFontDatabase.addApplicationFont(str(f))
    have = set(QFontDatabase.families())
    _CUSTOM_FAMILIES = [f for f in PREFERRED_DISPLAY if f in have]


def _replace_existing_instance():
    """Terminate any older Liquify Island process so the updated script takes over cleanly."""
    pid_file = Path(QDir.temp().filePath("liquify_island.pid"))
    try:
        if pid_file.exists():
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
            if old_pid and old_pid != os.getpid():
                h = _kernel32.OpenProcess(0x0001, False, old_pid)  # PROCESS_TERMINATE
                if h:
                    _kernel32.TerminateProcess(h, 0)
                    _kernel32.CloseHandle(h)
                    time.sleep(0.15)
    except Exception:
        pass
    try:
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    try:
        app = QApplication(sys.argv)
        app.setApplicationName("Liquify Island")
        app.setQuitOnLastWindowClosed(False)
        load_custom_fonts()
        refresh_autostart_path()
        _replace_existing_instance()
        lock = QLockFile(QDir.temp().filePath("liquify_island.lock"))
        lock.setStaleLockTime(500)
        if not lock.tryLock(500):
            lock.removeStaleLockFile()
            if not lock.tryLock(500):
                return 0
        island = LiquidIsland()
        island.show()
        app.aboutToQuit.connect(island.shutdown)
        code = app.exec()
        lock.unlock()
        return code
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        _user32.MessageBoxW(None, err, "Liquify Island Startup Error", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(main())