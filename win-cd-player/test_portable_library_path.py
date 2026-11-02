# -*- coding: utf-8 -*-
"""
test_portable_library_path.py
验证 v1.10 便携优先资源库路径策略：
1. 正常运行时资源库锚定在工程/程序根目录的 library 下，绝不写入 E 盘；
2. 环境变量 DISCFLOW_LIBRARY 可显式覆盖，便于开发与测试隔离；
3. 便携根目录识别带有系统目录防护，避免在 Python 安装目录误建 library；
4. 「打开资源库存储文件夹」前后端桥接完整可用，界面不再硬编码 E 盘路径。
"""

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from library_service import (
    _is_portable_root,
    get_preferences_file_path,
    is_under_test_runner,
    resolve_library_dir,
)
from main import APPLE_MUSIC_HTML, DiscflowController, DiscflowJsApi

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextmanager
def allow_real_library():
    """临时关闭测试沙箱，用于验证真实的便携路径解析逻辑"""
    old = os.environ.get("DISCFLOW_ALLOW_REAL_LIBRARY")
    os.environ["DISCFLOW_ALLOW_REAL_LIBRARY"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("DISCFLOW_ALLOW_REAL_LIBRARY", None)
        else:
            os.environ["DISCFLOW_ALLOW_REAL_LIBRARY"] = old


class TestPortableLibraryPath(unittest.TestCase):
    def test_test_runner_is_sandboxed(self):
        """单元测试运行期间资源库必须落在临时沙箱，绝不指向真实资料库"""
        self.assertTrue(is_under_test_runner(), "unittest 环境下应判定为测试运行")
        target = os.path.abspath(resolve_library_dir())
        self.assertNotEqual(
            target.lower(),
            os.path.join(PROJECT_ROOT, "library").lower(),
            "测试期间不得使用真实资料库路径",
        )
        temp_root = os.path.abspath(tempfile.gettempdir()).lower()
        self.assertTrue(
            target.lower().startswith(temp_root),
            f"测试沙箱应位于系统临时目录下：{target}",
        )

    def test_resolve_anchors_to_project_root(self):
        """默认解析结果应落在工程根目录的 library 下"""
        with allow_real_library():
            target = os.path.abspath(resolve_library_dir())
        self.assertEqual(os.path.basename(target).lower(), "library")
        self.assertEqual(os.path.dirname(target).lower(), PROJECT_ROOT.lower())

    def test_resolve_never_prefers_e_drive(self):
        """即便 E 盘存在，便携根目录也应优先命中，不得回落到 E 盘"""
        with allow_real_library():
            target = os.path.abspath(resolve_library_dir())
        self.assertFalse(target.lower().startswith("e:\\"), f"资源库不应落在 E 盘：{target}")

    def test_env_override(self):
        """环境变量 DISCFLOW_LIBRARY 应拥有最高优先级"""
        tmp = tempfile.mkdtemp(prefix="discflow_lib_")
        try:
            old = os.environ.get("DISCFLOW_LIBRARY")
            os.environ["DISCFLOW_LIBRARY"] = tmp
            try:
                self.assertEqual(os.path.abspath(resolve_library_dir()), os.path.abspath(tmp))
            finally:
                if old is None:
                    os.environ.pop("DISCFLOW_LIBRARY", None)
                else:
                    os.environ["DISCFLOW_LIBRARY"] = old
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_preferences_sits_beside_library(self):
        """偏好配置文件应与资源库同级存放"""
        with allow_real_library():
            lib_dir = os.path.abspath(resolve_library_dir())
            prefs = os.path.abspath(get_preferences_file_path())
        self.assertEqual(os.path.dirname(lib_dir), os.path.dirname(prefs))
        self.assertEqual(os.path.basename(prefs), "preferences.json")

    def test_portable_root_detection(self):
        """工程根目录应被识别为便携根，系统目录与无标识临时目录应被拒绝"""
        self.assertTrue(_is_portable_root(PROJECT_ROOT))
        tmp = tempfile.mkdtemp(prefix="plain_scratch_")
        try:
            self.assertFalse(_is_portable_root(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(_is_portable_root(sys.base_prefix))

    def test_open_library_folder_bridge_present(self):
        """前后端「打开资源库文件夹」链路完整，避免出现死按钮"""
        self.assertTrue(hasattr(DiscflowController, "open_library_folder"))
        self.assertTrue(hasattr(DiscflowController, "get_library_dir"))
        self.assertTrue(hasattr(DiscflowJsApi, "open_library_folder"))
        self.assertTrue(hasattr(DiscflowJsApi, "get_library_dir"))
        self.assertIn("async function openLibraryFolder()", APPLE_MUSIC_HTML)
        self.assertIn("function syncLibraryPathText()", APPLE_MUSIC_HTML)
        self.assertIn('id="openLibraryFolderBtn"', APPLE_MUSIC_HTML)
        self.assertIn('id="libraryPathSub"', APPLE_MUSIC_HTML)

    def test_ui_has_no_hardcoded_e_drive(self):
        """界面文案不得再硬编码 E:\\Discflow 路径"""
        self.assertNotIn("E:\\\\Discflow", APPLE_MUSIC_HTML)


if __name__ == "__main__":
    unittest.main()
