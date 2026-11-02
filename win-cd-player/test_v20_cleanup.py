# -*- coding: utf-8 -*-
"""
test_v20_cleanup.py — Discflow 2.0 收尾体检

覆盖 v2.0 这轮清理/修复的回归点：
1. HTML 结构健康：无重复 ID、无残留死弹窗、无失效兼容节点；
2. 资源库搜索框常驻可见（修掉了 width:0 + opacity:0 导致看不见的 bug）；
3. 底栏左右配比：左侧曲目区有固定基准宽度，右侧动作区自适应；
4. 控制器：clear_all_library_data 用对属性名，且会重置内存播放状态；
5. 歌词进程内缓存有容量上限，长时间运行不无限增长；
6. 「放入光盘自动播放」偏好贯通前后端；
7. 曲目兜底标题用 track_number（不再是 undefined）。
"""

import collections
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import library_service
import metadata_service
import main as M

HTML = M.APPLE_MUSIC_HTML
BODY = HTML[HTML.find("</style>"):]
JS = "\n".join(re.findall(r"<script>(.*?)</script>", HTML, re.S))


class TestHtmlStructure(unittest.TestCase):
    def test_no_duplicate_ids(self):
        ids = re.findall(r'id="([A-Za-z][\w-]*)"', BODY)
        dups = {k: v for k, v in collections.Counter(ids).items() if v > 1}
        self.assertEqual(dups, {}, f"HTML 中不应有重复 id：{dups}")

    def test_dead_modals_removed(self):
        for dead in ("menuModal", "appearanceModal", "libraryOverlay"):
            self.assertNotIn(f'id="{dead}"', HTML, f"已废弃的 {dead} 弹窗不应再出现在 HTML 里")
        self.assertNotIn("library-vertical-toolbar", HTML, "旧的竖排功能区应已移除")

    def test_no_dead_compat_hooks(self):
        for dead in ("sideNavCd", "sideNavLibrary", "sideNavLyrics"):
            self.assertNotIn(dead, HTML, f"侧边栏兼容空节点 {dead} 应已移除")

    def test_inline_script_has_no_undefined_helper_calls(self):
        # 明确断言几个被删掉的死函数不再被引用
        for gone in ("showAboutDialog", "clearMetadataCache", "toggleLibrarySearch", "toggleLibraryView"):
            self.assertNotIn(f"function {gone}(", JS, f"死函数 {gone} 应已删除")
            self.assertNotIn(f"{gone}()", JS, f"不应再调用已删除的 {gone}()")


class TestLibrarySearchVisible(unittest.TestCase):
    def test_search_box_not_hidden_by_css(self):
        m = re.search(r"\.library-search-box-wrap\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(m, "应存在 .library-search-box-wrap 样式")
        css = m.group(1)
        self.assertNotIn("width: 0", css)
        self.assertNotIn("opacity: 0", css)
        self.assertNotIn("pointer-events: none", css)

    def test_search_input_bound_to_instant_filter(self):
        self.assertIn('oninput="filterLibraryCards(this.value)"', HTML)


class TestDockProportions(unittest.TestCase):
    def test_left_cluster_has_basis_and_right_is_auto(self):
        left = re.search(r"\.dock-album-cluster\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(left, "应存在 .dock-album-cluster 样式")
        self.assertIn("flex: 0 1", left.group(1), "左侧曲目区需要固定基准宽度，避免切歌抖动")

        right = re.search(r"\.dock-right-actions\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(right, "应存在 .dock-right-actions 样式")
        self.assertIn("flex: 0 0 auto", right.group(1), "右侧动作区应随内容自适应")


class TestControllerFixes(unittest.TestCase):
    def setUp(self):
        self.ctrl = M.DiscflowController()

    def test_clear_all_library_data_resets_state(self):
        self.ctrl.current_toc = object()
        self.ctrl.lyrics_cache = {1: [{"time": 0, "text": "x"}]}
        self.ctrl.lyrics_locked_tracks = {1}
        self.ctrl.lyrics_missed_tracks = {2}
        ok = self.ctrl.clear_all_library_data()
        self.assertTrue(ok)
        self.assertIsNone(self.ctrl.current_toc)
        self.assertEqual(self.ctrl.lyrics_cache, {})
        self.assertEqual(self.ctrl.lyrics_locked_tracks, set())
        self.assertEqual(self.ctrl.lyrics_missed_tracks, set())

    def test_start_track_accepts_autoplay_flag(self):
        import inspect

        sig = inspect.signature(self.ctrl._start_track)
        self.assertIn("autoplay", sig.parameters, "_start_track 应支持 autoplay 参数")

    def test_no_library_mgr_typo(self):
        with open(os.path.join(os.path.dirname(__file__), "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("self.library_mgr", src, "应统一使用 self.library_manager")


class TestLyricsCacheBounded(unittest.TestCase):
    def test_cache_evicts_beyond_cap(self):
        cap = metadata_service._LYRICS_CACHE_MAX
        self.assertGreater(cap, 0)
        metadata_service._LYRICS_CACHE.clear()
        for i in range(cap + 25):
            metadata_service._lyrics_cache_put(f"k{i}", [{"time": 0, "text": "x"}])
        self.assertLessEqual(len(metadata_service._LYRICS_CACHE), cap)
        # 最早的应被淘汰，最新的应保留
        self.assertNotIn("k0", metadata_service._LYRICS_CACHE)
        self.assertIn(f"k{cap + 24}", metadata_service._LYRICS_CACHE)
        metadata_service._LYRICS_CACHE.clear()


class TestAutoPlayPreference(unittest.TestCase):
    def test_default_has_autoplay(self):
        prefs = library_service.load_user_preferences()
        self.assertIn("autoPlay", prefs)
        self.assertIsInstance(prefs["autoPlay"], bool)

    def test_frontend_wired(self):
        self.assertIn('id="autoPlayLabel"', HTML, "设置里应有自动播放开关")
        self.assertIn("function toggleAutoPlay()", JS)
        self.assertIn("save_preferences({ autoPlay", JS, "切换后应持久化")
        with open(os.path.join(os.path.dirname(__file__), "main.py"), encoding="utf-8") as f:
            py_src = f.read()
        self.assertIn('load_user_preferences().get("autoPlay"', py_src, "后端应读取 autoPlay 偏好")


class TestTrackFallbackTitle(unittest.TestCase):
    def test_uses_track_number(self):
        # 注意：track_number 里也含 track_num 前缀，必须用负向断言区分
        self.assertIsNone(re.search(r"track_num(?!ber)", HTML), "兜底标题应使用 track_number")
        self.assertIn("tr.track_number", HTML)


class TestResilience(unittest.TestCase):
    """弱网兜底：元数据/封面首次失败后要能自愈"""

    def test_cover_refetch_helper(self):
        self.assertTrue(hasattr(metadata_service, "refetch_album_cover"))
        self.assertEqual(metadata_service.refetch_album_cover("", ""), "", "空标题应直接返回空串")

    def test_controller_has_cover_retry(self):
        ctrl = M.DiscflowController()
        self.assertTrue(hasattr(ctrl, "_retry_cover_async"), "控制器应提供封面补抓入口")

    def test_metadata_retry_on_failure(self):
        with open(os.path.join(os.path.dirname(__file__), "main.py"), encoding="utf-8") as f:
            src = f.read()
        # 首次没匹配上时应重试一次
        self.assertIn('meta.album_title == "Audio CD"', src, "应在未匹配时重试元数据")
        self.assertIn("refetch_album_cover(album_title, artist)", src, "应在封面缺失时补抓")


if __name__ == "__main__":
    unittest.main(verbosity=2)
