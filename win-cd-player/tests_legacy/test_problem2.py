# -*- coding: utf-8 -*-
"""
自动化测试用例：验证问题 2（极简双栏左侧纯封面与居中悬浮控制栏）
1. 播放界面左侧部分只保留专辑封面，右侧依然是歌词。
2. 下边悬浮的状态框居中悬浮（left: 50%; transform: translateX(-50%);），半透明毛玻璃悬浮效果。
3. 状态栏防重叠：固定位置比例（flex-shrink: 0, white-space: nowrap）。
4. 状态栏只保留：
   - 专辑图和旁边的歌名和歌手（点击在右侧歌词和曲目之间切换）
   - 上一首、暂停/播放键、下一首
   - 声音图标（点击展开调节音量）
   - 沉浸黑胶图标（点击进入该模式）
"""

import unittest
import py_compile
import re

class TestProblem2MinimalLayoutAndCenteredDock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_player_column_only_preserves_artwork(self):
        """验证播放界面左侧部分只保留专辑封面，右侧依然为滚动歌词区"""
        col_start = self.content.find('<section class="player-column">')
        self.assertNotEqual(col_start, -1)
        col_end = self.content.find('</section>', col_start)
        col_html = self.content[col_start:col_end]

        # 验证左侧包含封面舞台和卡片
        self.assertIn('class="artwork-stage"', col_html)
        self.assertIn('class="artwork-card"', col_html)
        self.assertIn('id="artworkImg"', col_html)

        # 验证右侧依然是滚动歌词区
        self.assertIn('id="lyricsViewport"', self.content)
        self.assertIn('class="right-stage"', self.content)

    def test_dock_centered_floating_styling_and_anti_overlap(self):
        """验证下边悬浮状态框居中悬浮且具备固定位置比例防重叠设置"""
        dock_css_match = re.search(r'\.bottom-dock-player\s*\{\{([\s\S]+?)\}\}\s*\.dock-album-cluster', self.content)
        self.assertIsNotNone(dock_css_match, "应存在 .bottom-dock-player CSS")
        dock_css = dock_css_match.group(1)

        # 居中悬浮验证
        self.assertIn('left: 50%', dock_css, "状态框应为居中定位 left: 50%")
        self.assertIn('translateX(-50%)', dock_css, "状态框应配合 translateX(-50%) 精准水平居中")
        self.assertIn('position: absolute;', dock_css, "状态框应为悬浮绝对定位")
        self.assertIn('backdrop-filter: blur(', dock_css, "状态框应具有半透明毛玻璃悬浮效果")

        # 防重叠设计验证
        self.assertIn('flex-shrink: 0;', dock_css, "状态框应设置 flex-shrink: 0 防止被挤压变形")
        self.assertIn('white-space: nowrap;', dock_css, "状态框应禁止换行折叠导致按键错位重叠")

    def test_dock_controls_and_interactions(self):
        """验证状态栏只保留核心按键，且交互逻辑完整准确"""
        dock_start = self.content.find('<footer class="bottom-dock-player"')
        self.assertNotEqual(dock_start, -1)
        dock_end = self.content.find('</footer>', dock_start)
        dock_html = self.content[dock_start:dock_end]

        # 1. 专辑图 + 歌名歌手，点击在右侧歌词和曲目之间切换
        self.assertIn('id="dockArtworkImg"', dock_html)
        self.assertIn('id="dockTrackTitle"', dock_html)
        self.assertIn('id="dockArtist"', dock_html)
        self.assertIn('toggleRightPanelFromDock()', dock_html)

        # 2. 上一首、播放/暂停、下一首
        self.assertIn('prevTrack()', dock_html)
        self.assertIn('togglePlayPause()', dock_html)
        self.assertIn('nextTrack()', dock_html)

        # 3. 声音图标（点击展开调节音量）
        self.assertIn('id="dockVolBtn"', dock_html)
        self.assertIn('toggleDockVolumeSlider(event)', dock_html)

        # 4. 沉浸黑胶图标（点击进入该模式）
        self.assertIn('id="dockImmersiveBtn"', dock_html)
        # 沉浸黑胶已直接移除，不再在控制栏显示
        self.assertNotIn('title="进入沉浸黑胶模式"', dock_html)

if __name__ == '__main__':
    unittest.main()
