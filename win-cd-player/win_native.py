# -*- coding: utf-8 -*-
"""
win_native.py
Windows 原生窗口与多媒体热键封装：
1. 进程/窗口级高 DPI 感知与缩放比例读取
2. Windows 11 DWM 原生圆角、暗色标题栏属性与 WinForms/WebView2 底色同步
3. 无边框窗体 8 向边缘拖拽缩放（WM_SYSCOMMAND SC_SIZE）
4. 全局多媒体按键监听（WH_KEYBOARD_LL + 后台消息循环）
"""

import ctypes
from ctypes import wintypes
import threading
from typing import Callable, Optional


def enable_dpi_awareness():
    """在读取屏幕指标或创建窗口前启用进程级 DPI 感知，统一物理/逻辑像素转换基准"""
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_dpi_scale(hwnd: int = 0) -> float:
    """获取指定窗口或主显示器的 DPI 缩放系数（96 DPI = 1.0）"""
    try:
        user32 = ctypes.windll.user32
        if hwnd and hasattr(user32, "GetDpiForWindow"):
            dpi = int(user32.GetDpiForWindow(hwnd) or 0)
            if dpi > 0:
                return dpi / 96.0
        if hasattr(user32, "GetDpiForSystem"):
            dpi = int(user32.GetDpiForSystem() or 0)
            if dpi > 0:
                return dpi / 96.0
    except Exception:
        pass
    return 1.0


def apply_dwm_rounded_dark_window(
    native,
    theme_mode: str,
    on_hwnd: Optional[Callable[[int], None]] = None,
):
    """在 WinForms UI 线程缓存窗口句柄，并启用 Windows 11 原生 DWM 圆角与匹配当前主题的属性"""
    if native is None:
        return
    try:
        norm_theme = "light" if str(theme_mode or "").strip().lower() == "light" else "dark"

        def _apply():
            try:
                hwnd = int(native.Handle.ToInt64()) if hasattr(native, "Handle") else 0
                if hwnd:
                    if on_hwnd:
                        on_hwnd(hwnd)
                    dwmapi = ctypes.windll.dwmapi
                    pref = ctypes.c_int(2)  # DWMWCP_ROUND
                    dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
                    dark_pref = ctypes.c_int(1 if norm_theme == "dark" else 0)
                    dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_pref), ctypes.sizeof(dark_pref))
            except Exception:
                pass

        if getattr(native, "InvokeRequired", False):
            import System
            native.BeginInvoke(System.Action(_apply))
        else:
            _apply()
    except Exception:
        pass


def sync_native_window_theme(
    native,
    cached_hwnd: int,
    mode: str,
    on_hwnd: Optional[Callable[[int], None]] = None,
):
    """同步原生窗体 DWM 深浅色属性及 WinForms/WebView2 底色"""
    if native is None:
        return
    try:
        norm = "light" if str(mode or "").strip().lower() == "light" else "dark"
        import System
        import System.Drawing as SD

        def _sync_native():
            try:
                hwnd = cached_hwnd or (int(native.Handle.ToInt64()) if hasattr(native, "Handle") else 0)
                if hwnd:
                    if on_hwnd:
                        on_hwnd(hwnd)
                    dark_pref = ctypes.c_int(1 if norm == "dark" else 0)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 20, ctypes.byref(dark_pref), ctypes.sizeof(dark_pref)
                    )
                bg = SD.ColorTranslator.FromHtml("#F5F5F7" if norm == "light" else "#000000")
                native.BackColor = bg
                if hasattr(native, "browser") and getattr(native.browser, "webview", None):
                    native.browser.webview.DefaultBackgroundColor = bg
            except Exception:
                pass

        if getattr(native, "InvokeRequired", False):
            native.BeginInvoke(System.Action(_sync_native))
        else:
            _sync_native()
    except Exception:
        pass


def begin_native_window_resize(
    native,
    edge_code: int,
    on_hwnd: Optional[Callable[[int], None]] = None,
):
    """无边框窗口 8 向边缘拖拽缩放（委托给 Win32 WM_SYSCOMMAND SC_SIZE）"""
    if edge_code not in range(1, 9) or native is None:
        return
    try:
        import System

        def _do_resize():
            try:
                hwnd = int(native.Handle.ToInt64())
                if hwnd and on_hwnd:
                    on_hwnd(hwnd)
                if hwnd and not ctypes.windll.user32.IsZoomed(hwnd):
                    ctypes.windll.user32.ReleaseCapture()
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0112, 0xF000 + int(edge_code), 0)
            except Exception:
                pass

        native.BeginInvoke(System.Action(_do_resize))
    except Exception:
        pass


class GlobalMediaKeyListener:
    """Windows 后台全局多媒体按键监听器 (WH_KEYBOARD_LL + 线程消息循环)"""

    def __init__(
        self,
        on_play_pause: Callable[[], None],
        on_next: Callable[[], None],
        on_prev: Callable[[], None],
        on_stop: Callable[[], None],
    ):
        self.on_play_pause = on_play_pause
        self.on_next = on_next
        self.on_prev = on_prev
        self.on_stop = on_stop
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._hook = None
        self._c_proc = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            try:
                self._thread.join(timeout=0.6)
            except Exception:
                pass
        self._thread = None

    def _run(self):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = kernel32.GetCurrentThreadId()

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_longlong,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_longlong
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        def hook_proc(nCode, wParam, lParam):
            if nCode == 0 and wParam in (0x0100, 0x0104):  # WM_KEYDOWN, WM_SYSKEYDOWN
                try:
                    kbd = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    vk = kbd.vkCode
                    if vk == 0xB3:  # VK_MEDIA_PLAY_PAUSE
                        threading.Thread(target=self.on_play_pause, daemon=True).start()
                    elif vk == 0xB0:  # VK_MEDIA_NEXT_TRACK
                        threading.Thread(target=self.on_next, daemon=True).start()
                    elif vk == 0xB1:  # VK_MEDIA_PREV_TRACK
                        threading.Thread(target=self.on_prev, daemon=True).start()
                    elif vk == 0xB2:  # VK_MEDIA_STOP
                        threading.Thread(target=self.on_stop, daemon=True).start()
                except Exception:
                    pass
            return user32.CallNextHookEx(self._hook, nCode, wParam, lParam)

        self._c_proc = HOOKPROC(hook_proc)
        self._hook = user32.SetWindowsHookExW(13, self._c_proc, None, 0)  # WH_KEYBOARD_LL = 13
        try:
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook:
                try:
                    user32.UnhookWindowsHookEx(self._hook)
                except Exception:
                    pass
                self._hook = None
