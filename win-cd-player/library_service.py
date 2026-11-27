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
import logging
import logging.handlers
import os
import shutil
import sys
import tempfile
import threading
import time
import wave
from dataclasses import asdict, dataclass, field, fields
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
    """自动将之前保存在 C 盘 ~/.discflow/library 的专辑无缝迁移至目标目录（仅首次执行一次，避免已删专辑复活）"""
    try:
        legacy_dir = os.path.join(os.path.expanduser("~"), ".discflow", "library")
        if os.path.abspath(legacy_dir).lower() == os.path.abspath(target_dir).lower():
            return
        if not os.path.isdir(legacy_dir):
            return
        sentinel = os.path.join(target_dir, ".legacy_migrated")
        if os.path.isfile(sentinel):
            return
        for item in os.listdir(legacy_dir):
            if item.startswith("."):
                continue
            src_item = os.path.join(legacy_dir, item)
            dst_item = os.path.join(target_dir, item)
            if not os.path.exists(dst_item):
                if os.path.isdir(src_item):
                    shutil.copytree(src_item, dst_item)
                elif os.path.isfile(src_item):
                    shutil.copy2(src_item, dst_item)
        try:
            with open(sentinel, "w", encoding="utf-8") as sf:
                sf.write("migrated\n")
        except Exception:
            pass
    except Exception:
        pass


LIBRARY_DIR = resolve_library_dir()


def get_preferences_file_path() -> str:
    """获取用户自定义偏好设置配置文件的持久化存储路径（与资源库同级，如 F:\\Discflow1.0\\preferences.json）"""
    lib_dir = resolve_library_dir()
    parent_dir = os.path.dirname(lib_dir)
    return os.path.join(parent_dir, "preferences.json")


_PREFS_LOCK = threading.Lock()
_RUNTIME_LOG_LOCK = threading.Lock()
_RUNTIME_LOG_CACHE: Dict[str, logging.Logger] = {}


def _atomic_write_json(path: str, payload) -> None:
    """原子写入 JSON 文件（同目录临时文件 + os.replace），防止并发或断电写半截损坏。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
        os.replace(tmp_path, path)
    finally:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _is_within_dir(base_dir: str, target_path: str) -> bool:
    try:
        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(target_path)
        return os.path.commonpath([abs_base, abs_target]) == abs_base
    except Exception:
        return False


def get_runtime_log_path() -> str:
    """获取轻量级本地运行诊断日志路径（与 preferences.json 同级，测试模式下自动隔离在沙盒目录）"""
    pref_path = get_preferences_file_path()
    return os.path.join(os.path.dirname(pref_path), "discflow.log")


def log_runtime_event(category: str, message: str, level: str = "INFO") -> None:
    """写入单行本地运行诊断日志（单文件上限 512KB 自动轮转，仅记录光驱/抓轨/更新等关键节点）"""
    try:
        log_path = get_runtime_log_path()
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        with _RUNTIME_LOG_LOCK:
            logger = _RUNTIME_LOG_CACHE.get(log_path)
            if logger is None:
                logger = logging.getLogger(f"discflow.runtime.{abs(hash(log_path))}")
                logger.setLevel(logging.INFO)
                logger.propagate = False
                for existing in list(logger.handlers):
                    logger.removeHandler(existing)
                    try:
                        existing.close()
                    except Exception:
                        pass
                handler = logging.handlers.RotatingFileHandler(
                    log_path,
                    maxBytes=512 * 1024,
                    backupCount=1,
                    encoding="utf-8",
                )
                handler.setFormatter(
                    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
                )
                logger.addHandler(handler)
                _RUNTIME_LOG_CACHE[log_path] = logger

            lvl_name = str(level or "INFO").upper()
            log_fn = getattr(logger, lvl_name.lower(), logger.info)
            clean_msg = str(message or "").replace("\r", " ").replace("\n", " ").strip()
            log_fn("[%s] %s", str(category or "APP"), clean_msg)
    except Exception:
        pass


def _default_user_preferences() -> dict:
    return {
        "lyricLineHeight": 1.4,
        "lyricLetterSpacing": 0,
        "dockWidth": 720,
        "dockOpacity": 5,
        "dockBlur": 2,
        "topbarOpacity": 5,
        "topbarBlur": 2,
        "themeMode": "dark",
        "volume": 80,
        "playMode": "loop",
        "showTranslation": True,
        "autoPlay": True,
        "windowBounds": {},
        "ignoredUpdateVersion": "",
        "lastSession": {},
        "lyricOffsets": {},
        "ripFormat": "flac",
        "audioOutputMode": "waveout",
    }


def _read_user_preferences_unlocked(path: str) -> dict:
    defaults = _default_user_preferences()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    defaults.update(data)
        except Exception:
            pass
    defaults.pop("closeToTray", None)
    return defaults


def load_user_preferences() -> dict:
    """从本地磁盘加载用户偏好设置，若不存在则返回标准默认配置"""
    path = get_preferences_file_path()
    with _PREFS_LOCK:
        return _read_user_preferences_unlocked(path)


def save_user_preferences(prefs: dict) -> bool:
    """将用户偏好设置加锁并原子写入物理磁盘配置文件"""
    if not isinstance(prefs, dict):
        return False
    path = get_preferences_file_path()
    with _PREFS_LOCK:
        try:
            current = _read_user_preferences_unlocked(path)
            current.update(prefs)
            current.pop("closeToTray", None)
            _atomic_write_json(path, current)
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


COVER_THUMB_FILENAME = "cover_thumb.jpg"


def generate_cover_thumbnail(
    raw_bytes: bytes,
    thumb_path: Optional[str] = None,
    max_size: int = 320,
    quality: int = 82,
) -> str:
    """将高清封面字节流等比缩放为轻量 JPEG 缩略图，可选落盘并返回 data URL。"""
    if not raw_bytes:
        return ""
    try:
        import io
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (24, 24, 26))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.BICUBIC)
            img.thumbnail((max_size, max_size), resample)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            thumb_bytes = buf.getvalue()

        if thumb_path:
            tmp_thumb = f"{thumb_path}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                with open(tmp_thumb, "wb") as tf:
                    tf.write(thumb_bytes)
                    tf.flush()
                os.replace(tmp_thumb, thumb_path)
            except Exception:
                try:
                    if os.path.isfile(tmp_thumb):
                        os.remove(tmp_thumb)
                except OSError:
                    pass
        return f"data:image/jpeg;base64,{base64.b64encode(thumb_bytes).decode('ascii')}"
    except Exception:
        return ""


@dataclass
class LocalAlbumInfo:
    disc_id: str
    title: str
    artist: str
    year: str = ""
    cover_file: str = ""
    cover_data_url: str = ""
    thumb_data_url: str = ""
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


_WIN_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_LOCAL_TRACK_FIELDS = frozenset(f.name for f in fields(LocalTrackInfo))


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
        raw_id = str(disc_id or "").strip()
        safe_id = "".join(c for c in raw_id if c.isalnum() or c in ("-", "_")).strip(".")
        if not safe_id or safe_id in (".", "..") or safe_id.upper() in _WIN_RESERVED_NAMES:
            raise ValueError(f"非法的唱片标识: {disc_id!r}")
        return os.path.join(self.base_dir, safe_id)

    def _resolve_existing_album_dir(self, disc_id: str) -> str:
        """定位已存在的专辑目录（兼容 DiscID 含 '.' 的旧版目录名），并确保路径严格落在 base_dir 内。"""
        raw_id = str(disc_id or "").strip()
        if not raw_id or raw_id in (".", ".."):
            return ""
        abs_base = os.path.abspath(self.base_dir)
        candidates: List[str] = []
        try:
            candidates.append(self.get_album_dir(raw_id))
        except ValueError:
            pass
        if (
            "/" not in raw_id
            and "\\" not in raw_id
            and ".." not in raw_id
            and raw_id.strip(".").upper() not in _WIN_RESERVED_NAMES
        ):
            candidates.append(os.path.join(self.base_dir, raw_id))

        for cand in candidates:
            abs_c = os.path.abspath(cand)
            if abs_c != abs_base and _is_within_dir(abs_base, abs_c) and os.path.isdir(cand):
                return cand

        # 兜底扫描目录内 album.json 精确匹配 disc_id（含旧版含点目录）
        try:
            safe_raw = "".join(c for c in raw_id if c.isalnum() or c in ("-", "_"))
            for entry in os.listdir(self.base_dir):
                if ".rip_staging_" in entry or ".rip_backup_" in entry:
                    continue
                sub = os.path.join(self.base_dir, entry)
                if not os.path.isdir(sub):
                    continue
                meta_p = os.path.join(sub, "album.json")
                if not os.path.isfile(meta_p):
                    continue
                try:
                    with open(meta_p, "r", encoding="utf-8") as mf:
                        mdata = json.load(mf)
                    md_id = str(mdata.get("disc_id") or "")
                    md_safe = "".join(c for c in md_id if c.isalnum() or c in ("-", "_"))
                    if md_id == raw_id or (safe_raw and md_safe == safe_raw):
                        return sub
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    def list_albums(self, force_refresh: bool = False) -> List[LocalAlbumInfo]:
        with self._lock:
            if not force_refresh and self._cached_albums is not None:
                return list(self._cached_albums)

            self._ensure_dir()
            albums: List[LocalAlbumInfo] = []

            try:
                for entry in os.listdir(self.base_dir):
                    if ".rip_staging_" in entry or ".rip_backup_" in entry:
                        continue
                    sub_dir = os.path.join(self.base_dir, entry)
                    if not os.path.isdir(sub_dir):
                        continue
                    meta_path = os.path.join(sub_dir, "album.json")
                    if not os.path.isfile(meta_path):
                        continue

                    try:
                        with open(meta_path, "r", encoding="utf-8") as f:
                            data = json.load(f)

                        tracks_data = [
                            LocalTrackInfo(**{k: v for k, v in t.items() if k in _LOCAL_TRACK_FIELDS})
                            for t in (data.get("tracks") or [])
                            if isinstance(t, dict)
                        ]
                        cover_data_url = data.get("cover_data_url", "")
                        thumb_data_url = ""

                        # 如果未存 Base64，但有 cover.jpg / cover.png 文件，则即时生成
                        cover_file = str(data.get("cover_file") or "")
                        raw_cover_bytes = b""
                        safe_cover_path = ""
                        if cover_file:
                            cand_cover = os.path.join(sub_dir, cover_file)
                            if _is_within_dir(sub_dir, cand_cover) and os.path.isfile(cand_cover):
                                safe_cover_path = cand_cover
                        if safe_cover_path and not cover_data_url:
                            try:
                                with open(safe_cover_path, "rb") as cf:
                                    raw_cover_bytes = cf.read()
                                    mime = "image/png" if cover_file.lower().endswith(".png") else "image/jpeg"
                                    b64 = base64.b64encode(raw_cover_bytes).decode("ascii")
                                    cover_data_url = f"data:{mime};base64,{b64}"
                            except Exception:
                                pass

                        thumb_path = os.path.join(sub_dir, COVER_THUMB_FILENAME)
                        if os.path.isfile(thumb_path):
                            try:
                                with open(thumb_path, "rb") as tf:
                                    tb64 = base64.b64encode(tf.read()).decode("ascii")
                                    thumb_data_url = f"data:image/jpeg;base64,{tb64}"
                            except Exception:
                                pass
                        elif cover_data_url or safe_cover_path:
                            if not raw_cover_bytes and safe_cover_path:
                                try:
                                    with open(safe_cover_path, "rb") as cf:
                                        raw_cover_bytes = cf.read()
                                except Exception:
                                    pass
                            if not raw_cover_bytes and "," in cover_data_url:
                                try:
                                    raw_cover_bytes = base64.b64decode(cover_data_url.split(",", 1)[1])
                                except Exception:
                                    raw_cover_bytes = b""
                            if raw_cover_bytes:
                                thumb_data_url = generate_cover_thumbnail(raw_cover_bytes, thumb_path=thumb_path)

                        alb = LocalAlbumInfo(
                            disc_id=data.get("disc_id", entry),
                            title=data.get("title", "Unknown Album"),
                            artist=data.get("artist", "Unknown Artist"),
                            year=data.get("year", ""),
                            cover_file=cover_file,
                            cover_data_url=cover_data_url,
                            thumb_data_url=thumb_data_url,
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
        if not path:
            return False, []
        abs_base = os.path.abspath(self.base_dir)
        abs_target = os.path.abspath(path)
        if abs_target == abs_base or not _is_within_dir(abs_base, abs_target):
            return False, [path]
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
            sub_dir = self._resolve_existing_album_dir(disc_id)
            if not sub_dir:
                try:
                    sub_dir = self.get_album_dir(disc_id)
                except ValueError:
                    return {"ok": False, "existed": False, "path": "", "failed": [], "reason": "非法的唱片标识"}
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
            sub_dir = self._resolve_existing_album_dir(disc_id)
            if not sub_dir:
                return False
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
                _atomic_write_json(meta_path, data)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def update_album_cover(self, disc_id: str, cover_data_url: str) -> bool:
        with self._lock:
            sub_dir = self._resolve_existing_album_dir(disc_id)
            if not sub_dir:
                return False
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                cover_str = str(cover_data_url or "").strip()
                if not cover_str:
                    # 显式清空封面：删除旧封面文件及缩略图
                    for stale in ("cover.jpg", "cover.png", COVER_THUMB_FILENAME):
                        sp = os.path.join(sub_dir, stale)
                        if os.path.isfile(sp):
                            try:
                                os.remove(sp)
                            except OSError:
                                pass
                    data["cover_file"] = ""
                    data["cover_data_url"] = ""
                else:
                    cover_filename = "cover.jpg"
                    if "," in cover_str:
                        hdr, b64data = cover_str.split(",", 1)
                        raw_bytes = base64.b64decode(b64data)
                        is_png = raw_bytes[:8].startswith(b"\x89PNG") or ("image/png" in hdr.lower())
                        cover_filename = "cover.png" if is_png else "cover.jpg"
                        other_filename = "cover.jpg" if is_png else "cover.png"
                        other_path = os.path.join(sub_dir, other_filename)
                        if os.path.isfile(other_path):
                            try:
                                os.remove(other_path)
                            except OSError:
                                pass
                        cover_path = os.path.join(sub_dir, cover_filename)
                        tmp_cov = f"{cover_path}.{os.getpid()}.tmp"
                        try:
                            with open(tmp_cov, "wb") as f_cov:
                                f_cov.write(raw_bytes)
                                f_cov.flush()
                            os.replace(tmp_cov, cover_path)
                        finally:
                            if os.path.isfile(tmp_cov):
                                try:
                                    os.remove(tmp_cov)
                                except OSError:
                                    pass
                        generate_cover_thumbnail(raw_bytes, thumb_path=os.path.join(sub_dir, COVER_THUMB_FILENAME))
                    data["cover_file"] = cover_filename
                    data["cover_data_url"] = cover_str
                _atomic_write_json(meta_path, data)
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
            sub_dir = self._resolve_existing_album_dir(disc_id)
            if not sub_dir:
                return False
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
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
                _atomic_write_json(meta_path, data)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def update_track_song_id(self, disc_id: str, track_number: int, source: str, song_id: str) -> bool:
        """只记录该曲目对应的歌曲 ID（不写歌词、不锁定），供后续按 ID 直取歌词"""
        with self._lock:
            sub_dir = self._resolve_existing_album_dir(disc_id)
            if not sub_dir:
                return False
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
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
                _atomic_write_json(meta_path, data)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def update_track_lyric_offset(self, disc_id: str, track_number: int, offset: float) -> bool:
        """持久化保存单曲歌词时间轴微调偏移量 (秒) 到 album.json"""
        with self._lock:
            sub_dir = self._resolve_existing_album_dir(disc_id)
            if not sub_dir:
                return False
            meta_path = os.path.join(sub_dir, "album.json")
            if not os.path.isfile(meta_path):
                return False
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
                _atomic_write_json(meta_path, data)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def export_library_zip(self, target_zip_path: str) -> bool:
        import zipfile
        with self._lock:
            try:
                abs_target = os.path.abspath(target_zip_path)
                parent = os.path.dirname(abs_target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with zipfile.ZipFile(abs_target, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(self.base_dir):
                        for file in files:
                            full_path = os.path.join(root, file)
                            if os.path.abspath(full_path) == abs_target:
                                continue
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
                abs_base = os.path.abspath(self.base_dir)
                with zipfile.ZipFile(source_zip_path, "r") as zf:
                    for info in zf.infolist():
                        target = os.path.abspath(os.path.join(abs_base, info.filename))
                        if not _is_within_dir(abs_base, target):
                            return False
                    zf.extractall(self.base_dir)
                self._cached_albums = None
                return True
            except Exception:
                return False

    def get_album_toc(self, disc_id: str) -> Optional[CdDiscToc]:
        albums = self.list_albums()
        target: Optional[LocalAlbumInfo] = None
        safe_id = "".join(c for c in str(disc_id or "") if c.isalnum() or c in ("-", "_"))

        for a in albums:
            a_safe = "".join(c for c in a.disc_id if c.isalnum() or c in ("-", "_"))
            if a.disc_id == disc_id or (safe_id and (a_safe == safe_id or a.disc_id == safe_id)):
                target = a
                break
        if not target or not target.tracks:
            return None

        album_dir = self._resolve_existing_album_dir(target.disc_id) or self._resolve_existing_album_dir(disc_id)
        if not album_dir:
            try:
                album_dir = self.get_album_dir(target.disc_id)
            except ValueError:
                return None

        tracks: List[CdTrackInfo] = []
        for t in target.tracks:
            fname = str(t.file_name or "")
            full_path = os.path.join(album_dir, fname) if fname else ""
            if full_path and not _is_within_dir(album_dir, full_path):
                continue
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
        if not tracks:
            return None

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

    def get_wav_stats(self) -> dict:
        """统计资料库中仍为 WAV 格式的专辑数、曲目数与总大小（MB）。"""
        albums = self.list_albums(force_refresh=True)
        wav_albums = 0
        wav_tracks = 0
        wav_bytes = 0
        for alb in albums:
            alb_dir = self._resolve_existing_album_dir(alb.disc_id)
            if not alb_dir:
                continue
            has_wav = False
            for t in (alb.tracks or []):
                fname = str(getattr(t, "file_name", "") or "")
                if fname.lower().endswith(".wav"):
                    fpath = os.path.join(alb_dir, fname)
                    if _is_within_dir(alb_dir, fpath) and os.path.isfile(fpath):
                        has_wav = True
                        wav_tracks += 1
                        try:
                            wav_bytes += os.path.getsize(fpath)
                        except OSError:
                            pass
            if has_wav:
                wav_albums += 1
        wav_mb = round(wav_bytes / (1024.0 * 1024.0), 1)
        estimated_save_mb = round((wav_bytes * 0.45) / (1024.0 * 1024.0), 1)
        return {
            "wavAlbums": wav_albums,
            "wavTracks": wav_tracks,
            "wavBytes": wav_bytes,
            "wavMb": wav_mb,
            "wavAlbumCount": wav_albums,
            "wavTrackCount": wav_tracks,
            "wavTotalBytes": wav_bytes,
            "wavTotalMb": wav_mb,
            "estimatedSaveMb": estimated_save_mb,
        }

    def convert_wav_albums_to_flac(
        self,
        on_progress: Optional[Callable[[dict], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> dict:
        """
        将资料库中旧 WAV 音轨逐轨无损转码为 FLAC 并写入元数据/内嵌封面：
        严格先校验新 FLAC 帧数与原 WAV 一致，加锁重新读取并原子更新 album.json 后再删除旧 WAV。
        """
        import soundfile as sf

        stats = self.get_wav_stats()
        total_tracks = int(stats.get("wavTracks", 0))
        result = {
            "ok": True,
            "convertedAlbums": 0,
            "convertedTracks": 0,
            "totalTracks": total_tracks,
            "savedBytes": 0,
            "savedMb": 0.0,
            "errors": [],
            "cancelled": False,
        }
        if total_tracks <= 0:
            if on_progress:
                on_progress({
                    "status": "completed",
                    "progressPct": 100.0,
                    "statusText": "资料库中没有需要转换的 WAV 音轨",
                    **result,
                })
            return result

        log_runtime_event("MIGRATE", f"开始 WAV -> FLAC 无损转换 albums={stats['wavAlbums']} tracks={total_tracks}")
        albums = self.list_albums(force_refresh=True)
        done_tracks = 0

        for alb in albums:
            if should_cancel and should_cancel():
                result["cancelled"] = True
                break
            alb_dir = self._resolve_existing_album_dir(alb.disc_id)
            if not alb_dir:
                continue
            meta_path = os.path.join(alb_dir, "album.json")
            if not os.path.isfile(meta_path):
                continue

            # 读取高清封面字节用于内嵌至 FLAC
            cover_bytes = b""
            cover_mime = "image/jpeg"
            if alb.cover_file:
                cp = os.path.join(alb_dir, alb.cover_file)
                if _is_within_dir(alb_dir, cp) and os.path.isfile(cp):
                    try:
                        with open(cp, "rb") as cf:
                            cover_bytes = cf.read()
                        if alb.cover_file.lower().endswith(".png"):
                            cover_mime = "image/png"
                    except Exception:
                        pass
            if not cover_bytes and alb.cover_data_url and "," in alb.cover_data_url:
                try:
                    hdr, b64 = alb.cover_data_url.split(",", 1)
                    cover_bytes = base64.b64decode(b64)
                    if "png" in hdr.lower() or cover_bytes[:8].startswith(b"\x89PNG"):
                        cover_mime = "image/png"
                except Exception:
                    pass

            try:
                with self._lock:
                    with open(meta_path, "r", encoding="utf-8") as mf:
                        meta_data = json.load(mf)
            except Exception as e:
                result["errors"].append(f"{alb.title}: 读取 album.json 失败 ({e})")
                continue

            meta_tracks = meta_data.get("tracks") or []
            album_converted_any = False
            total_in_album = len(meta_tracks)

            for trk_dict in meta_tracks:
                if should_cancel and should_cancel():
                    result["cancelled"] = True
                    break
                fname = str(trk_dict.get("file_name") or "")
                if not fname.lower().endswith(".wav"):
                    continue
                wav_path = os.path.join(alb_dir, fname)
                if not _is_within_dir(alb_dir, wav_path) or not os.path.isfile(wav_path):
                    continue

                flac_name = os.path.splitext(fname)[0] + ".flac"
                flac_path = os.path.join(alb_dir, flac_name)
                if not _is_within_dir(alb_dir, flac_path):
                    continue
                tmp_flac_path = flac_path + ".tmp"
                trk_title = str(trk_dict.get("title") or fname)
                trk_no = trk_dict.get("track_number")

                if on_progress:
                    on_progress({
                        "status": "running",
                        "progressPct": round((done_tracks / max(1, total_tracks)) * 100.0, 1),
                        "statusText": f"正在无损转换 ({done_tracks + 1}/{total_tracks}): {alb.title} - {trk_title}",
                        "convertedTracks": result["convertedTracks"],
                        "totalTracks": total_tracks,
                        "savedMb": round(result["savedBytes"] / (1024.0 * 1024.0), 1),
                    })

                try:
                    wav_size = os.path.getsize(wav_path)
                    with wave.open(wav_path, "rb") as wf:
                        n_channels = wf.getnchannels()
                        sampwidth = wf.getsampwidth()
                        framerate = wf.getframerate()
                        expected_frames = wf.getnframes()
                        if sampwidth != 2:
                            raise RuntimeError(f"暂不支持 {sampwidth * 8}-bit WAV 转换")
                        with sf.SoundFile(
                            tmp_flac_path,
                            mode="w",
                            samplerate=framerate,
                            channels=n_channels,
                            subtype="PCM_16",
                            format="FLAC",
                        ) as sf_out:
                            while True:
                                chunk = wf.readframes(65536)
                                if not chunk:
                                    break
                                sf_out.buffer_write(chunk, dtype="int16")

                    # 帧数严格校验：确认无损转换一帧未丢
                    info = sf.info(tmp_flac_path)
                    if int(info.frames) != int(expected_frames):
                        raise RuntimeError(f"帧数校验不一致 ({info.frames} != {expected_frames})")

                    write_flac_tags(
                        tmp_flac_path,
                        title=trk_title,
                        artist=str(trk_dict.get("artist") or alb.artist or ""),
                        album=str(alb.title or ""),
                        year=str(alb.year or ""),
                        track_number=int(trk_no or 0),
                        total_tracks=total_in_album,
                        cover_bytes=cover_bytes,
                        cover_mime=cover_mime,
                    )

                    os.replace(tmp_flac_path, flac_path)
                    flac_size = os.path.getsize(flac_path)

                    # 加锁重新读取最新 album.json 并原子写回，再删除旧 WAV
                    with self._lock:
                        with open(meta_path, "r", encoding="utf-8") as mf:
                            fresh_meta = json.load(mf)
                        for ft in (fresh_meta.get("tracks") or []):
                            if (trk_no is not None and ft.get("track_number") == trk_no) or ft.get("file_name") == fname:
                                ft["file_name"] = flac_name
                                break
                        _atomic_write_json(meta_path, fresh_meta)
                        self._cached_albums = None
                    try:
                        os.remove(wav_path)
                    except OSError:
                        pass

                    result["convertedTracks"] += 1
                    result["savedBytes"] += (wav_size - flac_size)
                    result["savedMb"] = round(result["savedBytes"] / (1024.0 * 1024.0), 1)
                    album_converted_any = True
                except Exception as e:
                    try:
                        if os.path.isfile(tmp_flac_path):
                            os.remove(tmp_flac_path)
                    except OSError:
                        pass
                    result["errors"].append(f"{alb.title}/{fname}: {e}")
                    log_runtime_event("MIGRATE", f"转换失败 {alb.title}/{fname}: {e}", level="ERROR")
                finally:
                    done_tracks += 1

            if album_converted_any:
                result["convertedAlbums"] += 1

        if result["errors"]:
            result["ok"] = False

        with self._lock:
            self._cached_albums = None
        self.list_albums(force_refresh=True)

        log_runtime_event(
            "MIGRATE",
            f"WAV -> FLAC 转换结束 albums={result['convertedAlbums']} tracks={result['convertedTracks']} saved={result['savedMb']}MB errs={len(result['errors'])}",
        )
        if on_progress:
            on_progress({
                "status": "cancelled" if result["cancelled"] else "completed",
                "progressPct": 100.0 if not result["cancelled"] else round((done_tracks / max(1, total_tracks)) * 100.0, 1),
                "statusText": (
                    f"已取消转换（已完成 {result['convertedTracks']} 首，释放 {result['savedMb']} MB）"
                    if result["cancelled"]
                    else f"转换完成：共压缩 {result['convertedTracks']} 首音轨，节省 {result['savedMb']} MB 空间"
                ),
                **result,
            })
        return result


def write_flac_tags(
    flac_path: str,
    title: str = "",
    artist: str = "",
    album: str = "",
    year: str = "",
    track_number: int = 0,
    total_tracks: int = 0,
    cover_bytes: bytes = b"",
    cover_mime: str = "image/jpeg",
) -> bool:
    """向 FLAC 文件写入标准 Vorbis Comment 标签与高清内嵌封面（Picture）。"""
    try:
        from mutagen.flac import FLAC, Picture

        audio = FLAC(flac_path)
        if title:
            audio["TITLE"] = [str(title)]
        if artist:
            audio["ARTIST"] = [str(artist)]
        if album:
            audio["ALBUM"] = [str(album)]
        if year:
            audio["DATE"] = [str(year)]
        if track_number > 0:
            audio["TRACKNUMBER"] = [str(int(track_number))]
        if total_tracks > 0:
            audio["TRACKTOTAL"] = [str(int(total_tracks))]
        if cover_bytes:
            pic = Picture()
            pic.type = 3  # Front Cover
            pic.mime = cover_mime or "image/jpeg"
            pic.desc = "Front Cover"
            pic.data = cover_bytes
            audio.clear_pictures()
            audio.add_picture(pic)
        audio.save()
        return True
    except Exception:
        return False


class _TrackAudioWriter:
    """统一抓轨无损流式写入器：支持 FLAC（零拷贝 buffer_write）与 WAV（1MB 缓冲 writeframesraw）。"""

    def __init__(self, filepath: str, fmt: str = "flac"):
        self.filepath = filepath
        self.fmt = "wav" if str(fmt or "").strip().lower() == "wav" else "flac"
        self._f_wav = None
        self._wf = None
        self._sf = None

    def __enter__(self):
        try:
            if self.fmt == "wav":
                self._f_wav = open(self.filepath, "wb", buffering=1024 * 1024)
                self._wf = wave.open(self._f_wav, "wb")
                self._wf.setnchannels(2)
                self._wf.setsampwidth(2)
                self._wf.setframerate(44100)
            else:
                import soundfile as sf
                self._sf = sf.SoundFile(
                    self.filepath,
                    mode="w",
                    samplerate=44100,
                    channels=2,
                    subtype="PCM_16",
                    format="FLAC",
                )
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def write_pcm(self, pcm_buf) -> None:
        if self._wf is not None:
            self._wf.writeframesraw(pcm_buf)
        elif self._sf is not None:
            self._sf.buffer_write(pcm_buf, dtype="int16")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._wf is not None:
            try:
                self._wf.close()
            except Exception:
                pass
            self._wf = None
        if self._f_wav is not None:
            try:
                self._f_wav.close()
            except Exception:
                pass
            self._f_wav = None
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:
                pass
            self._sf = None
        return False


class CdRipperTask:
    """零损 CD-DA 原始 PCM 扇区抓轨并编码为 FLAC（默认）或标准 WAV 的后台任务"""

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
        rip_format: str = "flac",
    ):
        self.toc = toc
        self.album_title = album_title or "Audio CD"
        self.artist = artist or "Unknown Artist"
        self.year = year or ""
        self.cover_data_url = cover_data_url or ""
        self.lyrics_cache = lyrics_cache or {}
        self.library_mgr = library_mgr
        self.on_progress = on_progress
        self.rip_format = "wav" if str(rip_format or "").strip().lower() == "wav" else "flac"

        self._lock = threading.Lock()
        self._cancelled = False
        self._progress = RippingProgress()

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def get_progress(self) -> RippingProgress:
        with self._lock:
            return RippingProgress(**asdict(self._progress))

    def _update_progress(self, **kwargs) -> RippingProgress:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self._progress, k, v)
            return RippingProgress(**asdict(self._progress))

    def run(self):
        audio_tracks = [
            t for t in (self.toc.tracks or [])
            if not getattr(t, "is_data_track", False) and (t.end_lba > t.start_lba)
        ]
        snap = self._update_progress(
            is_ripping=True,
            total_tracks=len(audio_tracks),
            status_text="正在初始化无损抓轨引擎...",
        )
        if self.on_progress:
            self.on_progress(snap)

        if not audio_tracks:
            snap = self._update_progress(
                is_ripping=False,
                error_message="当前光盘没有可抓取的音频音轨",
                status_text="抓轨失败：未检测到音频音轨",
            )
            if self.on_progress:
                self.on_progress(snap)
            return

        try:
            target_dir = self.library_mgr.get_album_dir(self.toc.disc_id)
        except ValueError as e:
            snap = self._update_progress(
                is_ripping=False,
                error_message=str(e),
                status_text=f"抓轨失败：{e}",
            )
            if self.on_progress:
                self.on_progress(snap)
            return

        staging_dir = f"{target_dir}.rip_staging_{os.getpid()}_{threading.get_ident()}"
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)
        os.makedirs(staging_dir, exist_ok=True)

        # 保存高清封面图至暂存目录
        cover_file = ""
        cover_raw_bytes = b""
        cover_mime = "image/jpeg"
        if self.cover_data_url:
            try:
                if "," in self.cover_data_url:
                    header, b64_data = self.cover_data_url.split(",", 1)
                    raw_img = base64.b64decode(b64_data)
                    is_png = raw_img[:8].startswith(b"\x89PNG") or ("png" in header.lower())
                    ext = "png" if is_png else "jpg"
                    cover_mime = "image/png" if ext == "png" else "image/jpeg"
                    cover_raw_bytes = raw_img
                    cover_file = f"cover.{ext}"
                    with open(os.path.join(staging_dir, cover_file), "wb") as f_cov:
                        f_cov.write(raw_img)
                    generate_cover_thumbnail(raw_img, thumb_path=os.path.join(staging_dir, COVER_THUMB_FILENAME))
            except Exception:
                pass

        h_drive = WindowsCdDriveManager.open_drive_handle(
            self.toc.drive_letter, read_access=True, allow_zero_fallback=False
        )
        if h_drive is None:
            log_runtime_event("RIP", f"无法以读取权限打开光驱 {self.toc.drive_letter}", level="ERROR")
            shutil.rmtree(staging_dir, ignore_errors=True)
            snap = self._update_progress(
                is_ripping=False,
                error_message=f"无法以读取权限打开光驱 {self.toc.drive_letter}",
                status_text="抓轨失败：无法访问光驱硬件",
            )
            if self.on_progress:
                self.on_progress(snap)
            return

        log_runtime_event(
            "RIP",
            f"开始抓轨 drive={self.toc.drive_letter} disc_id={self.toc.disc_id} album=《{self.album_title}》 tracks={len(audio_tracks)} format={self.rip_format}",
        )

        # 自适应批次读取（v1.11 提速 + 零拷贝/流式写入优化）：
        # 优先以 64 扇区（约 147KB）大批量传输，显著减少 DeviceIoControl 往返次数；
        # 若光驱/驱动在首次探测时拒绝大批量，自动逐级降档 48 → 32 → 27 → 16（以及 12 → 8 → 4 → 1 兼容受限 USB 桥接器）；
        # 若某一批已稳定成功过，后续偶发单点划痕只针对该批单扇区兜底，不永久降速整张专辑。
        BATCH_CANDIDATES = (64, 48, 32, 27, 16)
        SUB_BATCH_CANDIDATES = (12, 8, 4, 1)
        sectors_per_batch = BATCH_CANDIDATES[0]
        raw_buffer = ctypes.create_string_buffer(CD_SECTOR_RAW_BYTES * BATCH_CANDIDATES[0])
        raw_mv = memoryview(raw_buffer)
        single_buf = ctypes.create_string_buffer(CD_SECTOR_RAW_BYTES)
        single_mv = memoryview(single_buf)
        bytes_returned = wintypes.DWORD(0)
        single_ret = wintypes.DWORD(0)
        raw_info = RAW_READ_INFO()
        single_info = RAW_READ_INFO()
        PROGRESS_MIN_INTERVAL = 0.12  # 进度回调节流间隔（秒），避免每批次跨线程唤醒 UI

        saved_tracks: List[LocalTrackInfo] = []
        total_audio_sectors = sum(t.end_lba - t.start_lba for t in audio_tracks)
        sectors_ripped_so_far = 0
        consecutive_hardware_errors = 0
        batch_succeeded_once = False
        rip_start_ts = time.perf_counter()

        try:
            for idx, trk in enumerate(audio_tracks):
                if self._cancelled:
                    self._update_progress(status_text="抓轨任务已由用户取消")
                    break

                trk_title = trk.title or f"Track {trk.track_number:02d}"
                snap = self._update_progress(
                    current_track_index=idx + 1,
                    track_title=trk_title,
                    status_text=f"正在抓取 ({idx + 1}/{len(audio_tracks)}): {trk_title}",
                )
                if self.on_progress:
                    self.on_progress(snap)

                ext = "wav" if self.rip_format == "wav" else "flac"
                track_filename = f"track_{trk.track_number:02d}.{ext}"
                track_path = os.path.join(staging_dir, track_filename)

                cur_lba = trk.start_lba
                end_lba = trk.end_lba
                track_total_sectors = max(1, end_lba - cur_lba)

                with _TrackAudioWriter(track_path, fmt=self.rip_format) as writer:
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
                            batch_succeeded_once = True
                            consecutive_hardware_errors = 0
                            writer.write_pcm(raw_mv[:required_bytes])
                        else:
                            # 仅在驱动尚未成功过时永久降档探测硬件单次传输上限；
                            # 若已稳定跑通当前档位，则视为局部划痕，仅对当前批单扇区重读而不降速整碟
                            if not batch_succeeded_once and batch_count >= sectors_per_batch:
                                lower_candidates = [c for c in BATCH_CANDIDATES if c < sectors_per_batch]
                                if lower_candidates:
                                    sectors_per_batch = lower_candidates[0]
                                    continue
                                sub_lower = [c for c in SUB_BATCH_CANDIDATES if c < sectors_per_batch]
                                if sub_lower:
                                    sectors_per_batch = sub_lower[0]
                                    continue

                            # 降级为单扇区逐一读取尝试，兼容老旧光盘或局部划痕介质
                            fallback_pcm = bytearray()
                            for sub_lba in range(cur_lba, cur_lba + batch_count):
                                if self._cancelled:
                                    break
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
                                    fallback_pcm.extend(single_mv)
                                else:
                                    consecutive_hardware_errors += 1
                                    if consecutive_hardware_errors >= 80:
                                        err_code = ctypes.get_last_error() or kernel32.GetLastError()
                                        raise RuntimeError(f"光驱扇区持续读取失败 (错误码 {err_code})，请检查光驱或光盘")
                                    fallback_pcm.extend(b"\x00" * CD_SECTOR_RAW_BYTES)
                            if self._cancelled:
                                break
                            writer.write_pcm(fallback_pcm)

                        cur_lba += batch_count
                        sectors_ripped_so_far += batch_count

                        track_pct = round(((cur_lba - trk.start_lba) / track_total_sectors) * 100, 1)
                        overall_pct = round((sectors_ripped_so_far / max(1, total_audio_sectors)) * 100, 1)
                        now_ts = time.time()
                        elapsed_rip = max(0.05, time.perf_counter() - rip_start_ts)
                        speed_x = (sectors_ripped_so_far / 75.0) / elapsed_rip
                        mb_s = (sectors_ripped_so_far * CD_SECTOR_RAW_BYTES) / (1024.0 * 1024.0 * elapsed_rip)
                        fmt_label = "FLAC" if self.rip_format == "flac" else "WAV"
                        snap = self._update_progress(
                            track_progress_pct=min(100.0, track_pct),
                            overall_progress_pct=min(100.0, overall_pct),
                            status_text=(
                                f"正在抓取 ({idx + 1}/{len(audio_tracks)}) · {fmt_label} · "
                                f"{speed_x:.1f}x ({mb_s:.2f} MB/s) · 批{sectors_per_batch}扇区"
                            ),
                        )
                        if self.on_progress and (
                            now_ts - last_progress_ts >= PROGRESS_MIN_INTERVAL or cur_lba >= end_lba
                        ):
                            last_progress_ts = now_ts
                            self.on_progress(snap)

                if self._cancelled:
                    break

                track_title = trk.title or f"Track {trk.track_number:02d}"
                track_artist = trk.artist or self.artist
                if self.rip_format == "flac":
                    write_flac_tags(
                        track_path,
                        title=track_title,
                        artist=track_artist,
                        album=self.album_title,
                        year=self.year,
                        track_number=trk.track_number,
                        total_tracks=len(audio_tracks),
                        cover_bytes=cover_raw_bytes,
                        cover_mime=cover_mime,
                    )

                lyrics_data = self.lyrics_cache.get(trk.track_number, [])
                saved_tracks.append(
                    LocalTrackInfo(
                        track_number=trk.track_number,
                        title=track_title,
                        artist=track_artist,
                        duration_sec=trk.duration_sec,
                        file_name=track_filename,
                        lyrics=lyrics_data,
                    )
                )

            if not self._cancelled:
                # 写入 album.json 元数据文件至暂存目录，随后原子切换至正式目录
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
                meta_json_path = os.path.join(staging_dir, "album.json")
                _atomic_write_json(meta_json_path, meta_payload)

                existing_dir = self.library_mgr._resolve_existing_album_dir(self.toc.disc_id) or target_dir
                backup_dir = ""
                if (
                    existing_dir
                    and os.path.abspath(existing_dir) != os.path.abspath(staging_dir)
                    and os.path.isdir(existing_dir)
                ):
                    backup_dir = f"{existing_dir}.rip_backup_{os.getpid()}_{threading.get_ident()}"
                    if os.path.exists(backup_dir):
                        shutil.rmtree(backup_dir, ignore_errors=True)
                    os.replace(existing_dir, backup_dir)
                try:
                    os.replace(staging_dir, target_dir)
                except Exception:
                    if backup_dir and os.path.isdir(backup_dir) and not os.path.exists(existing_dir):
                        try:
                            os.replace(backup_dir, existing_dir)
                        except Exception:
                            pass
                    raise
                if backup_dir and os.path.isdir(backup_dir):
                    shutil.rmtree(backup_dir, ignore_errors=True)

                self.library_mgr.list_albums(force_refresh=True)
                total_rip_sec = max(0.05, time.perf_counter() - rip_start_ts)
                avg_speed_x = (sectors_ripped_so_far / 75.0) / total_rip_sec
                avg_mb_s = (sectors_ripped_so_far * CD_SECTOR_RAW_BYTES) / (1024.0 * 1024.0 * total_rip_sec)
                log_runtime_event(
                    "RIP",
                    f"抓轨完成 album=《{self.album_title}》 format={self.rip_format} tracks={len(saved_tracks)} batch={sectors_per_batch} elapsed={total_rip_sec:.1f}s speed={avg_speed_x:.1f}x ({avg_mb_s:.2f} MB/s)",
                )

                snap = self._update_progress(
                    is_ripping=False,
                    is_completed=True,
                    overall_progress_pct=100.0,
                    status_text=f"专辑《{self.album_title}》已成功归档至黑胶资料库！",
                )
                if self.on_progress:
                    self.on_progress(snap)
        except Exception as e:
            log_runtime_event("RIP", f"抓轨异常 album=《{self.album_title}》 err={e}", level="ERROR")
            snap = self._update_progress(
                is_ripping=False,
                error_message=str(e),
                status_text=f"抓轨失败: {e}",
            )
            if self.on_progress:
                self.on_progress(snap)
        finally:
            kernel32.CloseHandle(h_drive)
            if os.path.isdir(staging_dir):
                shutil.rmtree(staging_dir, ignore_errors=True)
            if self._cancelled:
                log_runtime_event("RIP", f"抓轨已取消 album=《{self.album_title}》", level="WARNING")
                snap = self._update_progress(
                    is_ripping=False,
                    status_text="抓轨任务已取消",
                )
                if self.on_progress:
                    self.on_progress(snap)
