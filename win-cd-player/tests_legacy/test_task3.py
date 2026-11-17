# -*- coding: utf-8 -*-
"""
自动化测试用例：验证资料库检索与路由交互（v2.0 规范）

1. 路由：点击资料库专辑不再跳沉浸黑胶，而是导航到常规播放界面。
2. 检索：搜索框常驻资源库顶栏，输入即过滤；旧的「右侧竖排功能区 + 点击展开」已移除。
"""

import unittest
import py_compile
import re


class TestTask3LibraryRouteAndSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 1. 语法检查：编译 main.py 确保 Python 语法与 f-string 正确无误
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_library_album_click_routes_to_normal_cd_view(self):
        """点击资料库专辑应导航至常规播放界面 switchMainView('cd')"""
        anim_func_match = re.search(
            r'async function playLocalAlbumWithAnimation\([^)]*\)\s*\{\{([\s\S]+?)\}\}\s*async function',
            self.content,
        )
        self.assertIsNotNone(anim_func_match, "应能找到 playLocalAlbumWithAnimation 函数")
        anim_body = anim_func_match.group(1)
        self.assertIn('switchMainView("cd")', anim_body, "playLocalAlbumWithAnimation 必须导航至常规播放界面 cd")
        self.assertNotIn('toggleImmersiveVinylMode(true)', anim_body, "playLocalAlbumWithAnimation 不应再强制打开沉浸黑胶")

        play_func_match = re.search(
            r'async function playLocalAlbum\([^)]*\)\s*\{\{([\s\S]+?)\}\}\s*async function',
            self.content,
        )
        self.assertIsNotNone(play_func_match, "应能找到 playLocalAlbum 函数")
        play_body = play_func_match.group(1)
        self.assertIn('switchMainView("cd")', play_body, "playLocalAlbum 必须导航至常规播放界面 cd")
        self.assertNotIn('toggleImmersiveVinylMode(true)', play_body, "playLocalAlbum 不应再强制打开沉浸黑胶")

    def test_library_search_always_visible_in_top_bar(self):
        """v2.0：搜索框常驻资源库顶栏（旧的右侧竖排功能区已移除）"""
        lib_start = self.content.find('<section id="viewLibrary"')
        lib_end = self.content.find('</section>', lib_start)
        lib_html = self.content[lib_start:lib_end]

        self.assertIn('id="librarySearchInput"', lib_html, "资源库应包含搜索输入框")
        self.assertIn('id="librarySearchBoxWrap"', lib_html, "搜索框应有独立容器，便于聚焦态样式")
        self.assertIn('id="librarySearchSubmitBtn"', lib_html, "应有搜索按钮")
        self.assertIn('id="librarySearchClearBtn"', lib_html, "应有清空按钮")
        self.assertNotIn('library-vertical-toolbar', lib_html, "旧的竖排功能区已移除")

    def test_library_search_instant_filter_and_visible(self):
        """搜索框必须常驻可见（不能再用 width:0/opacity:0 隐藏），且输入即过滤"""
        wrap_css_match = re.search(r'\.library-search-box-wrap\s*\{\{([^}]+)\}\}', self.content)
        self.assertIsNotNone(wrap_css_match, "应存在 .library-search-box-wrap CSS 定义")
        wrap_css = wrap_css_match.group(1)

        # 回归保护：这三个属性会让搜索框彻底看不见、点不动
        self.assertNotIn('width: 0', wrap_css, "搜索框不应被压成 0 宽")
        self.assertNotIn('opacity: 0', wrap_css, "搜索框不应被完全透明")
        self.assertNotIn('pointer-events: none', wrap_css, "搜索框必须可点击")

        # 输入即过滤
        self.assertIn('oninput="filterLibraryCards(this.value)"', self.content, "输入时应实时过滤卡片")
        self.assertIn('function filterLibraryCards(', self.content)
        self.assertIn('function clearLibrarySearch()', self.content)


if __name__ == '__main__':
    unittest.main()
