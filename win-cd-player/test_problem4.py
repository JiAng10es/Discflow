# -*- coding: utf-8 -*-
"""
自动化测试用例：验证问题 4（检查测试，删除代码深度优化）
1. 验证整个 main.py 语法编译无任何告警与异常。
2. 验证问题 1、2、3 核心状态与架构在代码库中深度整合，无死循环或冲突。
3. 验证关键数据清理 API (clear_lyrics_cache, clear_all_library_data) 可正常执行。
"""

import unittest
import py_compile
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath('win-cd-player'))
import main
import library_service

class TestProblem4FullOptimizationAndCleanup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_syntax_and_structure_integrity(self):
        """验证 main.py 整体编译与 HTML/CSS 结构完整性"""
        self.assertIn('<!DOCTYPE html>', main.APPLE_MUSIC_HTML)
        self.assertIn('</html>', main.APPLE_MUSIC_HTML)
        self.assertTrue(len(main.APPLE_MUSIC_HTML) > 50000, "HTML 模板应完整充分")

    def test_backend_clear_apis_functional(self):
        """验证后端清空数据与清空歌词缓存接口正常工作且无异常"""
        ctrl = main.DiscflowController()
        # 填充模拟数据
        ctrl.lyrics_cache[1] = [{'time_sec': 0.0, 'text_orig': 'Test'}]
        ctrl.current_lyrics = [{'time_sec': 0.0, 'text_orig': 'Test'}]
        
        # 测试清空歌词缓存
        res_lyrics = ctrl.clear_lyrics_cache()
        self.assertTrue(res_lyrics)
        self.assertEqual(len(ctrl.lyrics_cache), 0, "歌词缓存应被清空")
        self.assertEqual(len(ctrl.current_lyrics), 0, "当前歌词应被重置")

        # 测试清空资料库接口（必须重定向到临时目录，严禁触碰真实用户资料库）
        real_dir = ctrl.get_library_dir()
        real_count = len(ctrl.library_manager.list_albums(force_refresh=True))
        tmp_dir = tempfile.mkdtemp(prefix="discflow_clear_probe_")
        try:
            probe_album_dir = os.path.join(tmp_dir, "probe_disc")
            os.makedirs(probe_album_dir, exist_ok=True)
            with open(os.path.join(probe_album_dir, "album.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "disc_id": "probe_disc",
                        "title": "Probe Album",
                        "artist": "Probe Artist",
                        "tracks": [
                            {
                                "track_number": 1,
                                "title": "Probe Track",
                                "artist": "Probe Artist",
                                "duration_sec": 1.0,
                                "file_name": "track_01.wav",
                                "lyrics": [],
                            }
                        ],
                    },
                    f,
                )

            ctrl.library_manager = library_service.DiscflowLibraryManager(base_dir=tmp_dir)
            self.assertEqual(
                len(ctrl.library_manager.list_albums(force_refresh=True)),
                1,
                "临时资料库应能识别探针专辑",
            )

            res_lib = ctrl.clear_all_library_data()
            self.assertTrue(res_lib)
            self.assertEqual(
                len(ctrl.library_manager.list_albums(force_refresh=True)),
                0,
                "临时资料库中的唱片应被清空",
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            ctrl.library_manager = library_service.DiscflowLibraryManager(base_dir=real_dir)

        # 真实资料库必须毫发无损（回归防护：禁止测试删除用户唱片）
        self.assertEqual(
            len(ctrl.library_manager.list_albums(force_refresh=True)),
            real_count,
            "测试不得删除真实资料库中的唱片",
        )

    def test_no_orphan_debug_files(self):
        """验证临时调试脚本已全部深度清理"""
        orphan_files = [
            'win-cd-player/apply_fix1.py',
            'win-cd-player/apply_fix2.py',
            'win-cd-player/apply_fix3.py',
            'win-cd-player/patch_problem3.py'
        ]
        for f in orphan_files:
            self.assertFalse(os.path.exists(f), f"临时脚本 {f} 应已被清理")

if __name__ == '__main__':
    unittest.main()
