import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cd_engine
import library_service
import metadata_service
from main import (
    APPLE_MUSIC_HTML,
    DiscflowController,
    DiscflowJsApi,
    render_frontend_html,
)


def _make_controller() -> DiscflowController:
    """构造测试用控制器并在必要时安全关闭后台资源。"""
    ctrl = DiscflowController()
    return ctrl


class TestWindowMemoryDpi(unittest.TestCase):
    """验证 96-DPI 逻辑坐标记忆、旧版物理坐标迁移及虚拟桌面边界钳制。"""

    def test_normalize_legacy_physical_bounds_migrates_by_scale(self):
        wb = {"x": 559, "y": 231, "width": 1536, "height": 1052, "maximized": False}
        w, h, x, y, maximized = DiscflowController._normalize_bounds_dict(
            wb, scale=1.5, log_vx=0.0, log_vy=0.0, log_vw=1707.0, log_vh=1067.0
        )
        self.assertEqual((w, h, maximized), (1024, 701, False))
        self.assertEqual((x, y), (373, 154))

    def test_normalize_logical_bounds_preserved_and_out_of_bounds_clamped(self):
        wb = {"x": 120, "y": 80, "width": 1120, "height": 740, "maximized": False, "logical": True}
        w, h, x, y, maximized = DiscflowController._normalize_bounds_dict(
            wb, scale=1.5, log_vx=0.0, log_vy=0.0, log_vw=1707.0, log_vh=1067.0
        )
        self.assertEqual((w, h, x, y, maximized), (1120, 740, 120, 80, False))

        offscreen = {"x": 9999, "y": 9999, "width": 3000, "height": 2000, "maximized": False, "logical": True}
        w2, h2, x2, y2, _ = DiscflowController._normalize_bounds_dict(
            offscreen, scale=1.5, log_vx=0.0, log_vy=0.0, log_vw=1707.0, log_vh=1067.0
        )
        self.assertEqual((w2, h2), (1707, 1067))
        self.assertIsNone(x2)
        self.assertIsNone(y2)

    def test_rect_to_logical_bounds_normal_and_maximized(self):
        normal = DiscflowController._rect_to_logical_bounds(
            left=150, top=90, right=1686, bottom=1140, scale=1.5, is_zoomed=False, prev_wb={}
        )
        self.assertEqual(
            normal,
            {"x": 100, "y": 60, "width": 1024, "height": 700, "maximized": False, "logical": True},
        )

        # 最大化时保留还原前尺寸，并将无 logical 标记的旧值同步换算为逻辑坐标
        legacy_prev = {"x": 150, "y": 90, "width": 1536, "height": 1050}
        zoomed = DiscflowController._rect_to_logical_bounds(
            left=0, top=0, right=2560, bottom=1600, scale=1.5, is_zoomed=True, prev_wb=legacy_prev
        )
        self.assertEqual(
            zoomed,
            {"x": 100, "y": 60, "width": 1024, "height": 700, "maximized": True, "logical": True},
        )

    def test_moved_resized_and_save_bounds_without_hwnd(self):
        ctrl = _make_controller()
        try:
            ctrl._hwnd = 0
            ctrl._window = None
            ctrl._on_window_moved(140, 95)
            ctrl._on_window_resized(1180, 790)
            ctrl._save_window_bounds()
            saved = library_service.load_user_preferences().get("windowBounds", {})
            self.assertTrue(saved.get("logical"))
            self.assertEqual(saved.get("x"), 140)
            self.assertEqual(saved.get("y"), 95)
            self.assertEqual(saved.get("width"), 1180)
            self.assertEqual(saved.get("height"), 790)
            self.assertFalse(saved.get("maximized"))
        finally:
            ctrl.shutdown()


class TestStartupThemeAndSsrHydration(unittest.TestCase):
    """验证首帧 HTML 主题注入、初始偏好/状态水合以及主题持久化一致性。"""

    def test_render_frontend_html_injects_light_theme_and_no_hardcoded_playing(self):
        html = render_frontend_html(
            init_prefs={"themeMode": "light", "dockOpacity": 5},
            init_state={"isPlaying": False, "albumTitle": "SSR Test Album"},
        )
        self.assertIn('<html lang="zh-CN" data-theme="light" class="is-booting">', html)
        self.assertNotIn('<body class="is-playing">', html)
        self.assertIn("<body>", html)
        self.assertIn('"themeMode": "light"', html)
        self.assertIn('"albumTitle": "SSR Test Album"', html)

    def test_rendered_scripts_boot_without_tdz_or_runtime_error(self):
        """整段渲染后的前端脚本在首帧执行时不得因 TDZ 或未定义变量抛出异常导致界面按钮失灵"""
        import re
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用，跳过前端启动执行校验")
        html = render_frontend_html(
            init_prefs={"themeMode": "light", "dockOpacity": 5},
            init_state={"isPlaying": False, "albumTitle": "Boot Test", "tracks": []},
        )
        scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html)
        harness = """
const makeEl = () => ({
  style: { setProperty: () => {} },
  classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
  setAttribute: () => {},
  getAttribute: () => 'light',
  addEventListener: () => {},
  querySelectorAll: () => [],
  querySelector: () => null,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 20, bottom: 20 }),
  getContext: () => ({ createRadialGradient: () => ({ addColorStop: () => {} }), fillRect: () => {}, clearRect: () => {} }),
  dataset: {}, innerHTML: '', textContent: '', value: '', checked: false, src: '',
  scrollHeight: 100, clientHeight: 100, scrollTop: 0,
});
global.window = global;
global.window.addEventListener = () => {};
global.window.removeEventListener = () => {};
global.navigator = { userAgent: 'test' };
global.document = {
  documentElement: makeEl(), body: makeEl(), hidden: false,
  getElementById: () => makeEl(), querySelectorAll: () => [], querySelector: () => null,
  addEventListener: () => {}, createElement: () => makeEl(),
};
global.localStorage = { getItem: () => null, setItem: () => {} };
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = () => {};
global.matchMedia = () => ({ matches: false });
global.Image = function() {};
global.setInterval = () => 1;
global.setTimeout = () => 1;
global.clearTimeout = () => {};
global.clearInterval = () => {};
"""
        tail = """
if (!window._discflowAppReady || typeof togglePlayPause !== 'function' || typeof switchMainView !== 'function') {
  throw new Error('Frontend functions failed to initialize');
}
"""
        path = os.path.join(tempfile.gettempdir(), "discflow_boot_smoke.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(harness + "\n" + "\n".join(scripts) + "\n" + tail)
        try:
            proc = subprocess.run([node, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"前端启动脚本执行异常：{proc.stderr[:800]}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_prefs_defaults_match_frontend_and_set_theme_mode(self):
        prefs = library_service.load_user_preferences()
        self.assertEqual(prefs.get("dockOpacity"), 5)
        self.assertEqual(prefs.get("dockBlur"), 2)
        self.assertEqual(prefs.get("topbarOpacity"), 5)
        self.assertEqual(prefs.get("topbarBlur"), 2)

        ctrl = _make_controller()
        try:
            self.assertTrue(ctrl.set_theme_mode("light"))
            self.assertEqual(library_service.load_user_preferences().get("themeMode"), "light")
            self.assertTrue(ctrl.set_theme_mode("dark"))
            self.assertEqual(library_service.load_user_preferences().get("themeMode"), "dark")
        finally:
            ctrl.shutdown()


class TestSystemTrayRemoved(unittest.TestCase):
    """验证系统托盘相关后端接口、前端开关与持久化键已彻底移除。"""

    def test_tray_apis_and_ui_completely_removed(self):
        for attr in ("set_close_to_tray", "hide_to_tray", "_setup_tray_icon", "_dispose_tray_icon"):
            self.assertFalse(hasattr(DiscflowController, attr), f"DiscflowController 不应保留 {attr}")
        self.assertFalse(hasattr(DiscflowJsApi, "set_close_to_tray"))
        self.assertNotIn("closeToTraySwitch", APPLE_MUSIC_HTML)
        self.assertNotIn("toggleCloseToTray", APPLE_MUSIC_HTML)

    def test_close_to_tray_stripped_from_preferences(self):
        library_service.save_user_preferences({"closeToTray": True, "themeMode": "light"})
        prefs = library_service.load_user_preferences()
        self.assertNotIn("closeToTray", prefs)

    def test_on_window_closing_allows_direct_exit(self):
        ctrl = _make_controller()
        try:
            self.assertTrue(ctrl.on_window_closing())
        finally:
            ctrl.shutdown()


class TestInAppUpdater(unittest.TestCase):
    """验证软件内检查更新、下载进度、PE 校验、覆盖安装与自更新脚本生成。"""

    def test_check_for_updates_extracts_download_url_and_size(self):
        ctrl = _make_controller()
        try:
            fake_resp = MagicMock()
            fake_resp.status_code = 200
            fake_resp.json.return_value = {
                "tag_name": "v9.9.9",
                "html_url": "https://github.com/JiAng10es/Discflow/releases/tag/v9.9.9",
                "body": "Test release notes",
                "published_at": "2026-11-17T00:00:00Z",
                "assets": [
                    {
                        "name": "Discflow.exe",
                        "browser_download_url": "https://github.com/JiAng10es/Discflow/releases/download/v9.9.9/Discflow.exe",
                        "size": 1024,
                    }
                ],
            }
            with patch("main.requests.get", return_value=fake_resp):
                res = ctrl.check_for_updates(force=True)
            self.assertTrue(res["ok"])
            self.assertTrue(res["hasUpdate"])
            self.assertEqual(res["latestVersion"], "9.9.9")
            self.assertEqual(
                res["downloadUrl"],
                "https://github.com/JiAng10es/Discflow/releases/download/v9.9.9/Discflow.exe",
            )
            self.assertEqual(res["assetSize"], 1024)
        finally:
            ctrl.shutdown()

    def test_download_rejects_non_mz_payload_and_cleans_temp(self):
        ctrl = _make_controller()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target_exe = os.path.join(tmp, "Discflow.exe")
                fake_resp = MagicMock()
                fake_resp.__enter__.return_value = fake_resp
                fake_resp.__exit__.return_value = False
                fake_resp.raise_for_status.return_value = None
                fake_resp.headers = {"Content-Length": "10"}
                fake_resp.iter_content.return_value = [b"NOT_AN_EXE"]

                with patch.object(DiscflowController, "_resolve_update_target_exe", return_value=target_exe):
                    with patch("main.requests.get", return_value=fake_resp):
                        ctrl._download_and_install_update_worker(
                            "https://example.com/Discflow.exe", "9.9.9", 10
                        )

                prog = ctrl.get_update_download_progress()
                self.assertEqual(prog["status"], "error")
                self.assertIn("格式校验失败", prog["error"])
                self.assertFalse(os.path.exists(os.path.join(tmp, "Discflow.exe.new.tmp")))
                self.assertFalse(os.path.exists(target_exe))
        finally:
            ctrl.shutdown()

    def test_download_valid_mz_replaces_target_in_dev_mode(self):
        ctrl = _make_controller()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target_exe = os.path.join(tmp, "Discflow.exe")
                with open(target_exe, "wb") as f_old:
                    f_old.write(b"MZ_OLD")

                payload1 = b"MZ" + b"A" * 510
                payload2 = b"B" * 512
                fake_resp = MagicMock()
                fake_resp.__enter__.return_value = fake_resp
                fake_resp.__exit__.return_value = False
                fake_resp.raise_for_status.return_value = None
                fake_resp.headers = {"Content-Length": "1024"}
                fake_resp.iter_content.return_value = [payload1, payload2]

                with patch.object(DiscflowController, "_resolve_update_target_exe", return_value=target_exe):
                    with patch("main.requests.get", return_value=fake_resp):
                        ctrl._download_and_install_update_worker(
                            "https://example.com/Discflow.exe", "9.9.9", 1024
                        )

                prog = ctrl.get_update_download_progress()
                self.assertEqual(prog["status"], "completed")
                self.assertEqual(prog["progressPct"], 100.0)
                self.assertEqual(prog["downloadedBytes"], 1024)
                with open(target_exe, "rb") as f_new:
                    self.assertEqual(f_new.read(), payload1 + payload2)
        finally:
            ctrl.shutdown()

    def test_build_self_update_script_and_frozen_restart(self):
        bat = DiscflowController._build_self_update_script(
            4321, r"C:\tmp\Discflow.exe.update.exe", r"C:\app\Discflow.exe"
        )
        self.assertIn("PID=4321", bat)
        self.assertIn('tasklist /FI "PID eq %PID%"', bat)
        self.assertIn('move /Y "%SRC%" "%DST%"', bat)
        self.assertIn('start "" "%DST%"', bat)
        self.assertIn('del "%~f0"', bat)

        ctrl = _make_controller()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                staged = os.path.join(tmp, "Discflow.exe.update.exe")
                target = os.path.join(tmp, "Discflow.exe")
                with open(staged, "wb") as f:
                    f.write(b"MZTEST")
                old_frozen = getattr(sys, "frozen", False)
                sys.frozen = True
                try:
                    with patch("main.subprocess.Popen") as mock_popen, \
                         patch("main.time.sleep"), \
                         patch.object(ctrl, "exit_app") as mock_exit:
                        ctrl._apply_staged_update_and_restart(staged, target)
                    mock_popen.assert_called_once()
                    args = mock_popen.call_args[0][0]
                    self.assertEqual(args[:2], ["cmd.exe", "/c"])
                    self.assertTrue(args[2].endswith(".bat"))
                    self.assertTrue(os.path.isfile(args[2]))
                    mock_exit.assert_called_once()
                finally:
                    if old_frozen:
                        sys.frozen = old_frozen
                    else:
                        delattr(sys, "frozen")
                    bat_file = os.path.join(tempfile.gettempdir(), f"discflow_updater_{os.getpid()}.bat")
                    if os.path.isfile(bat_file):
                        os.remove(bat_file)
        finally:
            ctrl.shutdown()


class TestFirstTrackLyricsFix(unittest.TestCase):
    """验证读盘后首曲优先抓词、空结果防覆盖、多艺人/QQ兜底及前端单调版本自愈。"""

    def test_is_placeholder_track(self):
        self.assertTrue(DiscflowController._is_placeholder_track("Track 01", "DiscID abc123"))
        self.assertTrue(DiscflowController._is_placeholder_track("Track 01", ""))
        self.assertTrue(DiscflowController._is_placeholder_track("Track 01", "未知艺术家"))
        self.assertFalse(DiscflowController._is_placeholder_track("Track 01", "Nujabes"))
        self.assertFalse(DiscflowController._is_placeholder_track("Feather", "Nujabes"))

    def test_apply_metadata_prioritizes_current_track_and_prevents_empty_overwrite(self):
        ctrl = _make_controller()
        try:
            t1 = cd_engine.CdTrackInfo(1, 0, 13500, 150, 180.0, title="Track 01", artist="DiscID x1")
            t2 = cd_engine.CdTrackInfo(2, 13500, 28500, 13650, 200.0, title="Track 02", artist="DiscID x1")
            ctrl.current_toc = cd_engine.CdDiscToc("E", 1, 2, 28650, [t1, t2], disc_id="disc_x1", freedb_id="fb1")
            ctrl.current_track_index = 0
            ctrl._load_token = 7

            meta = metadata_service.AlbumMetadataResult(
                disc_id="disc_x1",
                album_title="Modal Soul",
                artist="Nujabes",
                track_titles={1: "Feather", 2: "ordinary joe"},
                track_artists={1: "Nujabes", 2: "Nujabes"},
                matched_source="MusicBrainz",
            )

            started_tracks = []

            class _ImmediateThread:
                def __init__(self, target=None, args=(), daemon=True):
                    started_tracks.append((target, args))

                def start(self):
                    pass

            with patch("main.threading.Thread", side_effect=_ImmediateThread):
                ctrl._apply_metadata_to_state(meta, 7)

            self.assertEqual(len(started_tracks), 1)
            self.assertEqual(started_tracks[0][1][0].track_number, 1)
            self.assertTrue(started_tracks[0][1][1])  # force_refresh=True

            # 已有第 1 首有效歌词后，空抓词结果禁止覆盖当前歌词
            valid_lines = [{"time": i * 2.0, "text": f"line {i}", "translation": f"译文 {i}"} for i in range(6)]
            ctrl.lyrics_cache[1] = valid_lines
            ctrl.current_lyrics = valid_lines
            ctrl._bump_lyrics()
            ver_before = ctrl._lyrics_version

            with patch("main.lookup_track_lyrics", return_value=[]):
                ctrl._fetch_lyrics_async(t1, force_refresh=True, my_token=7)

            self.assertEqual(ctrl.current_lyrics, valid_lines)
            self.assertEqual(ctrl._lyrics_version, ver_before)
        finally:
            ctrl.shutdown()

    def test_lookup_track_lyrics_cleans_artists_and_falls_back_to_qq_and_lrclib(self):
        qq_lines = [
            {"time": 1.0, "text": "Hello world", "translation": "你好世界"},
            {"time": 3.0, "text": "Discflow", "translation": "碟流"},
        ]

        ne_empty = MagicMock()
        ne_empty.status_code = 200
        ne_empty.json.return_value = {"result": {"songs": []}}

        with patch("metadata_service.requests.post", return_value=ne_empty) as mock_ne, \
             patch(
                 "metadata_service._qq_search_songs",
                 return_value=[{"song_id": "mid_v230", "title": "Unique QQ Song", "duration_sec": 200}],
             ) as mock_qq_s, \
             patch("metadata_service._qq_lyrics_by_mid", return_value=qq_lines):
            res = metadata_service.lookup_track_lyrics("Unique QQ Song", "Artist Alpha / Artist Beta feat. Gamma", 200.0)

        self.assertEqual(res, qq_lines)
        # 首轮网易云检索只带主艺人 Artist Alpha，且无理想命中时回退纯歌名检索
        queries = [c.kwargs["data"]["s"] for c in mock_ne.call_args_list]
        self.assertEqual(queries, ["Artist Alpha Unique QQ Song", "Unique QQ Song"])
        mock_qq_s.assert_called_once_with("Unique QQ Song", "Artist Alpha", limit=6)

        # Various Artists 应被清洗为空，且 QQ 也为空时回退 LRCLIB
        lrclib_empty_ne = ne_empty
        lrclib_resp = MagicMock()
        lrclib_resp.status_code = 200
        lrclib_resp.json.return_value = [{"duration": 200, "syncedLyrics": "[00:01.00]Only LRC Line"}]
        with patch("metadata_service.requests.post", return_value=lrclib_empty_ne), \
             patch("metadata_service._qq_search_songs", return_value=[]) as mock_qq_va, \
             patch("metadata_service.requests.get", return_value=lrclib_resp):
            res_va = metadata_service.lookup_track_lyrics("Unique LRC Song", "Various Artists", 200.0)
        mock_qq_va.assert_called_once_with("Unique LRC Song", "", limit=6)
        self.assertEqual(len(res_va), 1)
        self.assertEqual(res_va[0]["text"], "Only LRC Line")

    def test_frontend_monotonic_version_guard_and_recovery(self):
        app_js_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "app.js")
        with open(app_js_path, encoding="utf-8") as f:
            js = f.read()
        self.assertIn("if (patch.metaVersion < localMetaVer)", js)
        self.assertIn("if (patch.lyricsVersion < localLyricsVer)", js)
        self.assertIn("const needLyricsRecovery = Boolean(", js)
        self.assertIn("needLyricsRecovery ? -1 : localLyricsVer", js)
        self.assertIn("const hasMetaPayload =", js)
        self.assertIn("const hasLyricsPayload =", js)

    def test_repeated_plain_lyrics_push_keeps_words_and_karaoke_scan(self):
        """连续两次下发不含 words 的普通歌词后，仍保留逐字时间轴且激活行 --wp 正常推进"""
        import re
        import shutil
        import subprocess

        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用，跳过前端卡拉 OK 状态校验")
        html = render_frontend_html(
            init_prefs={"themeMode": "light", "dockOpacity": 5},
            init_state={"isPlaying": False, "albumTitle": "Init", "tracks": []},
        )
        scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html)
        harness = """
function makeNode(tag) {
  const classes = new Set();
  const children = [];
  const styles = {};
  const node = {
    tagName: tag || 'DIV',
    dataset: {},
    children,
    style: {
      setProperty: (k, v) => { styles[k] = String(v); },
      getProperty: (k) => styles[k],
    },
    _styles: styles,
    className: '',
    innerHTML: '',
    textContent: '',
    value: '',
    checked: false,
    src: '',
    scrollHeight: 200,
    clientHeight: 200,
    scrollTop: 0,
    options: [],
    classList: {
      add: (...xs) => xs.forEach(x => classes.add(x)),
      remove: (...xs) => xs.forEach(x => classes.delete(x)),
      toggle: (x, f) => { if (f === undefined) { classes.has(x) ? classes.delete(x) : classes.add(x); } else if (f) { classes.add(x); } else { classes.delete(x); } },
      contains: (x) => classes.has(x),
    },
    setAttribute: () => {},
    getAttribute: () => 'light',
    addEventListener: () => {},
    appendChild: (c) => { children.push(c); return c; },
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 40, bottom: 40 }),
    getContext: () => ({ createRadialGradient: () => ({ addColorStop: () => {} }), fillRect: () => {}, clearRect: () => {} }),
  };
  node.querySelectorAll = (sel) => {
    const out = [];
    const match = (el) => {
      if (sel === '.lyric-word') return el.className === 'lyric-word';
      if (sel === '.lyric-item') return el.className === 'lyric-item';
      const m = sel.match(/^\\.lyric-item\\[data-index="(\\d+)"\\]$/);
      if (m) return el.className === 'lyric-item' && String(el.dataset.index) === m[1];
      return false;
    };
    const walk = (root) => {
      for (const ch of (root.children || [])) {
        if (match(ch)) out.push(ch);
        walk(ch);
      }
    };
    walk(node);
    return out;
  };
  node.querySelector = (sel) => node.querySelectorAll(sel)[0] || null;
  return node;
}
const els = {};
const getEl = (id) => (els[id] || (els[id] = makeNode('DIV')));
global.window = global;
global.window.addEventListener = () => {};
global.window.removeEventListener = () => {};
global.navigator = { userAgent: 'test' };
global.document = {
  documentElement: makeNode('HTML'),
  body: makeNode('BODY'),
  hidden: false,
  getElementById: getEl,
  querySelectorAll: (sel) => {
    if (sel.startsWith('.lyric-')) {
      return [...getEl('lyricsViewport').querySelectorAll(sel), ...getEl('standaloneLyricsViewport').querySelectorAll(sel)];
    }
    return [];
  },
  querySelector: (sel) => {
    if (sel === '#lyricsViewport .lyric-word') return getEl('lyricsViewport').querySelector('.lyric-word');
    return null;
  },
  addEventListener: () => {},
  createElement: (tag) => makeNode(tag),
};
global.localStorage = { getItem: () => null, setItem: () => {} };
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = () => {};
global.matchMedia = () => ({ matches: false });
global.Image = function() {};
global.setInterval = () => 1;
global.setTimeout = (fn) => { global._scheduledTimers.push(fn); return global._scheduledTimers.length; };
global._scheduledTimers = [];
global.clearTimeout = () => {};
global.clearInterval = () => {};
"""
        test_code = """
const plainLyrics = [
  { time: 1.0, text: 'Hello world', translation: '你好世界' },
  { time: 5.0, text: 'Second line', translation: '第二行' }
];
const trk = [{ track_number: 1, title: 'Song 1', artist: 'Artist', duration_sec: 200 }];

// 模拟首次完整下发
window.__onBackendStatePush({
  discId: 'd1', currentTrackIndex: 0, tracks: trk, albumTitle: 'Album 1',
  isPlaying: true, elapsedSec: 2.0, durationSec: 200,
  metaVersion: 2, lyricsVersion: 2, lyrics: plainLyrics.map(x => ({ ...x }))
});

// 模拟空载荷高版本号推送：不得提前吞掉 localMetaVer / localLyricsVer
window.__onBackendStatePush({
  isPlaying: true, elapsedSec: 2.5, durationSec: 200,
  metaVersion: 3, lyricsVersion: 3
});
if (localMetaVer !== 2 || localLyricsVer !== 2) {
  throw new Error('Empty payload must not advance local versions: ' + localMetaVer + ',' + localLyricsVer);
}

// 模拟相同纯 LRC 歌词二次下发（如批量任务结束 _bump_lyrics）：appState.lyrics 必须依然保有 words
window.__onBackendStatePush({
  discId: 'd1', currentTrackIndex: 0, tracks: trk, albumTitle: 'Album 1',
  isPlaying: true, elapsedSec: 2.6, durationSec: 200,
  metaVersion: 3, lyricsVersion: 4, lyrics: plainLyrics.map(x => ({ ...x }))
});

if (!Array.isArray(appState.lyrics[0].words) || appState.lyrics[0].words.length === 0) {
  throw new Error('words missing on appState.lyrics after duplicate push');
}
renderKaraokeWords(0, 2.8);
const wordEls = getEl('lyricsViewport').querySelectorAll('.lyric-word');
if (!wordEls.length) throw new Error('no .lyric-word elements in DOM');
const wps = wordEls.map(w => parseFloat(w._styles['--wp'] || '0'));
if (!wps.some(v => v > 0)) {
  throw new Error('Expected non-zero --wp scan progress, got: ' + JSON.stringify(wps));
}
"""
        path = os.path.join(tempfile.gettempdir(), "discflow_karaoke_sync_test.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(harness + "\n" + "\n".join(scripts) + "\n" + test_code)
        try:
            proc = subprocess.run([node, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"前端卡拉OK与版本同步校验失败：{proc.stderr[:800]}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_enrich_lyrics_with_word_timings_upgrades_qq_lrc(self):
        base = [
            {"time": 1.0, "text": "Hello", "translation": "你好"},
            {"time": 3.0, "text": "World", "translation": "世界"},
        ]
        ne_yrc = [
            {"time": 1.0, "text": "Hello", "translation": "", "words": [{"t": 1.0, "d": 0.5, "w": "Hello"}]},
            {"time": 3.0, "text": "World", "translation": "", "words": [{"t": 3.0, "d": 0.6, "w": "World"}]},
        ]
        ne_search = MagicMock()
        ne_search.status_code = 200
        ne_search.json.return_value = {
            "result": {"songs": [{"id": 12345, "name": "Hello Song", "duration": 200000}]}
        }
        with patch("metadata_service.requests.post", return_value=ne_search), \
             patch("metadata_service._fetch_netease_song_lyrics", return_value=ne_yrc):
            enriched = metadata_service.enrich_lyrics_with_word_timings("Hello Song", "Artist", 200.0, base)
        self.assertEqual(len(enriched), 2)
        self.assertTrue(all(ln.get("words") for ln in enriched))
        self.assertEqual(enriched[0]["translation"], "你好")
        self.assertEqual(enriched[1]["translation"], "世界")


class TestDockTranslationIconRedraw(unittest.TestCase):
    """验证底栏翻译按钮重绘图标、去掉破坏笔画斜杠及设置页开关同步。"""

    def test_dock_trans_svg_redrawn_and_slash_removed(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_dir, "ui", "index.html"), encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base_dir, "ui", "styles.css"), encoding="utf-8") as f:
            css = f.read()
        with open(os.path.join(base_dir, "ui", "app.js"), encoding="utf-8") as f:
            js = f.read()

        self.assertIn('id="dockTransBtn"', html)
        self.assertIn("M7.4 3.2v2.1M3.2 5.3h8.4", html)
        self.assertIn("M13.2 20.2l3.8-9.2 3.8 9.2M14.5 17.2h5.2", html)
        self.assertNotIn(".dock-trans-btn.is-off::after", css)
        self.assertIn(".dock-trans-btn.is-off", css)
        self.assertIn('document.getElementById("prefTransSwitch")', js)


if __name__ == "__main__":
    unittest.main()
