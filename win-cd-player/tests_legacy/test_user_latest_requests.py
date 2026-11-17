# -*- coding: utf-8 -*-
"""
自动化测试用例：验证用户最新反馈需求
1. 直接移除沉浸黑胶模式。
2. 移除播放界面左下那个长条状态，只保留居中悬浮控制栏。
3. Discflow 图标点击菜单：
   - 第一个是资料库（抓轨过的专辑）
   - 第二个是搜索（可搜索资料库中的专辑、歌名、歌手）
   - 最下面是设置按钮（关于 Discflow、清空数据、清空歌词缓存）
"""

import unittest
import py_compile
import re

class TestUserLatestRequests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_immersive_vinyl_mode_removed(self):
        """验证沉浸黑胶模式已被直接移除/禁用"""
        # 1. 检查 CSS 中强制隐藏沉浸黑胶舞台
        self.assertIn('#immersiveVinylStage', self.content)
        self.assertIn('display: none !important;', self.content)

        # 2. 检查底部居中悬浮条右侧可见按键已无沉浸黑胶图标
        dock_right_start = self.content.find('class="dock-right-actions"')
        self.assertNotEqual(dock_right_start, -1)
        dock_right_end = self.content.find('<!-- 隐藏兼容属性', dock_right_start)
        dock_right_visible = self.content[dock_right_start:dock_right_end]
        self.assertNotIn('title="进入沉浸黑胶模式"', dock_right_visible, "可见控制栏不应再提供进入沉浸黑胶按钮")

    def test_only_centered_dock_preserved(self):
        """验证播放界面底部只保留居中悬浮长条，无左下方长条状态"""
        dock_match = re.search(r'\.bottom-dock-player\s*\{\{([\s\S]+?)\}\}\s*\.dock-album-cluster', self.content)
        self.assertIsNotNone(dock_match)
        dock_css = dock_match.group(1)
        self.assertIn('left: 50%', dock_css, "控制长条必须居中")
        self.assertIn('translateX(-50%)', dock_css)

    def test_discflow_menu_and_deep_search(self):
        """验证 Discflow 图标菜单完整可用，且搜索深度支持专辑、歌名、歌手"""
        # 1. 验证 JS 中存在完整定义的 toggleDiscflowMenu 与 selectDiscflowMenu
        self.assertIn('function toggleDiscflowMenu(', self.content)
        self.assertIn('function selectDiscflowMenu(', self.content)

        # 2. 验证菜单项包含资料库、搜索、设置
        menu_start = self.content.find('id="discflowAppMenu"')
        self.assertNotEqual(menu_start, -1)
        menu_end = self.content.find('</div>\n    </header>', menu_start) + 6
        menu_html = self.content[menu_start:menu_end]

        self.assertIn("selectDiscflowMenu('library')", menu_html)
        self.assertIn("selectDiscflowMenu('search')", menu_html)
        self.assertIn("selectDiscflowMenu('settings')", menu_html)

        # 3. 验证卡片包含 data-tracks，且 filterLibraryCards 支持深度匹配歌名
        self.assertIn('data-tracks=', self.content)
        self.assertIn('tracks.includes(term)', self.content, "搜索必须支持在资料库中搜索单曲歌名")

        # 4. 验证设置弹窗包含关于 Discflow、清空数据、清空歌词缓存
        settings_start = self.content.find('id="settingsModal"')
        self.assertNotEqual(settings_start, -1)
        settings_end = self.content.find('</div>\n    </div>', settings_start)
        settings_html = self.content[settings_start:settings_end]

        self.assertIn("关于 Discflow", settings_html)
        self.assertIn("清空资料库数据", settings_html)
        self.assertIn("清空歌词缓存", settings_html)

    def test_toggle_lyrics_tracklist_and_volume_slider(self):
        """验证点击封面/底栏在歌词与曲目间切换，以及音量滑块展开与实时更新"""
        self.assertIn('function toggleLyricsAndTracklist(', self.content)
        self.assertIn('const toggleRightPanelFromDock = toggleLyricsAndTracklist;', self.content)
        self.assertIn('onclick="toggleLyricsAndTracklist(event)"', self.content)
        self.assertIn('function toggleDockVolumeSlider(', self.content)
        self.assertIn('onclick="toggleDockVolumeSlider(event)"', self.content)
        # v1.8：渲染循环统一经 renderVolumeUI() 实时刷新竖向音量填充条（原 dVolFill 直写逻辑含覆盖 Bug，已替换）
        self.assertIn('if (!isDraggingDockVol) renderVolumeUI(appState.volume);', self.content)
        self.assertIn('dvf.style.height = `${{v}}%`;', self.content)

    def test_dock_progress_and_volume_fixed_proportions(self):
        """验证底部导航栏显示歌曲进程，音量条弹出卡片且底栏比例固定防抖动"""
        # 1. 验证底部导航栏显示歌曲进程 (已播时间、进度条、剩余时间)
        dock_start = self.content.find('id="bottomDockPlayer"')
        self.assertNotEqual(dock_start, -1)
        dock_end = self.content.find('</footer>', dock_start)
        dock_html = self.content[dock_start:dock_end]

        self.assertIn('id="dockElapsedTime"', dock_html, "底栏需显示已播放时长")
        self.assertIn('id="dockScrubberHit"', dock_html, "底栏需包含进度拖动热区")
        self.assertIn('id="dockScrubberFill"', dock_html, "底栏需包含进度填充条")
        self.assertIn('id="dockRemainingTime"', dock_html, "底栏需显示剩余时长")

        # 2. 验证音量弹出卡片 (dock-volume-popover vertical) 且位置比例固定
        self.assertIn('dock-volume-popover', dock_html, "需通过独立浮动卡片展示音量条，避免挤压底栏")
        self.assertIn('vertical', dock_html, "音量调节卡片需为竖向设计")
        self.assertIn('id="dockVolValText"', dock_html, "需展示音量实时百分比数值")

        # 3. 底栏左右配比：左侧曲目信息有固定基准宽度（保证不抖动），
        #    右侧动作区改为按内容自适应——v2.0 修掉了「右侧空占 170px、左侧标题被挤成省略号」的问题
        self.assertIn('flex: 0 1 236px;', self.content, "左侧曲目区需有固定基准宽度，避免切换曲目时底栏抖动")
        self.assertIn('flex: 0 0 auto;', self.content, "右侧动作区应随内容自适应，不再空占固定宽度")

    def test_library_renamed_and_back_to_player_button(self):
        """验证资源库更名、删除重合搜索按钮，并新增返回播放页面按钮"""
        lib_start = self.content.find('id="viewLibrary"')
        self.assertNotEqual(lib_start, -1)
        lib_end = self.content.find('</section>', lib_start)
        lib_html = self.content[lib_start:lib_end]

        self.assertIn('>资源库</h1>', lib_html, "本地黑胶资料库应更名为资源库")
        self.assertIn("switchMainView('cd')", lib_html, "资源库中需新增返回播放页面按钮")
        self.assertIn("返回播放", lib_html)
        self.assertIn("startRippingCurrentCd()", lib_html, "抓轨按钮正常保留")

    def test_custom_settings_features(self):
        """验证设置中的自定义功能：调节歌词行间距/字间距、导航栏长短与透明度"""
        # 1. 设置弹窗中包含自定义功能入口
        self.assertIn('openCustomSettingsModal()', self.content, "设置菜单中应提供打开自定义设置入口")
        self.assertIn('自定义功能', self.content)

        # 2. 自定义弹窗包含四项核心调节控件
        modal_start = self.content.find('id="customSettingsModal"')
        self.assertNotEqual(modal_start, -1, "应存在 customSettingsModal 自定义偏好弹窗")
        modal_end = self.content.find('<!-- 操作行 -->', modal_start)
        modal_html = self.content[modal_start:modal_end]

        self.assertIn('id="lyricLineHeightInput"', modal_html, "需提供歌词行间距调节滑块")
        self.assertIn('id="lyricLetterSpacingInput"', modal_html, "需提供歌词字间距调节滑块")
        self.assertIn('id="dockWidthInput"', modal_html, "需提供底部导航栏长短(宽度)调节滑块")
        self.assertIn('id="dockOpacityInput"', modal_html, "需提供底部导航栏透明度调节滑块")

        # 3. JS 状态管理与实时应用
        self.assertIn('DEFAULT_CUSTOM_PREFS', self.content)
        self.assertIn('loadCustomPrefs(', self.content)
        self.assertIn('applyCustomPrefs(', self.content)
        self.assertIn('saveCustomPrefs(', self.content)
        self.assertIn('--lyrics-line-height', self.content)
        self.assertIn('--lyrics-letter-spacing', self.content)
        self.assertIn('--dock-custom-width', self.content)
        self.assertIn('--dock-custom-opacity', self.content)
        self.assertIn('--dock-custom-blur', self.content)
        self.assertIn('id="dockBlurInput"', modal_html, "需提供底部导航栏模糊度调节滑块")

    def test_theme_adaptation_and_bare_icon(self):
        """验证浅色深色模式统一适配与 Discflow 裸图标展示"""
        # 1. 验证 Discflow 图标移除外层圆形按钮背景与边框
        self.assertIn('#discflowAppBtn.top-icon-btn', self.content)
        self.assertIn('background: transparent !important;', self.content)

        # 2. 验证浅色模式下统一设计变量
        self.assertIn('--dock-bg-rgb: 255, 255, 255;', self.content)
        self.assertIn('--popover-bg: rgba(255, 255, 255,', self.content)

        # 3. 验证本地播放动画函数为 async
        self.assertIn('async function playLocalAlbumWithAnimation(', self.content)

if __name__ == '__main__':
    unittest.main()
