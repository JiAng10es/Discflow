# -*- coding: utf-8 -*-
"""
test_volume_and_playback.py
验证：
1. 音量指示条：前端 setVolumePercent 同步 appState.volume，渲染循环不再用硬编码 80% 覆盖用户音量
2. 音量后端状态：set_volume 记录音量、poll_state 下发、重启从 preferences.json 恢复
3. 播放界面细节：Dock 预留区、曲目列表底部留白、Dock 缩略图占位、Ctrl+F 快捷键
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from library_service import load_user_preferences, save_user_preferences
from main import APPLE_MUSIC_HTML, DiscflowController


class TestVolumeIndicatorFrontend(unittest.TestCase):
    def setUp(self):
        self.html = APPLE_MUSIC_HTML

    def test_render_loop_no_longer_forces_80_percent(self):
        # 旧 Bug：渲染循环每 150ms 写入 `appState.volume || 80`，拖动后指示条弹回 80%，音量 0 也显示 80%
        self.assertNotIn("appState.volume || 80", self.html)
        self.assertIn("if (!isDraggingDockVol) renderVolumeUI(appState.volume);", self.html)

    def test_set_volume_updates_app_state(self):
        start = self.html.index("function setVolumePercent(")
        body = self.html[start:start + 600]
        self.assertIn("appState.volume = v;", body)
        self.assertIn("renderVolumeUI(v);", body)
        self.assertIn("set_volume(v)", body)

    def test_drag_state_and_persist_on_release(self):
        self.assertIn('dockVolHit.classList.add("is-dragging")', self.html)
        self.assertIn('addEventListener("pointerup", endDockVolDrag)', self.html)
        self.assertIn('addEventListener("pointercancel", endDockVolDrag)', self.html)
        self.assertIn("save_preferences({ volume: appState.volume })", self.html)

    def test_knob_mute_and_dynamic_icon(self):
        self.assertIn('id="dockVolKnob"', self.html)
        self.assertIn('onclick="toggleMute()"', self.html)
        self.assertIn('id="dockVolBtnIcon"', self.html)
        self.assertIn("function volumeIconMarkup(", self.html)
        self.assertIn('addEventListener("wheel"', self.html)


class TestVolumeBackend(unittest.TestCase):
    def setUp(self):
        self._orig_volume = load_user_preferences().get("volume", 80)

    def tearDown(self):
        save_user_preferences({"volume": self._orig_volume})

    def test_set_volume_reflected_in_poll_state(self):
        ctrl = DiscflowController()
        ctrl.set_volume(37)
        self.assertEqual(ctrl.volume_pct, 37)
        self.assertEqual(ctrl.poll_state()["volume"], 37)
        self.assertAlmostEqual(ctrl.player._volume, 0.37, places=3)

    def test_set_volume_clamps_and_ignores_garbage(self):
        ctrl = DiscflowController()
        ctrl.set_volume(150)
        self.assertEqual(ctrl.volume_pct, 100)
        ctrl.set_volume(-20)
        self.assertEqual(ctrl.volume_pct, 0)
        ctrl.set_volume("abc")
        self.assertEqual(ctrl.volume_pct, 0)

    def test_volume_restored_after_restart(self):
        save_user_preferences({"volume": 23})
        ctrl = DiscflowController()
        self.assertEqual(ctrl.volume_pct, 23)
        self.assertEqual(ctrl.poll_state()["volume"], 23)
        self.assertAlmostEqual(ctrl.player._volume, 0.23, places=3)


class TestPlaybackDetails(unittest.TestCase):
    def setUp(self):
        self.html = APPLE_MUSIC_HTML

    def test_artwork_column_reserves_dock_space(self):
        self.assertIn("#viewCd .player-column {", self.html)
        self.assertIn("padding-bottom: var(--dock-reserve, 108px);", self.html)
        self.assertIn("padding-bottom: calc(var(--dock-reserve, 108px) + 16px);", self.html)

    def test_dock_thumbnail_placeholder_and_no_redundant_decode(self):
        self.assertIn(".dock-artwork-thumb.is-empty {", self.html)
        self.assertIn("dockArt.dataset.coverSrc !== nextSrc", self.html)

    def test_ctrl_f_opens_search(self):
        idx = self.html.index('window.addEventListener("keydown"')
        self.assertIn("e.ctrlKey", self.html[idx:idx + 400])
        self.assertIn("openSearchModal();", self.html[idx:idx + 400])


if __name__ == "__main__":
    unittest.main()
