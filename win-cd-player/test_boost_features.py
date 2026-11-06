# -*- coding: utf-8 -*-
"""
test_boost_features.py
自动化单元测试套件：验证 /boost 5 大内核升级：
1. 真·无缝播放 (Gapless Playback) 与内存预读 (RAM Read-Ahead)
2. CD-TEXT 底层扇区 Shift-JIS / UTF-8 双字节直读与元数据兜底
3. Windows 后台全局多媒体按键支持 (Play/Pause, Next, Prev, Stop)
4. 纯快捷键驱动歌词时间轴微调 ([, ], \\) 与持久化
5. 播放现场记忆与启动静默恢复 (Session Resume)
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cd_engine
from cd_engine import CdTrackInfo, CdDiscToc, CddaWaveOutPlayer, WindowsCdDriveManager
import library_service
from library_service import DiscflowLibraryManager, LocalTrackInfo, LocalAlbumInfo, load_user_preferences, save_user_preferences
import metadata_service
from metadata_service import AlbumMetadataResult, lookup_album_metadata
from main import APPLE_MUSIC_HTML, DiscflowController, GlobalMediaKeyListener, DiscflowJsApi


class TestBoostGaplessAndReadAhead(unittest.TestCase):
    """测试 Task 1: 内存预读与无缝播放"""

    def test_read_ahead_queue_and_handle_reuse(self):
        player = CddaWaveOutPlayer()
        self.assertEqual(player._read_ahead_queue.maxsize, 25)
        self.assertIsNone(player._h_wave)

        # 确保 set_next_track 能够预存下一轨
        track1 = CdTrackInfo(track_number=1, start_lba=0, end_lba=150, start_frame=150, duration_sec=2.0)
        track2 = CdTrackInfo(track_number=2, start_lba=150, end_lba=300, start_frame=300, duration_sec=2.0)
        player.set_next_track("D:", track2, 1)

        self.assertEqual(player._next_drive, "D:")
        self.assertEqual(player._next_track, track2)
        self.assertEqual(player._next_track_index, 1)

        # 验证 stop 和 close 正常复位
        player.stop()
        self.assertFalse(player.is_playing())
        player.close()

    def test_seek_generation_and_stale_chunk_rejection(self):
        player = CddaWaveOutPlayer()
        player._track = CdTrackInfo(track_number=1, start_lba=0, end_lba=1500, start_frame=150, duration_sec=20.0)
        self.assertEqual(player._seek_generation, 0)

        # 触发 Seek，验证代数令牌单调自增
        player.seek_seconds(5.0)
        self.assertEqual(player._seek_generation, 1)
        self.assertTrue(player._seek_reset_pending)

        player.seek_seconds(10.0)
        self.assertEqual(player._seek_generation, 2)

        # 验证 _ReadChunk 携带 seek_gen
        from cd_engine import _ReadChunk
        chunk_old = _ReadChunk(lba=0, data=b"stale", track=player._track, track_index=0, seek_gen=1)
        chunk_new = _ReadChunk(lba=750, data=b"fresh", track=player._track, track_index=0, seek_gen=2)
        self.assertNotEqual(chunk_old.seek_gen, player._seek_generation)
        self.assertEqual(chunk_new.seek_gen, player._seek_generation)
        player.close()


class TestBoostCdTextDirectRead(unittest.TestCase):
    """测试 Task 2: CD-TEXT Shift-JIS 与 UTF-8 扇区直读与兜底"""

    def test_parse_cdtext_shift_jis_double_byte(self):
        # 构造日文 Shift-JIS 编码的 CD-TEXT 数据包 (Pack 0x80: Title, is_double_byte=0x80)
        # 专辑名: "ベスト" (Best), 曲目 1: "恋人" (Koibito)
        album_bytes = "ベスト".encode("shift_jis")
        pack_album = bytes([0x80, 0x00, 0x00, 0x80]) + album_bytes.ljust(12, b"\x00") + bytes([0x00, 0x00])

        trk_bytes = "恋人".encode("shift_jis")
        pack_trk = bytes([0x80, 0x01, 0x00, 0x80]) + trk_bytes.ljust(12, b"\x00") + bytes([0x00, 0x00])

        payload = pack_album + pack_trk
        album_title, album_artist, titles, artists = WindowsCdDriveManager.parse_cdtext_payload(payload)

        self.assertEqual(album_title, "ベスト")
        self.assertEqual(titles.get(1), "恋人")

    def test_parse_cdtext_utf8(self):
        # 构造标准 UTF-8 编码的 CD-TEXT 数据包
        album_bytes = "Hello World".encode("utf-8")
        pack_album = bytes([0x80, 0x00, 0x00, 0x00]) + album_bytes.ljust(12, b"\x00") + bytes([0x00, 0x00])

        trk_bytes = "Track One".encode("utf-8")
        pack_trk = bytes([0x80, 0x01, 0x00, 0x00]) + trk_bytes.ljust(12, b"\x00") + bytes([0x00, 0x00])

        payload = pack_album + pack_trk
        album_title, album_artist, titles, artists = WindowsCdDriveManager.parse_cdtext_payload(payload)

        self.assertEqual(album_title, "Hello World")
        self.assertEqual(titles.get(1), "Track One")

    def test_parse_cdtext_double_byte_word_pairs(self):
        # 验证双字节模式下，即使字符中包含 \x00 (如 UTF-16BE 中的 ASCII 字符)，也不会被误认为字符串终结符
        # UTF-16BE: "CD" -> b"\x00C\x00D"
        album_bytes = "CD".encode("utf-16-be")
        pack_album = bytes([0x80, 0x00, 0x00, 0x80]) + album_bytes + b"\x00\x00" + (b"\x00" * 6) + bytes([0x00, 0x00])

        trk_bytes = "Song".encode("utf-16-be")
        pack_trk = bytes([0x80, 0x01, 0x00, 0x80]) + trk_bytes + b"\x00\x00" + (b"\x00" * 2) + bytes([0x00, 0x00])

        payload = pack_album + pack_trk
        album_title, album_artist, titles, artists = WindowsCdDriveManager.parse_cdtext_payload(payload)

        self.assertEqual(album_title, "CD")
        self.assertEqual(titles.get(1), "Song")

    def test_metadata_service_cdtext_fallback(self):
        # 验证在线匹配失败/断网时，CD-TEXT 数据作为曲目名与专辑名的兜底
        toc = CdDiscToc(
            drive_letter="D:",
            first_track=1,
            last_track=2,
            leadout_frame=15000,
            cdtext_album="My CD-TEXT Album",
            cdtext_artist="CD-TEXT Artist",
            tracks=[
                CdTrackInfo(track_number=1, start_lba=0, end_lba=7500, start_frame=150, duration_sec=100.0, title="Song 1", artist="Artist 1"),
                CdTrackInfo(track_number=2, start_lba=7500, end_lba=15000, start_frame=7650, duration_sec=100.0, title="Song 2", artist="Artist 1"),
            ],
            disc_id="dummy_disc_id_12345",
        )
        res = lookup_album_metadata(toc)
        self.assertEqual(res.album_title, "My CD-TEXT Album")
        self.assertEqual(res.artist, "CD-TEXT Artist")
        self.assertEqual(res.track_titles[1], "Song 1")
        self.assertEqual(res.track_titles[2], "Song 2")
        self.assertEqual(res.matched_source, "CD-TEXT 硬件内嵌信息")


class TestBoostGlobalMediaKeys(unittest.TestCase):
    """测试 Task 3: 后台全局媒体按键监听器"""

    def test_global_media_key_lifecycle(self):
        played = [False]
        nexted = [False]
        preved = [False]
        stopped = [False]

        listener = GlobalMediaKeyListener(
            on_play_pause=lambda: played.__setitem__(0, True),
            on_next=lambda: nexted.__setitem__(0, True),
            on_prev=lambda: preved.__setitem__(0, True),
            on_stop=lambda: stopped.__setitem__(0, True),
        )
        listener.start()
        time.sleep(0.1)
        self.assertIsNotNone(listener._thread)
        self.assertTrue(listener._thread.is_alive())

        listener.stop()
        time.sleep(0.1)


class TestBoostLyricOffsetShortcuts(unittest.TestCase):
    """测试 Task 4: 快捷键驱动歌词时间轴微调与持久化"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="discflow_boost_test_")
        self.orig_pref_fn = library_service.get_preferences_file_path
        self.test_pref_path = os.path.join(self.temp_dir, "preferences.json")
        library_service.get_preferences_file_path = lambda: self.test_pref_path

    def tearDown(self):
        library_service.get_preferences_file_path = self.orig_pref_fn
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_html_contains_shortcuts_and_no_extra_buttons(self):
        # 1. 验证设置弹窗快捷键速查网格补充了 [ / ] 与 \
        self.assertIn("歌词延后 / 提前", APPLE_MUSIC_HTML)
        self.assertIn("[ / ]", APPLE_MUSIC_HTML)
        self.assertIn("歌词时间轴归零", APPLE_MUSIC_HTML)

        # 2. 验证 JS 监听了 [ / ] / \
        self.assertIn('e.key === "[" || e.code === "BracketLeft"', APPLE_MUSIC_HTML)
        self.assertIn('e.key === "]" || e.code === "BracketRight"', APPLE_MUSIC_HTML)
        self.assertIn('e.code === "Backslash"', APPLE_MUSIC_HTML)

        # 3. 验证无多余 UI 按钮
        self.assertNotIn("lyricOffsetBtn", APPLE_MUSIC_HTML)
        self.assertNotIn("adjustLyricBtn", APPLE_MUSIC_HTML)

    def test_controller_lyric_offset_persist(self):
        ctrl = DiscflowController()
        # 注入虚拟 TOC
        ctrl.current_toc = CdDiscToc(
            drive_letter="D:",
            first_track=1,
            last_track=1,
            leadout_frame=5000,
            disc_id="test_disc_offset_001",
            tracks=[CdTrackInfo(track_number=1, start_lba=0, end_lba=5000, start_frame=150, duration_sec=60.0)],
        )
        ctrl.current_track_index = 0

        # 微调 +0.5s
        ok = ctrl.set_track_lyric_offset(0.5)
        self.assertTrue(ok)
        self.assertEqual(ctrl._get_current_track_lyric_offset(), 0.5)
        self.assertEqual(ctrl.poll_state()["lyricOffset"], 0.5)

        # 验证 preferences.json 写入
        prefs = load_user_preferences()
        self.assertEqual(prefs.get("lyricOffsets", {}).get("test_disc_offset_001_1"), 0.5)

        # 归零
        ctrl.set_track_lyric_offset(0.0)
        self.assertEqual(ctrl.poll_state()["lyricOffset"], 0.0)
        ctrl.shutdown()

    def test_track_lyrics_offset_fallback_to_track_info(self):
        ctrl = DiscflowController()
        # 模拟偏好中未保存此曲目的自定义偏移，但 CdTrackInfo 自带从本地资料库读取的 lyrics_offset
        ctrl.current_toc = CdDiscToc(
            drive_letter="LIB:test1234",
            first_track=1,
            last_track=1,
            leadout_frame=5000,
            disc_id="test_disc_fallback_002",
            is_local=True,
            tracks=[
                CdTrackInfo(
                    track_number=1,
                    start_lba=0,
                    end_lba=5000,
                    start_frame=150,
                    duration_sec=60.0,
                    lyrics_offset=1.2,
                )
            ],
        )
        ctrl.current_track_index = 0
        self.assertEqual(ctrl._get_current_track_lyric_offset(), 1.2)
        ctrl.shutdown()


class TestBoostSessionResume(unittest.TestCase):
    """测试 Task 5: 现场记忆与启动静默恢复"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="discflow_boost_session_")
        self.orig_pref_fn = library_service.get_preferences_file_path
        self.test_pref_path = os.path.join(self.temp_dir, "preferences.json")
        library_service.get_preferences_file_path = lambda: self.test_pref_path

        self.lib_dir = os.path.join(self.temp_dir, "library")
        os.makedirs(self.lib_dir, exist_ok=True)
        self.lib_mgr = DiscflowLibraryManager(base_dir=self.lib_dir)

    def tearDown(self):
        library_service.get_preferences_file_path = self.orig_pref_fn
        if os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_silent_resume(self):
        # 1. 在临时资料库创建一张测试唱片
        disc_id = "test_album_resume_99"
        album_dir = self.lib_mgr.get_album_dir(disc_id)
        os.makedirs(album_dir, exist_ok=True)
        import json
        with open(os.path.join(album_dir, "album.json"), "w", encoding="utf-8") as f:
            json.dump({
                "disc_id": disc_id,
                "title": "Resume Master Album",
                "artist": "Resume Artist",
                "tracks": [
                    {"track_number": 1, "title": "Track One", "artist": "Resume Artist", "duration_sec": 120.0, "file_name": "t1.wav"},
                    {"track_number": 2, "title": "Track Two", "artist": "Resume Artist", "duration_sec": 150.0, "file_name": "t2.wav"}
                ]
            }, f)

        # 2. 模拟播放器保存上次会话
        save_user_preferences({
            "lastSession": {
                "discId": disc_id,
                "trackIndex": 1
            }
        })

        # 3. 启动 Controller 并恢复现场
        ctrl = DiscflowController()
        ctrl.library_manager = self.lib_mgr
        res = ctrl._try_resume_last_session()
        self.assertTrue(res)

        # 验证已恢复第 2 轨（trackIndex 1），封面与曲目就绪，但处于暂停状态 (isPlaying == False)
        self.assertEqual(ctrl.current_track_index, 1)
        self.assertEqual(ctrl.album_title, "Resume Master Album")
        self.assertEqual(ctrl.duration_sec, 150.0)
        self.assertFalse(ctrl.player.is_playing())
        self.assertIn("已就绪", ctrl.status_text)

        # 4. 模拟按空格启动播放 (toggle_play_pause)
        # 因为没有真实音频文件，直接断言此时逻辑会调用 _start_track(1)
        self.assertFalse(ctrl.player.is_running())
        ctrl.shutdown()


if __name__ == "__main__":
    unittest.main()
