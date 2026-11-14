from __future__ import annotations

import asyncio
import ctypes
import datetime as dt
import math
import os
import random
import subprocess
import sys
import time
import traceback
from ctypes import wintypes
from dataclasses import dataclass
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
except ImportError:
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager,
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
        )
        from winrt.windows.storage.streams import Buffer, DataReader, InputStreamOptions
        HAVE_MEDIA_SDK = True
    except ImportError:  # pragma: no cover
        HAVE_MEDIA_SDK = False

from PySide6.QtCore import (
    QDir, QEasingCurve, QLockFile, QPoint, QPointF,
    QRect, QRectF, QSize, Qt, QThread, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QAction, QColor, QCursor, QFont, QFontDatabase, QGuiApplication,
    QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap, QPolygonF, QRadialGradient, QTransform,
)
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QGraphicsBlurEffect,
    QGraphicsPixmapItem, QGraphicsScene, QMenu,
    QSlider, QSystemTrayIcon, QWidget,
)

# --------------------------------------------------------------------------- #
# Geometry & Visual Constants
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
RING_RECT = QRect(543, 44, 90, 90)   # 9px clear of the right edge
SANS = ["Segoe UI Variable Text", "Segoe UI"]
DISPLAY = ["Bahnschrift", "Segoe UI Variable Display", "Segoe UI"]
PREFERRED_DISPLAY = [
    "Space Grotesk", "Sora", "Outfit", "Manrope",
    "Plus Jakarta Sans", "Inter Display", "Inter",
]
_CUSTOM_FAMILIES: list[str] = []
DEFAULT_TINT = QColor(34, 40, 62)
DEFAULT_TINT_2 = QColor(52, 60, 96)

BASE_FILL_ALPHA = 10                 # subtle frost tint over refracted backdrop
BACKDROP_BLUR = 1.5
BACKDROP_DIV = 2                     # backdrop captured at 1/2 resolution

# --- Fluid Glass Refraction & Chromatic Aberration
FG_IOR, FG_THICKNESS, FG_CHROMA = 1.15, 5.0, 0.1
_FG_REFRACT = (FG_IOR - 1.0) * FG_THICKNESS
FG_CENTER_SHIFT = _FG_REFRACT * 1.5
FG_BANDS = ((4, _FG_REFRACT * 14.0),
            (10, _FG_REFRACT * 7.0))
FG_CA = FG_CHROMA * 3.0

# --- Elastic Slider
ES_MAX_OVERFLOW = 22.0
ES_PAD = 22

# --------------------------------------------------------------------------- #
# Win32 Helpers & System Audio Mute (Core Audio COM + keybd_event fallback)
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
    """Hide from taskbar/Alt+Tab; exclude from screen capture (Win10 2004+)."""
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
# Graphics & Color Helpers
# --------------------------------------------------------------------------- #
def ui_font(px: int, weight=QFont.Weight.Normal, display: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont()
    f.setFamilies((_CUSTOM_FAMILIES + DISPLAY) if display else SANS)
    f.setPixelSize(px)
    f.setWeight(weight)
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
# Media State & Worker Thread
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._stop = None
        self._manager = None
        self._art_key, self._art, self._art_tries = "", b"", 0

    def run(self):
        hr = _ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
        did_com = hr in (0, 1)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        finally:
            loop.close()
            self._loop = None
            if did_com:
                try:
                    _ole32.CoUninitialize()
                except Exception:
                    pass

    async def _main(self):
        self._stop = asyncio.Event()
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
# Motion Helpers
# --------------------------------------------------------------------------- #
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
# UI Widgets
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

        cap = clamp01(rv * 1.6 - 0.35)
        p.setOpacity(ease_out_cubic(cap))
        cx = self.width() / 2
        self._icon.paint(p, QRect(int(cx - 7.5), 55, 15, 15))
        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 1.3))
        p.setPen(QColor(255, 255, 255, 175))
        p.drawText(QRectF(0, 72, self.width(), 16), Qt.AlignCenter,
                   f"{t:%A} · {t:%b} {t.day}".upper())


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


# --------------------------------------------------------------------------- #
# Main Island Window
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
        self._title = self._artist = self._album = ""
        self._art_sig = None
        self._dragging = False
        self._battery = None
        self._last_time_text = ("", "")

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

        self.update_battery()
        self.update_clock()
        self._sync_mute_state()
        self._set_media_visible(False)
        self._push_tints()
        self.content.hide()

        self.worker = MediaWorker(self)
        self.worker.state_changed.connect(self.apply_state)
        self.worker.start()

    # ------------------------------------------------------------------ UI #
    def _build_ui(self):
        self.content = QWidget(self)
        self.content.setFixedSize(W, H)

        self.tab_island = TabButton("island", "Island")
        self.tab_island.setParent(self.content)
        self.tab_island.move(12, 6)
        self.tab_island.setChecked(True)
        self.tab_island.clicked.connect(lambda: self.tab_island.setChecked(True))

        self.gear = GhostButton("gear", QSize(26, 26), 16)
        self.gear.setParent(self.content)
        self.gear.move(W - 12 - 26, 5)
        self.gear.clicked.connect(self.open_menu)

        self.home_page = QWidget(self.content)
        self.home_page.setGeometry(0, 0, W, H)

        self.art_widget = ArtWidget()
        self.art_widget.setParent(self.home_page)
        self.art_widget.move(ART_POS - QPoint(ART_M, ART_M))
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
            lbl.setGeometry(TEXT_X, y, TEXT_W, hh)

        self.media_hit = ClickArea(self.home_page)
        self.media_hit.setGeometry(TEXT_X, 45, TEXT_W, 46)
        self.media_hit.clicked.connect(self.open_media_app)

        self.prev_btn = GhostButton("prev", QSize(26, 26), 13)
        self.play_btn = GhostButton("play", QSize(34, 26), 15, solid=True)
        self.next_btn = GhostButton("next", QSize(26, 26), 13)
        for b, x in ((self.prev_btn, TEXT_X - 2), (self.play_btn, TEXT_X + 25),
                     (self.next_btn, TEXT_X + 60)):
            b.setParent(self.home_page)
            b.move(x, 91)
        self.prev_btn.clicked.connect(lambda: self.worker.command("prev"))
        self.play_btn.clicked.connect(lambda: self.worker.command("toggle"))
        self.next_btn.clicked.connect(lambda: self.worker.command("next"))

        self.eq = Equalizer()
        self.eq.setParent(self.home_page)
        self.eq.move(TEXT_X + TEXT_W - self.eq.width() + 3, 93)
        self.eq.clicked.connect(self._on_toggle_mute)

        self.slider = SeekSlider()
        self.slider.setParent(self.home_page)
        self.slider.setGeometry(TEXT_X - ES_PAD, 119, TEXT_W + 2 * ES_PAD, 12)
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
        self.current_time_label.setGeometry(TEXT_X, 130, 50, 12)
        self.total_time_label.setGeometry(TEXT_X + TEXT_W - 50, 130, 50, 12)
        self.current_time_label.set_full_text("0:00")
        self.total_time_label.set_full_text("0:00")

        self.dividers = []
        for x in (DIVIDER_1_X, DIVIDER_2_X):
            d = Divider()
            d.setParent(self.home_page)
            d.move(x, 46)
            self.dividers.append(d)

        self.calendar = CalendarColumn()
        self.calendar.setParent(self.home_page)
        self.calendar.move(CAL_RECT.topLeft())

        self.ring = RingGauge()
        self.ring.setParent(self.home_page)
        self.ring.move(RING_RECT.topLeft())

        self.title_label.set_full_text("Nothing playing")
        self.home_page.lower()
        self.tab_island.raise_()
        self.gear.raise_()

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
        q = QAction("Quit Liquify Island", self.tray_menu)
        q.triggered.connect(QApplication.quit)
        self.tray_menu.addAction(q)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

    def open_menu(self):
        self._menu_open = True
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: rgba(28,31,44,240); color: white; padding: 4px;"
            " border: 1px solid rgba(255,255,255,70); border-radius: 10px; }"
            "QMenu::item { padding: 6px 18px; border-radius: 6px; }"
            "QMenu::item:selected { background-color: rgba(255,255,255,40); }"
            "QMenu::separator { height: 1px; background: rgba(255,255,255,40); margin: 4px 8px; }")
        pin = menu.addAction("Keep open")
        pin.setCheckable(True)
        pin.setChecked(self._pinned)
        menu.addSeparator()
        quit_act = menu.addAction("Quit Liquify Island")
        chosen = menu.exec(self.gear.mapToGlobal(QPoint(0, self.gear.height() + 4)))
        self._menu_open = False
        if chosen is pin:
            self._pinned = pin.isChecked()
        elif chosen is quit_act:
            QApplication.quit()

    def _on_toggle_mute(self):
        muted = toggle_system_mute()
        self.eq.set_muted(muted)

    def _sync_mute_state(self):
        muted = get_system_mute()
        if muted is not None:
            self.eq.set_muted(muted)

    # ---------------------------------------------------------- Geometry --#
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
            curve.setOvershoot(0.9)
            self.anim.setDuration(460)
        else:
            curve = QEasingCurve(QEasingCurve.InOutCubic)
            self.anim.setDuration(260)
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

    # -------------------------------------------------------- Native Bits --#
    def showEvent(self, e):
        super().showEvent(e)
        try:
            self._capture_ok = configure_native_window(int(self.winId()))
        except Exception:
            self._capture_ok = False
        self.check_environment(force=True)

    # ------------------------------------------------- Show / Hide Logic --#
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
        self.animate_to(W, H, DOCK_Y)

    def collapse(self):
        self._expanded = False
        self._leave_since = None
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
        if self._blocked:
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
            or self.tab_island.underMouse()
            or self.gear.underMouse()
        ):
            self._leave_since = None
        elif self._leave_since is None:
            self._leave_since = time.monotonic()
        elif time.monotonic() - self._leave_since > HIDE_GRACE_S:
            self.collapse()

    def check_environment(self, force: bool = False):
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

    # ------------------------------------------------------------ Backdrop #
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

    # --------------------------------------------------------- System Info #
    def update_clock(self):
        now = dt.datetime.now()
        self.calendar.set_date(now.date())
        self._refresh_ring(now)

    def update_battery(self):
        try:
            self._battery = read_battery()
        except Exception:
            self._battery = None
        self._refresh_ring()

    def _refresh_ring(self, now=None):
        now = now or dt.datetime.now()
        self.ring.set_data(now.strftime("%I:%M").lstrip("0"), now.strftime("%p"), self._battery)
        if not self._expanded and self._pill_clear:
            self.update()

    # --------------------------------------------------------------- Media #
    def _set_media_visible(self, on: bool):
        for w in (self.album_label, self.artist_label, self.prev_btn, self.play_btn,
                  self.next_btn, self.slider, self.current_time_label, self.total_time_label,
                  self.eq):
            w.setVisible(on)
        self.art_widget.set_badge(None, not on, "")
        self.art_widget.set_interactive(on)
        self.title_label.setGeometry(TEXT_X, 45 if on else 68, TEXT_W, 19)
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
            if not self._dragging and (changed or st.playing != was_playing or not st.playing
                                       or abs(st.position - estimate) > 0.8):
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
        self.tick_smooth_progress(force=True)

    def _apply_art(self, st: MediaState):
        sig = (st.art_key, bool(st.art))
        if sig == self._art_sig:
            return
        self._art_sig = sig
        raw = QPixmap()
        if st.art:
            raw.loadFromData(st.art)
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

    def _on_mix(self, v):
        self._mix = float(v)
        self._push_tints()
        self.update()

    # ---------------------------------------------------- Progress / Slider #
    def estimated_position(self) -> float:
        pos = self._pos_base + ((time.monotonic() - self._pos_t0) if self._playing else 0.0)
        if self._duration > 0:
            pos = min(pos, self._duration)
        return max(0.0, pos)

    def tick_smooth_progress(self, force: bool = False):
        if not (self._expanded or force) or self._dragging:
            return
        pos = self.estimated_position()
        if self._duration > 0:
            self.slider.setValue(int(pos / self._duration * 1000))
        cur = self.format_time(pos)
        tot = self.format_time(self._duration) if self._duration > 0 else "—:—"
        if (cur, tot) != self._last_time_text:
            self._last_time_text = (cur, tot)
            self.current_time_label.set_full_text(cur)
            self.total_time_label.set_full_text(tot)

    def _on_slider_stretch(self, left_dx: float, right_dx: float):
        self.current_time_label.move(TEXT_X + int(round(left_dx)), 130)
        self.total_time_label.move(TEXT_X + TEXT_W - 50 + int(round(right_dx)), 130)

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

    # ------------------------------------------------------------- Motion #
    def _apply_reveal(self):
        R = self._reveal_t
        plan = ((self.art_widget, 0.00, 0.55), (self.title_label, 0.06, 0.45),
                (self.album_label, 0.10, 0.45), (self.artist_label, 0.14, 0.45),
                (self.prev_btn, 0.16, 0.40), (self.play_btn, 0.20, 0.40),
                (self.next_btn, 0.24, 0.40), (self.eq, 0.26, 0.40), (self.slider, 0.28, 0.45),
                (self.current_time_label, 0.30, 0.40), (self.total_time_label, 0.30, 0.40),
                (self.calendar, 0.10, 0.90), (self.ring, 0.22, 0.80),
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
        self.update()

    # ------------------------------------------------------------- Painting #
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
        p.save()
        p.setOpacity(alpha)

        now = dt.datetime.now()
        time_str = now.strftime("%I:%M").lstrip("0")
        ampm_str = now.strftime("%p")
        date_str = f"{now:%a} · {now:%b} {now.day}".upper()

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

        p.setFont(ui_font(10, QFont.Weight.DemiBold, True, 0.9))
        p.setPen(QColor(0, 0, 0, 110))
        p.drawText(QRectF(left_x, 1, right_x - left_x, h), Qt.AlignCenter, date_str)
        p.setPen(QColor(255, 255, 255, 215))
        p.drawText(QRectF(left_x, 0, right_x - left_x, h), Qt.AlignCenter, date_str)

        p.setBrush(QColor(12, 15, 24, 155))
        p.setPen(QPen(QColor(255, 255, 255, 55), 1))
        p.drawEllipse(disc)

        arc = disc.adjusted(2.5, 2.5, -2.5, -2.5)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 40), 2.5))
        p.drawEllipse(arc)

        if self._battery is not None:
            pct, charging = self._battery
            low = pct <= 20 and not charging
            ring_col = (QColor(255, 105, 95) if low else
                        QColor(80, 220, 130) if charging else
                        pastel(c2, 0.7))
            span = int(-360 * 16 * max(0.02, pct / 100.0))
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
        p.restore()

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, 46), 1.0))
        p.drawPath(rim)
        inner = notch_path(r.adjusted(1.5, 0, -1.5, -1.5), max(0.0, radius - 1.5), closed=False)
        p.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
        p.drawPath(inner)

    # ------------------------------------------------------------ Shutdown #
    def shutdown(self):
        self.edge_timer.stop()
        self.env_timer.stop()
        self.fx_timer.stop()
        self.worker.stop()
        self.tray.hide()


def load_custom_fonts():
    global _CUSTOM_FAMILIES
    folder = Path(__file__).resolve().parent / "fonts"
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