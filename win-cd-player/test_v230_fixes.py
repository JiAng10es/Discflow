import os
import re
import shutil
import subprocess
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


def _fake_dom_harness_js(default_theme: str = "light") -> str:
    """生成前端脚本单测共用的轻量 DOM 模拟脚手架，避免重复维护。"""
    return f"""
function makeNode(tag) {{
  const styles = {{}};
  const classes = new Set();
  const children = [];
  const node = {{
    tagName: tag,
    dataset: {{}},
    children,
    style: {{ setProperty: (k, v) => {{ styles[k] = String(v); }}, getProperty: (k) => styles[k] }},
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
    classList: {{
      add: (...xs) => xs.forEach(x => classes.add(x)),
      remove: (...xs) => xs.forEach(x => classes.delete(x)),
      toggle: (x, f) => {{ if (f === undefined) {{ classes.has(x) ? classes.delete(x) : classes.add(x); }} else if (f) {{ classes.add(x); }} else {{ classes.delete(x); }} }},
      contains: (x) => classes.has(x),
    }},
    setAttribute: () => {{}},
    getAttribute: () => '{default_theme}',
    addEventListener: () => {{}},
    appendChild: (c) => {{ children.push(c); return c; }},
    getBoundingClientRect: () => ({{ left: 0, top: 0, width: 100, height: 40, bottom: 40 }}),
    getContext: () => ({{ createRadialGradient: () => ({{ addColorStop: () => {{}} }}), fillRect: () => {{}}, clearRect: () => {{}} }}),
  }};
  node.querySelectorAll = (sel) => {{
    const out = [];
    const hasCls = (el, cls) => String(el.className || '').split(/\\s+/).includes(cls);
    const match = (el) => {{
      if (sel === '.lyric-word') return hasCls(el, 'lyric-word');
      if (sel === '.lyric-item') return hasCls(el, 'lyric-item');
      const m = sel.match(/^\\.lyric-item\\[data-index="(\\d+)"\\]$/);
      if (m) return hasCls(el, 'lyric-item') && String(el.dataset.index) === m[1];
      return false;
    }};
    const walk = (root) => {{
      for (const ch of (root.children || [])) {{
        if (match(ch)) out.push(ch);
        walk(ch);
      }}
    }};
    walk(node);
    return out;
  }};
  node.querySelector = (sel) => node.querySelectorAll(sel)[0] || null;
  return node;
}}
const els = {{}};
const getEl = (id) => (els[id] || (els[id] = makeNode('DIV')));
global.window = global;
global.window.addEventListener = () => {{}};
global.window.removeEventListener = () => {{}};
global.navigator = {{ userAgent: 'test' }};
global.document = {{
  documentElement: makeNode('HTML'),
  body: makeNode('BODY'),
  hidden: false,
  getElementById: getEl,
  querySelectorAll: (sel) => {{
    if (sel.startsWith('.lyric-')) {{
      return [...getEl('lyricsViewport').querySelectorAll(sel), ...getEl('standaloneLyricsViewport').querySelectorAll(sel)];
    }}
    return [];
  }},
  querySelector: (sel) => {{
    if (sel === '#lyricsViewport .lyric-word') return getEl('lyricsViewport').querySelector('.lyric-word');
    return null;
  }},
  addEventListener: () => {{}},
  createElement: (tag) => makeNode(tag),
}};
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.requestAnimationFrame = () => 1;
global.cancelAnimationFrame = () => {{}};
global.matchMedia = () => ({{ matches: false }});
global.Image = function() {{}};
global.setInterval = () => 1;
global.setTimeout = (fn) => {{ global._scheduledTimers.push(fn); return global._scheduledTimers.length; }};
global._scheduledTimers = [];
global.clearTimeout = () => {{}};
global.clearInterval = () => {{}};
"""


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
        with tempfile.TemporaryDirectory() as tmpdir:
            pref_file = os.path.join(tmpdir, "preferences.json")
            with patch.object(library_service, "get_preferences_file_path", return_value=pref_file):
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
        node = shutil.which("node")
        if not node:
            self.skipTest("node 不可用，跳过前端卡拉 OK 状态校验")
        html = render_frontend_html(
            init_prefs={"themeMode": "light", "dockOpacity": 5},
            init_state={"isPlaying": False, "albumTitle": "Init", "tracks": []},
        )
        scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", html)
        harness = _fake_dom_harness_js("light")
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


class TestV231UrgentImprovements(unittest.TestCase):
    """验证本次 3 项紧迫改进：日文音拍/片头行降噪、网易云候选借用曲目、强制重刷与旧版解锁升级。"""

    def test_japanese_mora_tokenization_and_credit_line_detection(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available")
        from main import render_frontend_html

        html = render_frontend_html(
            init_prefs={"themeMode": "dark"},
            init_state={"albumTitle": "Test", "tracks": [], "lyrics": []},
        )
        scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html)
        self.assertGreaterEqual(len(scripts), 2)

        harness = _fake_dom_harness_js("dark")
        test_code = """
const jpTokens = tokenizeKaraokeLine('しゃぼん玉 ちょっとー');
if (jpTokens.includes('ゃ') || jpTokens.includes('っ') || jpTokens.includes('ー')) {
  throw new Error('Japanese small kana/chouonpu should not be split into standalone tokens: ' + JSON.stringify(jpTokens));
}
if (!jpTokens.includes('しゃ') || !jpTokens.includes('とー')) {
  throw new Error('Expected mora tokens しゃ and とー, got: ' + JSON.stringify(jpTokens));
}
if (!isCreditLyricLine('作词 : 椎名林檎', 0, 0.2) || !isCreditLyricLine('作曲：林正樹', 2, 4.0)) {
  throw new Error('Expected credit lines to be detected');
}
if (isCreditLyricLine('しゃぼん玉が消えた', 4, 24.0)) {
  throw new Error('Normal vocal line must not be flagged as credit line');
}

renderLyricsList([
  { time: 0.2, text: '作词 : 椎名林檎', translation: '' },
  { time: 18.0, text: 'しゃぼん玉', translation: '肥皂泡' }
]);
const items = getEl('lyricsViewport').querySelectorAll('.lyric-item');
if (items.length !== 2) throw new Error('Expected 2 lyric items');
if (!String(items[0].className).includes('is-credit-line')) {
  throw new Error('Credit line missing is-credit-line class: ' + items[0].className);
}
if (items[0].querySelectorAll('.lyric-word').length !== 0) {
  throw new Error('Credit line should not create karaoke .lyric-word spans');
}
if (String(items[1].className).includes('is-credit-line')) {
  throw new Error('Normal line should not have is-credit-line');
}
if (items[1].querySelectorAll('.lyric-word').length === 0) {
  throw new Error('Normal line should create .lyric-word spans');
}
"""
        path = os.path.join(tempfile.gettempdir(), "discflow_mora_credit_test.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(harness + "\n" + "\n".join(scripts) + "\n" + test_code)
        try:
            proc = subprocess.run([node, path], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"日文音拍与片头降噪测试失败：{proc.stderr[:800]}")
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_netease_album_candidate_backfills_tracks_from_donor(self):
        from types import SimpleNamespace
        from main import DiscflowController

        qq_cands = [{
            "source": "qq", "album_id": "QQ_ALB", "title": "大人",
            "artist": "東京事変", "year": "2006", "track_count": 2,
            "cover_url": "https://example.com/qq.jpg", "tracks": [],
        }]
        ne_cands = [{
            "source": "netease", "album_id": "NE_ALB", "title": "大人 (ADULT)",
            "artist": "東京事変", "year": "2006", "track_count": 2,
            "cover_url": "https://example.com/ne.jpg", "tracks": [],
        }]
        qq_tracks = [
            {"title": "秘密", "duration_sec": 230.0, "song_id": "QQ_S1"},
            {"title": "喧嘩上等", "duration_sec": 143.0, "song_id": "QQ_S2"},
        ]

        orig = (
            metadata_service._qq_album_search,
            metadata_service._itunes_album_search_lite,
            metadata_service._netease_album_search,
            metadata_service._qq_album_detail,
            metadata_service._netease_album_detail,
        )
        try:
            metadata_service._qq_album_search = lambda q, limit=6: [dict(x) for x in qq_cands]
            metadata_service._itunes_album_search_lite = lambda q, c, limit=6: []
            metadata_service._netease_album_search = lambda q, limit=6: [dict(x) for x in ne_cands]
            metadata_service._qq_album_detail = lambda mid: ("2006", [dict(t) for t in qq_tracks])
            metadata_service._netease_album_detail = lambda aid: []

            cands = metadata_service.search_album_candidates("大人", "東京事変", expected_track_count=2)
        finally:
            (
                metadata_service._qq_album_search,
                metadata_service._itunes_album_search_lite,
                metadata_service._netease_album_search,
                metadata_service._qq_album_detail,
                metadata_service._netease_album_detail,
            ) = orig

        ne_res = next((c for c in cands if c.get("source") == "netease"), None)
        self.assertIsNotNone(ne_res)
        self.assertEqual(len(ne_res["tracks"]), 2, "网易云候选应从同批次 QQ donor 借用完整曲目表")
        self.assertTrue(ne_res["supports_track_ids"])
        self.assertEqual(ne_res["tracks"][0]["source"], "qq")
        self.assertEqual(ne_res["tracks"][0]["song_id"], "QQ_S1")

        ctrl = DiscflowController()
        ctrl.start_album_lyrics_batch = lambda *a, **kw: {"ok": False}
        ctrl.current_toc = SimpleNamespace(
            disc_id="probe_ne_backfill",
            is_local=False,
            tracks=[
                SimpleNamespace(track_number=1, title="Track 01", artist="未知艺术家", duration_sec=230.0),
                SimpleNamespace(track_number=2, title="Track 02", artist="未知艺术家", duration_sec=143.0),
            ],
        )
        with patch("main.download_image_bytes", return_value=None):
            res = ctrl.apply_album_candidate(ne_res)
        self.assertTrue(res["ok"])
        self.assertEqual(ctrl.current_toc.tracks[0].title, "秘密")
        self.assertEqual(ctrl.track_song_ids[1], {"source": "qq", "song_id": "QQ_S1"})

    def test_force_refresh_and_legacy_locked_lyrics_upgrade(self):
        from types import SimpleNamespace
        from main import DiscflowController

        ctrl = DiscflowController()
        toc = SimpleNamespace(
            disc_id="probe_legacy_upgrade",
            is_local=False,
            tracks=[
                SimpleNamespace(track_number=1, title="秘密", artist="東京事変", duration_sec=230.0, builtin_lrc=""),
                SimpleNamespace(track_number=2, title="喧嘩上等", artist="東京事変", duration_sec=143.0, builtin_lrc=""),
            ],
        )
        ctrl.current_toc = toc
        ctrl.current_track_index = 0
        # 曲1为旧版伪锁定歌词（无 words 且无翻译），曲2为已完全达标的手动锁定歌词
        ctrl.lyrics_locked_tracks = {1, 2}
        ctrl.lyrics_cache = {
            1: [{"time": 1.0, "text": "旧版纯原文", "translation": ""}],
            2: [
                {"time": float(i + 1), "text": f"Line {i}", "translation": f"译文 {i}",
                 "words": [{"t": float(i + 1), "d": 0.4, "w": "Line"}]}
                for i in range(6)
            ],
        }

        upgraded_lines = [
            {"time": float(i + 1), "text": f"秘密 {i}", "translation": f"秘密译 {i}",
             "words": [{"t": float(i + 1), "d": 0.5, "w": "秘密"}]}
            for i in range(6)
        ]

        with patch("main.lookup_track_lyrics", return_value=upgraded_lines), \
             patch("main.enrich_lyrics_with_word_timings", side_effect=lambda t, a, d, lns: lns):
            ctrl._prefetch_lyrics_async(ctrl._load_token)

        self.assertTrue(any(ln.get("words") for ln in ctrl.lyrics_cache[1]), "旧版无逐字且无翻译的锁定歌词应自动升级")
        self.assertEqual(ctrl.lyrics_cache[2][0]["text"], "Line 0", "已达标锁定歌词在预取阶段应保持不变")

        forced_lines = [
            {"time": float(i + 1), "text": f"重刷 {i}", "translation": f"重刷译 {i}",
             "words": [{"t": float(i + 1), "d": 0.5, "w": "重刷"}]}
            for i in range(6)
        ]
        with patch("main.lookup_track_lyrics", return_value=forced_lines), \
             patch("main.enrich_lyrics_with_word_timings", side_effect=lambda t, a, d, lns: lns):
            ctrl._run_album_lyrics_batch(toc, toc.tracks, force_refresh=True)

        self.assertEqual(ctrl.lyrics_cache[2][0]["text"], "重刷 0", "force_refresh=True 应能强制重刷已锁定音轨")


class TestCdRippingSpeedAndReliability(unittest.TestCase):
    """验证抓轨数据轨跳过、零拷贝流式 WAV 写入、局部划痕不永久降速与播放器句柄空闲等待。"""

    def test_wait_reader_idle_returns_true_when_idle(self):
        player = cd_engine.CddaWaveOutPlayer()
        self.assertTrue(player.wait_reader_idle(timeout=0.1))

    def test_ripper_skips_data_track_and_recovers_scratch_without_crippling_batch(self):
        import ctypes
        import wave
        from types import SimpleNamespace

        tmp_dir = tempfile.mkdtemp(prefix="discflow_rip_test_")
        try:
            mgr = library_service.DiscflowLibraryManager(base_dir=tmp_dir)
            # Track 1: 130 sectors (64 + 64 + 2), Track 2: data track (must be skipped!)
            toc = SimpleNamespace(
                disc_id="rip_probe_disc",
                drive_letter="G:",
                tracks=[
                    SimpleNamespace(
                        track_number=1, title="Track One", artist="Artist",
                        duration_sec=1.73, start_lba=0, end_lba=130, is_data_track=False,
                    ),
                    SimpleNamespace(
                        track_number=2, title="Data Track", artist="Artist",
                        duration_sec=10.0, start_lba=130, end_lba=500, is_data_track=True,
                    ),
                ],
            )
            task = library_service.CdRipperTask(
                toc=toc,
                album_title="Rip Album",
                artist="Artist",
                year="2026",
                cover_data_url="",
                lyrics_cache={},
                library_mgr=mgr,
                rip_format="wav",
            )

            sector_counts_seen = []

            def fake_ioctl(h, code, in_ptr, in_sz, out_buf, out_sz, ret_ptr, overlapped):
                raw_info = ctypes.cast(in_ptr, ctypes.POINTER(cd_engine.RAW_READ_INFO)).contents
                lba = int(raw_info.DiskOffset // 2048)
                cnt = int(raw_info.SectorCount)
                sector_counts_seen.append((lba, cnt))
                # 模拟第 2 批 (lba=64, cnt=64) 遇到 1 处局部划痕整批失败，但单扇区读时仅 lba=65 坏道，后续批恢复 64 扇区
                if cnt == 64 and lba == 64:
                    return 0
                if cnt == 1 and lba == 65:
                    return 0
                ret_val = ctypes.cast(ret_ptr, ctypes.POINTER(ctypes.c_ulong))
                ret_val.contents.value = out_sz
                ctypes.memset(out_buf, 0x2A, out_sz)
                return 1

            with patch.object(library_service.WindowsCdDriveManager, "open_drive_handle", return_value=12345), \
                 patch.object(library_service.kernel32, "DeviceIoControl", side_effect=fake_ioctl), \
                 patch.object(library_service.kernel32, "CloseHandle", return_value=1):
                task.run()

            prog = task.get_progress()
            self.assertTrue(prog.is_completed, f"抓轨应成功完成（跳过数据轨），实际：{prog}")
            self.assertEqual(prog.total_tracks, 1)

            wav_path = os.path.join(tmp_dir, "rip_probe_disc", "track_01.wav")
            self.assertTrue(os.path.isfile(wav_path))
            with wave.open(wav_path, "rb") as wf:
                # 130 sectors * 2352 bytes / 4 bytes per stereo 16-bit frame = 76440 frames
                self.assertEqual(wf.getnframes(), 130 * 2352 // 4)
            # 首个 64 扇区成功后，局部划痕不应导致永久降档到 48/32/16
            self.assertNotIn(48, [c for _, c in sector_counts_seen])
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_ripper_cleans_up_partial_dir_on_cancel(self):
        from types import SimpleNamespace

        tmp_dir = tempfile.mkdtemp(prefix="discflow_rip_cancel_")
        try:
            mgr = library_service.DiscflowLibraryManager(base_dir=tmp_dir)
            toc = SimpleNamespace(
                disc_id="rip_cancel_disc",
                drive_letter="G:",
                tracks=[
                    SimpleNamespace(
                        track_number=1, title="Track One", artist="Artist",
                        duration_sec=1.0, start_lba=0, end_lba=64, is_data_track=False,
                    ),
                ],
            )
            task = library_service.CdRipperTask(
                toc=toc, album_title="Cancel Album", artist="Artist", year="2026",
                cover_data_url="", lyrics_cache={}, library_mgr=mgr,
            )
            task.cancel()

            with patch.object(library_service.WindowsCdDriveManager, "open_drive_handle", return_value=12345), \
                 patch.object(library_service.kernel32, "CloseHandle", return_value=1):
                task.run()

            self.assertFalse(os.path.exists(os.path.join(tmp_dir, "rip_cancel_disc")), "取消抓轨应清理半成品目录")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_playback_and_watcher_blocked_while_ripping(self):
        from types import SimpleNamespace

        ctrl = _make_controller()
        ctrl.current_toc = SimpleNamespace(
            disc_id="rip_lock_disc",
            drive_letter="G:",
            is_local=False,
            tracks=[
                SimpleNamespace(
                    track_number=1, title="Track 1", artist="Artist",
                    duration_sec=180.0, start_lba=0, end_lba=13500, is_data_track=False,
                )
            ],
        )
        ctrl.ripper_task = SimpleNamespace(
            get_progress=lambda: library_service.RippingProgress(is_ripping=True)
        )
        self.assertTrue(ctrl._is_ripping())
        with patch.object(ctrl.player, "load_and_play") as mock_play, \
             patch.object(ctrl.player, "seek_seconds") as mock_seek:
            ctrl.toggle_play_pause()
            ctrl._start_track(0)
            ctrl.seek_to(10.0)
            mock_play.assert_not_called()
            mock_seek.assert_not_called()
            self.assertIn("抓轨", ctrl.status_text)

    def test_check_for_updates_falls_back_to_web_redirect_on_403(self):
        ctrl = _make_controller()
        api_resp = MagicMock(status_code=403)
        web_resp = MagicMock(status_code=200, url="https://github.com/JiAng10es/Discflow/releases/tag/v9.9.9")
        head_resp = MagicMock(status_code=200, headers={"Content-Length": "15260569"})

        def _fake_get(url, *args, **kwargs):
            if "/releases/latest" in str(url) and "api.github.com" not in str(url):
                return web_resp
            return api_resp

        try:
            with patch("main.requests.get", side_effect=_fake_get), \
                 patch("main.requests.head", return_value=head_resp):
                info = ctrl.check_for_updates(force=True)
        finally:
            ctrl.shutdown()

        self.assertTrue(info["ok"])
        self.assertTrue(info["hasUpdate"])
        self.assertEqual(info["latestVersion"], "9.9.9")
        self.assertEqual(info["downloadUrl"], "https://github.com/JiAng10es/Discflow/releases/download/v9.9.9/Discflow.exe")
        self.assertEqual(info["assetSize"], 15260569)


class TestRealEnvSmokeAndLogging(unittest.TestCase):
    """v2.3.2 真实环境回归链路：轻量运行日志 + 无头 Edge 真机 DOM/JS 冒烟测试"""

    def test_runtime_logger_writes_in_sandbox_and_avoids_real_root(self):
        from library_service import get_runtime_log_path, log_runtime_event

        log_path = get_runtime_log_path()
        self.assertFalse(
            os.path.normcase(log_path).startswith(os.path.normcase(r"F:\Discflow1.0\discflow.log")),
            f"测试模式下日志必须隔离在临时沙盒，不能污染真实根目录: {log_path}",
        )
        marker = f"smoke-marker-{os.getpid()}"
        log_runtime_event("SMOKE", marker)
        self.assertTrue(os.path.isfile(log_path), f"日志文件未生成: {log_path}")
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("[INFO] [SMOKE]", content)
        self.assertIn(marker, content)

    def test_headless_edge_ui_smoke_zero_errors(self):
        import main as app_main
        import shutil
        import subprocess
        import tempfile

        edge_exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        if not os.path.isfile(edge_exe):
            self.skipTest("未安装 Microsoft Edge，跳过无头浏览器冒烟测试")

        init_prefs = {
            "themeMode": "dark",
            "dockOpacity": 5,
            "dockBlur": 2,
            "topbarOpacity": 5,
            "topbarBlur": 2,
            "dockWidth": 720,
            "lyricLineHeight": 1.4,
            "lyricLetterSpacing": 0,
            "showTranslation": True,
        }
        init_state = {
            "mode": "library",
            "albumTitle": "Smoke Test Album",
            "artist": "Test Artist",
            "tracks": [{"trackNumber": 1, "title": "Track 01", "durationSec": 210, "durationFormatted": "03:30"}],
            "currentTrackIndex": 0,
            "lyrics": [{"time": 0.0, "text": "Hello Discflow", "trans": "你好", "words": []}],
            "libraryAlbums": [],
        }
        html = app_main.render_frontend_html(init_prefs, init_state)
        probe_head = """<script>
window.__smokeErrors = [];
window.addEventListener('error', function (e) {
  window.__smokeErrors.push(String(e && (e.message || e.error) || 'error'));
});
window.addEventListener('unhandledrejection', function (e) {
  window.__smokeErrors.push('rejection:' + String(e && e.reason || 'unknown'));
});
window.pywebview = {
  api: new Proxy({}, {
    get: function (_t, prop) {
      return function () {
        if (prop === 'get_state') return Promise.resolve({ mode: 'library', tracks: [], lyrics: [] });
        if (prop === 'check_for_updates') return Promise.resolve({ ok: true, hasUpdate: false, latestVersion: '2.3.2' });
        if (prop === 'get_library_albums') return Promise.resolve({ albums: [] });
        return Promise.resolve({ ok: true });
      };
    }
  })
};
</script>"""
        probe_body = """<div id="__smoke_report" data-ready="0"></div>
<script>
setTimeout(function () {
  try {
    var settingsBtn = document.getElementById('settingsBtn');
    if (settingsBtn) settingsBtn.click();
    var closeSettings = document.getElementById('closeSettingsBtn');
    if (closeSettings) closeSettings.click();
    var libTab = document.getElementById('navLibraryBtn');
    if (libTab) libTab.click();
    var playerTab = document.getElementById('navPlayerBtn');
    if (playerTab) playerTab.click();
  } catch (err) {
    window.__smokeErrors.push('interact:' + String(err));
  }
  var rep = document.getElementById('__smoke_report');
  if (rep) {
    rep.setAttribute('data-ready', '1');
    rep.setAttribute('data-errors', String(window.__smokeErrors.length));
    rep.setAttribute('data-error-detail', window.__smokeErrors.join(' | '));
    rep.setAttribute('data-has-dock', document.getElementById('bottomDockPlayer') ? '1' : '0');
  }
}, 400);
</script>"""
        patched_html = html.replace("<head>", "<head>\n" + probe_head, 1).replace("</body>", probe_body + "\n</body>", 1)

        tmp_dir = tempfile.mkdtemp(prefix="discflow_edge_smoke_")
        try:
            html_path = os.path.join(tmp_dir, "smoke.html")
            profile_dir = os.path.join(tmp_dir, "profile")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(patched_html)
            file_uri = "file:///" + html_path.replace("\\", "/")
            proc = subprocess.run(
                [
                    edge_exe,
                    "--headless",
                    "--disable-gpu",
                    f"--user-data-dir={profile_dir}",
                    "--virtual-time-budget=2500",
                    "--dump-dom",
                    file_uri,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, f"Edge headless 执行失败: {proc.stderr}")
            dom = proc.stdout
            self.assertIn('id="__smoke_report"', dom)
            self.assertIn('data-ready="1"', dom)
            self.assertIn('data-has-dock="1"', dom)
            self.assertIn('data-errors="0"', dom, f"无头 Edge 检测到前端运行时报错:\n{dom[-1200:]}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_nested_continuous_corners_tokens_and_supports_block(self):
        styles_path = os.path.join(os.path.dirname(__file__), "ui", "styles.css")
        with open(styles_path, "r", encoding="utf-8") as f:
            css = f.read()

        for token in (
            "--radius-dialog: 28px;",
            "--radius-card: 20px;",
            "--radius-panel: 12px;",
            "--radius-control: 8px;",
            "--radius-chip: 4px;",
        ):
            self.assertIn(token, css, f"缺少递进式圆角 Token: {token}")

        self.assertIn("@supports (corner-shape: superellipse(1.25))", css)
        self.assertIn("corner-shape: superellipse(1.25);", css)
        self.assertIn("corner-shape: round;", css)
        self.assertIn("border-radius: 50%;", css)

    def test_library_playback_smooth_transition_and_async_start(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "ui", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(base_dir, "ui", "styles.css"), "r", encoding="utf-8") as f:
            css = f.read()
        with open(os.path.join(base_dir, "main.py"), "r", encoding="utf-8") as f:
            py = f.read()

        anim_body = js[js.index("async function playLocalAlbumWithAnimation"):js.index("async function playLocalAlbum(")]
        self.assertIn("applyOptimisticLocalAlbum(discId)", anim_body)
        self.assertIn('applyRightPanelMode("lyrics")', anim_body)
        self.assertNotIn("setTimeout(r, 460)", anim_body)
        self.assertNotIn(", 280);", anim_body)
        self.assertIn("window._libraryRenderSig", js)
        self.assertIn('decoding="async"', js)
        self.assertIn("@keyframes viewPageFadeIn", css)
        active_lyric_css = css[css.index(".lyric-item.active {"):css.index(".lyric-item.active .lyric-orig {")]
        self.assertIn("background: transparent;", active_lyric_css)
        self.assertNotIn("lyricActiveRise", css)
        self.assertIn("transform 0.32s linear", css)
        self.assertIn("def _async_start_local_album():", py)

    def test_phase1_cover_thumbnail_and_adaptive_polling(self):
        import base64
        import io
        import json
        import tempfile
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            alb_dir = os.path.join(tmpdir, "test_disc")
            os.makedirs(alb_dir, exist_ok=True)
            img = Image.new("RGB", (1000, 1000), color=(42, 120, 210))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            raw_png = buf.getvalue()
            cover_path = os.path.join(alb_dir, "cover.png")
            with open(cover_path, "wb") as f:
                f.write(raw_png)

            meta = {
                "disc_id": "test_disc",
                "title": "Thumb Album",
                "artist": "Tester",
                "year": "2026",
                "cover_file": "cover.png",
                "cover_data_url": f"data:image/png;base64,{base64.b64encode(raw_png).decode('ascii')}",
                "ripped_at": 100.0,
                "tracks": [{"track_number": 1, "title": "T1", "artist": "Tester", "duration_sec": 120.0, "file_name": "01.wav", "lyrics": [{"time": 1.0, "text": "hi"}]}],
            }
            with open(os.path.join(alb_dir, "album.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f)

            mgr = library_service.DiscflowLibraryManager(base_dir=tmpdir)
            albums = mgr.list_albums(force_refresh=True)
            self.assertEqual(len(albums), 1)
            self.assertTrue(albums[0].thumb_data_url.startswith("data:image/jpeg;base64,"))
            self.assertTrue(os.path.isfile(os.path.join(alb_dir, library_service.COVER_THUMB_FILENAME)))
            self.assertLess(len(albums[0].thumb_data_url), 25000)

            ctrl = _make_controller()
            try:
                ctrl.library_manager = mgr
                light = ctrl.list_local_albums()
                self.assertEqual(len(light), 1)
                self.assertEqual(light[0]["cover_data_url"], "")
                self.assertTrue(light[0]["thumb_data_url"].startswith("data:image/jpeg;base64,"))
                self.assertNotIn("lyrics", light[0]["tracks"][0])
            finally:
                ctrl.shutdown()

        with open(os.path.join(os.path.dirname(__file__), "ui", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("function syncBackendPollSchedule()", js)
        self.assertIn("alb.thumb_data_url || alb.cover_data_url", js)

    def test_phase2_modular_split_win_native_and_updater_service(self):
        import updater_service
        import win_native

        for fn_name in (
            "enable_dpi_awareness",
            "get_dpi_scale",
            "apply_dwm_rounded_dark_window",
            "sync_native_window_theme",
            "begin_native_window_resize",
            "GlobalMediaKeyListener",
        ):
            self.assertTrue(hasattr(win_native, fn_name), f"win_native 缺少 {fn_name}")

        for fn_name in (
            "parse_version_tuple",
            "resolve_update_target_exe",
            "build_self_update_script",
            "check_for_updates_info",
            "download_and_stage_update",
            "apply_staged_update_and_restart",
        ):
            self.assertTrue(hasattr(updater_service, fn_name), f"updater_service 缺少 {fn_name}")

    def test_phase3_flac_default_ripping_tags_and_local_reader(self):
        import base64
        import ctypes
        import io
        from types import SimpleNamespace
        from PIL import Image
        from mutagen.flac import FLAC

        tmp_dir = tempfile.mkdtemp(prefix="discflow_rip_flac_")
        try:
            mgr = library_service.DiscflowLibraryManager(base_dir=tmp_dir)
            img = Image.new("RGB", (200, 200), color=(30, 90, 180))
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            cover_data_url = f"data:image/jpeg;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"

            toc = SimpleNamespace(
                disc_id="rip_flac_disc",
                drive_letter="D:",
                tracks=[
                    SimpleNamespace(
                        track_number=1, title="FLAC Song", artist="FLAC Artist",
                        duration_sec=2.0, start_lba=0, end_lba=75, is_data_track=False,
                    ),
                ],
            )
            task = library_service.CdRipperTask(
                toc=toc,
                album_title="FLAC Album",
                artist="FLAC Artist",
                year="2026",
                cover_data_url=cover_data_url,
                lyrics_cache={},
                library_mgr=mgr,
            )
            self.assertEqual(task.rip_format, "flac")

            def fake_ioctl(h, code, in_ptr, in_sz, out_buf, out_sz, ret_ptr, overlapped):
                ret_val = ctypes.cast(ret_ptr, ctypes.POINTER(ctypes.c_ulong))
                ret_val.contents.value = out_sz
                ctypes.memset(out_buf, 0x15, out_sz)
                return 1

            with patch.object(library_service.WindowsCdDriveManager, "open_drive_handle", return_value=12345), \
                 patch.object(library_service.kernel32, "DeviceIoControl", side_effect=fake_ioctl), \
                 patch.object(library_service.kernel32, "CloseHandle", return_value=1):
                task.run()

            prog = task.get_progress()
            self.assertTrue(prog.is_completed, f"FLAC 抓轨应成功完成，实际：{prog}")

            flac_path = os.path.join(tmp_dir, "rip_flac_disc", "track_01.flac")
            self.assertTrue(os.path.isfile(flac_path))
            expected_frames = 75 * 2352 // 4

            # 验证 Vorbis Comment 标签与内嵌封面
            meta = FLAC(flac_path)
            self.assertEqual(meta["TITLE"], ["FLAC Song"])
            self.assertEqual(meta["ARTIST"], ["FLAC Artist"])
            self.assertEqual(meta["ALBUM"], ["FLAC Album"])
            self.assertEqual(meta["DATE"], ["2026"])
            self.assertEqual(meta["TRACKNUMBER"], ["1"])
            self.assertEqual(meta["TRACKTOTAL"], ["1"])
            self.assertEqual(len(meta.pictures), 1)
            self.assertEqual(meta.pictures[0].type, 3)

            # 验证 _LocalAudioReader 无缝读取与 Seek
            reader = cd_engine._LocalAudioReader(flac_path)
            try:
                self.assertEqual(reader.getnframes(), expected_frames)
                chunk = reader.readframes(588)
                self.assertEqual(len(chunk), 588 * 4)
                reader.setpos(1000)
                chunk2 = reader.readframes(588)
                self.assertEqual(len(chunk2), 588 * 4)
            finally:
                reader.close()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_phase3_wav_to_flac_migration_and_stats(self):
        import io
        import json
        import wave
        from PIL import Image
        from mutagen.flac import FLAC

        tmp_dir = tempfile.mkdtemp(prefix="discflow_migrate_")
        try:
            alb_dir = os.path.join(tmp_dir, "old_wav_disc")
            os.makedirs(alb_dir, exist_ok=True)
            img = Image.new("RGB", (120, 120), color=(200, 60, 40))
            cover_path = os.path.join(alb_dir, "cover.jpg")
            img.save(cover_path, format="JPEG")

            wav_path = os.path.join(alb_dir, "track_01.wav")
            expected_frames = 44100  # 1 秒 44.1kHz 16-bit 立体声
            pcm_bytes = (b"\x10\x02\xf0\xfd") * expected_frames
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(pcm_bytes)

            meta = {
                "disc_id": "old_wav_disc",
                "title": "Legacy WAV Album",
                "artist": "Legacy Artist",
                "year": "2024",
                "cover_file": "cover.jpg",
                "cover_data_url": "",
                "ripped_at": 100.0,
                "tracks": [
                    {
                        "track_number": 1,
                        "title": "Legacy Track",
                        "artist": "Legacy Artist",
                        "duration_sec": 1.0,
                        "file_name": "track_01.wav",
                        "lyrics": [],
                    }
                ],
            }
            with open(os.path.join(alb_dir, "album.json"), "w", encoding="utf-8") as mf:
                json.dump(meta, mf)

            mgr = library_service.DiscflowLibraryManager(base_dir=tmp_dir)
            stats_before = mgr.get_wav_stats()
            self.assertEqual(stats_before["wavAlbumCount"], 1)
            self.assertEqual(stats_before["wavTrackCount"], 1)
            self.assertGreater(stats_before["wavTotalBytes"], 0)

            res = mgr.convert_wav_albums_to_flac()
            self.assertEqual(res["convertedAlbums"], 1)
            self.assertEqual(res["convertedTracks"], 1)
            self.assertEqual(res["errors"], [])

            flac_path = os.path.join(alb_dir, "track_01.flac")
            self.assertTrue(os.path.isfile(flac_path))
            self.assertFalse(os.path.isfile(wav_path))

            reader = cd_engine._LocalAudioReader(flac_path)
            try:
                self.assertEqual(reader.getnframes(), expected_frames)
                decoded = reader.readframes(expected_frames)
                self.assertEqual(decoded, pcm_bytes)
            finally:
                reader.close()

            tags = FLAC(flac_path)
            self.assertEqual(tags["TITLE"], ["Legacy Track"])
            self.assertEqual(tags["ALBUM"], ["Legacy WAV Album"])
            self.assertEqual(len(tags.pictures), 1)

            stats_after = mgr.get_wav_stats()
            self.assertEqual(stats_after["wavTrackCount"], 0)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_phase3_audio_output_mode_and_wasapi_fallback(self):
        prefs = library_service.load_user_preferences()
        self.assertIn(prefs.get("ripFormat"), ("flac", "wav"))
        self.assertIn(prefs.get("audioOutputMode"), cd_engine.CddaWaveOutPlayer.VALID_OUTPUT_MODES)

        fallback_msgs = []
        player = cd_engine.CddaWaveOutPlayer(on_output_fallback=lambda m: fallback_msgs.append(m))
        self.assertEqual(player.get_output_mode(), "waveout")
        self.assertEqual(player.set_output_mode("wasapi_exclusive"), "wasapi_exclusive")
        self.assertEqual(player.set_output_mode("invalid_mode"), "waveout")

        # 100% 音量 Bit-Perfect 直通（原字节返回）；50% 音量衰减
        raw = b"\x00\x20\x00\xe0"
        self.assertEqual(cd_engine.CddaWaveOutPlayer._scale_pcm16_volume(raw, 1.0), raw)
        scaled = cd_engine.CddaWaveOutPlayer._scale_pcm16_volume(raw, 0.5)
        self.assertNotEqual(scaled, raw)
        self.assertEqual(len(scaled), len(raw))

        # 模拟 WASAPI 打开失败 -> 触发自动回退通知并返回 False（交由 waveOut 兜底）
        dummy_trk = cd_engine.CdTrackInfo(
            track_number=1, start_lba=0, end_lba=75, start_frame=150, duration_sec=1.0
        )
        with patch.object(player, "_open_wasapi_stream", side_effect=RuntimeError("mock busy")):
            handled = player._run_wasapi_loop(player._session_id, "", dummy_trk, 0, exclusive=True)
        self.assertFalse(handled)
        self.assertEqual(len(fallback_msgs), 1)
        self.assertIn("回退", fallback_msgs[0])

    def test_phase3_ui_and_spec_wiring(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "ui", "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base_dir, "ui", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()
        with open(os.path.join(base_dir, "Discflow.spec"), "r", encoding="utf-8") as f:
            spec = f.read()

        self.assertIn('id="audioOutputModeControl"', html)
        self.assertIn('id="ripFormatControl"', html)
        self.assertIn('id="wavMigrationBtn"', html)
        self.assertIn("function onAudioOutputModeChange(", js)
        self.assertIn("function onRipFormatChange(", js)
        self.assertIn("function startWavToFlacMigration(", js)
        self.assertIn("_soundfile_data", spec)
        self.assertIn("_sounddevice_data", spec)
        self.assertIn("mutagen.flac", spec)

    def test_lyric_offset_dock_controls_and_ime_shortcuts(self):
        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "ui", "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base_dir, "ui", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn('id="dockLyricOffsetWrap"', html)
        self.assertIn('id="dockLyricOffsetBtn"', html)
        self.assertIn('id="dockLyricOffsetVal"', html)
        self.assertIn("_lyricOffsetGuardUntil", js)
        self.assertIn('e.key === "【"', js)
        self.assertIn('e.key === "】"', js)
        self.assertIn('e.key === "、"', js)

    def test_album_matching_trad_simp_duration_fit_and_cdtext_backfill(self):
        import metadata_service

        # 1. 繁简汉字归一化相似度
        self.assertEqual(metadata_service._title_similarity("周杰倫 葉惠美", "周杰伦 叶惠美"), 1.0)

        # 2. 逐轨时长吻合度打分将真正吻合压盘的专辑排在第一位
        fake_cands = [
            {
                "source": "qq", "album_id": "live_ver", "title": "Test Album",
                "artist": "Tester", "year": "2020", "track_count": 2, "cover_url": "",
            },
            {
                "source": "itunes", "album_id": "studio_ver", "title": "Test Album",
                "artist": "Tester", "year": "2020", "track_count": 2, "cover_url": "",
            },
        ]

        def fake_fill(cand):
            out = dict(cand)
            if cand["album_id"] == "live_ver":
                out["tracks"] = [
                    {"track_number": 1, "title": "Song A (Live)", "duration_sec": 300.0},
                    {"track_number": 2, "title": "Song B (Live)", "duration_sec": 320.0},
                ]
            else:
                out["tracks"] = [
                    {"track_number": 1, "title": "Song A", "duration_sec": 210.5},
                    {"track_number": 2, "title": "Song B", "duration_sec": 241.0},
                ]
            out["_cleanliness"] = 1.0
            out["_dirty_tracks"] = 0
            return out

        with patch.object(metadata_service, "_qq_album_search", return_value=[fake_cands[0]]), \
             patch.object(metadata_service, "_itunes_album_search_lite", return_value=[fake_cands[1]]), \
             patch.object(metadata_service, "_netease_album_search", return_value=[]), \
             patch.object(metadata_service, "_fill_album_tracks", side_effect=fake_fill):
            res = metadata_service.search_album_candidates(
                "Test Album", "Tester", expected_track_count=2, expected_durations=[210.0, 240.0]
            )
        self.assertGreaterEqual(len(res), 2)
        self.assertEqual(res[0]["album_id"], "studio_ver")
        self.assertEqual(res[0]["durationMatchedCount"], 2)

        # 3. CD-TEXT 有专辑名但曲目为占位 Track 01 时自动三源补全
        toc = cd_engine.CdDiscToc(
            drive_letter="D:",
            first_track=1,
            last_track=2,
            leadout_frame=34000,
            tracks=[
                cd_engine.CdTrackInfo(track_number=1, start_lba=0, end_lba=15750, start_frame=150, duration_sec=210.0, title="Track 01"),
                cd_engine.CdTrackInfo(track_number=2, start_lba=15750, end_lba=33750, start_frame=15900, duration_sec=240.0, title="Track 02"),
            ],
            disc_id="cdtext_backfill_disc",
            freedb_id="12345678",
            toc_query_string="1+2+34000+150+15900",
            cdtext_album="Test Album",
            cdtext_artist="Tester",
        )
        metadata_service._METADATA_CACHE.pop("cdtext_backfill_disc", None)
        with patch.object(metadata_service.requests, "get", side_effect=RuntimeError("offline mb/gnudb")), \
             patch.object(metadata_service, "search_album_candidates", return_value=[{
                 "source": "itunes", "title": "Test Album", "artist": "Tester", "year": "2020",
                 "cover_url": "", "garbled": False, "titleSimilarity": 1.0, "durationFitScore": 1.0,
                 "tracks": [
                     {"track_number": 1, "title": "Real Track 1", "duration_sec": 210.0},
                     {"track_number": 2, "title": "Real Track 2", "duration_sec": 240.0},
                 ],
             }]), \
             patch.object(metadata_service, "_fetch_cover_art_bytes", return_value=b""):
            meta = metadata_service.lookup_album_metadata(toc)
        self.assertEqual(meta.track_titles[1], "Real Track 1")
        self.assertEqual(meta.track_titles[2], "Real Track 2")
        self.assertIn("补全", meta.matched_source)

    def test_compact_initial_state_prevents_webview2_oversize_black_screen(self):
        import main
        huge_cover = "data:image/png;base64," + ("A" * 1200000)
        state = {"discId": "nonexistent", "metaVersion": 5, "coverDataUrl": huge_cover}
        compact = main._compact_initial_state_for_html(state)
        self.assertLessEqual(len(compact["coverDataUrl"]), 65536)
        self.assertEqual(compact["metaVersion"], -1)

    def test_fullscreen_immersive_and_merged_lyrics_popover(self):
        import main
        import win_native

        self.assertTrue(hasattr(win_native, "set_native_window_fullscreen"))
        ctrl = _make_controller()
        try:
            api = main.DiscflowJsApi(ctrl)
            self.assertTrue(callable(getattr(api, "toggle_fullscreen_window", None)))
            self.assertTrue(callable(getattr(api, "set_fullscreen_window", None)))
            # 无窗口环境下切换状态安全无异常，且全屏时不覆写 windowBounds
            self.assertTrue(ctrl.set_fullscreen_window(True))
            with patch.object(main, "save_user_preferences") as mock_save:
                ctrl._save_window_bounds()
                mock_save.assert_not_called()
            self.assertFalse(ctrl.set_fullscreen_window(False))
        finally:
            ctrl.shutdown()

        base_dir = os.path.dirname(__file__)
        with open(os.path.join(base_dir, "ui", "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        with open(os.path.join(base_dir, "ui", "styles.css"), "r", encoding="utf-8") as f:
            css = f.read()
        with open(os.path.join(base_dir, "ui", "app.js"), "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn('id="dockFullscreenBtn"', html)
        self.assertIn('onclick="toggleFullscreenImmersive()"', html)
        # 确认双语翻译与歌词对齐已整合进 dockLyricOffsetPopover 内
        pop_slice = html[html.index('id="dockLyricOffsetPopover"'):html.index('id="dockVolumeWrap"')]
        self.assertIn('id="dockTransBtn"', pop_slice)
        self.assertIn('id="dockLyricOffsetVal"', pop_slice)

        for token in ("is-fullscreen-immersive", "is-hud-hidden", "dock-trans-row", "dock-mini-switch"):
            self.assertIn(token, css)
        for token in ("function toggleFullscreenImmersive(", "function scheduleFullscreenHudHide(", 'e.key === "F11"'):
            self.assertIn(token, js)


if __name__ == "__main__":
    unittest.main()
