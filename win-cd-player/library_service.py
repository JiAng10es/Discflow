# -*- coding: utf-8 -*-
"""
library_service.py
Discflow 离线黑胶资料库与零损 CD-DA 抓轨归档系统：
1. 抓轨引擎：后台多倍速逐扇区抓取 CD-DA 原始 PCM，直接编码为高保真 WAV
2. 持久化存储：自动保存专辑元数据、高清封面与同步双语歌词
3. 资料库管理：极速扫描与离线唱片加载，免光驱直接回放
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
import ctypes
from ctypes import wintypes

from cd_engine import (
    CD_FRAMES_PER_SECOND,
    CD_SECTOR_RAW_BYTES,
    CDDA_TRACK_MODE,
    CdDiscToc,
    CdTrackInfo,
    IOCTL_CDROM_RAW_READ,
    RAW_READ_INFO,
    WindowsCdDriveManager,
    kernel32,
)

_TEST_SANDBOX_ROOT: Optional[str] = None


def is_under_test_runner() -> bool:
    """
    检测当前进程是否由单元测试运行器（unittest / pytest）启动。
    用于把资源库强制重定向到临时沙箱，从根上杜绝测试误删真实用户资料库。
    如确需在测试中访问真实资料库，可显式设置环境变量 DISCFLOW_ALLOW_REAL_LIBRARY=1。
    """
    if os.environ.get("DISCFLOW_ALLOW_REAL_LIBRARY") == "1":
        return False
    try:
        if "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        if "unittest" in sys.modules:
            argv_probe = " ".join(sys.argv[:3]).lower()
            if "unittest" in argv_probe or "discover" in argv_probe:
                return True
    except Exception:
        pass
    return False


def _test_sandbox_root() -> str:
    """返回本进程专属的测试沙箱根目录（进程内复用，保证资源库与偏好文件路径一致）"""
    global _TEST_SANDBOX_ROOT
    if _TEST_SANDBOX_ROOT is None:
        _TEST_SANDBOX_ROOT = tempfile.mkdtemp(prefix="discflow_test_sandbox_")
    return _TEST_SANDBOX_ROOT


def _candidate_app_roots() -> List[tuple]:
    """
    收集可能的「程序根目录」候选，按可靠性从高到低返回 (目录, 是否强制采用) 二元组：
    1. 冻结打包后的 Discflow.exe 所在目录（自身目录无条件视为便携根目录）；
    2. 本模块所在目录的父目录（源码工程根，如 <root>/win-cd-player/library_service.py -> <root>）；
    3. 本模块所在目录（单目录部署场景）；
    4. 源码入口文件所在目录及其父目录；
    5. 当前工作目录（兜底）。
    以 `__file__` 为最高优先级的源码锚点，避免 cwd 漂移导致资源库落到 win-cd-player 子目录。
    """
    import sys

    roots: List[tuple] = []

    if getattr(sys, "frozen", False):
        try:
            roots.append((os.path.dirname(os.path.abspath(sys.executable)), True))
        except Exception:
            pass

    here = os.path.dirname(os.path.abspath(__file__))
    roots.append((os.path.dirname(here), False))
    roots.append((here, False))

    try:
        entry = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    except Exception:
        entry = ""
    if entry and os.path.isfile(entry):
        entry_dir = os.path.dirname(entry)
        roots.append((entry_dir, False))
        roots.append((os.path.dirname(entry_dir), False))

    try:
        roots.append((os.getcwd(), False))
    except Exception:
        pass

    return roots


def _looks_like_system_dir(path: str) -> bool:
    """判断目录是否落在 Python 安装目录或系统目录内，禁止在这些位置创建资源库"""
    try:
        target = os.path.abspath(path).lower().rstrip("\\/")
        prefixes = []
        for base in (getattr(__import__("sys"), "base_prefix", ""), getattr(__import__("sys"), "prefix", "")):
            if base:
                prefixes.append(os.path.abspath(base).lower().rstrip("\\/"))
        for p in prefixes:
            if p and (target == p or target.startswith(p + os.sep)):
                return True
        if target.startswith("c:\\windows"):
            return True
    except Exception:
        pass
    return False


def _is_portable_root(path: str) -> bool:
    """
    判定目录是否可作为便携模式的程序根目录。
    必须带有明确的便携标识，避免在 Python 标准库目录、系统目录中误建 library 文件夹。
    """
    if not path or not os.path.isdir(path):
        return False
    if _looks_like_system_dir(path):
        return False
    if "discflow" in os.path.basename(path).lower():
        return True
    if os.path.isfile(os.path.join(path, "Discflow.exe")):
        return True
    if os.path.isfile(os.path.join(path, "preferences.json")):
        return True
    if os.path.isdir(os.path.join(path, "library")):
        return True
    # 源码工程根目录标识：包含 win-cd-player 源码子目录
    if os.path.isdir(os.path.join(path, "win-cd-player")):
        return True
    return False


def resolve_library_dir() -> str:
    """
    智能解析 Discflow 资源库物理存储目录（v1.10 便携优先策略）：
    0. 环境变量 DISCFLOW_LIBRARY 显式指定时直接采用（便于开发/测试隔离）；
    1. 便携优先：程序根目录下的 library/（F 盘开发目录、打包 exe 同级目录等）；
    2. 兜底：E:\\Discflow\\library（仅当便携目录完全不可用时才回退）；
    3. 最终兜底：~/.discflow/library。
    正常便携运行时绝不会写入 E 盘。
    """
    # 0. 环境变量强制指定
    env_dir = (os.environ.get("DISCFLOW_LIBRARY") or "").strip()
    if env_dir:
        try:
            os.makedirs(env_dir, exist_ok=True)
            return env_dir
        except Exception:
            pass

    # 0.5 测试运行器强制沙箱隔离：任何单元测试都不得触碰真实用户资料库
    if is_under_test_runner():
        sandbox_lib = os.path.join(_test_sandbox_root(), "library")
        try:
            os.makedirs(sandbox_lib, exist_ok=True)
            return sandbox_lib
        except Exception:
            pass

    # 1. 便携优先：程序根目录
    seen = set()
    for root, forced in _candidate_app_roots():
        try:
            key = os.path.abspath(root).lower()
        except Exception:
            continue
        if not key or key in seen:
            continue
        seen.add(key)
        if not forced and not _is_portable_root(root):
            continue
        target = os.path.join(root, "library")
        try:
            os.makedirs(target, exist_ok=True)
            _auto_migrate_legacy_library(target)
            return target
        except Exception:
            continue

    # 2. 兜底：E:\\Discflow\\library
    try:
        if os.path.isdir(r"E:\\Discflow") or os.path.exists("E:\\"):
            e_target = r"E:\\Discflow\\library"
            os.makedirs(e_target, exist_ok=True)
            _auto_migrate_legacy_library(e_target)
            return e_target
    except Exception:
        pass

    # 3. 最终兜底
    fallback = os.path.join(os.path.expanduser("~"), ".discflow", "library")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _auto_migrate_legacy_library(target_dir: str):
    """自动将之前保存在 C 盘 ~/.discflow/library 的专辑无缝迁移至目标目录，确保数据不丢失"""
    try:
        legacy_dir = os.path.join(os.path.expanduser("~"), ".discflow", "library")
        if os.path.abspath(legacy_dir).lower() == os.path.abspath(target_dir).lower():
            return
        if not os.path.isdir(legacy_dir):
            return
        for item in os.listdir(legacy_dir):
            src_item = os.path.join(legacy_dir, item)
            dst_item = os.path.join(target_dir, item)
            if not os.path.exists(dst_item):
                if os.path.isdir(src_item):
                    shutil.copytree(src_item, dst_item)
                elif os.path.isfile(src_item):
                    shutil.copy2(src_item, dst_item)
    except Exception:
        pass


LIBRARY_DIR = resolve_library_dir()


def get_preferences_file_path() -> str:
    """获取用户自定义偏好设置配置文件的持久化存储路径（与资源库同级，如 F:\\Discflow1.0\\preferences.json）"""
    lib_dir = resolve_library_dir()
    parent_dir = os.path.dirname(lib_dir)
    return os.path.join(parent_dir, "preferences.json")


def load_user_preferences() -> dict:
    """从本地磁盘加载用户偏好设置，若不存在则返回标准默认配置"""
    defaults = {
        "lyricLineHeight": 1.4,
        "lyricLetterSpacing": 0,
        "dockWidth": 720,
        "dockOpacity": 75,
        "dockBlur": 40,
        "themeMode": "dark",
        "volume": 80,
        "playMode": "loop",
        "showTranslation": True,
        "autoPlay": True,
        "lastSession": {},
        "lyricOffsets": {},
    }
    path = get_preferences_file_path()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    defaults.update(data)
        except Exception:
            pass
    return defaults


def save_user_preferences(prefs: dict) -> bool:
    """将用户偏好设置安全写入物理磁盘配置文件"""
    if not isinstance(prefs, dict):
        return False
    path = get_preferences_file_path()
    try:
        current = load_user_preferences()
        current.update(prefs)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False



@dataclass
class LocalTrackInfo:
    track_number: int
    title: str
    artist: str
    duration_sec: float
    file_name: str
    lyrics: List[dict] = field(default_factory=list)
    # 歌词来源与锁定状态：手动指定的歌词会被锁定，自动预取不再覆盖
    lyrics_source: str = ""
    lyrics_song_id: str = ""
    lyrics_locked: bool = False
    lyrics_matched_at: float = 0.0
    lyrics_offset: float = 0.0


@dataclass
class LocalAlbumInfo:
    disc_id: str
    title: str
    artist: str
    year: str = ""
    cover_file: str = ""
    cover_data_url: str = ""
    tracks: List[LocalTrackInfo] = field(default_factory=list)
    ripped_at: float = 0.0
    total_duration_sec: float = 0.0


@dataclass
class RippingProgress:
    is_ripping: bool = False
    current_track_index: int = 0
    total_tracks: int = 0
    track_title: str = ""
    track_progress_pct: float = 0.0
    overall_progress_pct: float = 0.0
    status_text: str = ""
    error_message: str = ""
    is_completed: bool = False


class DiscflowLibraryManager:
    """本地黑胶唱片资料库管理服务"""

    def __init__(self, base_dir: str = LIBRARY_DIR):
        self.base_dir = base_dir
        self._ensure_dir()
        self._cached_albums: Optional[List[LocalAlbumInfo]] = None
        self._lock = threading.Lock()

    def _ensure_dir(self):
        try:
            os.makedirs(self.base_dir, exist_ok=True)
        except Exception:
            pass

    def get_album_dir(self, disc_id: str) -> str:
        safe_id = "".join(c for c in disc_id if c.isalnum() or c in ("-", "_"))
        return os.path.join(self.base_dir, safe_id)

    def list_albums(self, force_refresh: bool = False) -> List[LocalAlbumInfo]:
        with self._lock:
            if not force_refresh and self._cached_albums is not None:
                return list(self._cached_albums)

            self._ensure_dir()
            albums: List[LocalAlbumInfo] = []

            try:
                for entry in os.listdir(self.base_dir):
                    sub_dir = os.path.join(self.base_dir, entry)
                    if not os.path.isdir(sub_dir):
                        continue
                    meta_path = os.path.join(sub_dir, "album.json")
                    if not os.path.isfile(meta_path):
                        continue

                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            data = json.load(f)

                        tracks_data = [LocalTrackInfo(**t) for t in data.get("tracks", [])]
                        cover_data_url = data.get("cover_data_url", "")

                        # 如果未存 Base64，但有 cover.jpg / cover.png 文件，则即时生成
                        cover_file = data.get("cover_file", "")
                        if not cover_data_url and cover_file:
                            full_cover = os.path.join(sub_dir, cover_file)
                            if os.path.isfile(full_cover):
                                try:
                                    with open(full_cover, "rb") as cf:
                                        raw_b = cf.read()
                                        mime = "image/png" if cover_file.lower().endswith(".png") else "image/jpeg"
                                        b64 = base64.b64encode(raw_b).decode("ascii")
                                        cover_data_url = f"data:{mime};base64,{b64}"
                                except Exception:
                                    pass

                        alb = LocalAlbumInfo(
                            disc_id=data.get("disc_id", entry),
                            title=data.get("title", "Unknown Album"),
                            artist=data.get("artist", "Unknown Artist"),
                            year=data.get("year", ""),
                            cover_file=cover_file,
                            cover_data_url=cover_data_url,
                            tracks=tracks_data,
                            ripped_at=data.get("ripped_at", 0.0),
                            total_duration_sec=sum(t.duration_sec for t in tracks_data),
                        )
                        albums.append(alb)
                    except Exception:
                        pass
            except Exception:
                pass

            albums.sort(key=lambda a: a.ripped_at, reverse=True)
            self._cached_albums = albums
            return list(albums)

    def delete_album(self, disc_id: str) -> bool:
        return bool(self.delete_album_verbose(disc_id).get("ok"))

    def _force_remove_tree(self, path: str, retries: int = 4) -> Tuple[bool, List[str]]:
        """
        强制删除目录树：逐文件重试，兼容 Windows 上音频文件句柄延迟释放导致 rmtree 半途失败的情况。
        返回 (是否删除干净, 仍无法删除的路径列表)。
        """
        if not os.path.isdir(path):
            return True, []

        failed: List[str] = []
        for attempt in range(retries):
            failed = []

            def _on_remove_error(func, fpath, exc_info=None):
                failed.append(fpath)

            try:
                # Python 3.12+ 使用 onexc，旧版本回退 onerror
                try:
                    shutil.rmtree(path, onexc=_on_remove_error)  # type: ignore[call-arg]
                except TypeError:
                    shutil.rmtree(path, onerror=_on_remove_error)  # type: ignore[call-arg]
            except Exception:
                pass

            if not os.path.isdir(path):
                return True, []
            # 句柄释放存在毫秒级延迟，逐轮退避重试
            time.sleep(0.15 * (attempt + 1))

        return (not os.path.isdir(path)), failed

    def delete_album_verbose(self, disc_id: str) -> dict:
        """
        物理删除整张专辑目录（含 WAV / 封面 / 元数据），并返回可反馈给 UI 的详细结果：
        {"ok": bool, "existed": bool, "path": str, "failed": [残留文件...]}
        """
        with self._lock:
            sub_dir = self.get_album_dir(disc_id)
            existed = os.path.isdir(sub_dir)
            ok, failed = self._force_remove_tree(sub_dir)
            self._cached_albums = None
            return {"ok": ok, "existed": existed, "path": sub_dir, "failed": failed}

    def update_album_metadata(
        self,
        disc_id: str,
        title: Optional[str] = None,
        artist: Optional[str] = None,
        year: Optional[str] = None,
        track_titles: Optional[Dict[int, str]] = None,
        track_artists: Optional[Dict[int, str]] = None,
    ) -> bool:
        with self._lock:
            sub_dir = self.get_album_dir(disc_id)
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if title is not None:
                    data["title"] = title
                if artist is not None:
                    data["artist"] = artist
                if year is not None:
                    data["year"] = year
                if track_titles and "tracks" in data:
                    for t in data["tracks"]:
                        t_num = t.get("track_number")
                        if t_num in track_titles:
                            t["title"] = track_titles[t_num]
                # 专辑艺术家变更时同步曲目艺术家，避免「专辑作者换了、曲目作者还是旧的」
                if track_artists and "tracks" in data:
                    for t in data["tracks"]:
                        t_num = t.get("track_number")
                        if t_num in track_artists:
                            t["artist"] = track_artists[t_num]
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def update_album_cover(self, disc_id: str, cover_data_url: str) -> bool:
        with self._lock:
            sub_dir = self.get_album_dir(disc_id)
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
            try:
                # 若包含 base64，提取并写入 cover.jpg
                if "," in cover_data_url:
                    _, b64data = cover_data_url.split(",", 1)
                    raw_bytes = base64.b64decode(b64data)
                    cover_jpg_path = os.path.join(sub_dir, "cover.jpg")
                    with open(cover_jpg_path, "wb") as f_cov:
                        f_cov.write(raw_bytes)

                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["cover_file"] = "cover.jpg"
                data["cover_data_url"] = cover_data_url
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def update_track_lyrics(
        self,
        disc_id: str,
        track_number: int,
        lyrics: List[dict],
        source: str = "",
        song_id: str = "",
        locked: bool = False,
        clear_lock: bool = False,
    ) -> bool:
        """
        把歌词写回 album.json（原子替换，避免半截文件）。
        - 自动重抓时只更新 lyrics；
        - 手动指定时同时写入 source / song_id / locked，锁定后自动流程不再覆盖。
        """
        with self._lock:
            sub_dir = self.get_album_dir(disc_id)
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
            tmp_path = meta_path + ".tmp"
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                hit = False
                for trk in data.get("tracks", []):
                    if int(trk.get("track_number", -1)) == int(track_number):
                        trk["lyrics"] = lyrics
                        if clear_lock:
                            trk["lyrics_source"] = ""
                            trk["lyrics_song_id"] = ""
                            trk["lyrics_locked"] = False
                            trk["lyrics_matched_at"] = 0.0
                        elif source or song_id or locked:
                            trk["lyrics_source"] = source
                            trk["lyrics_song_id"] = song_id
                            trk["lyrics_locked"] = bool(locked)
                            trk["lyrics_matched_at"] = time.time()
                        hit = True
                        break
                if not hit:
                    return False
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, meta_path)
                self._cached_albums = None
                return True
            except Exception:
                try:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return False

    def update_track_song_id(self, disc_id: str, track_number: int, source: str, song_id: str) -> bool:
        """只记录该曲目对应的歌曲 ID（不写歌词、不锁定），供后续按 ID 直取歌词"""
        with self._lock:
            sub_dir = self.get_album_dir(disc_id)
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
            tmp_path = meta_path + ".tmp"
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                hit = False
                for trk in data.get("tracks", []):
                    if int(trk.get("track_number", -1)) == int(track_number):
                        trk["lyrics_source"] = source
                        trk["lyrics_song_id"] = song_id
                        hit = True
                        break
                if not hit:
                    return False
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, meta_path)
                self._cached_albums = None
                return True
            except Exception:
                try:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return False

    def update_track_lyric_offset(self, disc_id: str, track_number: int, offset: float) -> bool:
        """持久化保存单曲歌词时间轴微调偏移量 (秒) 到 album.json"""
        with self._lock:
            sub_dir = self.get_album_dir(disc_id)
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
            tmp_path = meta_path + ".tmp"
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                hit = False
                for trk in data.get("tracks", []):
                    if int(trk.get("track_number", -1)) == int(track_number):
                        trk["lyrics_offset"] = round(float(offset), 2)
                        hit = True
                        break
                if not hit:
                    return False
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp_path, meta_path)
                self._cached_albums = None
                return True
            except Exception:
                try:
                    if os.path.isfile(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                return False

    def export_library_zip(self, target_zip_path: str) -> bool:
        import zipfile
        with self._lock:
            try:
                with zipfile.ZipFile(target_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(self.base_dir):
                        for file in files:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, self.base_dir)
                            zf.write(full_path, rel_path)
                return True
            except Exception:
                return False

    def import_library_zip(self, source_zip_path: str) -> bool:
        import zipfile
        with self._lock:
            if not os.path.isfile(source_zip_path):
                return False
            try:
                with zipfile.ZipFile(source_zip_path, "r") as zf:
                    zf.extractall(self.base_dir)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def get_album_toc(self, disc_id: str) -> Optional[CdDiscToc]:
        albums = self.list_albums()
        target: Optional[LocalAlbumInfo] = None
        safe_id = "".join(c for c in disc_id if c.isalnum() or c in ("-", "_"))

        for a in albums:
            a_safe = "".join(c for c in a.disc_id if c.isalnum() or c in ("-", "_"))
            if a.disc_id == disc_id or a_safe == safe_id or a.disc_id == safe_id:
                target = a
                break
        if not target or not target.tracks:
            return None

        # 优先探测实际存在的磁盘目录
        possible_dirs = [
            self.get_album_dir(target.disc_id),
            self.get_album_dir(disc_id),
            os.path.join(self.base_dir, target.disc_id),
            os.path.join(self.base_dir, disc_id),
        ]
        album_dir = ""
        for d in possible_dirs:
            if os.path.isdir(d):
                album_dir = d
                break
        if not album_dir:
            album_dir = self.get_album_dir(disc_id)

        tracks: List[CdTrackInfo] = []
        for t in target.tracks:
            full_path = os.path.join(album_dir, t.file_name)
            trk = CdTrackInfo(
                track_number=t.track_number,
                start_lba=0,
                end_lba=max(1, int(t.duration_sec * CD_FRAMES_PER_SECOND)),
                start_frame=0,
                duration_sec=t.duration_sec,
                is_data_track=False,
                title=t.title,
                artist=t.artist,
                local_filepath=full_path,
                lyrics_offset=float(getattr(t, "lyrics_offset", 0.0)),
            )
            tracks.append(trk)

        return CdDiscToc(
            drive_letter="LIB:" + target.disc_id[:8],
            first_track=1,
            last_track=len(tracks),
            leadout_frame=sum(int(t.duration_sec * CD_FRAMES_PER_SECOND) for t in tracks),
            tracks=tracks,
            disc_id=target.disc_id,
            freedb_id="",
            toc_query_string="",
            cdtext_album=target.title,
            cdtext_artist=target.artist,
            is_simulated=False,
            is_local=True,
        )


class CdRipperTask:
    """零损 CD-DA 原始 PCM 扇区抓轨并编码为标准 WAV 的后台任务"""

    def __init__(
        self,
        toc: CdDiscToc,
        album_title: str,
        artist: str,
        year: str,
        cover_data_url: str,
        lyrics_cache: Dict[int, List[dict]],
        library_mgr: DiscflowLibraryManager,
        on_progress: Optional[Callable[[RippingProgress], None]] = None,
    ):
        self.toc = toc
        self.album_title = album_title or "Audio CD"
        self.artist = artist or "Unknown Artist"
        self.year = year or ""
        self.cover_data_url = cover_data_url or ""
        self.lyrics_cache = lyrics_cache or {}
        self.library_mgr = library_mgr
        self.on_progress = on_progress

        self._cancelled = False
        self._progress = RippingProgress()

    def cancel(self):
        self._cancelled = True

    def get_progress(self) -> RippingProgress:
        return self._progress

    def run(self):
        self._progress.is_ripping = True
        self._progress.total_tracks = len(self.toc.tracks)
        self._progress.status_text = "正在初始化无损抓轨引擎..."
        if self.on_progress:
            self.on_progress(self._progress)

        target_dir = self.library_mgr.get_album_dir(self.toc.disc_id)
        os.makedirs(target_dir, exist_ok=True)

        # 保存高清封面图
        cover_file = ""
        if self.cover_data_url:
            try:
                if "," in self.cover_data_url:
                    header, b64_data = self.cover_data_url.split(",", 1)
                    raw_img = base64.b64decode(b64_data)
                    ext = "png" if "png" in header else "jpg"
                    cover_file = f"cover.{ext}"
                    with open(os.path.join(target_dir, cover_file), "wb") as f_cov:
                        f_cov.write(raw_img)
            except Exception:
                pass

        h_drive = WindowsCdDriveManager.open_drive_handle(self.toc.drive_letter, read_access=True)
        if h_drive is None:
            self._progress.is_ripping = False
            self._progress.error_message = f"无法以读取权限打开光驱 {self.toc.drive_letter}"
            self._progress.status_text = "抓轨失败：无法访问光驱硬件"
            if self.on_progress:
                self.on_progress(self._progress)
            return

        # 自适应批次读取（v1.11 提速）：
        # 优先以 64 扇区（约 147KB）大批量传输，显著减少 DeviceIoControl 往返次数；
        # 若光驱/驱动拒绝大批量，自动逐级降档 48 → 32 → 27 → 16 扇区（16 扇区 = 37,632 字节，严守 64KB USB 安全线）；
        # 最小批量仍失败时才降级为单扇区容错重读。
        BATCH_CANDIDATES = (64, 48, 32, 27, 16)
        sectors_per_batch = BATCH_CANDIDATES[0]
        raw_buffer = ctypes.create_string_buffer(CD_SECTOR_RAW_BYTES * BATCH_CANDIDATES[0])
        bytes_returned = wintypes.DWORD(0)
        raw_info = RAW_READ_INFO()
        PROGRESS_MIN_INTERVAL = 0.12  # 进度回调节流间隔（秒），避免每批次跨线程唤醒 UI

        saved_tracks: List[LocalTrackInfo] = []
        total_audio_sectors = sum(t.end_lba - t.start_lba for t in self.toc.tracks if not t.is_data_track)
        sectors_ripped_so_far = 0
        consecutive_hardware_errors = 0

        try:
            for idx, trk in enumerate(self.toc.tracks):
                if self._cancelled:
                    self._progress.status_text = "抓轨任务已由用户取消"
                    break

                self._progress.current_track_index = idx + 1
                self._progress.track_title = trk.title or f"Track {trk.track_number:02d}"
                self._progress.status_text = f"正在抓取 ({idx + 1}/{len(self.toc.tracks)}): {self._progress.track_title}"
                if self.on_progress:
                    self.on_progress(self._progress)

                wav_filename = f"track_{trk.track_number:02d}.wav"
                wav_path = os.path.join(target_dir, wav_filename)

                cur_lba = trk.start_lba
                end_lba = trk.end_lba
                track_total_sectors = max(1, end_lba - cur_lba)

                with wave.open(wav_path, "wb") as wf:
                    wf.setnchannels(2)
                    wf.setsampwidth(2)
                    wf.setframerate(44100)

                    last_progress_ts = 0.0
                    while cur_lba < end_lba:
                        if self._cancelled:
                            break

                        batch_count = min(sectors_per_batch, end_lba - cur_lba)
                        required_bytes = batch_count * CD_SECTOR_RAW_BYTES

                        raw_info.DiskOffset = cur_lba * 2048
                        raw_info.SectorCount = batch_count
                        raw_info.TrackMode = CDDA_TRACK_MODE

                        ok = kernel32.DeviceIoControl(
                            h_drive,
                            IOCTL_CDROM_RAW_READ,
                            ctypes.byref(raw_info),
                            ctypes.sizeof(raw_info),
                            raw_buffer,
                            required_bytes,
                            ctypes.byref(bytes_returned),
                            None,
                        )

                        if ok and bytes_returned.value >= required_bytes:
                            consecutive_hardware_errors = 0
                            pcm_data = raw_buffer.raw[: required_bytes]
                        else:
                            # 大批量被光驱固件/驱动拒绝时，自动降档并在同一 LBA 重试（仅整批失败才降档）
                            lower_candidates = [c for c in BATCH_CANDIDATES if c < sectors_per_batch]
                            if lower_candidates and batch_count >= sectors_per_batch:
                                sectors_per_batch = lower_candidates[0]
                                continue

                            # 已降到最小批量仍失败时，降级为单扇区逐一读取尝试，兼容老旧光盘或深痕介质
                            single_buf = ctypes.create_string_buffer(CD_SECTOR_RAW_BYTES)
                            single_ret = wintypes.DWORD(0)
                            fallback_pcm = bytearray()
                            for sub_lba in range(cur_lba, cur_lba + batch_count):
                                single_info = RAW_READ_INFO()
                                single_info.DiskOffset = sub_lba * 2048
                                single_info.SectorCount = 1
                                single_info.TrackMode = CDDA_TRACK_MODE
                                single_ok = kernel32.DeviceIoControl(
                                    h_drive,
                                    IOCTL_CDROM_RAW_READ,
                                    ctypes.byref(single_info),
                                    ctypes.sizeof(single_info),
                                    single_buf,
                                    CD_SECTOR_RAW_BYTES,
                                    ctypes.byref(single_ret),
                                    None,
                                )
                                if single_ok and single_ret.value >= CD_SECTOR_RAW_BYTES:
                                    consecutive_hardware_errors = 0
                                    fallback_pcm.extend(single_buf.raw[:CD_SECTOR_RAW_BYTES])
                                else:
                                    consecutive_hardware_errors += 1
                                    if consecutive_hardware_errors >= 80:
                                        raise RuntimeError(f"光驱扇区持续读取失败 (错误码 {kernel32.GetLastError()})，请检查光驱或光盘")
                                    fallback_pcm.extend(b"\x00" * CD_SECTOR_RAW_BYTES)
                            pcm_data = bytes(fallback_pcm)

                        wf.writeframes(pcm_data)
                        cur_lba += batch_count
                        sectors_ripped_so_far += batch_count

                        track_pct = round(((cur_lba - trk.start_lba) / track_total_sectors) * 100, 1)
                        overall_pct = round((sectors_ripped_so_far / max(1, total_audio_sectors)) * 100, 1)
                        self._progress.track_progress_pct = min(100.0, track_pct)
                        self._progress.overall_progress_pct = min(100.0, overall_pct)
                        # 进度回调节流：最多每 120ms 唤醒一次 UI，避免大批量传输时被跨线程刷新拖慢
                        now_ts = time.time()
                        if self.on_progress and (
                            now_ts - last_progress_ts >= PROGRESS_MIN_INTERVAL or cur_lba >= end_lba
                        ):
                            last_progress_ts = now_ts
                            self.on_progress(self._progress)

                lyrics_data = self.lyrics_cache.get(trk.track_number, [])
                saved_tracks.append(
                    LocalTrackInfo(
                        track_number=trk.track_number,
                        title=trk.title or f"Track {trk.track_number:02d}",
                        artist=trk.artist or self.artist,
                        duration_sec=trk.duration_sec,
                        file_name=wav_filename,
                        lyrics=lyrics_data,
                    )
                )

            if not self._cancelled:
                # 写入 album.json 元数据文件
                meta_payload = {
                    "disc_id": self.toc.disc_id,
                    "title": self.album_title,
                    "artist": self.artist,
                    "year": self.year,
                    "cover_file": cover_file,
                    "cover_data_url": self.cover_data_url,
                    "ripped_at": time.time(),
                    "tracks": [asdict(t) for t in saved_tracks],
                }
                meta_json_path = os.path.join(target_dir, "album.json")
                with open(meta_json_path, "w", encoding="utf-8") as f_meta:
                    json.dump(meta_payload, f_meta, ensure_ascii=False, indent=2)

                self.library_mgr.list_albums(force_refresh=True)

                self._progress.is_ripping = False
                self._progress.is_completed = True
                self._progress.overall_progress_pct = 100.0
                self._progress.status_text = f"专辑《{self.album_title}》已成功归档至黑胶资料库！"
                if self.on_progress:
                    self.on_progress(self._progress)
        except Exception as e:
            self._progress.is_ripping = False
            self._progress.error_message = str(e)
            self._progress.status_text = f"抓轨失败: {e}"
            if self.on_progress:
                self.on_progress(self._progress)
        finally:
            kernel32.CloseHandle(h_drive)
            if self._cancelled:
                self._progress.is_ripping = False
                self._progress.status_text = "抓轨任务已取消"
                if self.on_progress:
                    self.on_progress(self._progress)
