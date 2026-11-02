# -*- coding: utf-8 -*-
"""
test_v1112_clean_titles.py
验证 v1.11.12「专辑优先 + 乱码治理」：

1. 标题清洗：HTML 实体反转义、零宽字符剔除、控制字符剔除、空白折叠；
2. 乱码判定：Shift-JIS 误解码产物（テルミ━蕙 / ソ━膝）能被识别，正常日文/英文曲名不误判；
3. 专辑候选重排：同名同艺术家的「干净版 / 乱码版」两个副本，干净版必须排前面；
4. 应用候选时：乱码曲名不得覆盖本地曲名；专辑艺术家同步写进曲目；
5. 批量歌词未命中的轨道会被记录，前端有「歌词待匹配」标记与逐轨兜底入口。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import library_service
import metadata_service
from metadata_service import (
    _garbled_score,
    _looks_garbled,
    _sanitize_title,
    _tracks_cleanliness,
)
from main import APPLE_MUSIC_HTML


class TestSanitizeTitle(unittest.TestCase):
    def test_unescape_html_entities(self):
        self.assertEqual(_sanitize_title("Rock &amp; Roll"), "Rock & Roll")
        self.assertEqual(_sanitize_title("Don&#39;t Stop"), "Don't Stop")

    def test_strip_zero_width_and_control(self):
        self.assertEqual(_sanitize_title("Luv\u200bsic"), "Luvsic")
        self.assertEqual(_sanitize_title("\ufeff予感"), "予感")
        self.assertEqual(_sanitize_title("a\x00b\x07c"), "abc")

    def test_collapse_whitespace(self):
        self.assertEqual(_sanitize_title("  三文   ゴシップ  "), "三文 ゴシップ")
        self.assertEqual(_sanitize_title("a\u3000\u3000b"), "a b")

    def test_keep_normal_text_untouched(self):
        for s in ("テル・ミー・オー・ママ", "Luv(sic), Part 2", "男孩 a portrait in time", "〇地点から"):
            self.assertEqual(_sanitize_title(s), s, f"正常曲名不应被改动：{s}")


class TestGarbledDetection(unittest.TestCase):
    def test_shiftjis_mojibake_detected(self):
        # 这两条是 QQ 音乐 001opQHb1GhBPD 里真实的误解码曲名
        self.assertTrue(_looks_garbled("テルミ━蕙"))
        self.assertTrue(_looks_garbled("ソ━膝"))
        self.assertTrue(_looks_garbled("テルミ━蕙リミックス)"))

    def test_replacement_char_detected(self):
        self.assertTrue(_looks_garbled("予感\ufffd"))
        self.assertEqual(_garbled_score("予感\ufffd"), 1.0)

    def test_clean_titles_not_flagged(self):
        for s in (
            "テル・ミー・オー・ママ",
            "Tell me oh mama (リミックス)",
            "予感 (预感)",
            "Luv(sic), Part 2",
            "日記,七月二十一號,晴(Skit)",
            "Sunday (星期天)",
            "no. no. no.",
            "スーパースター",
        ):
            self.assertFalse(_looks_garbled(s), f"正常曲名被误判为乱码：{s}")

    def test_tracks_cleanliness(self):
        clean = [{"title": "予感"}, {"title": "Sunday"}]
        dirty = [{"title": "予感"}, {"title": "ソ━膝"}]
        self.assertEqual(_tracks_cleanliness(clean), 1.0)
        self.assertAlmostEqual(_tracks_cleanliness(dirty), 0.5)
        self.assertEqual(_tracks_cleanliness([]), 1.0)


class TestAlbumCandidateRerank(unittest.TestCase):
    """同名同艺术家的干净版 / 乱码版，必须干净版优先"""

    CLEAN_TRACKS = [
        {"title": "テル・ミー・オー・ママ", "duration_sec": 250.0, "song_id": "A1"},
        {"title": "Tell me oh mama (リミックス)", "duration_sec": 260.0, "song_id": "A2"},
        {"title": "予感 (预感)", "duration_sec": 240.0, "song_id": "A3"},
    ]
    DIRTY_TRACKS = [
        {"title": "テル・ミー・オー・ママ", "duration_sec": 250.0, "song_id": "B1"},
        {"title": "テルミ━蕙", "duration_sec": 260.0, "song_id": "B2"},
        {"title": "ソ━膝", "duration_sec": 240.0, "song_id": "B3"},
    ]

    def setUp(self):
        self._orig = (
            metadata_service._qq_album_search,
            metadata_service._itunes_album_search_lite,
            metadata_service._netease_album_search,
            metadata_service._qq_album_detail,
        )

    def tearDown(self):
        (
            metadata_service._qq_album_search,
            metadata_service._itunes_album_search_lite,
            metadata_service._netease_album_search,
            metadata_service._qq_album_detail,
        ) = self._orig

    def _install_fakes(self):
        base = {
            "source": "qq",
            "title": "Quiet Emotion",
            "artist": "具島直子",
            "year": "1997",
            "track_count": 3,
            "cover_url": "",
            "tracks": [],
        }
        dirty = dict(base, album_id="DIRTY")
        clean = dict(base, album_id="CLEAN")

        # 故意让乱码版先返回，验证重排能把干净版顶上去
        metadata_service._qq_album_search = lambda q, limit=6: [dirty, clean]
        metadata_service._itunes_album_search_lite = lambda q, country, limit=6: []
        metadata_service._netease_album_search = lambda q, limit=6: []

        def _detail(mid):
            if mid == "DIRTY":
                return "1997", self.DIRTY_TRACKS
            return "1997", self.CLEAN_TRACKS

        metadata_service._qq_album_detail = _detail

    def test_clean_album_ranked_first(self):
        self._install_fakes()
        cands = metadata_service.search_album_candidates("Quiet Emotion", "具島直子", 3)
        self.assertGreaterEqual(len(cands), 2)
        self.assertEqual(cands[0]["album_id"], "CLEAN", "干净版专辑必须排第一")
        self.assertFalse(cands[0]["garbled"])
        self.assertEqual(cands[0]["dirtyTracks"], 0)
        self.assertEqual(cands[0]["cleanliness"], 1.0)

        dirty = next(c for c in cands if c["album_id"] == "DIRTY")
        self.assertTrue(dirty["garbled"], "含乱码曲名的候选应被标记")
        self.assertEqual(dirty["dirtyTracks"], 2)
        self.assertGreater(dirty["score"], cands[0]["score"], "乱码候选得分应更差")

    def test_candidates_expose_cleanliness_fields(self):
        self._install_fakes()
        cands = metadata_service.search_album_candidates("Quiet Emotion", "具島直子", 3)
        for c in cands:
            for key in ("cleanliness", "garbled", "dirtyTracks", "score"):
                self.assertIn(key, c, f"候选缺少字段 {key}")


class TestApplyCandidateGuards(unittest.TestCase):
    """应用候选时不得把乱码写进曲名，且专辑艺术家要同步到曲目"""

    def setUp(self):
        from main import DiscflowController

        self.ctrl = DiscflowController()
        self.ctrl.start_album_lyrics_batch = lambda: {"ok": False}  # 不触发联网批量

        self.ctrl.current_toc = SimpleNamespace(
            disc_id="probe_v1112",
            is_local=False,
            tracks=[
                SimpleNamespace(track_number=1, title="テル・ミー・オー・ママ", artist="旧作者", duration_sec=250.0),
                SimpleNamespace(track_number=2, title="テルミー", artist="旧作者", duration_sec=260.0),
                SimpleNamespace(track_number=3, title="予感", artist="旧作者", duration_sec=240.0),
            ],
        )

    def test_garbled_track_name_not_applied(self):
        cand = {
            "source": "qq",
            "title": "Quiet Emotion",
            "artist": "具島直子",
            "year": "1997",
            "cover_url": "",
            "tracks": [
                {"title": "テル・ミー・オー・ママ", "duration_sec": 250.0, "song_id": "A1"},
                {"title": "テルミ━蕙", "duration_sec": 260.0, "song_id": "A2"},
                {"title": "予感", "duration_sec": 240.0, "song_id": "A3"},
            ],
        }
        res = self.ctrl.apply_album_candidate(cand)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["skippedGarbled"], 1, "乱码曲名应被跳过")
        titles = [t.title for t in self.ctrl.current_toc.tracks]
        self.assertEqual(titles[1], "テルミー", "本地曲名应保留，不被乱码覆盖")
        self.assertFalse(any("━" in t for t in titles), f"曲名中不应出现乱码符号：{titles}")

    def test_album_artist_synced_to_tracks(self):
        cand = {
            "source": "qq",
            "title": "Quiet Emotion",
            "artist": "具島直子",
            "year": "1997",
            "cover_url": "",
            "tracks": [
                {"title": "テル・ミー・オー・ママ", "duration_sec": 250.0, "song_id": "A1"},
                {"title": "Tell me oh mama", "duration_sec": 260.0, "song_id": "A2"},
                {"title": "予感", "duration_sec": 240.0, "song_id": "A3"},
            ],
        }
        self.ctrl.apply_album_candidate(cand)
        artists = {t.artist for t in self.ctrl.current_toc.tracks}
        self.assertEqual(artists, {"具島直子"}, "曲目作者应与专辑作者保持一致")

    def test_album_title_sanitized(self):
        cand = {
            "source": "qq",
            "title": "Rock &amp; Roll\u200b",
            "artist": " Someone ",
            "year": "1997",
            "cover_url": "",
            "tracks": [],
        }
        res = self.ctrl.apply_album_candidate(cand)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["albumTitle"], "Rock & Roll")
        self.assertEqual(res["artist"], "Someone")

    def test_garbled_local_title_replaced_by_latin(self):
        """本地曲名本身就是乱码时，不该再受 CJK 保护，应被曲库的拉丁名替换"""
        self.ctrl.current_toc = SimpleNamespace(
            disc_id="probe_v1112b",
            is_local=False,
            tracks=[
                SimpleNamespace(track_number=1, title="ソ━膝", artist="旧作者", duration_sec=200.0),
            ],
        )
        cand = {
            "source": "qq",
            "title": "Quiet Emotion",
            "artist": "具島直子",
            "year": "1997",
            "cover_url": "",
            "tracks": [{"title": "So high So high", "duration_sec": 200.0, "song_id": "A1"}],
        }
        self.ctrl.apply_album_candidate(cand)
        self.assertEqual(self.ctrl.current_toc.tracks[0].title, "So high So high")

    def test_duplicate_local_titles_not_protected(self):
        """本地两首同名说明元数据不可信，应让曲库曲名覆盖"""
        self.ctrl.current_toc = SimpleNamespace(
            disc_id="probe_v1112c",
            is_local=False,
            tracks=[
                SimpleNamespace(track_number=1, title="まどろみ", artist="旧作者", duration_sec=200.0),
                SimpleNamespace(track_number=2, title="まどろみ", artist="旧作者", duration_sec=210.0),
            ],
        )
        cand = {
            "source": "qq",
            "title": "Quiet Emotion",
            "artist": "具島直子",
            "year": "1997",
            "cover_url": "",
            "tracks": [
                {"title": "no. no. no.", "duration_sec": 200.0, "song_id": "A1"},
                {"title": "まどろみ (小睡)", "duration_sec": 210.0, "song_id": "A2"},
            ],
        }
        self.ctrl.apply_album_candidate(cand)
        titles = [t.title for t in self.ctrl.current_toc.tracks]
        self.assertEqual(titles, ["no. no. no.", "まどろみ (小睡)"])

    def test_unique_cjk_title_still_protected(self):
        """茎 → Stem 的老场景仍要保护：本地唯一的中文曲名不被纯拉丁覆盖"""
        self.ctrl.current_toc = SimpleNamespace(
            disc_id="probe_v1112d",
            is_local=False,
            tracks=[
                SimpleNamespace(track_number=1, title="茎", artist="旧作者", duration_sec=200.0),
                SimpleNamespace(track_number=2, title="ギャンブル", artist="旧作者", duration_sec=210.0),
            ],
        )
        cand = {
            "source": "qq",
            "title": "平成風俗",
            "artist": "椎名林檎",
            "year": "2007",
            "cover_url": "",
            "tracks": [
                {"title": "Stem", "duration_sec": 200.0, "song_id": "A1"},
                {"title": "ギャンブル (赌局)", "duration_sec": 210.0, "song_id": "A2"},
            ],
        }
        self.ctrl.apply_album_candidate(cand)
        titles = [t.title for t in self.ctrl.current_toc.tracks]
        self.assertEqual(titles[0], "茎", "本地唯一的中文曲名应保留")
        self.assertEqual(titles[1], "ギャンブル (赌局)")


class TestTrackArtistsPersisted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="discflow_v1112_")
        self.mgr = library_service.DiscflowLibraryManager(base_dir=self.tmp)
        self.disc_id = "probe_v1112"
        d = os.path.join(self.tmp, self.disc_id)
        os.makedirs(d, exist_ok=True)
        payload = {
            "disc_id": self.disc_id,
            "title": "旧专辑名",
            "artist": "旧作者",
            "tracks": [
                {"track_number": 1, "title": "曲1", "artist": "旧作者", "duration_sec": 200.0, "file_name": "t1.wav"},
                {"track_number": 2, "title": "曲2", "artist": "旧作者", "duration_sec": 210.0, "file_name": "t2.wav"},
            ],
        }
        with open(os.path.join(d, "album.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_track_artists_written(self):
        ok = self.mgr.update_album_metadata(
            self.disc_id,
            title="新专辑名",
            artist="新作者",
            track_titles={1: "新曲1"},
            track_artists={1: "新作者", 2: "新作者"},
        )
        self.assertTrue(ok)
        with open(os.path.join(self.tmp, self.disc_id, "album.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["title"], "新专辑名")
        self.assertEqual(data["artist"], "新作者")
        self.assertEqual(data["tracks"][0]["title"], "新曲1")
        self.assertEqual(data["tracks"][0]["artist"], "新作者")
        self.assertEqual(data["tracks"][1]["artist"], "新作者")

    def test_track_artists_optional(self):
        ok = self.mgr.update_album_metadata(self.disc_id, title="只改标题")
        self.assertTrue(ok)
        with open(os.path.join(self.tmp, self.disc_id, "album.json"), encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["tracks"][0]["artist"], "旧作者")


class TestBatchMissedTracks(unittest.TestCase):
    def test_controller_tracks_missed_set(self):
        from main import DiscflowController

        ctrl = DiscflowController()
        self.assertTrue(hasattr(ctrl, "lyrics_missed_tracks"), "控制器应维护未命中曲目集合")
        self.assertIn("missed", ctrl._lyrics_batch, "批量进度应带 missed 字段")

    def test_poll_state_exposes_missed_flag(self):
        src = APPLE_MUSIC_HTML
        self.assertIn("lyricsMissed", src, "曲目下发应带 lyricsMissed 标记")
        self.assertIn("lyric-miss-badge", src, "曲目行应渲染「歌词待匹配」小标")
        self.assertIn("needs-lyric", src, "未命中曲目应有独立样式类")

    def test_frontend_surfaces_missed_count(self):
        src = APPLE_MUSIC_HTML
        self.assertIn("逐轨指定", src, "批量未命中后应引导用户逐轨匹配")
        self.assertIn("b.missed", src, "前端应读取批量结果的 missed 列表")


if __name__ == "__main__":
    unittest.main(verbosity=2)
