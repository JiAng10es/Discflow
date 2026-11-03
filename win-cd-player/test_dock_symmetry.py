# -*- coding: utf-8 -*-
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main as M

HTML = M.APPLE_MUSIC_HTML


class TestDockSymmetry(unittest.TestCase):
    def test_dock_uses_symmetric_grid_layout(self):
        """验证底栏采用三段式对称 Grid 布局，中控物理居中"""
        dock_match = re.search(r"\.bottom-dock-player\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(dock_match, "应存在 .bottom-dock-player 样式定义")
        dock_css = dock_match.group(1)

        self.assertIn("display: grid", dock_css, "底栏应切换为 display: grid 消除 flex 偏心")
        self.assertIn("grid-template-columns", dock_css, "底栏应明确定义三列对称 grid-template-columns")
        self.assertIn("left: 50%", dock_css, "底栏必须保持水平居中定位 left: 50%")
        self.assertIn("translateX(-50%)", dock_css, "底栏必须配合 translateX(-50%) 精确居中")

    def test_clusters_alignment_and_proportions(self):
        """验证左翼靠左、中控居中、右翼靠右的绝对对称对齐属性"""
        # 1. 左翼曲目信息区
        left_match = re.search(r"\.dock-album-cluster\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(left_match, "应存在 .dock-album-cluster 样式")
        left_css = left_match.group(1)
        self.assertIn("justify-self: start", left_css, "左翼曲目卡片应靠左网格对齐")
        self.assertIn("max-width: 236px", left_css, "左翼应有 236px 最大宽度约束")

        # 2. 中控播放区
        center_match = re.search(r"\.dock-center-cluster\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(center_match, "应存在 .dock-center-cluster 样式")
        center_css = center_match.group(1)
        self.assertIn("justify-self: center", center_css, "中控播控区必须为 justify-self: center 绝对居中")
        self.assertIn("max-width: 380px", center_css, "中控进度条与按钮区应有最大宽度限制")

        # 3. 右翼操作区
        right_match = re.search(r"\.dock-right-actions\s*\{([^}]+)\}", HTML)
        self.assertIsNotNone(right_match, "应存在 .dock-right-actions 样式")
        right_css = right_match.group(1)
        self.assertIn("justify-self: end", right_css, "右翼操作区应靠右网格对齐")
        self.assertIn("max-width: 236px", right_css, "右翼操作区应保持 236px 最大宽度与左翼对称")

    def test_controls_remain_intact(self):
        """验证底栏现有按键（上一曲、播放/暂停、下一曲、进度条、双语翻译、音量）均完整保留"""
        required_elements = [
            'id="dockArtworkImg"',
            'id="dockTrackTitle"',
            'id="dockArtist"',
            'onclick="prevTrack()"',
            'id="dockPlayPauseBtn"',
            'onclick="nextTrack()"',
            'id="dockScrubberHit"',
            'id="dockTransBtn"',
            'id="dockVolBtn"',
            'id="dockVolumeWrap"',
        ]
        for elem in required_elements:
            self.assertIn(elem, HTML, f"底栏核心元素 {elem} 必须完整保留")


if __name__ == "__main__":
    unittest.main()
