# -*- coding: utf-8 -*-
"""
test_ambient_and_opacity.py
验证：
1. 左侧专辑封面周边散光自然融合 (径向羽化遮罩、多级扩展、深浅双模式专属光影)
2. 自定义导航栏透明度最低支持为 0% 且 0 值正常持久化及视觉适配
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from library_service import load_user_preferences, save_user_preferences
from main import APPLE_MUSIC_HTML


class TestAmbientAndOpacity(unittest.TestCase):
    def setUp(self):
        self.html = APPLE_MUSIC_HTML
        self._orig_prefs = load_user_preferences()

    def tearDown(self):
        save_user_preferences(self._orig_prefs)

    def test_ambient_shadow_radial_feathering_and_spread(self):
        # 1. 散光层采用径向遮罩消除硬边界
        self.assertIn("mask-image: radial-gradient(closest-side,", self.html)
        self.assertIn("-webkit-mask-image: radial-gradient(closest-side,", self.html)

        # 2. 散光层范围扩大至 -28% 且为圆形衰减
        self.assertIn("inset: -28%;", self.html)
        self.assertIn("border-radius: 50%;", self.html)

        # 3. 散光不透明度调优至柔和自然范围
        self.assertIn("opacity: 0.26;", self.html)
        self.assertIn("body.is-playing .artwork-ambient-shadow", self.html)
        self.assertIn("opacity: 0.55;", self.html)

    def test_light_mode_ambient_and_shadow_adaptation(self):
        # 1. 浅色模式散光柔化与提亮
        self.assertIn('[data-theme="light"] .artwork-ambient-shadow', self.html)
        self.assertIn("brightness(1.06)", self.html)
        self.assertIn("opacity: 0.5;", self.html)

        # 2. 浅色模式专辑卡片投影去黑化
        self.assertIn('[data-theme="light"] .artwork-card', self.html)
        self.assertIn("rgba(0, 0, 0, 0.10)", self.html)

        # 3. 浅色模式背景流体光斑混合模式优化
        self.assertIn('[data-theme="light"] .mesh-blob', self.html)
        self.assertIn("mix-blend-mode: multiply;", self.html)

        # 4. 浅色模式流体背景必须有足够存在感，不能只剩纯白底色（v1.11.2）
        self.assertIn('[data-theme="light"] .mesh-canvas', self.html)
        self.assertIn('[data-theme="light"] body.is-playing .mesh-canvas.has-cover', self.html)
        self.assertIn("opacity: 0.62;", self.html)
        self.assertIn("linear-gradient(180deg, #FCFCFE 0%, #F4F4F8 46%, #ECECF2 100%)", self.html)

    def test_dock_opacity_slider_min_zero(self):
        # 1. 输入滑块 min 改为 0
        self.assertIn('id="dockOpacityInput" min="0" max="100"', self.html)

        # 2. 0% 透明度视觉状态适配
        self.assertIn('.bottom-dock-player.is-fully-transparent', self.html)
        self.assertIn('is-fully-transparent', self.html)

        # 3. 0% 透明度物理持久化与读取
        ok = save_user_preferences({"dockOpacity": 0})
        self.assertTrue(ok)
        loaded = load_user_preferences()
        self.assertEqual(loaded.get("dockOpacity"), 0)


if __name__ == "__main__":
    unittest.main()
