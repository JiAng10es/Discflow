"""
Unit tests for v2.0.5 Settings Layout Unification & Zero-Jitter Optimization:
1. Dialog dimensions stability (fixed size across all 4 tabs)
2. Scrollbar-gutter stability (no horizontal twitching/shift)
3. Elimination of brand slogans & design philosophy
4. Unified card groups and typography across all tabs
5. Clean tab switching with scrollTop reset
"""

import unittest
import os
import re

class TestSettingsV205(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_py_path = os.path.join(os.path.dirname(__file__), 'main.py')
        with open(main_py_path, 'r', encoding='utf-8') as f:
            cls.content = f.read()

    def test_settings_dialog_stable_dimensions(self):
        # 1. Dialog width and height are strictly defined
        self.assertIn('width: 680px !important;', self.content)
        self.assertIn('height: 590px !important;', self.content)
        self.assertIn('box-sizing: border-box !important;', self.content)

    def test_settings_body_scrollbar_gutter_stable(self):
        # 2. Scrollbar gutter is reserved to prevent layout twitching
        self.assertIn('scrollbar-gutter: stable;', self.content)
        self.assertIn('overflow-x: hidden;', self.content)

    def test_switch_tab_resets_scroll(self):
        # 3. Tab switching resets scrollTop to top smoothly
        self.assertIn('body.scrollTop = 0;', self.content)

    def test_brand_slogans_completely_removed(self):
        # 4. Brand slogans removed as requested by user
        self.assertNotIn('把繁杂留给技术，让你静下心来，好好享受音乐。', self.content)
        self.assertNotIn('极简克制，专注纯粹 · 好好享受音乐', self.content)
        self.assertNotIn('设计理念', self.content)

    def test_unified_cards_and_typography(self):
        # 5. All tabs use unified .settings-card-group architecture
        start_idx = self.content.find('id="settingsModal"')
        end_idx = self.content.find('<!-- 逐轨手动匹配歌词弹窗 -->', start_idx)
        modal_html = self.content[start_idx:end_idx]

        # Verify cards present in modal
        self.assertIn('class="settings-card-group"', modal_html)
        self.assertIn('class="settings-row-item"', modal_html)
        self.assertIn('data-tab-pane="appearance"', modal_html)
        self.assertIn('data-tab-pane="playback"', modal_html)
        self.assertIn('data-tab-pane="storage"', modal_html)
        self.assertIn('data-tab-pane="about"', modal_html)

if __name__ == '__main__':
    unittest.main()
