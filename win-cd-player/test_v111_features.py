# -*- coding: utf-8 -*-
"""
test_v111_features.py
验证 v1.11 六项体验升级：
1. 资料库删除唱片时物理删除磁盘文件（WAV / 封面 / 元数据），并返回可反馈的结果；
2. 测试运行器强制沙箱，任何测试都不会删除真实资料库；
3. 抓轨自适应大批次提速与进度节流；
4. 封面多源择优与手动更换封面入口；
5. 歌词并发预取提速；
6. 逐字同步歌词（yrc 解析 + 前端逐字高亮）。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import library_service
import metadata_service
from main import APPLE_MUSIC_HTML


class TestDeleteRemovesFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="discflow_del_probe_")
        self.mgr = library_service.DiscflowLibraryManager(base_dir=self.tmp)
        self.disc_id = "probe_disc_01"
        self.album_dir = os.path.join(self.tmp, self.disc_id)
        os.makedirs(self.album_dir, exist_ok=True)
        for name in ("album.json", "cover.jpg", "track_01.wav"):
            with open(os.path.join(self.album_dir, name), "wb") as f:
                f.write(b"x" * 64)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_delete_removes_album_directory(self):
        self.assertTrue(os.path.isdir(self.album_dir))
        res = self.mgr.delete_album_verbose(self.disc_id)
        self.assertTrue(res["ok"], f"删除应成功：{res}")
        self.assertFalse(os.path.isdir(self.album_dir), "专辑目录应被物理删除")
        self.assertEqual(res["failed"], [])

    def test_delete_verbose_reports_missing(self):
        res = self.mgr.delete_album_verbose("not_exist_disc")
        self.assertTrue(res["ok"], "删除不存在的唱片应视为已清理成功")
        self.assertFalse(res["existed"])

    def test_force_remove_tree_missing_path(self):
        ok, failed = self.mgr._force_remove_tree(os.path.join(self.tmp, "nope"))
        self.assertTrue(ok)
        self.assertEqual(failed, [])


class TestTestSandboxGuard(unittest.TestCase):
    def test_library_isolated_from_real_data(self):
        self.assertTrue(library_service.is_under_test_runner())
        lib = os.path.abspath(library_service.resolve_library_dir())
        real = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library"))
        self.assertNotEqual(lib.lower(), real.lower(), "测试期间资源库必须隔离，不得指向真实资料库")


class TestRipSpeedStrategy(unittest.TestCase):
    def test_adaptive_batch_and_throttle(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "library_service.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("BATCH_CANDIDATES = (64, 48, 32, 27, 16)", src)
        self.assertIn("sectors_per_batch = lower_candidates[0]", src)
        self.assertIn("PROGRESS_MIN_INTERVAL", src)
        # 最小批次必须仍在 64KB 安全线内
        self.assertLessEqual(16 * 2352, 65536)


class TestYrcWordLyrics(unittest.TestCase):
    YRC = "[1000,2000](1000,500,0)Hel(1500,500,0)lo\n[3000,1500](3000,750,0)世(3750,750,0)界"

    def test_parse_yrc_words(self):
        lines = metadata_service.parse_yrc_word_lyrics(self.YRC)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["time"], 1.0)
        self.assertEqual(lines[0]["text"], "Hello")
        self.assertEqual(len(lines[0]["words"]), 2)
        self.assertEqual(lines[0]["words"][0], {"t": 1.0, "d": 0.5, "w": "Hel"})
        self.assertEqual(lines[1]["text"], "世界")

    def test_parse_yrc_empty(self):
        self.assertEqual(metadata_service.parse_yrc_word_lyrics(""), [])
        self.assertEqual(metadata_service.parse_yrc_word_lyrics("not a yrc"), [])

    def test_merge_keeps_translation(self):
        words = metadata_service.parse_yrc_word_lyrics(self.YRC)
        plain = [
            {"time": 1.0, "text": "Hello", "translation": "你好"},
            {"time": 3.0, "text": "世界", "translation": "World"},
        ]
        merged = metadata_service.merge_word_lyrics(words, plain)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["translation"], "你好")
        self.assertTrue(merged[0]["words"])

    def test_merge_falls_back_when_sparse(self):
        words = metadata_service.parse_yrc_word_lyrics("[1000,500](1000,500,0)只(1500,0,0)有")
        plain = [{"time": float(i), "text": f"line{i}", "translation": ""} for i in range(1, 21)]
        merged = metadata_service.merge_word_lyrics(words, plain)
        self.assertEqual(merged, plain, "逐字歌词过少时应回退到普通歌词")


class TestLyricsPrefetchAndCover(unittest.TestCase):
    def test_prefetch_helper_exists(self):
        self.assertTrue(callable(metadata_service.prefetch_lyrics_for_tracks))
        self.assertEqual(metadata_service.prefetch_lyrics_for_tracks([]), {})

    def test_lyrics_cache_key_stable(self):
        k1 = metadata_service._lyrics_cache_key("Song", "Artist", 213.4)
        k2 = metadata_service._lyrics_cache_key("song ", "artist", 213.0)
        self.assertEqual(k1, k2, "缓存键应忽略大小写与秒级以下误差")


class TestFrontendWordKaraoke(unittest.TestCase):
    def test_word_lyric_dom_and_css(self):
        self.assertIn(".lyric-word", APPLE_MUSIC_HTML)
        self.assertIn("--karaoke-idle", APPLE_MUSIC_HTML)
        self.assertIn("function startKaraokeLoop()", APPLE_MUSIC_HTML)
        self.assertIn("startKaraokeLoop();", APPLE_MUSIC_HTML)
        self.assertIn("line.words", APPLE_MUSIC_HTML)

    def test_manual_cover_picker(self):
        self.assertIn('id="coverFileInput"', APPLE_MUSIC_HTML)
        self.assertIn("function pickAlbumCover", APPLE_MUSIC_HTML)
        self.assertIn("function handleCoverFileSelected", APPLE_MUSIC_HTML)
        self.assertIn("update_album_cover", APPLE_MUSIC_HTML)

    def test_delete_reports_result(self):
        self.assertIn("res.failed", APPLE_MUSIC_HTML)
        self.assertIn("delete_local_album", APPLE_MUSIC_HTML)


class TestV1111LyricFixes(unittest.TestCase):
    """v1.11.1 三个歌词缺陷修复：逐字覆盖不全、翻译断续、高亮跳动"""

    def test_translation_alignment_survives_drift(self):
        orig = "[00:01.00]Line one\n[00:04.00]Line two\n[00:07.50]Line three\n[00:11.00]Line four"
        # 翻译整体漂移 +0.35s，且第三行本身缺失翻译
        trans = "[00:01.35]第一行\n[00:04.30]第二行\n[00:11.40]第四行"
        res = metadata_service.build_structured_bilingual_lyrics(orig, trans)
        self.assertEqual(len(res), 4)
        self.assertEqual(res[0]["translation"], "第一行")
        self.assertEqual(res[1]["translation"], "第二行")
        self.assertEqual(res[2]["translation"], "")
        self.assertEqual(res[3]["translation"], "第四行")

    def test_align_translations_window(self):
        times = [1.0, 4.0, 8.0]
        trans = [(1.3, "A"), (4.5, "B"), (8.2, "C")]
        out = metadata_service.align_translations(times, trans)
        self.assertEqual(out, ["A", "B", "C"])
        # 超出窗口不应错配
        out2 = metadata_service.align_translations([1.0], [(5.0, "X")])
        self.assertEqual(out2, [""])

    def test_frontend_synthesizes_words_for_plain_lines(self):
        # 任何没有逐字数据的行都要在前端合成时间轴，保证「每首歌都有」逐字效果
        self.assertIn("function ensureWordTimings", APPLE_MUSIC_HTML)
        self.assertIn("KARAOKE_TOKEN_RE", APPLE_MUSIC_HTML)
        self.assertIn("lyrics = ensureWordTimings(lyrics);", APPLE_MUSIC_HTML)

    def test_karaoke_clock_is_continuous(self):
        # 高亮必须由自走时钟推进 + 柔性纠偏，而不是每次轮询硬重置锚点
        self.assertIn("karaokeClock", APPLE_MUSIC_HTML)
        self.assertIn("karaokeLastTs", APPLE_MUSIC_HTML)
        self.assertIn("Math.abs(drift) > 0.45", APPLE_MUSIC_HTML)
        self.assertIn("karaokeClock += drift * 0.12", APPLE_MUSIC_HTML)
        self.assertNotIn("karaokeAnchorSec", APPLE_MUSIC_HTML)

    def test_active_line_has_hysteresis(self):
        self.assertIn("const probe = (Number(elapsedSec) || 0) + 0.06;", APPLE_MUSIC_HTML)

    def test_update_track_lyrics_persists(self):
        tmp = tempfile.mkdtemp(prefix="discflow_lyr_probe_")
        try:
            mgr = library_service.DiscflowLibraryManager(base_dir=tmp)
            album_dir = os.path.join(tmp, "probe_disc")
            os.makedirs(album_dir, exist_ok=True)
            meta = {
                "disc_id": "probe_disc",
                "title": "Probe",
                "artist": "Probe",
                "cover_file": "",
                "tracks": [
                    {"track_number": 1, "title": "T1", "artist": "A", "duration_sec": 10.0,
                     "file_name": "track_01.wav", "lyrics": []},
                    {"track_number": 2, "title": "T2", "artist": "A", "duration_sec": 12.0,
                     "file_name": "track_02.wav", "lyrics": []},
                ],
            }
            with open(os.path.join(album_dir, "album.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)

            new_lyrics = [{"time": 1.0, "text": "hi", "translation": "嗨",
                           "words": [{"t": 1.0, "d": 0.5, "w": "hi"}]}]
            self.assertTrue(mgr.update_track_lyrics("probe_disc", 2, new_lyrics))

            with open(os.path.join(album_dir, "album.json"), encoding="utf-8") as f:
                after = json.load(f)
            self.assertEqual(after["tracks"][1]["lyrics"], new_lyrics)
            self.assertEqual(after["tracks"][0]["lyrics"], [], "其他曲目不应被改动")
            self.assertFalse(os.path.isfile(os.path.join(album_dir, "album.json.tmp")), "不应残留临时文件")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_lyrics_quality_gate(self):
        from main import DiscflowController
        good = [{"time": i, "text": "x", "translation": "y"} for i in range(10)]
        bad = [{"time": i, "text": "x", "translation": ""} for i in range(10)]
        self.assertTrue(DiscflowController._lyrics_quality_ok(good))
        self.assertFalse(DiscflowController._lyrics_quality_ok(bad))
        self.assertFalse(DiscflowController._lyrics_quality_ok([]))
        self.assertFalse(DiscflowController._lyrics_quality_ok([{"time": 0, "text": "x"}]))


class TestV1112LyricsAndLightMode(unittest.TestCase):
    """v1.11.2：翻译断续（选错版本）与浅色模式氛围修复"""

    def test_title_normalization_strips_version_suffix(self):
        self.assertEqual(
            metadata_service._normalize_song_title("Luv(sic.) Part 4 (Original)"),
            metadata_service._normalize_song_title("Luv(sic), Part 4"),
        )

    def test_title_similarity_ranking(self):
        base = "Luv(sic), Part 4"
        self.assertEqual(metadata_service._title_similarity("Luv(sic.) Part 4 (Original)", base), 1.0)
        self.assertGreater(
            metadata_service._title_similarity("Luv(sic) Part 4", base),
            metadata_service._title_similarity("Luv(sic) Part 6 Uyama Hiroto Remix", base),
        )
        self.assertLess(metadata_service._title_similarity("", base), 0.2)

    def test_lyrics_rank_prefers_translations(self):
        full = [{"time": i, "text": "x", "translation": "y", "words": [{"t": i, "d": 1, "w": "x"}]} for i in range(5)]
        poor = [{"time": i, "text": "x", "translation": ""} for i in range(5)]
        self.assertGreater(metadata_service._lyrics_rank(full), metadata_service._lyrics_rank(poor))
        self.assertEqual(metadata_service._lyrics_rank([]), (0, 0, 0))

    def test_lyrics_quality_gate_requires_70pct(self):
        from main import DiscflowController
        mixed = [{"time": i, "text": "x", "translation": ("y" if i < 7 else "")} for i in range(10)]
        self.assertTrue(DiscflowController._lyrics_quality_ok(mixed))
        low = [{"time": i, "text": "x", "translation": ("y" if i < 5 else "")} for i in range(10)]
        self.assertFalse(DiscflowController._lyrics_quality_ok(low))

    def test_light_mode_has_ambient_rendering(self):
        # 浅色模式不能只剩纯白底：必须强化流体光斑与底色渐变
        self.assertIn('[data-theme="light"] .mesh-canvas', APPLE_MUSIC_HTML)
        self.assertIn("opacity: 0.88;", APPLE_MUSIC_HTML)
        self.assertIn("linear-gradient(180deg, #FCFCFE 0%, #F4F4F8 46%, #ECECF2 100%)", APPLE_MUSIC_HTML)


class TestV114FirstTrackLyrics(unittest.TestCase):
    """v1.11.4：首曲歌词不显示（前端脚本语法 + 歌词下发时机）"""

    def test_inline_script_parses(self):
        """整段前端脚本必须能被 JS 引擎解析（曾因 f-string 里写 \\n 导致全站脚本崩溃）"""
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用，跳过 JS 语法校验")
        html = APPLE_MUSIC_HTML
        start = html.index("<script>")
        end = html.rindex("</script>")
        js = html[start + len("<script>"):end]
        path = os.path.join(tempfile.gettempdir(), "discflow_inline_check.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(js)
        try:
            proc = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"前端脚本语法错误：{proc.stderr[:600]}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_lyrics_resent_on_track_change(self):
        """切歌后即使版本号未变，也必须重新下发歌词"""
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("client_track_index: int = -1", src)
        self.assertIn("int(client_track_index) != self.current_track_index", src)
        self.assertIn("window.pywebview.api.poll_state(", APPLE_MUSIC_HTML)
        self.assertIn("appState.currentTrackIndex", APPLE_MUSIC_HTML)

    def test_start_track_fetches_missing_lyrics(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def _start_track("):src.index("def _fetch_lyrics_async(")]
        self.assertIn("_fetch_lyrics_async", body)
        self.assertNotIn('not getattr(self.current_toc, "is_local", False)', body)

    def test_placeholder_titles_skip_lyrics_lookup(self):
        import metadata_service
        self.assertEqual(metadata_service.lookup_track_lyrics("Track 01", "DiscID abc123", 200.0), [])
        self.assertEqual(metadata_service.lookup_track_lyrics("Track 07", "", 200.0), [])


class TestV115DockTranslationToggle(unittest.TestCase):
    """v1.11.5：底栏双语翻译开关"""

    def test_dock_button_present(self):
        self.assertIn('id="dockTransBtn"', APPLE_MUSIC_HTML)
        self.assertIn('onclick="toggleBilingualLyrics()"', APPLE_MUSIC_HTML)
        self.assertIn(".dock-trans-btn.is-off", APPLE_MUSIC_HTML)

    def test_toggle_uses_root_class_not_inline_dom(self):
        # 必须用 <html> 上的类控制，否则歌词重渲染后译文会重新出现
        self.assertIn("html.no-translation .lyric-trans", APPLE_MUSIC_HTML)
        self.assertIn('classList.toggle("no-translation"', APPLE_MUSIC_HTML)
        self.assertIn("function applyTranslationVisibility(show)", APPLE_MUSIC_HTML)

    def test_toggle_persists_preference(self):
        self.assertIn("currentCustomPrefs.showTranslation = next", APPLE_MUSIC_HTML)
        self.assertIn("showTranslation: currentCustomPrefs.showTranslation !== false", APPLE_MUSIC_HTML)
        prefs = library_service.load_user_preferences()
        self.assertIn("showTranslation", prefs, "翻译开关需持久化到 preferences.json")

    def test_menu_and_dock_share_state(self):
        # 菜单项与底栏按钮共用同一状态，且菜单标签有两处，需全部更新
        self.assertIn('querySelectorAll("#transToggleLabel")', APPLE_MUSIC_HTML)


class TestV116LibraryScrollbar(unittest.TestCase):
    """v1.11.6：资源库悬停末列卡片时不再出现白色横向滚动条"""

    def test_library_scroll_area_hides_horizontal_overflow(self):
        self.assertIn("overflow-x: hidden", APPLE_MUSIC_HTML)
        # 两处 library-scroll-area 定义都必须带 overflow-x: hidden
        idx = 0
        count = 0
        while True:
            idx = APPLE_MUSIC_HTML.find(".library-scroll-area {", idx)
            if idx == -1:
                break
            block = APPLE_MUSIC_HTML[idx: idx + 320]
            if "overflow-y: auto" in block:
                self.assertIn("overflow-x: hidden", block, "每个滚动容器都必须裁掉横向溢出")
                count += 1
            idx += 10
        self.assertGreaterEqual(count, 2)

    def test_library_scrollbar_is_styled(self):
        self.assertIn(".library-scroll-area::-webkit-scrollbar", APPLE_MUSIC_HTML)
        self.assertIn(".library-scroll-area::-webkit-scrollbar-thumb", APPLE_MUSIC_HTML)
        self.assertIn("scrollbar-color: rgba(255, 255, 255, 0.16) transparent", APPLE_MUSIC_HTML)


class TestV117ManualAlbumMatch(unittest.TestCase):
    """v1.11.7：曲目栏手动匹配专辑信息 + 资料库点击自动切曲目栏"""

    def test_backend_api_present(self):
        from main import DiscflowController, DiscflowJsApi
        self.assertTrue(hasattr(DiscflowController, "apply_manual_metadata"))
        self.assertTrue(hasattr(DiscflowJsApi, "apply_manual_metadata"))
        self.assertTrue(callable(metadata_service.lookup_album_by_name))
        self.assertTrue(callable(metadata_service.match_tracks_by_duration))

    def test_ui_entries_present(self):
        self.assertIn('id="matchMetadataModal"', APPLE_MUSIC_HTML)
        self.assertIn("function openMatchMetadataModal()", APPLE_MUSIC_HTML)
        self.assertIn("function submitMatchMetadata()", APPLE_MUSIC_HTML)
        self.assertIn("match-meta-btn", APPLE_MUSIC_HTML)
        self.assertIn("apply_album_candidate", APPLE_MUSIC_HTML)

    def test_library_click_switches_to_queue_panel(self):
        self.assertIn("function applyRightPanelMode(mode)", APPLE_MUSIC_HTML)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("async function playLocalAlbumWithAnimation"):src.index("async function playLocalAlbum(")]
        self.assertIn('applyRightPanelMode("queue")', body, "从资料库点开专辑后应自动切到曲目栏")

    def test_track_matching_by_duration(self):
        # 数量一致：顺序对应
        cand = [{"title": f"T{i}", "duration_sec": 200.0 + i} for i in range(3)]
        m = metadata_service.match_tracks_by_duration([200.0, 201.0, 202.0], cand)
        self.assertEqual([m[i]["title"] for i in range(3)], ["T0", "T1", "T2"])

    def test_track_matching_multi_disc_window(self):
        # 本地单碟 3 首，曲库是 2CD 版 6 首（后 3 首为同曲长伴奏）
        base = [200.0, 201.0, 202.0]
        cand = [{"title": f"V{i}", "duration_sec": d} for i, d in enumerate(base)]
        cand += [{"title": f"Inst{i}", "duration_sec": d} for i, d in enumerate(base)]
        m = metadata_service.match_tracks_by_duration(base, cand)
        self.assertEqual([m[i]["title"] for i in range(3)], ["V0", "V1", "V2"],
                         "多碟版本应按连续窗口顺序对齐，不能错配到伴奏版本")

    def test_empty_title_rejected(self):
        # 空专辑名不应发起任何请求
        r = metadata_service.lookup_album_by_name("", "x", 0)
        self.assertFalse(r["matched"])


class TestV118AppleMusicBackdrop(unittest.TestCase):
    """v1.11.8：播放页 Apple Music 风格封面模糊铺底"""

    def test_backdrop_layers_present(self):
        self.assertIn('id="artBackdrop"', APPLE_MUSIC_HTML)
        self.assertIn('class="art-backdrop"', APPLE_MUSIC_HTML)
        self.assertIn('class="ambient-vignette"', APPLE_MUSIC_HTML)

    def test_backdrop_css_blurred_and_scaled(self):
        self.assertIn(".art-backdrop {", APPLE_MUSIC_HTML)
        self.assertIn("filter: blur(76px) saturate(195%)", APPLE_MUSIC_HTML)
        self.assertIn("body.has-artwork .art-backdrop", APPLE_MUSIC_HTML)
        self.assertIn("body.has-artwork:not(.is-playing) .art-backdrop", APPLE_MUSIC_HTML)

    def test_vignette_for_readability(self):
        self.assertIn(".ambient-vignette {", APPLE_MUSIC_HTML)
        self.assertIn("radial-gradient(125% 92% at 50% 42%", APPLE_MUSIC_HTML)
        self.assertIn('[data-theme="light"] .ambient-vignette', APPLE_MUSIC_HTML)

    def test_backdrop_follows_cover(self):
        self.assertIn('document.body.classList.add("has-artwork")', APPLE_MUSIC_HTML)
        self.assertIn('document.body.classList.remove("has-artwork")', APPLE_MUSIC_HTML)
        self.assertIn('backdropEl.style.backgroundImage', APPLE_MUSIC_HTML)


class TestV119PerTrackLyrics(unittest.TestCase):
    """v1.11.9：逐轨手动匹配歌词（三源 + 锁定）"""

    def test_backend_api_present(self):
        from main import DiscflowController, DiscflowJsApi
        for name in ("search_track_lyric_candidates", "apply_track_lyrics",
                     "clear_track_lyrics", "auto_match_track_lyrics"):
            self.assertTrue(hasattr(DiscflowController, name), name)
            self.assertTrue(hasattr(DiscflowJsApi, name), name)
        self.assertTrue(callable(metadata_service.search_lyric_candidates))
        self.assertTrue(callable(metadata_service.fetch_lyrics_for_candidate))

    def test_ui_entries_present(self):
        self.assertIn('id="lyricMatchModal"', APPLE_MUSIC_HTML)
        self.assertIn("function openLyricMatchModal(", APPLE_MUSIC_HTML)
        self.assertIn("function applyLyricCandidate(", APPLE_MUSIC_HTML)
        self.assertIn("track-lyric-btn", APPLE_MUSIC_HTML)
        self.assertIn("lyric-cand-preview", APPLE_MUSIC_HTML)

    def test_prefetch_skips_locked_tracks(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def _prefetch_lyrics_async("):src.index("def _on_player_position(")]
        self.assertIn("lyrics_locked_tracks", body, "预取必须跳过被手动锁定的曲目")

    def test_artist_sanitized_for_search(self):
        self.assertEqual(metadata_service._primary_artist("椎名林檎 / 斎藤ネコ"), "椎名林檎")
        self.assertEqual(metadata_service._primary_artist("A & B"), "A")
        self.assertEqual(metadata_service._primary_artist("Solo"), "Solo")

    def test_locked_lyrics_persist_and_clear(self):
        tmp = tempfile.mkdtemp(prefix="discflow_lock_probe_")
        try:
            mgr = library_service.DiscflowLibraryManager(base_dir=tmp)
            album_dir = os.path.join(tmp, "probe_disc")
            os.makedirs(album_dir, exist_ok=True)
            meta = {
                "disc_id": "probe_disc", "title": "P", "artist": "A", "cover_file": "",
                "tracks": [{"track_number": 1, "title": "T1", "artist": "A", "duration_sec": 10.0,
                            "file_name": "t.wav", "lyrics": []}],
            }
            with open(os.path.join(album_dir, "album.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)

            lyrics = [{"time": 1.0, "text": "hi", "translation": "嗨"}]
            self.assertTrue(mgr.update_track_lyrics("probe_disc", 1, lyrics,
                                                    source="netease", song_id="123", locked=True))
            with open(os.path.join(album_dir, "album.json"), encoding="utf-8") as f:
                trk = json.load(f)["tracks"][0]
            self.assertTrue(trk["lyrics_locked"])
            self.assertEqual(trk["lyrics_source"], "netease")
            self.assertEqual(trk["lyrics_song_id"], "123")

            self.assertTrue(mgr.update_track_lyrics("probe_disc", 1, [], clear_lock=True))
            with open(os.path.join(album_dir, "album.json"), encoding="utf-8") as f:
                trk = json.load(f)["tracks"][0]
            self.assertFalse(trk["lyrics_locked"])
            self.assertEqual(trk["lyrics_source"], "")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_track_dataclass_has_lock_fields(self):
        fields = library_service.LocalTrackInfo.__dataclass_fields__
        for name in ("lyrics_source", "lyrics_song_id", "lyrics_locked", "lyrics_matched_at"):
            self.assertIn(name, fields)


class TestV1110AlbumLyricsBatch(unittest.TestCase):
    """v1.11.10：识别出专辑后整专辑歌词一次性套用"""

    def test_backend_api_present(self):
        from main import DiscflowController, DiscflowJsApi
        self.assertTrue(hasattr(DiscflowController, "start_album_lyrics_batch"))
        self.assertTrue(hasattr(DiscflowController, "_run_album_lyrics_batch"))
        self.assertTrue(hasattr(DiscflowJsApi, "start_album_lyrics_batch"))

    def test_batch_progress_in_poll_state(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def poll_state("):src.index("def ui_ready(")]
        self.assertIn('"lyricsBatch": dict(self._lyrics_batch)', body)

    def test_apply_album_candidate_triggers_batch(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def apply_album_candidate("):src.index("def apply_manual_metadata(")]
        self.assertIn("start_album_lyrics_batch()", body, "专辑套用成功后应自动整专辑套用歌词")
        self.assertIn("lyricsBatchStarted", body)

    def test_batch_skips_locked_tracks(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def _run_album_lyrics_batch("):src.index("def get_library_dir(")]
        self.assertIn("lyrics_locked_tracks", body)
        self.assertIn("_lyrics_quality_ok", body)

    def test_frontend_waits_for_batch(self):
        self.assertIn("function waitForLyricsBatch", APPLE_MUSIC_HTML)
        self.assertIn("res.lyricsBatchStarted", APPLE_MUSIC_HTML)
        self.assertIn("正在批量匹配歌词", APPLE_MUSIC_HTML)


class TestV1111AlbumThreeSource(unittest.TestCase):
    """v1.11.11：专辑匹配三源并发 + 歌曲 ID 直取歌词"""

    def test_backend_api_present(self):
        from main import DiscflowController, DiscflowJsApi
        for name in ("search_album_candidates_for_current", "apply_album_candidate"):
            self.assertTrue(hasattr(DiscflowController, name), name)
            self.assertTrue(hasattr(DiscflowJsApi, name), name)
        self.assertTrue(callable(metadata_service.search_album_candidates))

    def test_ui_candidate_list(self):
        self.assertIn('id="matchAlbumResults"', APPLE_MUSIC_HTML)
        self.assertIn("function renderAlbumCandidates(", APPLE_MUSIC_HTML)
        self.assertIn("function applyAlbumCandidate(", APPLE_MUSIC_HTML)
        self.assertIn("album-cand-cover", APPLE_MUSIC_HTML)

    def test_batch_prefers_song_id(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def _run_album_lyrics_batch("):src.index("def get_library_dir(")]
        self.assertIn("track_song_ids", body, "批量取词应优先按已绑定的歌曲 ID 直取")

    def test_track_title_cjk_guard(self):
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"), encoding="utf-8") as f:
            src = f.read()
        body = src[src.index("def apply_album_candidate("):src.index("def apply_manual_metadata(")]
        self.assertIn("_has_cjk", body, "本地 CJK 曲名不应被英文候选覆盖")

    def test_qq_lyric_parsing_works(self):
        # 曾经因 metadata_service 缺少 json 导入导致 QQ 取词静默失败
        self.assertIn("import json", open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata_service.py"), encoding="utf-8"
        ).read())

    def test_song_id_persist_api(self):
        self.assertTrue(hasattr(library_service.DiscflowLibraryManager, "update_track_song_id"))


if __name__ == "__main__":
    unittest.main()
