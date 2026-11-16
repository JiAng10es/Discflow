# -*- coding: utf-8 -*-
"""
test_v2_fixes.py
针对 v2.2.0 修复与增强的自动化回归测试：
1. 窗口记忆 DPI 逻辑像素换算与异常超大尺寸自愈；
2. 启动首帧主题注入 (data-theme + window.__INITIAL_THEME__)；
3. 系统托盘驻留功能彻底移除；
4. GitHub Release .exe 资源解析、软件内下载 MZ 魔数校验与开发模式安全拦截。
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import main  # noqa: E402


class TestDiscflowV2Fixes(unittest.TestCase):
    def test_window_bounds_dpi_conversion_and_clamp(self):
        # 1. 模拟逻辑桌面 1440x900 下读取被旧版物理像素污染的 windowBounds (2400x1600)
        corrupted_prefs = {
            "windowBounds": {"x": 200, "y": 100, "width": 2400, "height": 1600, "maximized": False}
        }
        with patch.object(main, "load_user_preferences", return_value=corrupted_prefs), \
             patch.object(main.DiscflowController, "_get_logical_virtual_screen", return_value=(0, 0, 1440, 900)):
            w, h, x, y, maximized = main.DiscflowController._load_initial_window_bounds()
            self.assertEqual(w, 1440)
            self.assertEqual(h, 900)
            self.assertEqual((x, y), (200, 100))
            self.assertFalse(maximized)

        # 2. 模拟 150% DPI (scale=1.5) 下物理矩形 (150, 75, 1830, 1185) -> 逻辑 (100, 50, 1120, 740)
        ctrl = main.DiscflowController.__new__(main.DiscflowController)
        ctrl._window = SimpleNamespace(native=SimpleNamespace(Handle=SimpleNamespace(ToInt64=lambda: 12345)))
        saved_payloads = []

        def fake_get_rect(hwnd, ref):
            rect = ref._obj
            rect.left, rect.top, rect.right, rect.bottom = 150, 75, 1830, 1185
            return 1

        fake_user32 = SimpleNamespace(
            IsZoomed=lambda h: 0,
            IsIconic=lambda h: 0,
            GetWindowRect=fake_get_rect,
        )
        with patch.object(main.ctypes.windll, "user32", fake_user32), \
             patch.object(main.DiscflowController, "_get_window_dpi_scale", return_value=1.5), \
             patch.object(main, "load_user_preferences", return_value={}), \
             patch.object(main, "save_user_preferences", side_effect=lambda p: saved_payloads.append(p) or True):
            ctrl._save_window_bounds()

        self.assertEqual(len(saved_payloads), 1)
        wb = saved_payloads[0]["windowBounds"]
        self.assertEqual(wb, {"x": 100, "y": 50, "width": 1120, "height": 740, "maximized": False})

    def test_startup_theme_injection(self):
        light_html = main._load_frontend_html("light")
        dark_html = main._load_frontend_html("dark")
        self.assertIn('<html lang="zh-CN" data-theme="light">', light_html)
        self.assertIn('window.__INITIAL_THEME__ = "light";', light_html)
        self.assertIn('<html lang="zh-CN" data-theme="dark">', dark_html)
        self.assertIn('window.__INITIAL_THEME__ = "dark";', dark_html)
        self.assertNotIn("__INIT_THEME__", light_html)

    def test_system_tray_removed(self):
        for attr in ("_setup_tray_icon", "_dispose_tray_icon", "show_window", "hide_to_tray", "set_close_to_tray", "on_window_closing", "exit_app"):
            self.assertFalse(hasattr(main.DiscflowController, attr), f"DiscflowController 不应保留托盘方法 {attr}")
        self.assertFalse(hasattr(main.DiscflowJsApi, "set_close_to_tray"))
        html = main._load_frontend_html("dark")
        self.assertNotIn("closeToTraySwitch", html)
        self.assertNotIn("toggleCloseToTray", html)

    def test_in_app_update_download_and_mz_validation(self):
        ctrl = main.DiscflowController.__new__(main.DiscflowController)
        import threading
        ctrl._closed = False
        ctrl._booted = False
        ctrl._window = None
        ctrl._meta_version = 1
        ctrl._ignored_update_version = ""
        ctrl._update_info = None
        ctrl._download_lock = threading.Lock()
        ctrl._download_state = {
            "status": "idle",
            "version": "",
            "progress": 0,
            "downloadedBytes": 0,
            "totalBytes": 0,
            "filePath": "",
            "error": "",
            "canSelfUpdate": False,
        }
        ctrl._push_event = threading.Event()
        ctrl._bump_meta = lambda: None
        ctrl._schedule_ui_push = lambda: None

        fake_release = {
            "tag_name": "v9.9.9",
            "html_url": "https://github.com/JiAng10es/Discflow/releases/tag/v9.9.9",
            "body": "Test release",
            "published_at": "2026-11-16T00:00:00Z",
            "assets": [
                {"name": "source.zip", "browser_download_url": "https://example.com/src.zip", "size": 100},
                {"name": "Discflow.exe", "browser_download_url": "https://example.com/Discflow.exe", "size": 64},
            ],
        }
        fake_get_meta = SimpleNamespace(status_code=200, json=lambda: fake_release)

        class FakeStreamResp:
            status_code = 200
            headers = {"Content-Length": "64"}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def iter_content(self, chunk_size=65536):
                yield b"MZ" + (b"\x00" * 62)

        with patch.object(main.requests, "get", return_value=fake_get_meta):
            info = ctrl.check_for_updates(force=True)
        self.assertTrue(info["ok"])
        self.assertTrue(info["hasUpdate"])
        self.assertEqual(info["latestVersion"], "9.9.9")
        self.assertEqual(info["assetUrl"], "https://example.com/Discflow.exe")
        self.assertEqual(info["assetSize"], 64)

        with patch.object(main, "_MIN_UPDATE_BYTES", 32), \
             patch.object(main.requests, "get", return_value=FakeStreamResp()):
            ctrl._download_update_worker("9.9.9", "https://example.com/Discflow.exe", 64)

        st = ctrl.get_update_download_state()
        self.assertEqual(st["status"], "ready")
        self.assertEqual(st["progress"], 100)
        self.assertTrue(os.path.isfile(st["filePath"]))
        try:
            apply_res = ctrl.apply_downloaded_update()
            self.assertFalse(apply_res["ok"])
            self.assertTrue(apply_res.get("devMode"))
        finally:
            if st.get("filePath") and os.path.isfile(st["filePath"]):
                os.remove(st["filePath"])


if __name__ == "__main__":
    unittest.main()
