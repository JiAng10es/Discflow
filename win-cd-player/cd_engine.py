# -*- coding: utf-8 -*-
"""
cd_engine.py
Discflow 底层光驱硬件通信、CD-TEXT 读取、MusicBrainz/FreeDB DiscID 计算
以及基于 Win32 winmm.dll (waveOut) 的非阻塞、零延迟 CDDA 原始 PCM 扇区流播放引擎。
"""

import base64
import collections
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


TIME_BYTES = 0x0004


class _MMTIME_U(ctypes.Union):
    _fields_ = [
        ("ms", wintypes.DWORD),
        ("sample", wintypes.DWORD),
        ("cb", wintypes.DWORD),
        ("ticks", wintypes.DWORD),
        ("smpte_pad", ctypes.c_ubyte * 8),
    ]


class MMTIME(ctypes.Structure):
    _fields_ = [
        ("wType", wintypes.UINT),
        ("u", _MMTIME_U),
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

winmm.waveOutGetPosition.argtypes = [wintypes.HANDLE, ctypes.POINTER(MMTIME), wintypes.UINT]
winmm.waveOutGetPosition.restype = wintypes.UINT


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
    def open_drive_handle(drive_letter: str, read_access: bool = True, allow_zero_fallback: bool = True):
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
        if allow_zero_fallback and (handle == INVALID_HANDLE_VALUE or handle is None or handle == 0):
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

        # 依据 MMC/Red Book CD-TEXT 规范过滤扩展标志 (pack[1] & 0x80) 与多语言 Block (pack[3] >> 4 & 0x07)
        available_blocks = set()
        for offset in range(0, len(payload) - 17, 18):
            pack = payload[offset : offset + 18]
            if pack[0] in (0x80, 0x81) and not (pack[1] & 0x80):
                available_blocks.add((pack[3] >> 4) & 0x07)
        if not available_blocks:
            return "", "", {}, {}
        primary_block = 0 if 0 in available_blocks else min(available_blocks)

        title_buffers: Dict[int, bytearray] = {}
        artist_buffers: Dict[int, bytearray] = {}
        double_byte_tracks = set()

        for offset in range(0, len(payload) - 17, 18):
            pack = payload[offset : offset + 18]
            pack_type = pack[0]
            if pack[1] & 0x80:
                continue
            block_num = (pack[3] >> 4) & 0x07
            if block_num != primary_block:
                continue
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

        handle = cls.open_drive_handle(drive_letter, read_access=True, allow_zero_fallback=False)
        if handle is None:
            handle = cls.open_drive_handle(drive_letter, read_access=False, allow_zero_fallback=True)
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
            if not ok or bytes_returned.value < 4:
                return None

            first_track = int(toc.FirstTrack)
            last_track = int(toc.LastTrack)
            if last_track < first_track or first_track < 1 or last_track > 99:
                return None

            num_tracks = last_track - first_track + 1
            if num_tracks <= 0 or num_tracks > 99:
                return None
            required_toc_bytes = 4 + (num_tracks + 1) * ctypes.sizeof(TRACK_DATA)
            if bytes_returned.value < required_toc_bytes:
                return None
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
    is_error: bool = False
    error_msg: str = ""


class _LocalAudioReader:
    """统一本地无损音频流读取器：优先通过 soundfile/libsndfile 读取 WAV 与 FLAC（自动支持 8/16/24/32-bit 与 float -> int16），仅在 16-bit WAV 时允许回退标准库 wave。"""

    TARGET_SAMPLE_RATE = 44100

    def __init__(self, filepath: str):
        self.filepath = filepath
        self._wave = None
        self._sf = None
        self._total_frames = 0
        self._channels = 2
        self._samplerate = self.TARGET_SAMPLE_RATE
        try:
            import soundfile as sf
            self._sf = sf.SoundFile(filepath, mode="r")
            self._total_frames = int(len(self._sf))
            self._channels = int(self._sf.channels or 2)
            self._samplerate = int(self._sf.samplerate or self.TARGET_SAMPLE_RATE)
        except Exception:
            self._sf = None
            if filepath.lower().endswith(".wav"):
                import wave
                wf = wave.open(filepath, "rb")
                if int(wf.getsampwidth()) != 2:
                    wf.close()
                    raise ValueError(f"不支持的 WAV 位深（仅回退支持 16-bit PCM，实际 {wf.getsampwidth() * 8}-bit）")
                self._wave = wf
                self._total_frames = int(self._wave.getnframes())
                self._channels = int(self._wave.getnchannels() or 2)
                self._samplerate = int(self._wave.getframerate() or self.TARGET_SAMPLE_RATE)
            else:
                raise

    @property
    def samplerate(self) -> int:
        return self._samplerate

    def getnframes(self) -> int:
        return self._total_frames

    def setpos(self, frame: int) -> None:
        if self._samplerate != self.TARGET_SAMPLE_RATE and self._samplerate > 0:
            scaled = int(round(frame * self._samplerate / float(self.TARGET_SAMPLE_RATE)))
        else:
            scaled = int(frame)
        pos = max(0, min(self._total_frames, scaled))
        if self._wave is not None:
            self._wave.setpos(pos)
        elif self._sf is not None:
            self._sf.seek(pos)

    @staticmethod
    def _resample_int16_stereo(arr, target_frames: int):
        import numpy as np
        src_frames = arr.shape[0]
        if src_frames <= 0 or target_frames <= 0 or src_frames == target_frames:
            return arr
        x_old = np.linspace(0.0, 1.0, num=src_frames, endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=target_frames, endpoint=False)
        ch0 = np.interp(x_new, x_old, arr[:, 0].astype(np.float32))
        ch1 = np.interp(x_new, x_old, arr[:, 1].astype(np.float32))
        out = np.stack([ch0, ch1], axis=1)
        np.clip(out, -32768.0, 32767.0, out=out)
        return out.astype("<i2")

    def readframes(self, frames: int) -> bytes:
        if frames <= 0:
            return b""
        need_resample = self._samplerate != self.TARGET_SAMPLE_RATE and self._samplerate > 0
        src_frames = (
            max(1, int(round(frames * self._samplerate / float(self.TARGET_SAMPLE_RATE))))
            if need_resample
            else frames
        )
        if self._wave is not None:
            raw = self._wave.readframes(src_frames)
            if not raw:
                return b""
            if not need_resample and self._channels == 2:
                return raw
            import numpy as np
            arr = np.frombuffer(raw, dtype="<i2")
            if self._channels > 1:
                arr = arr.reshape(-1, self._channels)
            else:
                arr = arr.reshape(-1, 1)
            if arr.shape[1] == 1:
                arr = np.repeat(arr, 2, axis=1)
            elif arr.shape[1] > 2:
                arr = arr[:, :2]
            if need_resample:
                actual_src = arr.shape[0]
                target_out = (
                    frames
                    if actual_src == src_frames
                    else max(1, int(round(actual_src * float(self.TARGET_SAMPLE_RATE) / float(self._samplerate))))
                )
                arr = self._resample_int16_stereo(arr, target_out)
            return np.ascontiguousarray(arr, dtype="<i2").tobytes()
        if self._sf is not None:
            import numpy as np
            arr = self._sf.read(src_frames, dtype="int16", always_2d=True)
            if arr.size == 0:
                return b""
            if arr.shape[1] == 1:
                arr = np.repeat(arr, 2, axis=1)
            elif arr.shape[1] > 2:
                arr = arr[:, :2]
            if need_resample:
                actual_src = arr.shape[0]
                target_out = (
                    frames
                    if actual_src == src_frames
                    else max(1, int(round(actual_src * float(self.TARGET_SAMPLE_RATE) / float(self._samplerate))))
                )
                arr = self._resample_int16_stereo(arr, target_out)
            return np.ascontiguousarray(arr, dtype="<i2").tobytes()
        return b""

    def close(self) -> None:
        if self._wave is not None:
            try:
                self._wave.close()
            except Exception:
                pass
            self._wave = None
        if self._sf is not None:
            try:
                self._sf.close()
            except Exception:
                pass
            self._sf = None


class CddaWaveOutPlayer:
    """
    基于代数令牌 (_session_id) 的非阻塞 CDDA 扇区流播放引擎 (v2.4.0 升级):
    - 连续读取缓冲队列 (RAM Read-Ahead): 预读 3~5 秒原始 PCM 扇区，消除光驱频繁寻道机械噪音
    - 跨轨平滑无缝过渡 (Gapless Playback): 跨轨复用音频输出句柄与底层读取句柄
    - 支持 WAV / FLAC 本地无损文件直读及 waveOut / WASAPI 共享 / WASAPI 独占直通三种输出模式
    """

    VALID_OUTPUT_MODES = ("waveout", "wasapi_shared", "wasapi_exclusive")

    def __init__(
        self,
        on_position: Optional[Callable[[float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_track_transition: Optional[Callable[[CdTrackInfo, int], None]] = None,
        on_output_fallback: Optional[Callable[[str], None]] = None,
    ):
        self._lock = threading.Lock()
        self._device_lock = threading.Lock()
        self._wave_handle_lock = threading.Lock()
        self._session_id: int = 0
        self._drive_letter: str = ""
        self._track: Optional[CdTrackInfo] = None
        self._current_track_index: int = 0
        self._output_mode: str = "waveout"
        self.on_output_fallback = on_output_fallback
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

        # 复用音频输出句柄与读线程空闲信号
        self._h_wave: Optional[wintypes.HANDLE] = None
        self._active_readers: int = 0
        self._reader_idle_event = threading.Event()
        self._reader_idle_event.set()
        self._session_done_event: Optional[threading.Event] = None

        self.on_position = on_position
        self.on_finished = on_finished
        self.on_error = on_error
        self.on_track_transition = on_track_transition

    def set_output_mode(self, mode: str) -> str:
        norm = str(mode or "").strip().lower()
        if norm not in self.VALID_OUTPUT_MODES:
            norm = "waveout"
        with self._lock:
            self._output_mode = norm
        return norm

    def get_output_mode(self) -> str:
        with self._lock:
            return self._output_mode

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
        read_q: queue.Queue = queue.Queue(maxsize=25)
        done_event = threading.Event()
        with self._lock:
            self._session_id += 1
            session_id = self._session_id
            prev_done_event = self._session_done_event
            self._session_done_event = done_event
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
            self._read_ahead_queue = read_q

        # 若上一会话正在 waveOut 阻塞等待中，先立即复位唤醒其退出
        with self._wave_handle_lock:
            if self._h_wave and self._h_wave.value:
                try:
                    winmm.waveOutReset(self._h_wave)
                except Exception:
                    pass

        t = threading.Thread(
            target=self._run_loop,
            args=(session_id, drive_letter, track, track_index, read_q, prev_done_event, done_event),
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
            max_lba = max(self._track.start_lba, self._track.end_lba - 1)
            self._current_lba = max(self._track.start_lba, min(target_lba, max_lba))
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
            vol = self._volume
            self._volume_dirty = True
        with self._wave_handle_lock:
            if self._h_wave and self._h_wave.value:
                self._apply_hardware_volume(self._h_wave, vol)

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
        with self._wave_handle_lock:
            if self._h_wave and self._h_wave.value:
                try:
                    winmm.waveOutReset(self._h_wave)
                except Exception:
                    pass

    def wait_reader_idle(self, timeout: float = 2.0) -> bool:
        """等待后台读盘线程彻底退出并关闭光驱句柄，避免抓轨时与播放预读争抢硬件。"""
        return self._reader_idle_event.wait(timeout=max(0.0, float(timeout)))

    def close(self):
        """彻底关闭并释放 waveOut 硬件句柄"""
        self.stop()
        with self._lock:
            done_evt = self._session_done_event
        if done_evt is not None:
            done_evt.wait(timeout=2.0)
        with self._wave_handle_lock:
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
        read_q: Optional[queue.Queue] = None,
        pre_registered: bool = False,
    ):
        """内存预读线程 (RAM Read-Ahead Worker)：后台连续读取扇区，支持无缝复用句柄平滑跨轨"""
        target_q = read_q if read_q is not None else self._read_ahead_queue
        with self._lock:
            if not pre_registered:
                self._active_readers += 1
            self._reader_idle_event.clear()

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
        rollback_next = None

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
                wf_reader = _LocalAudioReader(local_fp)
                total_frames = wf_reader.getnframes()
                if total_frames > 0:
                    sr = float(wf_reader.samplerate or 44100)
                    dur = total_frames / sr
                    trk.duration_sec = dur
                    trk.end_lba = max(1, int(round(dur * CD_FRAMES_PER_SECOND)))
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

        def push_chunk(chunk: _ReadChunk, ignore_seek: bool = False) -> str:
            while True:
                with self._lock:
                    if self._session_id != my_session_id:
                        return "stopped"
                    if not ignore_seek and self._seek_generation != chunk.seek_gen:
                        return "stale_seek"
                try:
                    target_q.put(chunk, timeout=0.08)
                    return "ok"
                except queue.Full:
                    continue

        def push_error_sentinel(msg: str):
            with self._lock:
                cur_gen = self._seek_generation
            push_chunk(
                _ReadChunk(
                    lba=-1,
                    data=b"",
                    track=cur_trk,
                    track_index=cur_idx,
                    seek_gen=cur_gen,
                    is_error=True,
                    error_msg=msg,
                ),
                ignore_seek=True,
            )

        try:
            open_media(cur_trk, cur_drv)
            consecutive_read_errors = 0

            while True:
                with self._lock:
                    if self._session_id != my_session_id:
                        break
                    cur_seek_gen = self._seek_generation
                    seek_lba = self._seek_target_lba
                    active_track = self._track
                    active_idx = self._current_track_index
                    active_drv = self._drive_letter

                if active_track is cur_trk:
                    rollback_next = None

                if cur_seek_gen != local_seek_gen:
                    local_seek_gen = cur_seek_gen
                    # 若在跨轨预读窗口内（播放仍在上一轨）触发了 Seek，回滚预读曲目并恢复 next_track
                    if active_track is not None and active_track is not cur_trk:
                        with self._lock:
                            if (
                                self._next_track is None
                                and rollback_next is not None
                                and rollback_next[1] is not active_track
                            ):
                                self._next_drive, self._next_track, self._next_track_index = rollback_next
                        rollback_next = None
                        cur_drv = active_drv or cur_drv
                        cur_trk = active_track
                        cur_idx = active_idx
                        open_media(cur_trk, cur_drv)
                    cur_lba = seek_lba
                    if wf_reader is not None:
                        try:
                            target_frame = int(max(0, (cur_lba - cur_trk.start_lba) * 588))
                            wf_reader.setpos(target_frame)
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
                        prev_state = (cur_drv, cur_trk, cur_idx, cur_lba)
                        rollback_next = (next_drv or cur_drv, next_trk, next_idx)
                        cur_drv = next_drv or cur_drv
                        cur_trk = next_trk
                        cur_idx = next_idx
                        cur_lba = next_trk.start_lba
                        open_media(cur_trk, cur_drv)
                        # 压入跨轨边界标记
                        push_res = push_chunk(
                            _ReadChunk(
                                lba=cur_lba,
                                data=b"",
                                track=cur_trk,
                                track_index=cur_idx,
                                seek_gen=cur_seek_gen,
                                is_track_boundary=True,
                            )
                        )
                        if push_res == "stopped":
                            break
                        if push_res == "stale_seek":
                            # 跨轨瞬间触发 Seek，立即回滚到原轨并保留下一轨供稍后再次预读
                            cur_drv, cur_trk, cur_idx, cur_lba = prev_state
                            with self._lock:
                                if self._next_track is None and rollback_next is not None:
                                    self._next_drive, self._next_track, self._next_track_index = rollback_next
                            rollback_next = None
                            open_media(cur_trk, cur_drv)
                            continue
                        continue
                    else:
                        # 全部曲目读取完毕，压入 EOF 标记
                        push_res = push_chunk(
                            _ReadChunk(
                                lba=-1,
                                data=b"",
                                track=cur_trk,
                                track_index=cur_idx,
                                seek_gen=cur_seek_gen,
                                is_eof=True,
                            )
                        )
                        if push_res == "stale_seek":
                            continue
                        if push_res == "stopped":
                            break
                        # 保持读线程待命直至会话结束或播放完成；若在最后 5 秒内用户向后 Seek 则立即唤醒恢复读取
                        resume_reading = False
                        while True:
                            with self._lock:
                                if self._session_id != my_session_id or not self._is_active:
                                    break
                                if self._seek_generation != local_seek_gen or self._next_track is not None:
                                    resume_reading = True
                                    break
                            time.sleep(0.03)
                        if resume_reading:
                            continue
                        break

                # 控制预读上限 (约 5 秒音频)，避免无限制占用内存
                if target_q.qsize() >= 25:
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
                            push_error_sentinel(f"读取本地音频失败: {e}")
                            break
                        pcm_bytes = b"\x00" * required_bytes
                else:
                    if h_drive is None:
                        push_error_sentinel(f"无法访问光驱设备 {cur_drv}")
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
                            push_error_sentinel("光盘已弹出或光驱读取中断")
                            break
                        pcm_bytes = b"\x00" * required_bytes

                push_res = push_chunk(
                    _ReadChunk(
                        lba=cur_lba,
                        data=pcm_bytes,
                        track=cur_trk,
                        track_index=cur_idx,
                        seek_gen=cur_seek_gen,
                    )
                )
                if push_res == "stopped":
                    break
                if push_res == "stale_seek":
                    continue
                cur_lba += chunk_count
        except Exception as e:
            push_error_sentinel(f"音频读取线程异常: {e}")
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
            with self._lock:
                self._active_readers = max(0, self._active_readers - 1)
                if self._active_readers == 0:
                    self._reader_idle_event.set()

    @staticmethod
    def _scale_pcm16_volume(pcm_bytes: bytes, vol_0_to_1: float) -> bytes:
        """软件音量缩放（vol >= 0.999 时原样返回，保持 Bit-Perfect 零修改直出）"""
        if vol_0_to_1 >= 0.999 or not pcm_bytes:
            return pcm_bytes
        if vol_0_to_1 <= 0.0005:
            return b"\x00" * len(pcm_bytes)
        import numpy as np
        samples = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32)
        samples *= float(vol_0_to_1)
        np.clip(samples, -32768.0, 32767.0, out=samples)
        return samples.astype("<i2").tobytes()

    def _open_wasapi_stream(
        self,
        exclusive: bool,
        callback: Optional[Callable] = None,
        fallback_sink: Optional[List[str]] = None,
    ):
        """打开 Windows WASAPI 输出流（优先回调模式）；独占模式失败时自动回退共享模式（auto_convert=True）。"""
        import sounddevice as sd

        wasapi_api_idx = None
        default_dev = None
        for idx, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in str(api.get("name", "")).upper():
                wasapi_api_idx = idx
                default_dev = api.get("default_output_device", -1)
                break
        if wasapi_api_idx is None or default_dev is None or default_dev < 0:
            raise RuntimeError("系统未检测到可用的 Windows WASAPI 音频设备")

        if exclusive:
            excl_stream = None
            try:
                excl_stream = sd.RawOutputStream(
                    device=default_dev,
                    samplerate=44100,
                    channels=2,
                    dtype="int16",
                    latency="low",
                    callback=callback,
                    extra_settings=sd.WasapiSettings(exclusive=True),
                )
                excl_stream.start()
                return excl_stream, True
            except Exception as ex:
                if excl_stream is not None:
                    try:
                        excl_stream.close()
                    except Exception:
                        pass
                msg = f"当前音频设备不支持 WASAPI 独占模式或已被占用（{ex}），已自动回退至 WASAPI 共享模式"
                if fallback_sink is not None:
                    fallback_sink.append(msg)
                elif self.on_output_fallback:
                    self.on_output_fallback(msg)

        stream = sd.RawOutputStream(
            device=default_dev,
            samplerate=44100,
            channels=2,
            dtype="int16",
            latency="low",
            callback=callback,
            extra_settings=sd.WasapiSettings(exclusive=False, auto_convert=True),
        )
        stream.start()
        return stream, False

    def _run_wasapi_loop(
        self,
        my_session_id: int,
        drive_letter: str,
        track: CdTrackInfo,
        track_index: int,
        exclusive: bool,
        read_q: Optional[queue.Queue] = None,
    ) -> bool:
        """
        使用 Windows WASAPI（共享/独占回调流）播放音频流。
        若 WASAPI 流成功打开并接管本轮播放则返回 True；若设备打开失败需回退 waveOut 则返回 False。
        """
        import sounddevice as sd

        target_q = read_q if read_q is not None else self._read_ahead_queue
        with self._lock:
            if self._session_id != my_session_id:
                return True

        # 若之前残留了 waveOut 句柄，先彻底关闭避免与 WASAPI 独占模式争抢声卡
        with self._wave_handle_lock:
            if self._h_wave and self._h_wave.value:
                try:
                    winmm.waveOutReset(self._h_wave)
                    winmm.waveOutClose(self._h_wave)
                except Exception:
                    pass
                self._h_wave = None

        cb_lock = threading.Lock()
        cb_items: collections.deque = collections.deque()
        cb_buffered_bytes = 0
        cb_max_bytes = 35280  # ~0.2s 回调环形缓冲，兼顾低延迟与抗抖动
        cb_paused = False
        cb_eof_queued = False
        cb_eof_drained = False
        cb_error_msg = ""
        cb_played_bytes = 0
        cb_current_lba = track.start_lba
        cb_transitions: collections.deque = collections.deque()

        def audio_callback(outdata, frames, time_info, status):
            nonlocal cb_buffered_bytes, cb_eof_drained, cb_error_msg
            nonlocal cb_played_bytes, cb_current_lba
            needed = len(outdata)
            written = 0
            try:
                with self._lock:
                    session_ok = (self._session_id == my_session_id)
                    playing_now = self._is_playing
                with cb_lock:
                    if not session_ok:
                        outdata[:] = b"\x00" * needed
                        raise sd.CallbackAbort()
                    if cb_paused or not playing_now:
                        outdata[:] = b"\x00" * needed
                        return
                    while written < needed and cb_items:
                        head = cb_items[0]
                        kind = head[0]
                        if kind == "boundary":
                            cb_items.popleft()
                            _, b_trk, b_idx = head
                            cb_played_bytes = 0
                            cb_current_lba = b_trk.start_lba
                            cb_transitions.append((b_trk, b_idx))
                            continue
                        if kind == "eof":
                            cb_items.popleft()
                            cb_eof_drained = True
                            break
                        data = head[1]
                        off = head[2]
                        avail = len(data) - off
                        take = min(needed - written, avail)
                        outdata[written : written + take] = data[off : off + take]
                        written += take
                        cb_buffered_bytes = max(0, cb_buffered_bytes - take)
                        cb_played_bytes += take
                        new_off = off + take
                        cb_current_lba = head[3] + (new_off // CD_SECTOR_RAW_BYTES)
                        if new_off >= len(data):
                            cb_items.popleft()
                        else:
                            head[2] = new_off
                    if written < needed:
                        outdata[written:needed] = b"\x00" * (needed - written)
                    if cb_eof_drained and not cb_items:
                        raise sd.CallbackStop()
            except (sd.CallbackStop, sd.CallbackAbort):
                raise
            except Exception as cb_ex:
                cb_error_msg = f"WASAPI 音频输出回调异常: {cb_ex}"
                with self._lock:
                    if self._session_id == my_session_id:
                        self._is_active = False
                        self._is_playing = False
                try:
                    outdata[:] = b"\x00" * needed
                except Exception:
                    pass
                raise sd.CallbackAbort()

        fallback_msgs: List[str] = []
        try:
            with self._device_lock:
                stream, _actual_exclusive = self._open_wasapi_stream(
                    exclusive=exclusive,
                    callback=audio_callback,
                    fallback_sink=fallback_msgs,
                )
        except Exception as ex:
            if self.on_output_fallback:
                self.on_output_fallback(f"WASAPI 输出打开失败（{ex}），已自动回退至标准兼容模式 (waveOut)")
            return False

        for msg in fallback_msgs:
            if self.on_output_fallback:
                self.on_output_fallback(msg)

        with self._lock:
            self._active_readers += 1
            self._reader_idle_event.clear()
        t_reader = threading.Thread(
            target=self._reader_loop,
            args=(my_session_id, drive_letter, track, track_index, target_q, True),
            daemon=True,
        )
        try:
            t_reader.start()
        except Exception:
            with self._lock:
                self._active_readers = max(0, self._active_readers - 1)
                if self._active_readers == 0:
                    self._reader_idle_event.set()
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass
            raise

        track_completed_normally = False
        error_to_report = ""
        current_playing_track = track
        run_seek_gen = 0
        track_base_sec = 0.0
        stream_paused = False
        latency_sec = float(getattr(stream, "latency", 0.0) or 0.0)

        try:
            while True:
                with self._lock:
                    if self._session_id != my_session_id:
                        break
                    playing = self._is_playing
                    vol = self._volume
                    self._volume_dirty = False
                    pause_cmd = self._pause_change_pending
                    self._pause_change_pending = None
                    seek_cmd = self._seek_reset_pending
                    self._seek_reset_pending = False
                    cur_seek_gen = self._seek_generation

                if cur_seek_gen != run_seek_gen or seek_cmd:
                    run_seek_gen = cur_seek_gen
                    with cb_lock:
                        cb_items.clear()
                        cb_buffered_bytes = 0
                        cb_eof_queued = False
                        cb_eof_drained = False
                        cb_played_bytes = 0
                        cb_transitions.clear()
                        cb_paused = not playing
                    try:
                        stream.abort()
                        if playing:
                            stream.start()
                            stream_paused = False
                        else:
                            stream_paused = True
                    except Exception as ex:
                        error_to_report = f"WASAPI 定位重置失败: {ex}"
                        with self._lock:
                            if self._session_id == my_session_id:
                                self._is_active = False
                                self._is_playing = False
                        break
                    with self._lock:
                        track_base_sec = max(
                            0.0,
                            (self._seek_target_lba - current_playing_track.start_lba) / CD_FRAMES_PER_SECOND,
                        )

                if pause_cmd is True and not stream_paused:
                    with cb_lock:
                        cb_paused = True
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    stream_paused = True
                elif pause_cmd is False and stream_paused:
                    with cb_lock:
                        cb_paused = False
                    try:
                        stream.start()
                    except Exception as ex:
                        error_to_report = f"WASAPI 恢复播放失败: {ex}"
                        with self._lock:
                            if self._session_id == my_session_id:
                                self._is_active = False
                                self._is_playing = False
                        break
                    stream_paused = False

                # 在锁外触发跨轨事件回调
                pending_transitions = []
                with cb_lock:
                    while cb_transitions:
                        pending_transitions.append(cb_transitions.popleft())
                    local_cb_err = cb_error_msg
                    local_eof_drained = cb_eof_drained
                    local_eof_queued = cb_eof_queued
                    cur_played_bytes = cb_played_bytes
                    cur_cb_lba = cb_current_lba
                    cur_buffered = cb_buffered_bytes

                for b_trk, b_idx in pending_transitions:
                    with self._lock:
                        if (
                            self._session_id != my_session_id
                            or self._seek_generation != run_seek_gen
                            or self._seek_reset_pending
                        ):
                            continue
                        current_playing_track = b_trk
                        track_base_sec = 0.0
                        self._track = b_trk
                        self._current_track_index = b_idx
                        self._current_lba = b_trk.start_lba
                    if self.on_track_transition:
                        self.on_track_transition(b_trk, b_idx)

                if local_cb_err:
                    error_to_report = local_cb_err
                    with self._lock:
                        if self._session_id == my_session_id:
                            self._is_active = False
                            self._is_playing = False
                    break

                if local_eof_drained:
                    # 等待 PortAudio 排空硬件尾部缓冲（最多 2.5s，支持 Seek/Stop 打断）
                    drain_deadline = time.time() + 2.5
                    seek_interrupted = False
                    stopped_during_eof = False
                    while True:
                        with self._lock:
                            if self._session_id != my_session_id:
                                stopped_during_eof = True
                                break
                            if self._seek_generation != run_seek_gen or self._seek_reset_pending:
                                seek_interrupted = True
                                break
                        if not getattr(stream, "active", False) or time.time() >= drain_deadline:
                            break
                        time.sleep(0.02)
                    if seek_interrupted:
                        continue
                    if not stopped_during_eof:
                        with self._lock:
                            if self._session_id == my_session_id:
                                track_completed_normally = True
                                self._is_active = False
                                self._is_playing = False
                    break

                if playing and not stream_paused and not local_eof_queued and not getattr(stream, "active", True):
                    error_to_report = "WASAPI 音频流意外中止"
                    with self._lock:
                        if self._session_id == my_session_id:
                            self._is_active = False
                            self._is_playing = False
                    break

                if playing:
                    with self._lock:
                        if self._session_id != my_session_id:
                            break
                        self._current_lba = cur_cb_lba
                    played_sec = max(0.0, cur_played_bytes / 176400.0 - latency_sec)
                    elapsed_sec = max(0.0, track_base_sec + played_sec)
                    if self.on_position:
                        self.on_position(elapsed_sec)

                if not playing or local_eof_queued or cur_buffered >= cb_max_bytes:
                    time.sleep(0.02)
                    continue

                try:
                    chunk: _ReadChunk = target_q.get(timeout=0.04)
                except queue.Empty:
                    continue

                if chunk.is_error:
                    error_to_report = chunk.error_msg or "音频读取失败"
                    with self._lock:
                        if self._session_id == my_session_id:
                            self._is_active = False
                            self._is_playing = False
                    break

                if chunk.seek_gen != run_seek_gen:
                    continue

                if chunk.is_track_boundary:
                    with cb_lock:
                        cb_items.append(("boundary", chunk.track, chunk.track_index))
                    continue

                if chunk.is_eof:
                    with cb_lock:
                        cb_items.append(("eof",))
                        cb_eof_queued = True
                    continue

                out_bytes = self._scale_pcm16_volume(chunk.data, vol)
                with cb_lock:
                    cb_items.append(["pcm", out_bytes, 0, chunk.lba, chunk.track, chunk.track_index])
                    cb_buffered_bytes += len(out_bytes)
        finally:
            try:
                if track_completed_normally:
                    stream.stop()
                else:
                    stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        if error_to_report and self.on_error:
            self.on_error(error_to_report)
        elif track_completed_normally and self.on_finished:
            self.on_finished()
        return True

    def _run_loop(
        self,
        my_session_id: int,
        drive_letter: str,
        track: CdTrackInfo,
        track_index: int = 0,
        read_q: Optional[queue.Queue] = None,
        prev_done_event: Optional[threading.Event] = None,
        done_event: Optional[threading.Event] = None,
    ):
        try:
            if prev_done_event is not None:
                prev_done_event.wait(timeout=2.0)

            target_q = read_q if read_q is not None else self._read_ahead_queue
            with self._lock:
                if self._session_id != my_session_id:
                    return
                out_mode = self._output_mode

            if out_mode in ("wasapi_shared", "wasapi_exclusive"):
                handled = self._run_wasapi_loop(
                    my_session_id,
                    drive_letter,
                    track,
                    track_index,
                    exclusive=(out_mode == "wasapi_exclusive"),
                    read_q=target_q,
                )
                if handled:
                    return

            with self._lock:
                if self._session_id != my_session_id:
                    return
                init_vol = self._volume

            # 复用已打开的 waveOut 设备句柄，消除跨轨重开设备的爆音与延迟
            open_err = 0
            with self._wave_handle_lock:
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
                        open_err = res
                    else:
                        self._h_wave = h_wave
                else:
                    # 复用时仅清空残留缓冲，微秒级准备就绪
                    winmm.waveOutReset(self._h_wave)

                h_wave = self._h_wave
                if not open_err and h_wave and h_wave.value:
                    self._apply_hardware_volume(h_wave, init_vol)

            if open_err or not h_wave or not h_wave.value:
                with self._lock:
                    if self._session_id == my_session_id:
                        self._is_active = False
                        self._is_playing = False
                if self.on_error:
                    self.on_error(f"无法打开音频输出设备 (错误码 {open_err})")
                return

            # 启动内存预读工作线程
            with self._lock:
                self._active_readers += 1
                self._reader_idle_event.clear()
            t_reader = threading.Thread(
                target=self._reader_loop,
                args=(my_session_id, drive_letter, track, track_index, target_q, True),
                daemon=True,
            )
            try:
                t_reader.start()
            except Exception:
                with self._lock:
                    self._active_readers = max(0, self._active_readers - 1)
                    if self._active_readers == 0:
                        self._reader_idle_event.set()
                raise

            num_buffers = 8
            headers = [WAVEHDR() for _ in range(num_buffers)]
            buffers = [None] * num_buffers
            buf_idx = 0
            track_completed_normally = False
            error_to_report = ""
            consecutive_wave_errors = 0
            current_playing_track = track
            run_seek_gen = 0
            submitted_bytes = 0
            track_base_bytes = 0
            track_base_sec = 0.0
            mmt = MMTIME()

            def invalidate_wave_handle():
                with self._wave_handle_lock:
                    if self._h_wave is h_wave and self._h_wave and self._h_wave.value:
                        try:
                            winmm.waveOutReset(h_wave)
                        except Exception:
                            pass
                        for hdr_item in headers:
                            if hdr_item.dwFlags & WHDR_PREPARED:
                                try:
                                    winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr_item), ctypes.sizeof(WAVEHDR))
                                except Exception:
                                    pass
                        try:
                            winmm.waveOutClose(h_wave)
                        except Exception:
                            pass
                        self._h_wave = None

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
                        with self._wave_handle_lock:
                            if self._h_wave is h_wave:
                                self._apply_hardware_volume(h_wave, vol)

                    if cur_seek_gen != run_seek_gen or seek_cmd:
                        run_seek_gen = cur_seek_gen
                        with self._wave_handle_lock:
                            if self._h_wave is h_wave:
                                winmm.waveOutReset(h_wave)
                                for h in headers:
                                    if h.dwFlags & WHDR_PREPARED:
                                        winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(h), ctypes.sizeof(WAVEHDR))
                                if not playing:
                                    winmm.waveOutPause(h_wave)
                        submitted_bytes = 0
                        track_base_bytes = 0
                        with self._lock:
                            track_base_sec = max(0.0, (self._seek_target_lba - current_playing_track.start_lba) / CD_FRAMES_PER_SECOND)

                    if pause_cmd is True:
                        with self._wave_handle_lock:
                            if self._h_wave is h_wave:
                                winmm.waveOutPause(h_wave)
                    elif pause_cmd is False:
                        with self._wave_handle_lock:
                            if self._h_wave is h_wave:
                                winmm.waveOutRestart(h_wave)

                    if not playing:
                        time.sleep(0.025)
                        continue

                    # 从内存预读缓冲队列取出数据块
                    try:
                        chunk: _ReadChunk = target_q.get(timeout=0.04)
                    except queue.Empty:
                        continue

                    if chunk.is_error:
                        error_to_report = chunk.error_msg or "音频读取失败"
                        with self._lock:
                            if self._session_id == my_session_id:
                                self._is_active = False
                                self._is_playing = False
                        break

                    # 丢弃 Seek 之前残留的旧世代音频块
                    if chunk.seek_gen != run_seek_gen:
                        continue

                    if chunk.is_track_boundary:
                        # 无缝跨轨过渡：平滑推进下一轨，绝不关闭与重开音频设备句柄
                        with self._lock:
                            if (
                                self._session_id != my_session_id
                                or self._seek_generation != run_seek_gen
                                or self._seek_reset_pending
                            ):
                                continue
                            current_playing_track = chunk.track
                            track_base_bytes = submitted_bytes
                            track_base_sec = 0.0
                            self._track = chunk.track
                            self._current_track_index = chunk.track_index
                            self._current_lba = chunk.track.start_lba
                        if self.on_track_transition:
                            self.on_track_transition(chunk.track, chunk.track_index)
                        continue

                    if chunk.is_eof:
                        # 等待最后几个缓冲块在硬件中输出完毕（>=2.5s），期间支持暂停/恢复/音量/Seek
                        active_drain_sec = 0.0
                        last_tick = time.time()
                        seeked_during_eof = False
                        stopped_during_eof = False
                        while active_drain_sec < 3.0:
                            now_tick = time.time()
                            dt = max(0.0, now_tick - last_tick)
                            last_tick = now_tick
                            with self._lock:
                                if self._session_id != my_session_id:
                                    stopped_during_eof = True
                                    break
                                if self._seek_generation != run_seek_gen or self._seek_reset_pending:
                                    seeked_during_eof = True
                                    break
                                eof_playing = self._is_playing
                                eof_vol_dirty = self._volume_dirty
                                eof_vol = self._volume
                                self._volume_dirty = False
                                eof_pause = self._pause_change_pending
                                self._pause_change_pending = None
                            if eof_vol_dirty:
                                with self._wave_handle_lock:
                                    if self._h_wave is h_wave:
                                        self._apply_hardware_volume(h_wave, eof_vol)
                            if eof_pause is True:
                                with self._wave_handle_lock:
                                    if self._h_wave is h_wave:
                                        winmm.waveOutPause(h_wave)
                            elif eof_pause is False:
                                with self._wave_handle_lock:
                                    if self._h_wave is h_wave:
                                        winmm.waveOutRestart(h_wave)
                            all_done = all(
                                (not (h.dwFlags & WHDR_PREPARED)) or bool(h.dwFlags & WHDR_DONE)
                                for h in headers
                            )
                            if all_done:
                                break
                            if eof_playing:
                                active_drain_sec += dt
                            time.sleep(0.02)
                        if seeked_during_eof:
                            continue
                        if not stopped_during_eof:
                            with self._lock:
                                if self._session_id == my_session_id:
                                    track_completed_normally = True
                                    self._is_active = False
                                    self._is_playing = False
                        break

                    hdr = headers[buf_idx]
                    if hdr.dwFlags & WHDR_PREPARED:
                        # 必须等待声卡真正播完该缓冲，绝不超时强行覆写正在 DMA 读取的内存；
                        # 等待期间同步响应暂停/恢复/音量/Seek/切歌指令，避免阻塞控制流。
                        aborted = False
                        while not (hdr.dwFlags & WHDR_DONE):
                            with self._lock:
                                if (
                                    self._session_id != my_session_id
                                    or self._seek_generation != run_seek_gen
                                    or self._seek_reset_pending
                                ):
                                    aborted = True
                                    break
                                inner_vol_dirty = self._volume_dirty
                                inner_vol = self._volume
                                self._volume_dirty = False
                                inner_pause = self._pause_change_pending
                                self._pause_change_pending = None
                            if inner_vol_dirty:
                                with self._wave_handle_lock:
                                    if self._h_wave is h_wave:
                                        self._apply_hardware_volume(h_wave, inner_vol)
                            if inner_pause is True:
                                with self._wave_handle_lock:
                                    if self._h_wave is h_wave:
                                        winmm.waveOutPause(h_wave)
                            elif inner_pause is False:
                                with self._wave_handle_lock:
                                    if self._h_wave is h_wave:
                                        winmm.waveOutRestart(h_wave)
                            time.sleep(0.006)
                        if aborted:
                            continue
                        with self._wave_handle_lock:
                            if self._h_wave is h_wave:
                                unprep_res = winmm.waveOutUnprepareHeader(
                                    h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                                )
                            else:
                                unprep_res = -1
                        if unprep_res == -1:
                            break
                        if unprep_res != 0:
                            time.sleep(0.01)
                            with self._wave_handle_lock:
                                if self._h_wave is h_wave:
                                    unprep_res = winmm.waveOutUnprepareHeader(
                                        h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR)
                                    )
                                else:
                                    unprep_res = -1
                            if unprep_res == -1:
                                break
                            if unprep_res != 0:
                                consecutive_wave_errors += 1
                                if consecutive_wave_errors >= 5:
                                    invalidate_wave_handle()
                                    error_to_report = f"音频设备缓冲释放失败 (错误码 {unprep_res})"
                                    with self._lock:
                                        if self._session_id == my_session_id:
                                            self._is_active = False
                                            self._is_playing = False
                                    break
                                continue

                    pcm_bytes = chunk.data
                    c_buf = ctypes.create_string_buffer(pcm_bytes, len(pcm_bytes))
                    buffers[buf_idx] = c_buf

                    hdr.lpData = ctypes.cast(c_buf, ctypes.c_char_p)
                    hdr.dwBufferLength = len(pcm_bytes)
                    hdr.dwFlags = 0
                    hdr.dwLoops = 0

                    with self._wave_handle_lock:
                        if self._h_wave is not h_wave:
                            prep_res = -1
                            write_res = 0
                        else:
                            prep_res = winmm.waveOutPrepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                            if prep_res == 0:
                                write_res = winmm.waveOutWrite(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                                if write_res != 0:
                                    winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                                    hdr.dwFlags = 0
                            else:
                                write_res = 0
                    if prep_res == -1:
                        break

                    if prep_res != 0 or write_res != 0:
                        err_code = prep_res if prep_res != 0 else write_res
                        consecutive_wave_errors += 1
                        if consecutive_wave_errors >= 5:
                            invalidate_wave_handle()
                            error_to_report = f"音频输出设备写入失败 (错误码 {err_code})"
                            with self._lock:
                                if self._session_id == my_session_id:
                                    self._is_active = False
                                    self._is_playing = False
                            break
                        continue

                    consecutive_wave_errors = 0
                    submitted_bytes += len(pcm_bytes)

                    buf_idx = (buf_idx + 1) % num_buffers

                    chunk_sec_count = len(pcm_bytes) // CD_SECTOR_RAW_BYTES
                    with self._lock:
                        if self._session_id != my_session_id:
                            break
                        self._current_lba = chunk.lba + chunk_sec_count
                        fallback_sec = max(0.0, (self._current_lba - current_playing_track.start_lba) / CD_FRAMES_PER_SECOND)

                    # 优先从声卡硬件位置读取真实已输出的音频进度，消除环形队列提前量
                    mmt.wType = TIME_BYTES
                    with self._wave_handle_lock:
                        pos_ok = (
                            self._h_wave is h_wave
                            and winmm.waveOutGetPosition(h_wave, ctypes.byref(mmt), ctypes.sizeof(mmt)) == 0
                            and mmt.wType == TIME_BYTES
                        )
                    if pos_ok:
                        hw_bytes = int(mmt.u.cb)
                        if hw_bytes >= track_base_bytes:
                            elapsed_sec = max(0.0, track_base_sec + (hw_bytes - track_base_bytes) / 176400.0)
                        else:
                            elapsed_sec = 0.0
                    else:
                        elapsed_sec = fallback_sec

                    if self.on_position:
                        self.on_position(elapsed_sec)
            finally:
                with self._wave_handle_lock:
                    if self._h_wave is h_wave and h_wave and h_wave.value:
                        try:
                            winmm.waveOutReset(h_wave)
                        except Exception:
                            pass
                        for hdr in headers:
                            if hdr.dwFlags & WHDR_PREPARED:
                                try:
                                    winmm.waveOutUnprepareHeader(h_wave, ctypes.byref(hdr), ctypes.sizeof(WAVEHDR))
                                except Exception:
                                    pass

            if error_to_report and self.on_error:
                self.on_error(error_to_report)
            elif track_completed_normally and self.on_finished:
                self.on_finished()
        finally:
            if done_event is not None:
                done_event.set()
