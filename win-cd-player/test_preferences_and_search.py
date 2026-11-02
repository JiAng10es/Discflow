# -*- coding: utf-8 -*-
"""
test_preferences_and_search.py
验证：
1. 用户偏好设置 (自定义行间距/字间距/Dock长短/透明度/模糊度/深浅色主题) 物理磁盘持久化与 API
2. 全局搜索弹窗与资源库搜索交互 (searchModal, executeModalSearch, filterLibraryCards)
3. 抓轨完成后确认直接返回资源库 (finishRippingAndShowLibrary -> switchMainView('library'))
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from library_service import (
    get_preferences_file_path,
    load_user_preferences,
    save_user_preferences,
)
from main import (
    APPLE_MUSIC_HTML,
    DiscflowController,
    DiscflowJsApi,
)


class TestPreferencesAndSearch(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="discflow_test_prefs_")
        self.pref_file = os.path.join(self.test_dir, "preferences.json")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_preferences_disk_persistence(self):
        # 1. 验证默认偏好加载
        defaults = load_user_preferences()
        self.assertEqual(defaults.get("lyricLineHeight"), 1.4)
        self.assertEqual(defaults.get("dockBlur"), 40)
        self.assertEqual(defaults.get("themeMode"), "dark")

        # 2. 验证偏好修改并写入磁盘
        custom_data = {
            "lyricLineHeight": 1.8,
            "lyricLetterSpacing": 2,
            "dockWidth": 800,
            "dockOpacity": 90,
            "dockBlur": 50,
            "themeMode": "light",
        }
        ok = save_user_preferences(custom_data)
        self.assertTrue(ok)

        # 验证读取最新配置
        loaded = load_user_preferences()
        self.assertEqual(loaded.get("lyricLineHeight"), 1.8)
        self.assertEqual(loaded.get("lyricLetterSpacing"), 2)
        self.assertEqual(loaded.get("dockWidth"), 800)
        self.assertEqual(loaded.get("dockOpacity"), 90)
        self.assertEqual(loaded.get("dockBlur"), 50)
        self.assertEqual(loaded.get("themeMode"), "light")

        # 恢复默认避免污染外部环境
        save_user_preferences({
            "lyricLineHeight": 1.4,
            "lyricLetterSpacing": 0,
            "dockWidth": 720,
            "dockOpacity": 75,
            "dockBlur": 40,
            "themeMode": "dark",
        })

    def test_controller_and_jsapi_preferences_bridge(self):
        ctrl = DiscflowController()
        api = DiscflowJsApi(ctrl)

        prefs = api.get_preferences()
        self.assertIsInstance(prefs, dict)
        self.assertIn("lyricLineHeight", prefs)

        saved_ok = api.save_preferences({"dockBlur": 36, "dockOpacity": 85})
        self.assertTrue(saved_ok)
        new_prefs = api.get_preferences()
        self.assertEqual(new_prefs.get("dockBlur"), 36)
        self.assertEqual(new_prefs.get("dockOpacity"), 85)

        # 恢复
        api.save_preferences({"dockBlur": 40, "dockOpacity": 75})

    def test_search_modal_and_library_search_elements(self):
        html = APPLE_MUSIC_HTML

        # 验证搜索弹窗与核心节点存在
        self.assertIn('id="searchModal"', html)
        self.assertIn('id="modalSearchInput"', html)
        self.assertIn('id="modalSearchResults"', html)
        self.assertIn('id="modalSearchClearBtn"', html)
        self.assertIn('id="modalSearchCountText"', html)

        # 验证搜索核心函数存在
        self.assertIn("openSearchModal()", html)
        self.assertIn("executeModalSearch()", html)
        self.assertIn("clearModalSearch()", html)
        self.assertIn("jumpToLibraryFromSearch()", html)
        self.assertIn("submitLibrarySearch()", html)
        self.assertIn("filterLibraryCards(", html)

        # 验证资源库工具栏包含可点击的搜索提交按钮与 Enter 键盘响应
        self.assertIn('id="librarySearchSubmitBtn"', html)
        self.assertIn('onclick="submitLibrarySearch()"', html)
        self.assertIn("event.key==='Enter'", html)

    def test_ripping_completion_returns_to_library(self):
        html = APPLE_MUSIC_HTML

        # 验证抓轨确认按钮文字提示明确
        self.assertIn('id="ripDoneBtn"', html)
        self.assertIn('确认 (回到资源库)', html)

        # 验证点击确认调用 switchMainView("library") 并刷新卡片
        self.assertIn('switchMainView("library");', html)
        self.assertIn("renderLibraryCards();", html)


if __name__ == "__main__":
    unittest.main()
