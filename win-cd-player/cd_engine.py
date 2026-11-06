# -*- coding: utf-8 -*-
"""
cd_engine.py
Discflow 底层光驱硬件通信、CD-TEXT 读取、MusicBrainz/FreeDB DiscID 计算
以及基于 Win32 winmm.dll (waveOut) 的非阻塞、零延迟 CDDA 原始 PCM 扇区流播放引擎。
"""

import base64
import ctypes
from ctypes import wintypes
import hashlib
import os
import queue
import string
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# ============================================================================
# 1. Windows Win32 底层 64-bit 安全函数签名与 IOCTL / WaveOut 常量定义
# ============================================================================
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

DRIVE_CDROM = 5

IOCTL_CDROM_READ_TOC = 0x00024000
IOCTL_CDROM_READ_TOC_EX = 0x00024054
IOCTL_CDROM_RAW_READ = 0x0002403E
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
IOCTL_STORAGE_CHECK_VERIFY = 0x002D4800

CDROM_READ_TOC_EX_FORMAT_CDTEXT = 0x05

CD_FRAMES_PER_SECOND = 75          # 1 秒 = 75 个 CD 扇区帧
CD_SECTOR_RAW_BYTES = 2352         # 每个 CD-DA 扇区包含 2352 字节原始 PCM (44.1kHz, 16-bit, Stereo)
CD_LEADIN_OFFSET_FRAMES = 150      # 标准 2 秒 Lead-in 偏移
CDDA_TRACK_MODE = 2                # TRACK_MODE_TYPE: CDDA = 2

WAVE_MAPPER = ctypes.c_uint(0xFFFFFFFF)
WAVE_FORMAT_PCM = 1
WHDR_DONE = 0x00000001
WHDR_PREPARED = 0x00000002

# 显式声明 64 位 Windows API 参数与返回值类型
kernel32.GetLogicalDrives.argtypes = []
kernel32.GetLogicalDrives.restype = wintypes.DWORD

kernel32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
kernel32.GetDriveTypeW.restype = wintypes.UINT

kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE

kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]
kernel32.DeviceIoControl.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


class TRACK_DATA(ctypes.Structure):
    _fields_ = [
        ("Reserved", ctypes.c_ubyte),
        ("Control", ctypes.c_ubyte, 4),
        ("Adr", ctypes.c_ubyte, 4),
        ("TrackNumber", ctypes.c_ubyte),
        ("Reserved1", ctypes.c_ubyte),
        ("Address", ctypes.c_ubyte * 4),
    ]


class CDROM_TOC(ctypes.Structure):
    _fields_ = [
        ("Length", ctypes.c_ubyte * 2),
        ("FirstTrack", ctypes.c_ubyte),
        ("LastTrack", ctypes.c_ubyte),
        ("TrackData", TRACK_DATA * 100),
    ]


class CDROM_READ_TOC_EX(ctypes.Structure):
    _fields_ = [
        ("Format", ctypes.c_ubyte, 4),
        ("Reserved1", ctypes.c_ubyte, 3),
        ("Msf", ctypes.c_ubyte, 1),
        ("SessionTrack", ctypes.c_ubyte),
        ("Reserved2", ctypes.c_ubyte),
        ("Reserved3", ctypes.c_ubyte),
    ]


class RAW_READ_INFO(ctypes.Structure):
    _fields_ = [
        ("DiskOffset", ctypes.c_longlong),
        ("SectorCount", wintypes.ULONG),
        ("TrackMode", ctypes.c_int),
    ]


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_char_p),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", wintypes.LPVOID),
        ("reserved", ctypes.c_size_t),
    ]


winmm.waveOutOpen.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.c_uint,
    ctypes.POINTER(WAVEFORMATEX),
    ctypes.c_size_t,
    ctypes.c_size_t,
    wintypes.DWORD,
]
winmm.waveOutOpen.restype = wintypes.UINT

winmm.waveOutPrepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveOutPrepareHeader.restype = wintypes.UINT

winmm.waveOutUnprepareHeader.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveOutUnprepareHeader.restype = wintypes.UINT

winmm.waveOutWrite.argtypes = [wintypes.HANDLE, ctypes.POINTER(WAVEHDR), wintypes.UINT]
winmm.waveOutWrite.restype = wintypes.UINT

winmm.waveOutPause.argtypes = [wintypes.HANDLE]
winmm.waveOutPause.restype = wintypes.UINT

winmm.waveOutRestart.argtypes = [wintypes.HANDLE]
winmm.waveOutRestart.restype = wintypes.UINT

winmm.waveOutReset.argtypes = [wintypes.HANDLE]
winmm.waveOutReset.restype = wintypes.UINT

winmm.waveOutSetVolume.argtypes = [wintypes.HANDLE, wintypes.DWORD]
winmm.waveOutSetVolume.restype = wintypes.UINT

winmm.waveOutClose.argtypes = [wintypes.HANDLE]
winmm.waveOutClose.restype = wintypes.UINT


# ============================================================================
# 2. 数据模型与 DiscID 计算
# ============================================================================
@dataclass
class CdTrackInfo:
    track_number: int
    start_lba: int
    end_lba: int
    start_frame: int
    duration_sec: float
    is_data_track: bool = False
    title: str = ""
    artist: str = ""
    builtin_lrc: str = ""
    local_filepath: str = ""
    lyrics_offset: float = 0.0


@dataclass
class CdDiscToc:
    drive_letter: str
    first_track: int
    last_track: int
    leadout_frame: int
    tracks: List[CdTrackInfo] = field(default_factory=list)
    disc_id: str = ""
    freedb_id: str = ""
    toc_query_string: str = ""
    cdtext_album: str = ""
    cdtext_artist: str = ""
    is_simulated: bool = False
    is_local: bool = False


def msf_to_lba(minute: int, second: int, frame: int) -> int:
    return (minute * 60 + second) * CD_FRAMES_PER_SECOND + frame - CD_LEADIN_OFFSET_FRAMES


def calculate_musicbrainz_disc_id(first_track: int, last_track: int, leadout_frame: int, track_frames: List[int]) -> str:
    parts = [
        f"{first_track:02X}",
        f"{last_track:02X}",
        f"{leadout_frame:08X}",
    ]
    for i in range(99):
        frame_offset = track_frames[i] if i < len(track_frames) else 0
        parts.append(f"{frame_offset:08X}")

    ascii_payload = "".join(parts).encode("ascii")
    sha1_digest = hashlib.sha1(ascii_payload).digest()
    b64_str = base64.b64encode(sha1_digest).decode("ascii")
    return b64_str.replace("+", ".").replace("/", "_").replace("=", "-")


def calculate_freedb_disc_id(track_frames: List[int], leadout_frame: int) -> str:
    if not track_frames:
        return "00000000"

    def sum_digits(n: int) -> int:
        total = 0
        while n > 0:
            total += n % 10
            n //= 10
        return total

    checksum = sum(sum_digits(frame // CD_FRAMES_PER_SECOND) for frame in track_frames)
    total_time_sec = (leadout_frame // CD_FRAMES_PER_SECOND) - (track_frames[0] // CD_FRAMES_PER_SECOND)
    num_tracks = len(track_frames)
    disc_id_num = ((checksum % 0xFF) << 24) | (total_time_sec << 8) | num_tracks
    return f"{disc_id_num:08x}"


# ============================================================================
# 3. Windows 光驱硬件访问接口 (含 CD-TEXT 解析与无光驱演示支持)
# ============================================================================
class WindowsCdDriveManager:
    @staticmethod
    def list_physical_cdrom_drives() -> List[str]:
        drives: List[str] = []
        bitmask = kernel32.GetLogicalDrives()
        for idx, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << idx):
                root_path = f"{letter}:\\"
                drive_type = kernel32.GetDriveTypeW(root_path)
                if drive_type == DRIVE_CDROM:
                    drives.append(f"{letter}:")
        return drives

    @staticmethod
    def open_drive_handle(drive_letter: str, read_access: bool = True):
        clean_letter = drive_letter.split()[0].rstrip("\\/")
        device_path = f"\\\\.\\{clean_letter}"
        access = GENERIC_READ if read_access else 0
        handle = kernel32.CreateFileW(
            device_path,
            access,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle == INVALID_HANDLE_VALUE or handle is None or handle == 0:
            # 降级兜底：以 0 权限 (仅查询属性) 打开，足够响应 IOCTL 状态检测与弹出
            handle = kernel32.CreateFileW(
                device_path,
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                0,
                None,
            )
        if handle == INVALID_HANDLE_VALUE or handle is None or handle == 0:
            return None
        return handle

    @classmethod
    def is_disc_ready(cls, drive_letter: str) -> bool:
        if not drive_letter:
            return False
        handle = cls.open_drive_handle(drive_letter, read_access=False)
        if handle is None:
            return False
        try:
            bytes_returned = wintypes.DWORD(0)
            ok = kernel32.DeviceIoControl(
                handle,
                IOCTL_STORAGE_CHECK_VERIFY,
                None,
                0,
                None,
                0,
                ctypes.byref(bytes_returned),
                None,
            )
            return bool(ok)
        finally:
            kernel32.CloseHandle(handle)

    @classmethod
    def eject_disc(cls, drive_letter: str) -> bool:
        if not drive_letter:
            return False
        handle = cls.open_drive_handle(drive_letter)
        if handle is None:
            return False
        try:
            bytes_returned = wintypes.DWORD(0)
            ok = kernel32.DeviceIoControl(
                handle,
                IOCTL_STORAGE_EJECT_MEDIA,
                None,
                0,
                None,
                0,
                ctypes.byref(bytes_returned),
                None,
            )
            return bool(ok)
        finally:
            kernel32.CloseHandle(handle)

    @classmethod
    def parse_cdtext_payload(cls, payload: bytes) -> tuple[str, str, Dict[int, str], Dict[int, str]]:
        """解析 CD-TEXT 原始数据包 (Pack 0x80/0x81)，完整支持 Shift-JIS、UTF-8、UTF-16、GBK 与双字节字符集"""
        if len(payload) < 18:
            return "", "", {}, {}

        title_buffers: Dict[int, bytearray] = {}
        artist_buffers: Dict[int, bytearray] = {}
        double_byte_tracks = set()

        for offset in range(0, len(payload) - 17, 18):
            pack = payload[offset : offset + 18]
            pack_type = pack[0]
            trk_no = pack[1] & 0x7F
            is_double_byte = bool(pack[3] & 0x80)
            text_chunk = pack[4:16]

            # 0x80: Title (Track 0 为专辑名, Track 1..99 为曲目名)
            # 0x81: Performer/Artist (Track 0 为专辑艺术家, Track 1..99 为曲目艺术家)
            if pack_type not in (0x80, 0x81):
                continue

            target_map = title_buffers if pack_type == 0x80 else artist_buffers
            cur_trk = trk_no
            if is_double_byte:
                double_byte_tracks.add(cur_trk)

            if is_double_byte:
                # 双字节字符集模式 (DB=1): 6 个 16-bit 字符，双空字节 \x00\x00 为字符串终止符
                for idx_w in range(0, 12, 2):
                    pair = text_chunk[idx_w : idx_w + 2]
                    if pair == b"\x00\x00":
                        cur_trk += 1
                        double_byte_tracks.add(cur_trk)
                    else:
                        target_map.setdefault(cur_trk, bytearray()).extend(pair)
            else:
                for b in text_chunk:
                    if b == 0:
                        cur_trk += 1
                    else:
                        target_map.setdefault(cur_trk, bytearray()).append(b)

        def decode_bytes(b_arr: bytearray, prefer_double_byte: bool = False) -> str:
            encodings = (
                ("cp932", "shift_jis", "utf-16-be", "utf-16-le", "utf-8", "gb18030", "gbk", "latin1")
                if prefer_double_byte
                else ("utf-8", "cp932", "shift_jis", "utf-16-be", "utf-16-le", "gb18030", "gbk", "latin1")
            )
            for enc in encodings:
                try:
                    res = bytes(b_arr).decode(enc).strip()
                    if res and "\x00" not in res:
                        return res
                except (UnicodeDecodeError, LookupError):
                    continue
            return ""

        titles = {k: decode_bytes(v, k in double_byte_tracks) for k, v in title_buffers.items() if decode_bytes(v, k in double_byte_tracks)}
        artists = {k: decode_bytes(v, k in double_byte_tracks) for k, v in artist_buffers.items() if decode_bytes(v, k in double_byte_tracks)}
        album_title = titles.pop(0, "")
        album_artist = artists.pop(0, "")
        return album_title, album_artist, titles, artists

    @classmethod
    def _read_cdtext_from_handle(cls, handle) -> tuple[str, str, Dict[int, str], Dict[int, str]]:
        toc_ex = CDROM_READ_TOC_EX()
        toc_ex.Format = CDROM_READ_TOC_EX_FORMAT_CDTEXT
        toc_ex.Msf = 0
        toc_ex.SessionTrack = 0

        buf_size = 4096
        out_buf = ctypes.create_string_buffer(buf_size)
        bytes_returned = wintypes.DWORD(0)

        ok = kernel32.DeviceIoControl(
            handle,
            IOCTL_CDROM_READ_TOC_EX,
            ctypes.byref(toc_ex),
            ctypes.sizeof(toc_ex),
            out_buf,
            buf_size,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok or bytes_returned.value < 4:
            return "", "", {}, {}

        raw = out_buf.raw[: bytes_returned.value]
        data_len = (raw[0] << 8) | raw[1]
        payload = raw[4 : min(len(raw), 2 + data_len)]
        return cls.parse_cdtext_payload(payload)

    @classmethod
    def read_toc(cls, drive_letter: str) -> Optional[CdDiscToc]:
        if not drive_letter:
            return None

        handle = cls.open_drive_handle(drive_letter)
        if handle is None:
            return None

        try:
            toc = CDROM_TOC()
            bytes_returned = wintypes.DWORD(0)
            ok = kernel32.DeviceIoControl(
                handle,
                IOCTL_CDROM_READ_TOC,
                None,
                0,
                ctypes.byref(toc),
                ctypes.sizeof(toc),
                ctypes.byref(bytes_returned),
                None,
            )
            if not ok:
                return None

            first_track = int(toc.FirstTrack)
            last_track = int(toc.LastTrack)
            if last_track < first_track or first_track < 1:
                return None

            num_tracks = last_track - first_track + 1
            raw_entries = []

            for i in range(num_tracks + 1):
                td = toc.TrackData[i]
                minute = int(td.Address[1])
                second = int(td.Address[2])
                frame = int(td.Address[3])
                lba = msf_to_lba(minute, second, frame)
                mb_frame = lba + CD_LEADIN_OFFSET_FRAMES
                is_data = bool(td.Control & 0x04)
                raw_entries.append((int(td.TrackNumber), lba, mb_frame, is_data))

            leadout_lba = raw_entries[-1][1]
            leadout_frame = raw_entries[-1][2]

            cdtext_album, cdtext_artist, cdtext_titles, cdtext_artists = cls._read_cdtext_from_handle(handle)

            tracks: List[CdTrackInfo] = []
            track_frames: List[int] = []

            for i in range(num_tracks):
                trk_num, start_lba, start_frame, is_data = raw_entries[i]
                end_lba = raw_entries[i + 1][1] if i + 1 < len(raw_entries) else leadout_lba
                duration_sec = max(1.0, (end_lba - start_lba) / CD_FRAMES_PER_SECOND)
                track_frames.append(start_frame)

                default_title = cdtext_titles.get(trk_num) or f"Track {trk_num:02d}"
                default_artist = cdtext_artists.get(trk_num) or cdtext_artist or "Audio CD"

                tracks.append(
                    CdTrackInfo(
                        track_number=trk_num,
                        start_lba=max(0, start_lba),
                        end_lba=max(0, end_lba),
                        start_frame=start_frame,
                        duration_sec=round(duration_sec, 2),
                        is_data_track=is_data,
                        title=default_title,
                        artist=default_artist,
                    )
                )

            disc_id = calculate_musicbrainz_disc_id(first_track, last_track, leadout_frame, track_frames)
            freedb_id = calculate_freedb_disc_id(track_frames, leadout_frame)
            offsets_str = "+".join(str(f) for f in track_frames)
            toc_query_str = f"{first_track}+{last_track}+{leadout_frame}+{offsets_str}"

            return CdDiscToc(
                drive_letter=drive_letter.split()[0].rstrip("\\/"),
                first_track=first_track,
                last_track=last_track,
                leadout_frame=leadout_frame,
                tracks=tracks,
                disc_id=disc_id,
                freedb_id=freedb_id,
                toc_query_string=toc_query_str,
                cdtext_album=cdtext_album,
                cdtext_artist=cdtext_artist,
                is_simulated=False,
            )
        finally:
            kernel32.CloseHandle(handle)


# ============================================================================
# 4. 非阻塞、零死锁的 Win32 waveOut 实时 CDDA 播放器
# ============================================================================
@dataclass
class _ReadChunk:
    lba: int
    data: bytes
    track: CdTrackInfo
    track_index: int
    seek_gen: int = 0
    is_track_boundary: bool = False
    is_eof: bool = False


class CddaWaveOutPlayer:
    """
    基于代数令牌 (_session_id) 的非阻塞 CDDA 扇区流播放引擎 (v2.1.0 升级):
    - 连续读取缓冲队列 (RAM Read-Ahead): 预读 3~5 秒原始 PCM 扇区，消除光驱频繁寻道机械噪音
    - 跨轨平滑无缝过渡 (Gapless Playback): 跨轨复用 winmm.waveOut 音频设备句柄与底层读取句柄
    - 原生 C 级微秒级控制 (SetVolume / Pause / Restart / Seek)
    """

    def __init__(
        self,
        on_position: Optional[Callable[[float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_track_transition: Optional[Callable[[CdTrackInfo, int], None]] = None,
    ):
        self._lock = threading.Lock()
        self._device_lock = threading.Lock()
        self._session_id: int = 0
        self._drive_letter: str = ""
        self._track: Optional[CdTrackInfo] = None
        self._current_track_index: int = 0
        self._current_lba: int = 0
        self._is_playing: bool = False
        self._is_active: bool = False
        self._seek_reset_pending: bool = False
        self._seek_generation: int = 0
        self._seek_target_lba: int = 0
        self._pause_change_pending: Optional[bool] = None
        self._volume: float = 0.80
        self._volume_dirty: bool = True

        # 连续读取缓冲队列（预读 25 个块，每块 15 扇区 = 0.2 秒，共约 5 秒音频预读）
        self._read_ahead_queue: queue.Queue = queue.Queue(maxsize=25)

        # 预设下一轨信息，供无缝跨轨过渡
        self._next_drive: str = ""
        self._next_track: Optional[CdTrackInfo] = None
        self._next_track_index: int = -1

        # 复用音频输出句柄
        self._h_wave: Optional[wintypes.HANDLE] = None

        self.on_position = on_position
        self.on_finished = on_finished
        self.on_error = on_error
        self.on_track_transition = on_track_transition

    def set_next_track(
        self,
        drive_letter: Optional[str],
        track: Optional[CdTrackInfo],
        track_index: int = -1,
    ):
        """预设下一轨信息，供读入线程在当前曲目接近尾声时无缝预读下一轨首段扇区"""
        with self._lock:
            self._next_drive = drive_letter or ""
            self._next_track = track
            self._next_track_index = track_index

    def load_and_play(self, drive_letter: str, track: CdTrackInfo, track_index: int = 0):
        with self._lock:
            self._session_id += 1
            session_id = self._session_id
            self._drive_letter = drive_letter
            self._track = track
            self._current_track_index = track_index
            self._current_lba = track.start_lba
            self._is_playing = True
            self._is_active = True
            self._seek_reset_pending = False
            self._seek_generation = 0
            self._pause_change_pending = None
            self._volume_dirty = True

            # 清空上一轮预读队列
            while not self._read_ahead_queue.empty():
                try:
                    self._read_ahead_queue.get_nowait()
                except Exception:
                    break

        t = threading.Thread(
            target=self._run_loop,
            args=(session_id, drive_letter, track, track_index),
            daemon=True,
        )
        t.start()

    def set_paused(self, paused: bool):
        with self._lock:
            self._is_playing = not paused
            self._pause_change_pending = paused

    def is_playing(self) -> bool:
        with self._lock:
            return self._is_playing and self._is_active

    def is_running(self) -> bool:
        with self._lock:
            return self._is_active

    def seek_seconds(self, seconds: float):
        elapsed = 0.0
        with self._lock:
            if not self._track:
                return
            offset_sectors = int(max(0.0, seconds) * CD_FRAMES_PER_SECOND)
            target_lba = self._track.start_lba + offset_sectors
            self._current_lba = min(max(self._track.start_lba, target_lba), self._track.end_lba - 1)
            self._seek_target_lba = self._current_lba
            self._seek_reset_pending = True
            self._seek_generation += 1
            # 清空预读队列，强制从目标 LBA 重新填充
            while not self._read_ahead_queue.empty():
                try:
                    self._read_ahead_queue.get_nowait()
                except Exception:
                    break
            elapsed = (self._current_lba - self._track.start_lba) / CD_FRAMES_PER_SECOND
        if self.on_position:
            self.on_position(elapsed)

    def set_volume(self, volume_0_to_1: float):
        with self._lock:
            self._volume = max(0.0, min(1.0, volume_0_to_1))
            self._volume_dirty = True
        with self._device_lock:
            if self._h_wave and self._h_wave.value:
                self._apply_hardware_volume(self._h_wave, self._volume)

    def stop(self):
        """非阻塞立即失效当前播放代数令牌，并复位清空输出缓冲"""
        with self._lock:
            self._session_id += 1
            self._is_playing = False
            self._is_active = False
            self._next_track = None
            while not self._read_ahead_queue.empty():
                try:
                    self._read_ahead_queue.get_nowait()
                except Exception:
                    break
        with self._device_lock:
            if self._h_wave and self._h_wave.value:
                try:
                    winmm.waveOutReset(self._h_wave)
                except Exception:
                    pass

    def close(self):
        """彻底关闭并释放 waveOut 硬件句柄"""
        self.stop()
        with self._device_lock:
            if self._h_wave and self._h_wave.value:
                try:
                    winmm.waveOutReset(self._h_wave)
                    winmm.waveOutClose(self._h_wave)
                except Exception:
                    pass
                self._h_wave = None

    @staticmethod
    def _apply_hardware_volume(h_wave, vol_0_to_1: float):
        word = int(max(0.0, min(1.0, vol_0_to_1)) * 0xFFFF) & 0xFFFF
        dw_vol = word | (word << 16)
        winmm.waveOutSetVolume(h_wave, wintypes.DWORD(dw_vol))

    def _reader_loop(
        self,
        my_session_id: int,
        initial_drive: str,
        initial_track: CdTrackInfo,
        initial_index: int,
    ):
        """内存预读线程 (RAM Read-Ahead Worker)：后台连续读取扇区，支持无缝复用句柄平滑跨轨"""
        cur_drv = initial_drive
        cur_trk = initial_track
        cur_idx = initial_index
        cur_lba = initial_track.start_lba
        sectors_per_chunk = 15
        raw_read_buf = ctypes.create_string_buffer(CD_SECTOR_RAW_BYTES * sectors_per_chunk)
        bytes_returned = wintypes.DWORD(0)

        h_drive = None
        current_opened_drive = ""
        wf_reader = None
        local_seek_gen = 0

        def open_media(trk: CdTrackInfo, drv: str):
            nonlocal h_drive, wf_reader, current_opened_drive
            local_fp = getattr(trk, "local_filepath", None)
            if local_fp and os.path.isfile(local_fp):
                if h_drive is not None:
                    try:
                        kernel32.CloseHandle(h_drive)
                    except Exception:
                        pass
                    h_drive = None
                    current_opened_drive = ""
                if wf_reader is not None:
                    try:
                        wf_reader.close()
                    except Exception:
                        pass
                    wf_reader = None
                import wave
                wf_reader = wave.open(local_fp, "rb")
                total_frames = wf_reader.getnframes()
                if total_frames > 0:
                    trk.end_lba = max(1, total_frames // 588)
                    trk.duration_sec = total_frames / 44100.0
            else:
                if wf_reader is not None:
                    try:
                        wf_reader.close()
                    except Exception:
                        pass
                    wf_reader = None
                # 若光盘句柄已打开且盘符相同，则直接复用句柄，避免反复打开造成的硬件中断
                if h_drive is None or current_opened_drive != drv:
                    if h_drive is not None:
                        try:
                            kernel32.CloseHandle(h_drive)
                        except Exception:
                            pass
                        h_drive = None
                    h_drive = WindowsCdDriveManager.open_drive_handle(drv)
                    current_opened_drive = drv

        def push_chunk(chunk: _ReadChunk) -> bool:
            while True:
                with self._lock:
                    if self._session_id != my_session_id:
                        return False
                    if self._seek_generation != chunk.seek_gen:
                        return False
                try:
                    self._read_ahead_queue.put(chunk, timeout=0.08)
                    return True
                except queue.Full:
                    continue

        try:
            open_media(cur_trk, cur_drv)
            consecutive_read_errors = 0

            while True:
                with self._lock:
                    if self._session_id != my_session_id:
                        break
                    cur_seek_gen = self._seek_generation
                    seek_lba = self._seek_target_lba

                if cur_seek_gen != local_seek_gen:
                    local_seek_gen = cur_seek_gen
                    cur_lba = seek_lba
                    if wf_reader is not None:
                        try:
                            target_frame = int(max(0, (cur_lba - cur_trk.start_lba) * 588))
                            total_frames = wf_reader.getnframes()
                            wf_reader.setpos(min(total_frames, target_frame))
                        except Exception:
                            pass

                # 当前曲目是否已读取完毕
                if cur_lba >= cur_trk.end_lba:
                    with self._lock:
                        next_drv = self._next_drive
                        next_trk = self._next_track
                        next_idx = self._next_track_index
                        self._next_track = None

                    if next_trk is not None:
                        # 顺序过渡到下一轨：同一光驱复用句柄，物理连续扇区免寻道
                        cur_drv = next_drv or cur_drv
                        cur_trk = next_trk
                        cur_idx = next_idx
                        cur_lba = next_trk.start_lba
                        open_media(cur_trk, cur_drv)
                        # 压入跨轨边界标记
                        if not push_chunk(
                            _ReadChunk(
                                lba=cur_lba,
                                data=b"",
                                track=cur_trk,
                                track_index=cur_idx,
                                seek_gen=cur_seek_gen,
                                is_track_boundary=True,
                            )
                        ):
                            break
                        continue
                    else:
                        # 全部曲目读取完毕，压入 EOF 标记
                        push_chunk(
                            _ReadChunk(
                                lba=-1,
                                data=b"",
                                track=cur_trk,
                                track_index=cur_idx,
                                seek_gen=cur_seek_gen,
                                is_eof=True,
                            )
                        )
                        break

                # 控制预读上限 (约 5 秒音频)，避免无限制占用内存
                if self._read_ahead_queue.qsize() >= 25:
                    time.sleep(0.02)
                    continue

                chunk_count = min(sectors_per_chunk, cur_trk.end_lba - cur_lba)
                required_bytes = chunk_count * CD_SECTOR_RAW_BYTES

                if wf_reader is not None:
                    try:
                        pcm_bytes = wf_reader.readframes(chunk_count * 588)
                        if len(pcm_bytes) == 0:
                            cur_lba = cur_trk.end_lba
                            continue
                        if len(pcm_bytes) < required_bytes:
                            pcm_bytes = pcm_bytes + b"\x00" * (required_bytes - len(pcm_bytes))
                        consecutive_read_errors = 0
                    except Exception as e:
                        consecutive_read_errors += 1
                        if consecutive_read_errors >= 8:
                            if self.on_error:
                                self.on_error(f"读取本地音频失败: {e}")
                            break
                        pcm_bytes = b"\x00" * required_bytes
                else:
                    if h_drive is None:
                        if self.on_error:
                            self.on_error(f"无法访问光驱设备 {cur_drv}")
                        break
                    raw_info = RAW_READ_INFO()
                    raw_info.DiskOffset = cur_lba * 2048
                    raw_info.SectorCount = chunk_count
                    raw_info.TrackMode = CDDA_TRACK_MODE

                    ok = kernel32.DeviceIoControl(
                        h_drive,
                        IOCTL_CDROM_RAW_READ,
                        ctypes.byref(raw_info),
                        ctypes.sizeof(raw_info),
                        raw_read_buf,
                        required_bytes,
                        ctypes.byref(bytes_returned),
                        None,
                    )
                    if ok and bytes_returned.value > 0:
                        consecutive_read_errors = 0
                        pcm_bytes = raw_read_buf.raw[: bytes_returned.value]
                    else:
                        consecutive_read_errors += 1
                        if consecutive_read_errors >= 8:
                            if self.on_error:
                                self.on_error("光盘已弹出或光驱读取中断")
                            break
                        pcm_bytes = b"\x00" * required_bytes

                if not push_chunk(
                    _ReadChunk(
                        lba=cur_lba,
                        data=pcm_bytes,
                        track=cur_trk,
                        track_index=cur_idx,
                        seek_gen=cur_seek_gen,
                    )
                ):
                    break
                cur_lba += chunk_count
        finally:
            if h_drive is not None:
                try:
                    kernel32.CloseHandle(h_drive)
                except Exception:
                    pass
            if wf_reader is not None:
                try:
                    wf_reader.close()
                except Exception:
                    pass

    def _run_loop(
        self,
        my_session_id: int,
        drive_letter: str,
        track: CdTrackInfo,
        track_index: int = 0,
    ):
        with self._device_lock:
            with self._lock:
                if self._session_id != my_session_id:
                    return

            # 复用已打开的 waveOut 设备句柄，消除跨轨重开设备的爆音与延迟
            if not self._h_wave or not self._h_wave.value:
                wfx = WAVEFORMATEX(
                    wFormatTag=WAVE_FORMAT_PCM,
                    nChannels=2,
                    nSamplesPerSec=44100,
                    nAvgBytesPerSec=44100 * 4,
                    nBlockAlign=4,
                    wBitsPerSample=16,
                    cbSize=0,
                )
                h_wave = wintypes.HANDLE()
                res = winmm.waveOutOpen(ctypes.byref(h_wave), WAVE_MAPPER, ctypes.byref(wfx), 0, 0, 0)
                if res != 0 or not h_wave.value:
                    with self._lock:
                        if self._session_id == my_session_id:
                            self._is_active = False
                            self._is_playing = False
                    if self.on_error:
                        self.on_error(f"无法打开音频输出设备 (错误码 {res})")
                    return
                self._h_wave = h_wave
            else:
                # 复用时仅清空残留缓冲，微秒级准备就绪
                winmm.waveOutReset(self._h_wave)

            h_wave = self._h_wave
            self._apply_hardware_volume(h_wave, self._volume)

            # 启动内存预读工作线程
            t_reader = threading.Thread(
                target=self._reader_loop,
                args=(my_session_id, drive_letter, track, track_index),
                daemon=True,
            )
            t_reader.start()

            num_buffers = 6
            headers = [WAVEHDR() for _ in range(num_buffers)]
            buffers = [None] * num_buffers
            buf_idx = 0
            track_completed_normally = False
            current_playing_track = track
            run_seek_gen = 0

            try:
                while True:
                    with self._lock:
                        if self._session_id != my_session_id:
                            break
                        playing = self._is_playing
                        vol_dirty = self._volume_dirty
                        vol = self._volume
                        self._volume_dirty = False
                        pause_cmd = self._pause_change_pending
                        self._pause_change_pending = None
                        seek_cmd = self._seek_reset_pending
                        self._seek_reset_pending = False
                        cur_seek_gen = self._seek_generation

                    if vol_dirty:
                        self._apply_hardware_volume(h_wave, vol)

                    if cur_seek_gen != run_seek_gen or seek_cmd:
                        run_seek_gen = cur_seek_gen
                        winmm.waveOutReset(h_wave)
                        for h in headers:
                            if h.dwFlags & WHDR_PREPARED:
                                winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(h), ctypes.sizeof(WAVEHDR))
                        if not playing:
                            winmm.waveOutPause(h_wave)

                    if pause_cmd is True:
                        winmm.waveOutPause(h_wave)
                    elif pause_cmd is False:
                        winmm.waveOutRestart(h_wave)

                    if not playing:
                        time.sleep(0.025)
                        continue

                    # 从内存预读缓冲队列取出数据块
                    try:
                        chunk: _ReadChunk = self._read_ahead_queue.get(timeout=0.04)
                    except queue.Empty:
                        continue

                    # 丢弃 Seek 之前残留的旧世代音频块
                    if chunk.seek_gen != run_seek_gen:
                        continue

                    if chunk.is_track_boundary:
                        # 无缝跨轨过渡：平滑推进下一轨，绝不关闭与重开音频设备句柄
                        current_playing_track = chunk.track
                        with self._lock:
                            self._track = chunk.track
                            self._current_track_index = chunk.track_index
                            self._current_lba = chunk.track.start_lba
                        if self.on_track_transition:
                            self.on_track_transition(chunk.track, chunk.track_index)
                        continue

                    if chunk.is_eof:
                        # 限时等待最后几个缓冲块在硬件中输出完毕
                        wait_start = time.time()
                        while time.time() - wait_start < 1.0:
                            with self._lock:
                                if self._session_id != my_session_id:
                                    break
                            all_done = all(
                                (not (h.dwFlags & WHDR_PREPARED)) or bool(h.dwFlags & WHDR_DONE)
                                for h in headers
                            )
                            if all_done:
                                break
                            time.sleep(0.02)
                        with self._lock:
                            if self._session_id == my_session_id:
                                track_completed_normally = True
                                self._is_active = False
                                self._is_playing = False
                        break

                    hdr = headers[buf_idx]
                    if hdr.dwFlags & WHDR_PREPARED:
                        if not (hdr.dwFlags & WHDR_DONE):
                            # 等待前一个缓冲播放完毕
                            spin_start = time.time()
                            while not (hdr.dwFlags & WHDR_DONE) and time.time() - spin_start < 0.25:
                                time.sleep(0.008)
                        winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

                    pcm_bytes = chunk.data
                    c_buf = ctypes.create_string_buffer(pcm_bytes, len(pcm_bytes))
                    buffers[buf_idx] = c_buf

                    hdr.lpData = ctypes.cast(c_buf, ctypes.c_char_p)
                    hdr.dwBufferLength = len(pcm_bytes)
                    hdr.dwFlags = 0
                    hdr.dwLoops = 0

                    winmm.waveOutPrepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                    winmm.waveOutWrite(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

                    buf_idx = (buf_idx + 1) % num_buffers

                    chunk_sec_count = len(pcm_bytes) // CD_SECTOR_RAW_BYTES
                    with self._lock:
                        if self._session_id != my_session_id:
                            break
                        self._current_lba = chunk.lba + chunk_sec_count
                        elapsed_sec = max(0.0, (self._current_lba - current_playing_track.start_lba) / CD_FRAMES_PER_SECOND)

                    if self.on_position:
                        self.on_position(elapsed_sec)
            finally:
                winmm.waveOutReset(h_wave)
                for hdr in headers:
                    if hdr.dwFlags & WHDR_PREPARED:
                        winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

        if track_completed_normally and self.on_finished:
            self.on_finished()
