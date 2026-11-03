"""
Unit tests for v2.0.3 Settings Redesign and Top Bar Buttons Customization:
1. Unified 4-category single-layer tabbed preferences center
2. Vector SF Symbols replacing crude system emojis
3. Top bar 3-button opacity and blur controls
4. Slogan & design philosophy update ("好好享受音乐")
5. Safety isolation and regression prevention
"""

import unittest
import os
import re

class TestSettingsRedesign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_py_path = os.path.join(os.path.dirname(__file__), 'main.py')
        with open(main_py_path, 'r', encoding='utf-8') as f:
            cls.content = f.read()

    def test_topbar_css_variables_and_styles(self):
        # 1. CSS vars in :root
        self.assertIn('--topbar-icon-opacity', self.content)
        self.assertIn('--topbar-icon-blur', self.content)

        # 2. .top-icon-btn uses vars
        self.assertIn('background: rgba(28, 28, 32, var(--topbar-icon-opacity, 0.65))', self.content)
        self.assertIn('backdrop-filter: blur(var(--topbar-icon-blur, 28px))', self.content)
        self.assertIn('background: rgba(255, 255, 255, var(--topbar-icon-opacity, 0.85))', self.content)

    def test_unified_settings_tabs(self):
        # Tabs bar and tab buttons
        self.assertIn('class="settings-tabs-bar"', self.content)
        self.assertIn('data-tab="appearance"', self.content)
        self.assertIn('data-tab="playback"', self.content)
        self.assertIn('data-tab="storage"', self.content)
        self.assertIn('data-tab="about"', self.content)

        # Tab panes
        self.assertIn('data-tab-pane="appearance"', self.content)
        self.assertIn('data-tab-pane="playback"', self.content)
        self.assertIn('data-tab-pane="storage"', self.content)
        self.assertIn('data-tab-pane="about"', self.content)

    def test_topbar_controls_and_handlers(self):
        # Sliders
        self.assertIn('id="topbarOpacityInput"', self.content)
        self.assertIn('id="topbarBlurInput"', self.content)
        self.assertIn('id="topbarOpacityVal"', self.content)
        self.assertIn('id="topbarBlurVal"', self.content)

        # JS Handlers
        self.assertIn('function onTopbarOpacityChange(', self.content)
        self.assertIn('function onTopbarBlurChange(', self.content)
        self.assertIn('function switchSettingsTab(', self.content)
        self.assertIn('function updateTopbarPreview(', self.content)

        # Defaults and persistence
        self.assertIn('topbarOpacity: 65', self.content)
        self.assertIn('topbarBlur: 28', self.content)

    def test_vector_icons_replace_emojis(self):
        # Find settings modal content
        start_idx = self.content.find('id="settingsModal"')
        end_idx = self.content.find('</div>\n    </div>', start_idx)
        settings_html = self.content[start_idx:end_idx]

        # Ensure no crude emojis in settings modal
        crude_emojis = ['🎨', '🔝', '⚡', '📂', '🧹', '🗑️', 'ℹ️']
        for emoji in crude_emojis:
            self.assertNotIn(emoji, settings_html, f"Emoji {emoji} should be replaced with vector SVG in settingsModal")

    def test_design_philosophy_slogan(self):
        # Slogan: "好好享受音乐"
        self.assertIn("好好享受音乐", self.content)
        self.assertIn("极简克制，专注纯粹 · 好好享受音乐", self.content)
        self.assertIn("把繁杂留给技术，让你静下心来，好好享受音乐。", self.content)

    def test_compatibility_and_safety(self):
        # Storage folder label preserved for portable mode
        self.assertIn('id="libraryPathSub"', self.content)

        # Clear data has caution styling and secondary action
        self.assertIn('confirmClearAllData()', self.content)
        self.assertIn('confirmClearLyricsCache()', self.content)

        # Modal opening helpers route to unified tabs
        self.assertIn('switchSettingsTab("appearance")', self.content)
        self.assertIn('switchSettingsTab("about")', self.content)

if __name__ == '__main__':
    unittest.main()
