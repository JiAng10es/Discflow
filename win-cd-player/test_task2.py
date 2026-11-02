# -*- coding: utf-8 -*-
import unittest
import py_compile
import re

class TestTask2LayoutAndDock(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        py_compile.compile('win-cd-player/main.py', doraise=True)
        with open('win-cd-player/main.py', encoding='utf-8') as f:
            cls.content = f.read()

    def test_sidebar_redundant_text_removed(self):
        self.assertNotIn('<aside class="app-sidebar">', self.content)
        self.assertNotIn('<span>正在播放 (CD)</span>', self.content)
        self.assertNotIn('<span>双语歌词</span>', self.content)
        self.assertNotIn('<span>全屏沉浸舞台</span>', self.content)
        self.assertNotIn('<span>无损抓轨归档</span>', self.content)

    def test_minimal_two_column_layout(self):
        self.assertIn('class="player-column"', self.content)
        self.assertIn('class="right-stage"', self.content)
        self.assertIn('class="artwork-card"', self.content)
        self.assertIn('id="lyricsViewport"', self.content)

    def test_dock_centered_pure_controls(self):
        dock_start = self.content.find('<footer class="bottom-dock-player"')
        dock_end = self.content.find('</footer>', dock_start)
        dock_html = self.content[dock_start:dock_end]

        self.assertIn('id="dockArtworkImg"', dock_html)
        self.assertIn('toggleRightPanelFromDock()', dock_html)
        self.assertIn('prevTrack()', dock_html)
        self.assertIn('togglePlayPause()', dock_html)
        self.assertIn('nextTrack()', dock_html)
        self.assertIn('id="dockVolBtn"', dock_html)
        self.assertIn('id="dockImmersiveBtn"', dock_html)

    def test_dock_centered_floating_styling(self):
        dock_match = re.search(r'\.bottom-dock-player\s*\{\{([\s\S]+?)\}\}\s*\.dock-album-cluster', self.content)
        self.assertIsNotNone(dock_match)
        dock_css = dock_match.group(1)
        self.assertIn('position: absolute;', dock_css)
        self.assertIn('left: 50%', dock_css)
        self.assertIn('translateX(-50%)', dock_css)
        self.assertIn('backdrop-filter: blur(', dock_css)

if __name__ == '__main__':
    unittest.main()
