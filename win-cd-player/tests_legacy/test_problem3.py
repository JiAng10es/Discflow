# -*- coding: utf-8 -*-
"""
自动化测试用例：验证问题 3（沉浸黑胶唱针恒定咬合、底栏精简与液态面板 Bug 修复）
1. 沉浸黑胶模式：黑胶播放指针固定比例，黑胶盘与唱臂置于统一正方形容器 .md-vinyl-rig 中，百分比锚定，杜绝缩放漂移。
2. 沉浸底栏：去除左下角和右下角两个按钮，去除播放键旁边的循环播放键。
3. 液态形变播放列表面板 3 个 Bug 修复：
   - 音量调节指示条无反馈修复（即时更新 + 轮询同步）。
   - 顺序/随机/单曲切换无反馈修复（调用后端 API + 显著高亮反馈）。
   - 歌曲切换无反馈修复（即时高亮当前选中项 + 状态同步）。
"""

import unittest
import py_compile
import re

class TestProblem3ImmersiveVinylAndFluidFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_tonearm_rigid_locking_in_vinyl_rig(self):
        """验证黑胶盘与唱臂置于统一唱机比例容器 .md-vinyl-rig 中，百分比锚定杜绝缩放漂移"""
        # 1. 检查 HTML 结构：immersiveVinylDisc 与 immersiveTonearm 均在 mdVinylRig 内部
        rig_start = self.content.find('id="mdVinylRig"')
        self.assertNotEqual(rig_start, -1, "应存在 mdVinylRig 统一固定比例唱机容器")
        rig_end = self.content.find('<!-- 悬浮双语歌词浮动层 -->', rig_start)
        rig_html = self.content[rig_start:rig_end]

        self.assertIn('id="immersiveVinylDisc"', rig_html, "黑胶盘必须位于 mdVinylRig 容器内")
        self.assertIn('id="immersiveTonearm"', rig_html, "机械唱臂必须位于 mdVinylRig 容器内以形成绝对几何约束")

        # 2. 检查 CSS 样式：md-vinyl-rig 为 1:1 固定正方形比例
        rig_css_match = re.search(r'\.md-vinyl-rig\s*\{\{([\s\S]+?)\}\}\s*\/\*', self.content)
        self.assertIsNotNone(rig_css_match, "应存在 .md-vinyl-rig CSS")
        rig_css = rig_css_match.group(1)
        self.assertIn('aspect-ratio: 1 / 1', rig_css, "唱机容器应为 1:1 等比容器")

        # 检查唱臂相对于唱机容器采用百分比定位
        tonearm_css_match = re.search(r'\.md-tonearm-assembly\s*\{\{([^}]+)\}\}', self.content)
        self.assertIsNotNone(tonearm_css_match, "应存在 .md-tonearm-assembly CSS")
        tonearm_css = tonearm_css_match.group(1)
        self.assertIn('%', tonearm_css, "唱臂应采用百分比锚定以杜绝缩放漂移")

    def test_immersive_dock_cleanup(self):
        """验证沉浸底栏删除了左下角微缩图按钮、右下角按钮以及播放键旁的模式按钮"""
        dock_start = self.content.find('class="md-dock-container">')
        self.assertNotEqual(dock_start, -1)
        dock_end = self.content.find('<div style="display:none;">', dock_start)
        dock_visible_html = self.content[dock_start:dock_end]

        # 验证可见结构中已无左下角/右下角按钮
        self.assertNotIn('md-dock-thumbnail-btn', dock_visible_html, "可见底栏不应包含左下角按钮")
        self.assertNotIn('md-dock-more-btn', dock_visible_html, "可见底栏不应包含右下角按钮")

        # 验证播放键旁已无循环播放模式按钮
        self.assertNotIn('id="mdDockModeBtn"', dock_visible_html, "可见底栏不应再包含循环模式按钮")

    def test_fluid_morph_bugs_resolved(self):
        """验证液态形变面板音量指示条、播放模式切换、切歌选歌的即时反馈修复"""
        # 1. 验证音量指示条即时更新代码
        self.assertIn('const fill = document.getElementById("fluidVolFill");', self.content)
        self.assertIn('if (fill) fill.style.width =', self.content)

        # 2. 验证模式切换调用后端 API 并提供高亮反馈
        self.assertIn('window.pywebview.api.set_play_mode(mode)', self.content, "切换播放模式应调用后端 set_play_mode API")
        self.assertIn('btnAll.classList.toggle("active"', self.content)

        # 3. 验证切歌选歌立即提供视觉高亮与状态更新反馈
        self.assertIn('r.classList.add("is-current")', self.content, "选歌切歌应立即添加 is-current 高亮反馈")

if __name__ == '__main__':
    unittest.main()
