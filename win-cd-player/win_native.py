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

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    _shcore = ctypes.WinDLL("shcore", use_last_error=True)
except Exception:
    _shcore = None

_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong,
    ctypes.c_int,
    wintypes.WPARAM,
    wintypes.LPARAM,
)

# 显式声明 64 位安全 Win32 API 签名
if hasattr(_user32, "SetProcessDpiAwarenessContext"):
    _user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    _user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
_user32.SetProcessDPIAware.argtypes = []
_user32.SetProcessDPIAware.restype = wintypes.BOOL
if hasattr(_user32, "GetDpiForWindow"):
    _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    _user32.GetDpiForWindow.restype = wintypes.UINT
if hasattr(_user32, "GetDpiForSystem"):
    _user32.GetDpiForSystem.argtypes = []
    _user32.GetDpiForSystem.restype = wintypes.UINT
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL
_user32.ReleaseCapture.argtypes = []
_user32.ReleaseCapture.restype = wintypes.BOOL
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = ctypes.c_longlong
_user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.PeekMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
_user32.GetMessageW.restype = ctypes.c_int
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.TranslateMessage.restype = wintypes.BOOL
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.restype = ctypes.c_longlong
_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_longlong
_user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL

_dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

if _shcore is not None and hasattr(_shcore, "SetProcessDpiAwareness"):
    _shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    _shcore.SetProcessDpiAwareness.restype = ctypes.c_long


def enable_dpi_awareness():
    """在读取屏幕指标或创建窗口前启用 Per-Monitor V2 DPI 感知，统一物理/逻辑像素转换基准"""
    try:
        if hasattr(_user32, "SetProcessDpiAwarenessContext"):
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (DPI_AWARENESS_CONTEXT)-4
            if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
                return
    except Exception:
        pass
    try:
        if _shcore is not None and hasattr(_shcore, "SetProcessDpiAwareness"):
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            if _shcore.SetProcessDpiAwareness(2) == 0:
                return
    except Exception:
        pass
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        pass


def get_dpi_scale(hwnd: int = 0) -> float:
    """获取指定窗口或主显示器的 DPI 缩放系数（96 DPI = 1.0）"""
    try:
        if hwnd and hasattr(_user32, "GetDpiForWindow"):
            dpi = int(_user32.GetDpiForWindow(wintypes.HWND(hwnd)) or 0)
            if dpi > 0:
                return dpi / 96.0
        if hasattr(_user32, "GetDpiForSystem"):
            dpi = int(_user32.GetDpiForSystem() or 0)
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
                    pref = ctypes.c_int(2)  # DWMWCP_ROUND
                    _dwmapi.DwmSetWindowAttribute(
                        wintypes.HWND(hwnd), 33, ctypes.byref(pref), ctypes.sizeof(pref)
                    )
                    dark_pref = ctypes.c_int(1 if norm_theme == "dark" else 0)
                    _dwmapi.DwmSetWindowAttribute(
                        wintypes.HWND(hwnd), 20, ctypes.byref(dark_pref), ctypes.sizeof(dark_pref)
                    )
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
                live_hwnd = int(native.Handle.ToInt64()) if hasattr(native, "Handle") else 0
                hwnd = live_hwnd or cached_hwnd
                if hwnd:
                    if on_hwnd:
                        on_hwnd(hwnd)
                    dark_pref = ctypes.c_int(1 if norm == "dark" else 0)
                    _dwmapi.DwmSetWindowAttribute(
                        wintypes.HWND(hwnd), 20, ctypes.byref(dark_pref), ctypes.sizeof(dark_pref)
                    )
                bg = SD.ColorTranslator.FromHtml("#F5F5F7" if norm == "light" else "#111217")
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
                hwnd = int(native.Handle.ToInt64()) if hasattr(native, "Handle") else 0
                if hwnd and on_hwnd:
                    on_hwnd(hwnd)
                if hwnd and not _user32.IsZoomed(wintypes.HWND(hwnd)):
                    _user32.ReleaseCapture()
                    _user32.SendMessageW(wintypes.HWND(hwnd), 0x0112, 0xF000 + int(edge_code), 0)
            except Exception:
                pass

        if getattr(native, "InvokeRequired", False):
            native.BeginInvoke(System.Action(_do_resize))
        else:
            _do_resize()
    except Exception:
        pass


def set_native_window_fullscreen(
    native,
    enable: bool,
    state_cache: dict,
    on_hwnd: Optional[Callable[[int], None]] = None,
) -> bool:
    """
    在 WinForms UI 线程切换无边框真全屏（铺满当前显示器并覆盖任务栏）：
    - 进入全屏时缓存原 WindowState / Bounds（最大化时读取 RestoreBounds） / Screen，关闭 Win11 圆角与外边框；
    - 退出全屏时无损还原原位置、尺寸与最大化状态，并恢复 Win11 原生圆角。
    """
    target = bool(enable)
    if native is None:
        return target
    try:
        import System
        import System.Windows.Forms as WinForms

        def _apply():
            try:
                if target:
                    if not state_cache.get("active", False):
                        old_state = native.WindowState
                        state_cache["old_state"] = old_state
                        if old_state == WinForms.FormWindowState.Maximized and hasattr(native, "RestoreBounds"):
                            state_cache["old_bounds"] = native.RestoreBounds
                        else:
                            state_cache["old_bounds"] = native.Bounds
                        state_cache["old_screen"] = WinForms.Screen.FromControl(native)
                        state_cache["active"] = True
                    scr = WinForms.Screen.FromControl(native)
                    b = scr.Bounds
                    if native.WindowState == WinForms.FormWindowState.Maximized:
                        native.WindowState = WinForms.FormWindowState.Normal
                    native.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
                    native.Bounds = b
                    # FormBorderStyle 变更可能重建 WinForms 句柄，必须在之后重新读取 Handle
                    hwnd = int(native.Handle.ToInt64()) if hasattr(native, "Handle") else 0
                    if hwnd:
                        if on_hwnd:
                            on_hwnd(hwnd)
                        # SWP_SHOWWINDOW (0x0040) | SWP_NOZORDER (0x0004) | SWP_FRAMECHANGED (0x0020)
                        _user32.SetWindowPos(
                            wintypes.HWND(hwnd),
                            None,
                            int(b.X),
                            int(b.Y),
                            int(b.Width),
                            int(b.Height),
                            0x0064,
                        )
                        no_round = ctypes.c_int(1)  # DWMWCP_DONOTROUND
                        _dwmapi.DwmSetWindowAttribute(
                            wintypes.HWND(hwnd), 33, ctypes.byref(no_round), ctypes.sizeof(no_round)
                        )
                        no_border = ctypes.c_uint(0xFFFFFFFE)  # DWMWA_COLOR_NONE
                        _dwmapi.DwmSetWindowAttribute(
                            wintypes.HWND(hwnd), 34, ctypes.byref(no_border), ctypes.sizeof(no_border)
                        )
                else:
                    if state_cache.get("active", False):
                        old_state = state_cache.get("old_state")
                        old_bounds = state_cache.get("old_bounds")
                        state_cache["active"] = False
                        native.WindowState = WinForms.FormWindowState.Normal
                        if old_bounds is not None:
                            native.Bounds = old_bounds
                        if old_state == WinForms.FormWindowState.Maximized:
                            native.WindowState = WinForms.FormWindowState.Maximized
                    hwnd = int(native.Handle.ToInt64()) if hasattr(native, "Handle") else 0
                    if hwnd:
                        if on_hwnd:
                            on_hwnd(hwnd)
                        round_pref = ctypes.c_int(2)  # DWMWCP_ROUND
                        _dwmapi.DwmSetWindowAttribute(
                            wintypes.HWND(hwnd), 33, ctypes.byref(round_pref), ctypes.sizeof(round_pref)
                        )
                        def_border = ctypes.c_uint(0xFFFFFFFF)  # DWMWA_COLOR_DEFAULT
                        _dwmapi.DwmSetWindowAttribute(
                            wintypes.HWND(hwnd), 34, ctypes.byref(def_border), ctypes.sizeof(def_border)
                        )
            except Exception:
                pass

        if getattr(native, "InvokeRequired", False):
            native.Invoke(System.Action(_apply))
        else:
            _apply()
    except Exception:
        pass
    return target


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
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._hook = None
        self._c_proc = None

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._ready_event.clear()
            self._thread_id = 0
            t = threading.Thread(target=self._run, daemon=True)
            self._thread = t
            t.start()
        self._ready_event.wait(timeout=0.5)

    def stop(self):
        with self._lock:
            self._stop_event.set()
            t = self._thread
        if t is not None and t.is_alive():
            self._ready_event.wait(timeout=0.5)
        with self._lock:
            tid = self._thread_id
        if tid:
            try:
                _user32.PostThreadMessageW(wintypes.DWORD(tid), 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
        if t is not None and t.is_alive() and t is not threading.current_thread():
            try:
                t.join(timeout=1.0)
            except Exception:
                pass
        with self._lock:
            if self._thread is t and (t is None or not t.is_alive()):
                self._thread = None
                self._thread_id = 0

    def _run(self):
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        hook = None
        try:
            tid = int(_kernel32.GetCurrentThreadId())
            with self._lock:
                self._thread_id = tid

            # 强制创建当前线程消息队列，确保 PostThreadMessageW 不会因队列未就绪而丢失 WM_QUIT
            msg = wintypes.MSG()
            _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

            if self._stop_event.is_set():
                return

            def hook_proc(nCode, wParam, lParam):
                if nCode == 0 and wParam in (0x0100, 0x0104) and not self._stop_event.is_set():
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
                return _user32.CallNextHookEx(hook, nCode, wParam, lParam)

            c_proc = _HOOKPROC(hook_proc)
            self._c_proc = c_proc
            hook = _user32.SetWindowsHookExW(13, c_proc, None, 0)  # WH_KEYBOARD_LL = 13
            self._hook = hook
            self._ready_event.set()

            while not self._stop_event.is_set():
                ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._ready_event.set()
            if hook:
                try:
                    _user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass
            with self._lock:
                self._hook = None
                self._c_proc = None
                self._thread_id = 0
