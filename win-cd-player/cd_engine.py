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
        if len(payload) < 18:
            return "", "", {}, {}

        title_buffers: Dict[int, bytearray] = {}
        artist_buffers: Dict[int, bytearray] = {}

        for offset in range(0, len(payload) - 17, 18):
            pack = payload[offset : offset + 18]
            pack_type = pack[0]
            trk_no = pack[1] & 0x7F
            is_double_byte = bool(pack[3] & 0x80)
            text_chunk = pack[4:16]

            if pack_type not in (0x80, 0x81) or is_double_byte:
                continue

            target_map = title_buffers if pack_type == 0x80 else artist_buffers
            cur_trk = trk_no
            for b in text_chunk:
                if b == 0:
                    cur_trk += 1
                else:
                    target_map.setdefault(cur_trk, bytearray()).append(b)

        def decode_bytes(b_arr: bytearray) -> str:
            for enc in ("utf-8", "shift_jis", "gbk", "latin1"):
                try:
                    return bytes(b_arr).decode(enc).strip()
                except UnicodeDecodeError:
                    continue
            return ""

        titles = {k: decode_bytes(v) for k, v in title_buffers.items() if decode_bytes(v)}
        artists = {k: decode_bytes(v) for k, v in artist_buffers.items() if decode_bytes(v)}
        album_title = titles.pop(0, "")
        album_artist = artists.pop(0, "")
        return album_title, album_artist, titles, artists

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
class CddaWaveOutPlayer:
    """
    基于代数令牌 (_session_id) 的非阻塞 CDDA 扇区流播放器：
    - 切换曲目、暂停、拖拽进度或停止时绝不阻塞调用方线程 (无 thread.join 死锁)
    - 使用原生 C 级 winmm.waveOutSetVolume / waveOutPause / waveOutRestart 实现微秒级控制
    """

    def __init__(
        self,
        on_position: Optional[Callable[[float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self._lock = threading.Lock()
        self._device_lock = threading.Lock()
        self._session_id: int = 0
        self._drive_letter: str = ""
        self._track: Optional[CdTrackInfo] = None
        self._current_lba: int = 0
        self._is_playing: bool = False
        self._is_active: bool = False
        self._seek_reset_pending: bool = False
        self._pause_change_pending: Optional[bool] = None
        self._volume: float = 0.80
        self._volume_dirty: bool = True

        self.on_position = on_position
        self.on_finished = on_finished
        self.on_error = on_error

    def load_and_play(self, drive_letter: str, track: CdTrackInfo):
        with self._lock:
            self._session_id += 1
            session_id = self._session_id
            self._drive_letter = drive_letter
            self._track = track
            self._current_lba = track.start_lba
            self._is_playing = True
            self._is_active = True
            self._seek_reset_pending = False
            self._pause_change_pending = None
            self._volume_dirty = True

        t = threading.Thread(
            target=self._run_loop,
            args=(session_id, drive_letter, track),
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
            self._seek_reset_pending = True
            elapsed = (self._current_lba - self._track.start_lba) / CD_FRAMES_PER_SECOND
        if self.on_position:
            self.on_position(elapsed)

    def set_volume(self, volume_0_to_1: float):
        with self._lock:
            self._volume = max(0.0, min(1.0, volume_0_to_1))
            self._volume_dirty = True

    def stop(self):
        """非阻塞立即失效当前播放代数令牌，旧线程自动在毫秒内复位释放硬件句柄"""
        with self._lock:
            self._session_id += 1
            self._is_playing = False
            self._is_active = False

    @staticmethod
    def _apply_hardware_volume(h_wave, vol_0_to_1: float):
        word = int(max(0.0, min(1.0, vol_0_to_1)) * 0xFFFF) & 0xFFFF
        dw_vol = word | (word << 16)
        winmm.waveOutSetVolume(h_wave, wintypes.DWORD(dw_vol))

    def _run_loop(self, my_session_id: int, drive_letter: str, track: CdTrackInfo):
        with self._device_lock:
            with self._lock:
                if self._session_id != my_session_id:
                    return

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

            # 初始化即时应用硬件音量
            self._apply_hardware_volume(h_wave, self._volume)

            local_wav_fp = getattr(track, "local_filepath", None)
            is_local_wav = bool(local_wav_fp and os.path.isfile(local_wav_fp))
            wf_reader = None

            if is_local_wav:
                try:
                    import wave
                    wf_reader = wave.open(local_wav_fp, "rb")
                    h_drive = None
                    total_frames = wf_reader.getnframes()
                    if total_frames > 0:
                        track_lba = total_frames // 588
                        track.end_lba = max(1, track_lba)
                        track.duration_sec = total_frames / 44100.0

                        # 探测本地音频是否为静音全零（如老版本未限制USB传输上限导致的空白抓轨）
                        sample_check_bytes = wf_reader.readframes(min(44100 * 2, total_frames))
                        wf_reader.setpos(0)
                        if sample_check_bytes and not any(b != 0 for b in sample_check_bytes):
                            if self.on_error:
                                self.on_error("提示：该本地曲目数据为全静音，请放入原CD使用新版抓轨重试")
                except Exception as e:
                    winmm.waveOutClose(h_wave)
                    with self._lock:
                        if self._session_id == my_session_id:
                            self._is_active = False
                            self._is_playing = False
                    if self.on_error:
                        self.on_error(f"无法读取本地音频文件: {e}")
                    return
            else:
                h_drive = WindowsCdDriveManager.open_drive_handle(drive_letter)
                if h_drive is None:
                    winmm.waveOutClose(h_wave)
                    with self._lock:
                        if self._session_id == my_session_id:
                            self._is_active = False
                            self._is_playing = False
                    if self.on_error:
                        self.on_error(f"无法访问光驱设备 {drive_letter}")
                    return

            sectors_per_chunk = 15  # 0.2 秒/块
            num_buffers = 4
            headers = [WAVEHDR() for _ in range(num_buffers)]
            buffers = [None] * num_buffers
            raw_read_buf = ctypes.create_string_buffer(CD_SECTOR_RAW_BYTES * sectors_per_chunk)
            bytes_returned = wintypes.DWORD(0)
            buf_idx = 0
            consecutive_read_errors = 0
            track_completed_normally = False

            try:
                while True:
                    with self._lock:
                        if self._session_id != my_session_id:
                            break
                        playing = self._is_playing
                        cur_lba = self._current_lba
                        end_lba = track.end_lba
                        start_lba = track.start_lba
                        vol_dirty = self._volume_dirty
                        vol = self._volume
                        self._volume_dirty = False
                        pause_cmd = self._pause_change_pending
                        self._pause_change_pending = None
                        seek_cmd = self._seek_reset_pending
                        self._seek_reset_pending = False

                    if vol_dirty:
                        self._apply_hardware_volume(h_wave, vol)

                    if seek_cmd:
                        winmm.waveOutReset(h_wave)
                        if is_local_wav and wf_reader is not None:
                            try:
                                target_frame = int(max(0, (cur_lba - start_lba) * 588))
                                total_frames = wf_reader.getnframes()
                                wf_reader.setpos(min(total_frames, target_frame))
                            except Exception:
                                pass
                        if not playing:
                            winmm.waveOutPause(h_wave)

                    if pause_cmd is True:
                        winmm.waveOutPause(h_wave)
                    elif pause_cmd is False:
                        winmm.waveOutRestart(h_wave)

                    if not playing:
                        time.sleep(0.025)
                        continue

                    if cur_lba >= end_lba:
                        # 限时等待剩余缓冲播放完成，同时随时响应新曲目切换
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
                            time.sleep(0.012)
                            continue
                        winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

                    chunk_count = min(sectors_per_chunk, end_lba - cur_lba)
                    required_bytes = chunk_count * CD_SECTOR_RAW_BYTES

                    if is_local_wav and wf_reader is not None:
                        try:
                            pcm_bytes = wf_reader.readframes(chunk_count * 588)
                            if len(pcm_bytes) == 0:
                                # 文件读取完毕
                                with self._lock:
                                    self._current_lba = end_lba
                                cur_lba = end_lba
                                continue
                            if len(pcm_bytes) < required_bytes:
                                pcm_bytes = pcm_bytes + b"\x00" * (required_bytes - len(pcm_bytes))
                            consecutive_read_errors = 0
                        except Exception as e:
                            consecutive_read_errors += 1
                            if consecutive_read_errors >= 8:
                                with self._lock:
                                    if self._session_id == my_session_id:
                                        self._is_active = False
                                        self._is_playing = False
                                if self.on_error:
                                    self.on_error(f"读取本地音频失败: {e}")
                                break
                            pcm_bytes = b"\x00" * required_bytes
                    else:
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
                                with self._lock:
                                    if self._session_id == my_session_id:
                                        self._is_active = False
                                        self._is_playing = False
                                if self.on_error:
                                    self.on_error("光盘已弹出或光驱读取中断")
                                break
                            pcm_bytes = b"\x00" * required_bytes

                    c_buf = ctypes.create_string_buffer(pcm_bytes, len(pcm_bytes))
                    buffers[buf_idx] = c_buf

                    hdr.lpData = ctypes.cast(c_buf, ctypes.c_char_p)
                    hdr.dwBufferLength = len(pcm_bytes)
                    hdr.dwFlags = 0
                    hdr.dwLoops = 0

                    winmm.waveOutPrepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                    winmm.waveOutWrite(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))

                    buf_idx = (buf_idx + 1) % num_buffers

                    with self._lock:
                        if self._session_id != my_session_id:
                            break
                        if self._current_lba == cur_lba:
                            self._current_lba += chunk_count
                        elapsed_sec = (self._current_lba - start_lba) / CD_FRAMES_PER_SECOND

                    if self.on_position:
                        self.on_position(elapsed_sec)
            finally:
                winmm.waveOutReset(h_wave)
                for hdr in headers:
                    if hdr.dwFlags & WHDR_PREPARED:
                        winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                winmm.waveOutClose(h_wave)
                if h_drive is not None:
                    kernel32.CloseHandle(h_drive)
                if wf_reader is not None:
                    try:
                        wf_reader.close()
                    except Exception:
                        pass

        if track_completed_normally and self.on_finished:
            self.on_finished()
