# -*- coding: utf-8 -*-
"""
test_icon_redesign.py
验证全软件小图标统一采用 Apple SF Symbols 矢量规范：
1. 顶部操作栏结构完全保持一致，内部小图标替换为统一圆角平滑矢量图标 (读盘、弹出、外观、专属菜单)；
2. 底部悬浮控制栏小图标重绘 (上一曲、播放、暂停、下一曲、双语翻译、音量及音量生成函数)；
3. 播放模式三态切换及歌曲匹配小图标规范化 (顺序/列表循环、随机、单曲循环、匹配元数据)；
4. 资源库操作栏小图标规范化 (去除 Emoji 📂 与字符 ◀，采用平滑矢量文件夹与 Chevron 箭头)；
5. 全局搜索模态框小图标规范化。
"""

import unittest
import py_compile

class TestIconRedesign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_top_bar_icons_redesigned_and_structure_preserved(self):
        """验证顶部栏保留完整布局与按键，小图标更新为 SF Symbols 规范"""
        # 1. 顶部容器及结构完整性
        self.assertIn('<header class="top-bar">', self.content)
        self.assertIn('class="top-floating-left"', self.content)
        self.assertIn('class="top-floating-right"', self.content)

        # 2. 读盘按钮：CD 环形轨道与顺时针箭头
        rescan_start = self.content.find('id="topRescanBtn"')
        self.assertNotEqual(rescan_start, -1)
        rescan_end = self.content.find('</button>', rescan_start)
        rescan_html = self.content[rescan_start:rescan_end]
        self.assertIn('circle cx="12" cy="12" r="2.5"', rescan_html)
        self.assertIn('M21.5 12A9.5 9.5 0 1 1 12 2.5v2.8', rescan_html)

        # 3. 出盘按钮：圆角倒角三角形 + 平滑托盘横条
        eject_start = self.content.find('id="topEjectBtn"')
        self.assertNotEqual(eject_start, -1)
        eject_end = self.content.find('</button>', eject_start)
        eject_html = self.content[eject_start:eject_end]
        self.assertIn('M12 4.4a1.2 1.2 0 0 0-1.02.58l-6.4 10.2', eject_html)
        self.assertIn('rect x="4.5" y="19" width="15" height="2.2" rx="1.1"', eject_html)

        # 4. 外观切换按钮：月牙矢量图形
        theme_start = self.content.find('id="topThemeBtn"')
        self.assertNotEqual(theme_start, -1)
        theme_end = self.content.find('</button>', theme_start)
        theme_html = self.content[theme_start:theme_end]
        self.assertIn('M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z', theme_html)

        # 5. Discflow 专属菜单项图标：圆角四宫格、45度放大镜、等线齿轮
        menu_start = self.content.find('id="discflowAppMenu"')
        self.assertNotEqual(menu_start, -1)
        menu_end = self.content.find('</div>\n    </header>', menu_start) + 6
        menu_html = self.content[menu_start:menu_end]
        self.assertIn('rx="2.5"', menu_html, "资料库图标四宫格应具备 R2.5 连续圆角")
        self.assertIn('circle cx="10.5" cy="10.5" r="7"', menu_html, "搜索图标应具备平滑圆环")
        self.assertIn('circle cx="12" cy="12" r="3.2"', menu_html, "设置齿轮图标应具备轴心配比")

    def test_bottom_dock_icons_redesigned(self):
        """验证底部控制栏的上一曲、播放、暂停、下一曲、翻译、音量图标"""
        dock_start = self.content.find('id="bottomDockPlayer"')
        dock_end = self.content.find('</footer>', dock_start)
        dock_html = self.content[dock_start:dock_end]

        # 上一曲与下一曲的药丸圆角柱与倒角三角
        self.assertIn('rect x="4" y="5" width="2" height="14" rx="1"', dock_html)
        self.assertIn('rect x="18" y="5" width="2" height="14" rx="1"', dock_html)

        # 播放键与暂停键
        self.assertIn('id="dockPlayIcon"', dock_html)
        self.assertIn('M7.8 4.6a1.2 1.2 0 0 0-1.8 1.05v12.7', dock_html)
        self.assertIn('id="dockPauseIcon"', dock_html)
        self.assertIn('rx="1.75"', dock_html)

        # 双语翻译按键
        self.assertIn('id="dockTransBtn"', dock_html)
        self.assertIn('M4 6.5h7M7.5 4v2.5', dock_html)

        # 音量按键及 JS 动态生成函数
        self.assertIn('id="dockVolBtnIcon"', dock_html)
        self.assertIn('function volumeIconMarkup(v)', self.content)
        self.assertIn('M11 4.5L6.5 8H3.5a1.5 1.5 0 0 0-1.5 1.5v5', self.content)

    def test_play_modes_and_library_actions_redesigned(self):
        """验证循环模式、资源库操作栏图标"""
        # 1. 播放模式 JS 对象与 HTML 队列头部模式按钮
        self.assertIn('const PLAY_MODE_INFO =', self.content)
        self.assertIn('M17 2l4 4-4 4', self.content)  # 列表循环流线
        self.assertIn('M16 3h5v5', self.content)      # 随机双S曲线
        self.assertIn('M11.2 10.5l1.6-1.1v5.2m-1.2 0h2.4', self.content) # 单曲循环中心等线1

        # 2. 资源库操作栏
        lib_start = self.content.find('id="viewLibrary"')
        lib_end = self.content.find('</section>', lib_start)
        lib_html = self.content[lib_start:lib_end]

        # 本地目录图标（替代 Emoji 📂）
        self.assertIn('id="openLibraryFolderBtn"', lib_html)
        self.assertIn('M3.5 6.5A2.5 2.5 0 0 1 6 4h3.5', lib_html)
        self.assertNotIn('📂', lib_html, "应彻底移除 Emoji 📂")

        # 返回播放按钮图标（替代 ◀ 字符）
        self.assertIn("switchMainView('cd')", lib_html)
        self.assertIn('M15 19l-7-7 7-7', lib_html)
        self.assertNotIn('◀', lib_html, "应彻底移除粗糙字符 ◀")

if __name__ == '__main__':
    unittest.main()
