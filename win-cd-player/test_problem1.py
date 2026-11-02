# -*- coding: utf-8 -*-
"""
自动化测试用例：验证问题 1（顶部栏精简与重构）
1. 顶部栏的边框消除，只留下4个图标（软件图标、读盘、弹出、外观模式切换按钮）和绿灯黄灯的状态标识，整体悬浮在界面上。
2. Discflow 图标点开后，弹出菜单中：
   - 第一个是资料库（抓轨过的专辑）
   - 第二个是搜索（搜索资料库中专辑、歌名、包括歌手）
   - 最下面是设置按钮（里面有关于 Discflow、清空数据、清空歌词缓存）
"""

import unittest
import py_compile
import re

class TestProblem1TopBarAndMenu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_topbar_borderless_and_floating_pure_icons(self):
        """验证顶部栏消除边框、悬浮于界面上，且只留下4个纯图标与绿/黄状态指示灯"""
        # 1. 检查顶部栏边框消除与透明悬浮 CSS
        tb_css_match = re.search(r'\.top-bar\s*\{\{([^}]+)\}\}', self.content)
        self.assertIsNotNone(tb_css_match, "应存在 .top-bar CSS")
        tb_css = tb_css_match.group(1)
        self.assertIn('border: none', tb_css, ".top-bar 应消除边框")
        self.assertIn('background: transparent', tb_css, ".top-bar 应为透明悬浮背景")

        # 2. 检查顶部栏 HTML 结构
        top_start = self.content.find('<header class="top-bar">')
        top_end = self.content.find('</header>', top_start)
        top_html = self.content[top_start:top_end]

        # 图标 1: 软件图标 (Discflow 图标)
        self.assertIn('id="discflowAppBtn"', top_html)
        self.assertIn('toggleDiscflowMenu(event)', top_html)

        # 图标 2: 读盘
        self.assertIn('id="topRescanBtn"', top_html)
        self.assertIn('rescanDrive()', top_html)

        # 图标 3: 弹出
        self.assertIn('id="topEjectBtn"', top_html)
        self.assertIn('ejectDrive()', top_html)

        # 图标 4: 外观模式切换按钮
        self.assertIn('id="topThemeBtn"', top_html)
        self.assertIn('cycleTheme()', top_html)

        # 状态指示灯（v2.0 起为三态：读盘中 loading 呼吸、空闲常亮、无盘 idle 暗灰）
        self.assertIn('id="statusDot"', top_html)
        self.assertIn('.status-dot {', self.content)
        self.assertIn('.status-dot.loading', self.content)
        self.assertIn('.status-dot.idle', self.content)

    def test_discflow_popover_menu_structure(self):
        """验证 Discflow 图标点开后的菜单项：第一个资料库、第二个搜索、最下面设置"""
        menu_start = self.content.find('id="discflowAppMenu"')
        self.assertNotEqual(menu_start, -1, "应存在 discflowAppMenu 菜单容器")
        menu_end = self.content.find('</div>\n    </header>', menu_start) + 6
        menu_html = self.content[menu_start:menu_end]

        # 验证 3 个菜单项顺序与绑定
        self.assertIn("selectDiscflowMenu('library')", menu_html)
        self.assertIn("资料库", menu_html)

        self.assertIn("selectDiscflowMenu('search')", menu_html)
        self.assertIn("搜索", menu_html)

        self.assertIn("selectDiscflowMenu('settings')", menu_html)
        self.assertIn("设置", menu_html)

        # 验证在 HTML 中的顺序：资料库在前，搜索在后，设置在最下
        pos_lib = menu_html.find("资料库")
        pos_search = menu_html.find("搜索")
        pos_settings = menu_html.find("设置")
        self.assertTrue(pos_lib < pos_search < pos_settings, "菜单项顺序应为：资料库 -> 搜索 -> 设置")

    def test_settings_modal_items_and_backend_apis(self):
        """验证设置弹窗包含关于 Discflow、清空数据、清空歌词缓存，且后端具备对应 API"""
        settings_start = self.content.find('id="settingsModal"')
        self.assertNotEqual(settings_start, -1, "应存在 settingsModal 设置模态框")
        settings_end = self.content.find('</div>\n    </div>', settings_start)
        settings_html = self.content[settings_start:settings_end]

        self.assertIn("关于 Discflow", settings_html)
        self.assertIn("清空资料库数据", settings_html)
        self.assertIn("清空歌词缓存", settings_html)

        # 验证后端 API 实现与暴露
        self.assertIn("def clear_lyrics_cache(", self.content)
        self.assertIn("def clear_all_library_data(", self.content)

if __name__ == '__main__':
    unittest.main()
