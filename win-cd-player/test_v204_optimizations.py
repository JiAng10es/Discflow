"""
Unit tests for v2.0.4 Optimizations:
1. Modern Apple-style floating scrollbar rules across all modal & candidate lists
2. Redesigned Apple Control Center style frosted capsule volume popover (eliminating harsh neon red)
3. Lyrics display bug fixes:
   - Intro stage (elapsedSec < first lyric time) does NOT prematurely activate line 0
   - Multi-viewport synchronization for standalone lyrics view (#standaloneLyricsViewport)
   - Pause-state and seek karaoke text rendering
4. Refined Apple SF Symbols rescan CD drive icon
"""

import unittest
import os
import py_compile

class TestV204Optimizations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_py_path = os.path.join(os.path.dirname(__file__), 'main.py')
        py_compile.compile(main_py_path, doraise=True)
        with open(main_py_path, 'r', encoding='utf-8') as f:
            cls.content = f.read()

    def test_modern_apple_scrollbars(self):
        # 1. Global webkit scrollbar rules
        self.assertIn('::-webkit-scrollbar {', self.content.replace('{{', '{'))
        self.assertIn('width: 6px', self.content)
        self.assertIn('background: rgba(255, 255, 255, 0.2)', self.content)

        # 2. Scrollbar width and color on modal lists
        self.assertIn('.lyric-candidate-list', self.content)
        self.assertIn('.album-cand-list', self.content)
        self.assertIn('.settings-modal-body', self.content)
        self.assertIn('#standaloneLyricsViewport', self.content)
        self.assertIn('scrollbar-width: thin', self.content)

    def test_volume_popover_capsule_design(self):
        # 1. No harsh neon red
        self.assertNotIn('background: var(--color-accent, #FA2D48);', self.content)

        # 2. Apple capsule thick track & pure white fill
        self.assertIn('width: 52px;', self.content)
        self.assertIn('background: #FFFFFF;', self.content)
        self.assertIn('border-radius: 14px;', self.content)

        # 3. Mute button as frosted circular button
        self.assertIn('.dock-vol-popover-icon', self.content)

    def test_rescan_cd_icon(self):
        # Refined Apple SF Symbols dual-stream rescan icon
        self.assertIn('id="topRescanBtn"', self.content)
        self.assertIn('circle cx="12" cy="12" r="3"', self.content)
        self.assertIn('M12 3a9 9 0 0 1 8.5 6', self.content)
        self.assertIn('M12 21a9 9 0 0 1-8.5-6', self.content)

    def test_lyrics_bug_fixes(self):
        # 1. Intro bug fix: activeIdx starts at -1
        self.assertIn('let activeIdx = -1;', self.content)

        # 2. Multi-viewport sync: standaloneLyricsViewport is populated
        self.assertIn('document.getElementById("standaloneLyricsViewport")', self.content)
        self.assertIn('viewports = [', self.content)

        # 3. Karaoke loop handles pause state with elapsedSec
        self.assertIn('appState.isPlaying ? karaokeClock : (Number(appState.elapsedSec) || 0)', self.content)

        # 4. updateActiveLyric supports forceUpdate
        self.assertIn('function updateActiveLyric(elapsedSec, forceUpdate = false)', self.content)

if __name__ == '__main__':
    unittest.main()
