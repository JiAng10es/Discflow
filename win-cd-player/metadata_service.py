# -*- coding: utf-8 -*-
"""
metadata_service.py
多源高精度 CD 元数据、Base64 高清专辑封面与双语同步 LRC 歌词服务：
1. 光盘硬件内嵌 CD-TEXT 预填充
2. MusicBrainz DiscID 精确查询 + TOC 扇区偏移智能匹配 (?toc=... 全轨时长加权排序)
3. GNUDb / FreeDB (cddb+query + cddb+read) 备选
4. 多源高清封面获取并编码为 Base64 Data URL (供前端 Canvas 动态提取主色调生成 Apple Music 流体渐变背景)
5. 网易云音乐双语 LRC (原文 + 中文译文) + LRCLIB 国际歌词库备选
6. 支持渐进式回调 (on_partial)：先秒级返回专辑名与歌单，再异步补齐高清封面，杜绝网络阻塞
"""

import base64
import difflib
import html
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple
import unicodedata
import requests

from cd_engine import CdDiscToc

USER_AGENT = "Discflow/3.2 (https://github.com/discflow-player)"
NETEASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://music.163.com/",
}
REQ_TIMEOUT = (2.2, 3.8)
TIME_TAG_REGEX = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")


@dataclass
class AlbumMetadataResult:
    disc_id: str
    album_title: str = "Audio CD"
    artist: str = "未知艺术家"
    year: str = ""
    cover_data_url: str = ""
    track_titles: Dict[int, str] = field(default_factory=dict)
    track_artists: Dict[int, str] = field(default_factory=dict)
    matched_source: str = "CD-DA Optical TOC"


_METADATA_CACHE: Dict[str, AlbumMetadataResult] = {}
# 歌词进程内缓存：键为「曲名|艺术家|时长」，设上限避免长时间运行无限增长
_LYRICS_CACHE: Dict[str, List[dict]] = {}
_LYRICS_CACHE_MAX = 400
_LYRICS_CACHE_LOCK = threading.Lock()


def _lyrics_cache_put(key: str, value: List[dict]) -> None:
    """写入歌词缓存，超过上限时按插入顺序淘汰最早的一批（FIFO，足够简单且无额外开销）"""
    with _LYRICS_CACHE_LOCK:
        _LYRICS_CACHE[key] = value
        overflow = len(_LYRICS_CACHE) - _LYRICS_CACHE_MAX
        if overflow > 0:
            for stale in list(_LYRICS_CACHE.keys())[:overflow]:
                _LYRICS_CACHE.pop(stale, None)


def _lyrics_cache_key(track_title: str, artist: str, duration_sec: float) -> str:
    return f"{(track_title or '').strip().lower()}|{(artist or '').strip().lower()}|{int(round(duration_sec or 0))}"


def parse_lrc_lyrics(lrc_text: str) -> List[Tuple[float, str]]:
    """解析 LRC 格式歌词文本，返回按秒数升序排列的 [(time_sec, text), ...]"""
    if not lrc_text:
        return []

    parsed: List[Tuple[float, str]] = []
    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        matches = list(TIME_TAG_REGEX.finditer(line))
        if not matches:
            continue
        lyric_content = TIME_TAG_REGEX.sub("", line).strip()
        if not lyric_content:
            continue

        for m in matches:
            minutes = int(m.group(1))
            seconds = int(m.group(2))
            frac = m.group(3) or "0"
            frac_sec = int(frac) / (10 ** len(frac))
            total_sec = round(minutes * 60 + seconds + frac_sec, 3)
            parsed.append((total_sec, lyric_content))

    parsed.sort(key=lambda item: item[0])
    return parsed


def align_translations(
    orig_times: List[float],
    trans_lines: List[Tuple[float, str]],
    window: float = 0.65,
) -> List[str]:
    """
    把翻译歌词按时间顺序与原文歌词做稳健对齐（v1.11.1 修复「翻译断断续续」）：
    - 双指针 + 小窗口最优匹配，容忍两边时间轴 ±0.65s 的整体漂移；
    - 不再使用「精确到 0.1s 的字典键 + 只有 ±0.2s 容差」的脆弱匹配，
      避免大量行因几十毫秒偏差而丢翻译，出现一首歌里有的句子有翻译、有的没有。
    """
    result: List[str] = [""] * len(orig_times)
    if not orig_times or not trans_lines:
        return result

    n = len(trans_lines)
    j = 0
    for i, t in enumerate(orig_times):
        t = float(t)
        while j < n and trans_lines[j][0] < t - window:
            j += 1

        best_k = -1
        best_dt = window + 1e-6
        for k in range(j, min(n, j + 4)):
            dt = abs(trans_lines[k][0] - t)
            if dt < best_dt:
                best_dt = dt
                best_k = k

        if best_k >= 0:
            text = trans_lines[best_k][1]
            if text:
                result[i] = text
            j = best_k + 1
    return result


def build_structured_bilingual_lyrics(orig_lrc: str, trans_lrc: str = "") -> List[dict]:
    """
    将原文 LRC 与中文翻译 LRC 对齐，返回结构化列表：
    [{"time": 10.42, "text": "私を包む メロディー", "translation": "包裹着我的旋律"}, ...]
    """
    orig_lines = parse_lrc_lyrics(orig_lrc)
    if not orig_lines:
        return []

    trans_lines: List[Tuple[float, str]] = []
    if trans_lrc:
        for t_sec, t_text in parse_lrc_lyrics(trans_lrc):
            if t_text and not t_text.startswith("//"):
                trans_lines.append((t_sec, t_text))

    aligned = align_translations([t for t, _ in orig_lines], trans_lines)

    structured: List[dict] = []
    for (o_sec, o_text), t_match in zip(orig_lines, aligned):
        if t_match == o_text:
            t_match = ""
        structured.append(
            {
                "time": o_sec,
                "text": o_text,
                "translation": t_match,
            }
        )
    return structured


YRC_LINE_REGEX = re.compile(r"^\[(\d+),(\d+)\](.*)$")
YRC_WORD_REGEX = re.compile(r"\((\d+),(\d+),-?\d+\)([^(]*)")


def parse_yrc_word_lyrics(yrc_text: str) -> List[dict]:
    """
    解析网易云 yrc 逐字歌词时间轴，返回：
    [{"time": 行起始秒, "text": "整行文本", "translation": "", "words": [{"t": 绝对秒, "d": 时长秒, "w": "字词"}, ...]}]
    yrc 原始格式： [行起始ms,行时长ms](字起始ms,字时长ms,0)字(字起始ms,字时长ms,0)字...
    """
    if not yrc_text:
        return []

    lines: List[dict] = []
    for raw_line in yrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = YRC_LINE_REGEX.match(line)
        if not m:
            continue
        line_start_ms = int(m.group(1))
        body = m.group(3)

        words: List[dict] = []
        for wm in YRC_WORD_REGEX.finditer(body):
            w_start_ms = int(wm.group(1))
            w_dur_ms = int(wm.group(2))
            w_text = wm.group(3)
            if not w_text:
                continue
            words.append(
                {
                    "t": round(w_start_ms / 1000.0, 3),
                    "d": round(max(1, w_dur_ms) / 1000.0, 3),
                    "w": w_text,
                }
            )

        text = "".join(w["w"] for w in words).strip()
        if not text or not words:
            continue
        lines.append(
            {
                "time": round(line_start_ms / 1000.0, 3),
                "text": text,
                "translation": "",
                "words": words,
            }
        )

    lines.sort(key=lambda item: item["time"])
    return lines


def merge_word_lyrics(word_lines: List[dict], plain_lines: List[dict]) -> List[dict]:
    """把逐字时间轴与普通 LRC 的翻译对齐，并判断逐字结果是否足够完整可替换普通歌词"""
    if not word_lines:
        return plain_lines
    if not plain_lines:
        return word_lines

    trans_lines: List[Tuple[float, str]] = []
    for ln in plain_lines:
        txt = ln.get("translation") or ""
        if txt:
            trans_lines.append((float(ln.get("time", 0.0)), txt))

    aligned = align_translations([float(wl.get("time", 0.0)) for wl in word_lines], trans_lines)
    for wl, t_match in zip(word_lines, aligned):
        if t_match and t_match != wl.get("text"):
            wl["translation"] = t_match
        elif "translation" not in wl:
            wl["translation"] = ""

    # 逐字歌词条数明显偏少时（例如只有副歌部分有逐字数据），保守回退到普通歌词
    if len(word_lines) < max(2, int(len(plain_lines) * 0.5)):
        return plain_lines
    return word_lines


def _bytes_to_data_url(image_bytes: Optional[bytes]) -> str:
    if not image_bytes or len(image_bytes) < 512:
        return ""
    mime = "image/png" if image_bytes[:8].startswith(b"\x89PNG") else "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _pick_best_musicbrainz_release(toc: CdDiscToc, releases: list) -> Optional[Tuple[dict, dict, float]]:
    cd_track_count = len(toc.tracks)
    cd_durations_ms = [t.duration_sec * 1000.0 for t in toc.tracks]
    best_candidate: Optional[Tuple[float, dict, dict, float]] = None

    for rel in releases:
        script = (rel.get("text-representation") or {}).get("script", "")
        script_bonus = -500.0 if script in ("Jpan", "Hans", "Hant", "Kore") else 0.0

        for medium in rel.get("media", []):
            m_tracks = medium.get("tracks", [])
            if len(m_tracks) != cd_track_count:
                continue

            total_diff_ms = 0.0
            valid_lengths = 0
            for cd_ms, mb_trk in zip(cd_durations_ms, m_tracks):
                mb_len = mb_trk.get("length")
                if mb_len is not None and mb_len > 0:
                    total_diff_ms += abs(float(mb_len) - cd_ms)
                    valid_lengths += 1

            avg_diff_ms = (total_diff_ms / valid_lengths) if valid_lengths > 0 else 2500.0
            score = avg_diff_ms + script_bonus

            if best_candidate is None or score < best_candidate[0]:
                best_candidate = (score, rel, medium, avg_diff_ms)

    if best_candidate is None:
        return None

    _, best_rel, best_med, avg_diff = best_candidate
    if avg_diff <= 6000.0:
        return best_rel, best_med, avg_diff
    return None


def _fetch_cover_from_caa(release_id: str) -> Optional[bytes]:
    if not release_id:
        return None
    try:
        caa_url = f"https://coverartarchive.org/release/{release_id}/front-500"
        r = requests.get(caa_url, headers={"User-Agent": USER_AGENT}, timeout=REQ_TIMEOUT)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.content
    except Exception:
        pass
    return None


def _fetch_cover_from_netease(artist: str, album_title: str) -> Optional[bytes]:
    clean_album = re.sub(r"\s*\+\d+$", "", album_title).strip()
    for query_term in (f"{artist} {album_title}", f"{artist} {clean_album}", album_title):
        if not query_term.strip() or query_term == "Audio CD":
            continue
        try:
            resp = requests.post(
                "https://music.163.com/api/search/get",
                data={"s": query_term, "type": 10, "limit": 4},
                headers=NETEASE_HEADERS,
                timeout=REQ_TIMEOUT,
            )
            if resp.status_code == 200:
                albums = resp.json().get("result", {}).get("albums", [])
                for alb in albums:
                    pic_url = alb.get("picUrl")
                    if pic_url:
                        img_resp = requests.get(
                            f"{pic_url}?param=600y600",
                            headers=NETEASE_HEADERS,
                            timeout=REQ_TIMEOUT,
                        )
                        if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                            return img_resp.content
        except Exception:
            pass
    return None


def _fetch_cover_from_itunes(artist: str, album_title: str) -> Optional[bytes]:
    clean_album = re.sub(r"\s*\+\d+$", "", album_title).strip()
    for country in ("JP", "CN", "US"):
        try:
            it_resp = requests.get(
                "https://itunes.apple.com/search",
                params={
                    "term": f"{artist} {clean_album}".strip(),
                    "entity": "album",
                    "country": country,
                    "limit": 3,
                },
                headers={"User-Agent": USER_AGENT},
                timeout=REQ_TIMEOUT,
            )
            if it_resp.status_code == 200:
                results = it_resp.json().get("results", [])
                for item in results:
                    art_url = item.get("artworkUrl100", "")
                    if art_url:
                        hi_res_url = art_url.replace("100x100bb", "600x600bb")
                        img_resp = requests.get(hi_res_url, headers={"User-Agent": USER_AGENT}, timeout=REQ_TIMEOUT)
                        if img_resp.status_code == 200 and len(img_resp.content) > 1000:
                            return img_resp.content
        except Exception:
            pass
    return None


def _fetch_cover_art_bytes(release_id: str, artist: str, album_title: str) -> Optional[bytes]:
    """
    多源并发获取专辑封面并择优（v1.11 优化）：
    - 优先级：CoverArtArchive 官方封面 > 网易云 > iTunes；
    - 官方封面一旦命中立即返回，避免被较慢的源拖住；
    - 同级来源中取体积更大（分辨率更高）的一张，而不是「谁先返回用谁」。
    """
    tier_of: Dict[object, int] = {}
    results: Dict[object, bytes] = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        if release_id:
            fut_caa = pool.submit(_fetch_cover_from_caa, release_id)
            tier_of[fut_caa] = 0
        fut_netease = pool.submit(_fetch_cover_from_netease, artist, album_title)
        tier_of[fut_netease] = 1
        fut_itunes = pool.submit(_fetch_cover_from_itunes, artist, album_title)
        tier_of[fut_itunes] = 2

        try:
            for future in as_completed(list(tier_of.keys()), timeout=3.2):
                try:
                    data = future.result()
                except Exception:
                    data = None
                if not data or len(data) <= 1000:
                    continue
                results[future] = data
                if tier_of.get(future) == 0:
                    return data
        except Exception:
            # 超时后使用已到达的结果，不再等待慢源
            pass

    if not results:
        return None
    best_future, best_bytes = min(
        results.items(), key=lambda kv: (tier_of.get(kv[0], 9), -len(kv[1]))
    )
    return best_bytes


def lookup_album_metadata(
    toc: CdDiscToc,
    on_partial: Optional[Callable[[AlbumMetadataResult], None]] = None,
) -> AlbumMetadataResult:
    """
    同步查询指定 CD TOC 的专辑信息、音轨标题列表与高清 Base64 封面（供后台线程调用）。
    支持 on_partial 回调：一旦解析出专辑名与音轨名立即触发回调刷新 UI，随后再下载封面图。
    """
    if toc.disc_id in _METADATA_CACHE:
        cached = _METADATA_CACHE[toc.disc_id]
        if on_partial:
            try:
                on_partial(cached)
            except Exception:
                pass
        return cached

    if not toc or not toc.tracks:
        return AlbumMetadataResult(disc_id=getattr(toc, "disc_id", "") if toc else "")

    result = AlbumMetadataResult(disc_id=toc.disc_id)
    if toc.cdtext_album:
        result.album_title = toc.cdtext_album
        result.matched_source = "CD-TEXT 硬件内嵌信息"
    elif any(trk.title and not trk.title.startswith("Track ") for trk in toc.tracks):
        result.album_title = "CD-TEXT Album"
        result.matched_source = "CD-TEXT 硬件内嵌信息"
    if toc.cdtext_artist:
        result.artist = toc.cdtext_artist

    for trk in toc.tracks:
        result.track_titles[trk.track_number] = trk.title or f"Track {trk.track_number:02d}"
        result.track_artists[trk.track_number] = trk.artist or result.artist

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    # 1. MusicBrainz DiscID + TOC 智能匹配
    try:
        mb_url = (
            f"https://musicbrainz.org/ws/2/discid/{toc.disc_id}"
            f"?toc={toc.toc_query_string}&inc=artists+recordings&fmt=json"
        )
        resp = requests.get(mb_url, headers=headers, timeout=(3.5, 6.0))
        if resp.status_code == 200:
            data = resp.json()
            releases = data.get("releases", [])
            best = _pick_best_musicbrainz_release(toc, releases)
            if best is not None:
                rel, medium, avg_diff_ms = best
                release_id = rel.get("id", "")
                result.album_title = rel.get("title") or result.album_title
                result.year = (rel.get("date") or "")[:4]

                credits = rel.get("artist-credit", [])
                artist_names = []
                for c in credits:
                    if isinstance(c, dict):
                        name = (c.get("artist") or {}).get("name") or c.get("name")
                        if name:
                            artist_names.append(name)
                if artist_names:
                    result.artist = " / ".join(artist_names)

                result.matched_source = (
                    "MusicBrainz DiscID"
                    if "id" in data
                    else f"MusicBrainz TOC Match (±{avg_diff_ms / 1000:.2f}s)"
                )

                tracks_json = medium.get("tracks", [])
                for idx, trk_obj in enumerate(tracks_json):
                    trk_num = toc.tracks[idx].track_number if idx < len(toc.tracks) else (idx + 1)
                    t_title = trk_obj.get("title") or (trk_obj.get("recording") or {}).get("title")
                    if t_title:
                        result.track_titles[trk_num] = t_title
                    result.track_artists[trk_num] = result.artist

                # 先回调通知文字元数据已就绪，让歌名、歌手和歌词立即显示
                if on_partial:
                    try:
                        on_partial(result)
                    except Exception:
                        pass

                cover_bytes = _fetch_cover_art_bytes(release_id, result.artist, result.album_title)
                result.cover_data_url = _bytes_to_data_url(cover_bytes)
                _METADATA_CACHE[toc.disc_id] = result
                return result
    except Exception:
        pass

    # 2. GNUDb / FreeDB 备选
    try:
        offsets_str = "+".join(str(t.start_frame) for t in toc.tracks)
        total_sec = toc.leadout_frame // 75
        gnudb_base = "http://gnudb.gnudb.org/~cddb/cddb.cgi"
        hello_param = "hello=user+windows+Discflow+3.1&proto=6"
        query_cmd = f"cddb+query+{toc.freedb_id}+{len(toc.tracks)}+{offsets_str}+{total_sec}"
        g_resp = requests.get(f"{gnudb_base}?cmd={query_cmd}&{hello_param}", headers=headers, timeout=REQ_TIMEOUT)

        if g_resp.status_code == 200:
            lines = [ln.strip() for ln in g_resp.text.splitlines() if ln.strip()]
            category, matched_discid, artist_album = "", "", ""
            if lines and lines[0].startswith("200 "):
                parts = lines[0].split(" ", 3)
                if len(parts) == 4:
                    category, matched_discid, artist_album = parts[1], parts[2], parts[3]
            elif len(lines) >= 2 and (lines[0].startswith("210 ") or lines[0].startswith("211 ")):
                parts = lines[1].split(" ", 2)
                if len(parts) == 3:
                    category, matched_discid, artist_album = parts[0], parts[1], parts[2]

            if artist_album:
                if " / " in artist_album:
                    art, alb = artist_album.split(" / ", 1)
                    result.artist = art.strip()
                    result.album_title = alb.strip()
                else:
                    result.album_title = artist_album.strip()
                result.matched_source = "GNUDb / FreeDB"

            if category and matched_discid:
                read_cmd = f"cddb+read+{category}+{matched_discid}"
                r_resp = requests.get(f"{gnudb_base}?cmd={read_cmd}&{hello_param}", headers=headers, timeout=REQ_TIMEOUT)
                if r_resp.status_code == 200:
                    ttitles: Dict[int, str] = {}
                    for raw_ln in r_resp.text.splitlines():
                        if raw_ln.startswith("DYEAR="):
                            result.year = raw_ln.split("=", 1)[1].strip()[:4]
                        elif raw_ln.startswith("TTITLE"):
                            eq_idx = raw_ln.find("=")
                            if eq_idx > 6:
                                idx_str = raw_ln[6:eq_idx]
                                if idx_str.isdigit():
                                    t_idx = int(idx_str) + 1
                                    ttitles[t_idx] = ttitles.get(t_idx, "") + raw_ln[eq_idx + 1 :]
                    for t_num, t_name in ttitles.items():
                        if t_name.strip():
                            result.track_titles[t_num] = t_name.strip()
                            result.track_artists[t_num] = result.artist
    except Exception:
        pass

    if on_partial and result.album_title != "Audio CD":
        try:
            on_partial(result)
        except Exception:
            pass

    if result.album_title != "Audio CD":
        cover_bytes = _fetch_cover_art_bytes("", result.artist, result.album_title)
        result.cover_data_url = _bytes_to_data_url(cover_bytes)
        _METADATA_CACHE[toc.disc_id] = result

    return result


def _normalize_song_title(text: str) -> str:
    """归一化歌名：去括号后缀（Original / Remastered / Live 等）、去标点、转小写"""
    s = (text or "").lower()
    s = re.sub(r"[\(（\[【][^\)）\]】]*[\)）\]】]", " ", s)
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)
    return " ".join(s.split())


def _title_similarity(a: str, b: str) -> float:
    na, nb = _normalize_song_title(a), _normalize_song_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ----------------------------------------------------------------------------
# 标题清洗与「脏数据」判定（v1.11.12）
#
# 曲库里有相当一部分条目是 Shift-JIS 被按 GBK 误解码的残次品，例如同一张专辑
# 「Quiet Emotion / 具島直子」在 QQ 音乐上有两个 mid：
#   002XiL49004d6d → テル・ミー・オー・ママ / Tell me oh mama (リミックス)   ← 干净
#   001opQHb1GhBPD → テルミ━蕙 / テルミ━蕙リミックス)                      ← 乱码
# 两者专辑名、艺术家、发行日期、曲目数完全一致，纯按名字+时长打分无法区分，
# 只能靠「曲名本身像不像乱码」来挑。下面这组函数就是干这个的。
# ----------------------------------------------------------------------------

# 「硬信号」：正常曲名里几乎不可能出现的码位，出现一个就足以判定为误解码产物
_HARD_GARBLED_RANGES = (
    (0x2500, 0x257F),  # Box Drawing（━ ─ │ ┌ 等），Shift-JIS→GBK 误解码高发区
    (0xE000, 0xF8FF),  # 私用区
    (0xFFF0, 0xFFF8),  # 特殊区未定义码位
)

# 「软信号」：偶尔会出现在正常曲名里的装饰符号，按占比判定
_SOFT_GARBLED_RANGES = (
    (0x2580, 0x259F),  # Block Elements
    (0x25A0, 0x25FF),  # Geometric Shapes
    (0x2E80, 0x2EFF),  # CJK 部首补充
)


def _sanitize_title(text: str) -> str:
    """清洗曲库标题：反转义 HTML 实体、去零宽/控制字符、折叠空白。不改动正常文字。"""
    s = html.unescape(str(text or ""))
    # 零宽字符会让曲名看起来「缺一块」或者比对时永远不相等
    for zw in ("\ufeff", "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060"):
        s = s.replace(zw, "")
    # 只保留可打印字符（C* 类：Cc 控制符 / Cf 格式符 / Cs 代理对 / Co 私用区）
    s = "".join(ch for ch in s if ch in "\t\n" or unicodedata.category(ch)[0] != "C")
    s = re.sub(r"[\s\u3000]+", " ", s)
    return s.strip()


def _garbled_score(text: str) -> float:
    """
    返回 0~1 的「脏度」：
    - 出现替换字符 U+FFFD 或制表符类硬信号，直接判满（解码失败的铁证）；
    - 装饰类软信号按占比折算，10 个字符里出现 2 个以上即视为可疑。
    """
    s = str(text or "")
    if not s:
        return 0.0
    if "\ufffd" in s:
        return 1.0
    soft = 0
    for ch in s:
        cp = ord(ch)
        for lo, hi in _HARD_GARBLED_RANGES:
            if lo <= cp <= hi:
                return 1.0
        for lo, hi in _SOFT_GARBLED_RANGES:
            if lo <= cp <= hi:
                soft += 1
                break
    return min(1.0, soft / max(len(s), 1))


def _looks_garbled(text: str) -> bool:
    """曲名是否像乱码。软信号阈值 0.12：10 个字符里出现 2 个可疑符号即判定。"""
    return _garbled_score(text) >= 0.12


def _tracks_cleanliness(tracks: List[dict]) -> float:
    """曲目表干净度 0~1（1 = 全部干净），用于候选排序时把乱码专辑沉底"""
    names = [str(t.get("title") or "").strip() for t in (tracks or [])]
    names = [n for n in names if n]
    if not names:
        return 1.0
    dirty = sum(1 for n in names if _looks_garbled(n))
    return 1.0 - dirty / len(names)



def _lyrics_rank(lines: List[dict]) -> Tuple[int, int, int]:
    """歌词质量排序键：翻译行数 > 逐字行数 > 总行数"""
    if not lines:
        return (0, 0, 0)
    trans = sum(1 for ln in lines if ln.get("translation"))
    words = sum(1 for ln in lines if ln.get("words"))
    return (trans, words, len(lines))


def _fetch_netease_song_lyrics(song_id) -> List[dict]:
    """按歌曲 ID 拉取原文 + 翻译 + 逐字时间轴，并做对齐合并"""
    if not song_id:
        return []
    try:
        lrc_resp = requests.get(
            f"https://music.163.com/api/song/lyric?id={song_id}&lv=-1&tv=-1&kv=-1&yv=-1",
            headers=NETEASE_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if lrc_resp.status_code != 200:
            return []
        lrc_json = lrc_resp.json()
        orig_lrc = (lrc_json.get("lrc") or {}).get("lyric", "")
        trans_lrc = (lrc_json.get("tlyric") or {}).get("lyric", "")
        yrc_lrc = (lrc_json.get("yrc") or {}).get("lyric", "")
        structured = build_structured_bilingual_lyrics(orig_lrc, trans_lrc)
        word_lines = parse_yrc_word_lyrics(yrc_lrc)
        if word_lines:
            structured = merge_word_lyrics(word_lines, structured)
        return structured
    except Exception:
        return []


def lookup_track_lyrics(
    track_title: str,
    artist: str,
    duration_sec: float,
    builtin_lrc: str = "",
) -> List[dict]:
    """查询指定音轨的双语结构化同步歌词列表（含逐字时间轴，带内存缓存）"""
    if builtin_lrc:
        return build_structured_bilingual_lyrics(builtin_lrc)

    # 元数据尚未匹配时的占位标题（Track 01 / DiscID xxxx）不得去查歌词：
    # 否则会把完全不相干的歌曲歌词缓存下来，导致首曲长期显示错误或空白歌词。
    placeholder_artist = (not artist) or artist in ("未知艺术家", "Audio CD") or artist.startswith("DiscID")
    if re.match(r"^Track\s*\d+$", track_title or "") and placeholder_artist:
        return []

    cache_key = _lyrics_cache_key(track_title, artist, duration_sec)
    with _LYRICS_CACHE_LOCK:
        cached = _LYRICS_CACHE.get(cache_key)
    if cached is not None:
        return cached

    lower_title = track_title.lower()
    is_inst_query = "instrumental" in lower_title or "伴奏" in lower_title or "カラオケ" in lower_title

    # 1. 优先查询网易云：先按「歌名相似度 + 时长接近度」给候选排序，再对前几个候选实际拉取歌词，
    #    择优取「翻译最全 / 逐字最全」的那一份。避免同名不同版本（Original / A-capella / Remix）
    #    选错，导致整首歌只剩零星几句翻译。
    try:
        resp = requests.post(
            "https://music.163.com/api/search/get",
            data={"s": f"{artist} {track_title}".strip(), "type": 1, "limit": 10},
            headers=NETEASE_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code == 200:
            songs = resp.json().get("result", {}).get("songs", [])
            target_ms = duration_sec * 1000.0
            candidates = []

            for s in songs:
                s_name = (s.get("name") or "").strip()
                s_lower = s_name.lower()
                s_is_inst = "instrumental" in s_lower or "伴奏" in s_lower or "off vocal" in s_lower
                if is_inst_query != s_is_inst:
                    continue

                dur_ms = float(s.get("duration") or 0)
                dur_diff = abs(dur_ms - target_ms) if dur_ms > 0 else 20000.0
                sim = _title_similarity(s_name, track_title)
                score = dur_diff + (1.0 - sim) * 30000.0
                candidates.append((score, s.get("id"), s_name))

            if not candidates:
                candidates = [(20000.0, s.get("id"), (s.get("name") or "")) for s in songs[:2]]

            candidates.sort(key=lambda item: item[0])

            best_lines: List[dict] = []
            best_rank: Tuple[int, int, int] = (0, 0, 0)
            for score, song_id, _name in candidates[:3]:
                lines = _fetch_netease_song_lyrics(song_id)
                if not lines:
                    continue
                rank = _lyrics_rank(lines)
                if rank > best_rank:
                    best_rank, best_lines = rank, lines
                # 翻译覆盖率已达 90% 以上，无需再试其他候选
                if rank[0] >= len(lines) * 0.9:
                    break

            if best_lines:
                _lyrics_cache_put(cache_key, best_lines)
                return best_lines
    except Exception:
        pass

    # 2. 备选查询 LRCLIB
    headers = {"User-Agent": USER_AGENT}
    try:
        resp = requests.get(
            "https://lrclib.net/api/get",
            params={
                "track_name": track_title,
                "artist_name": artist,
                "duration": round(duration_sec),
            },
            headers=headers,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            synced = resp.json().get("syncedLyrics")
            if synced:
                parsed = build_structured_bilingual_lyrics(synced)
                if parsed:
                    _lyrics_cache_put(cache_key, parsed)
                return parsed

        search_resp = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{track_title} {artist}"},
            headers=headers,
            timeout=REQ_TIMEOUT,
        )
        if search_resp.status_code == 200 and isinstance(search_resp.json(), list):
            for candidate in search_resp.json():
                if isinstance(candidate, dict) and candidate.get("syncedLyrics"):
                    parsed = build_structured_bilingual_lyrics(candidate["syncedLyrics"])
                    if parsed:
                        _lyrics_cache_put(cache_key, parsed)
                    return parsed
    except Exception:
        pass

    return []


ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
ITUNES_COUNTRIES = ("JP", "CN", "US")


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" for ch in (text or ""))


def _itunes_album_candidates(query: str, country: str, limit: int = 8) -> List[dict]:
    if not (query or "").strip():
        return []
    try:
        resp = requests.get(
            ITUNES_SEARCH,
            params={"term": query.strip(), "entity": "album", "country": country, "limit": limit},
            headers={"User-Agent": USER_AGENT},
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get("results", []) or []
    except Exception:
        pass
    return []


def _itunes_album_songs(collection_id, country: str) -> List[dict]:
    if not collection_id:
        return []
    try:
        resp = requests.get(
            ITUNES_LOOKUP,
            params={"id": collection_id, "entity": "song", "country": country, "limit": 200},
            headers={"User-Agent": USER_AGENT},
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code == 200:
            return [r for r in (resp.json().get("results") or []) if r.get("wrapperType") == "track"]
    except Exception:
        pass
    return []


def download_image_bytes(url: str) -> Optional[bytes]:
    """通用图片下载（用于专辑候选封面）"""
    if not url:
        return None
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT, "Referer": "https://y.qq.com/"}, timeout=REQ_TIMEOUT)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return resp.content
    except Exception:
        pass
    return None


def refetch_album_cover(album_title: str, artist: str) -> str:
    """
    按专辑名 + 艺术家重新抓一次封面，返回 Base64 Data URL（失败返回空串）。

    用途：首次匹配时封面源偶发超时（弱网 / 图床抖动），专辑名和曲目都拿到了、
    唯独封面是空的，界面会一直显示占位图标。调用方可稍后重试一次补上。
    """
    if not (album_title or "").strip():
        return ""
    try:
        raw = _fetch_cover_art_bytes("", artist or "", album_title)
        if raw:
            return _bytes_to_data_url(raw)
    except Exception:
        pass
    return ""


def _itunes_cover_bytes(art_url: str) -> Optional[bytes]:
    if not art_url:
        return None
    hi_res = art_url.replace("100x100bb", "800x800bb").replace("100x100", "800x800")
    try:
        img = requests.get(hi_res, headers={"User-Agent": USER_AGENT}, timeout=REQ_TIMEOUT)
        if img.status_code == 200 and len(img.content) > 1000:
            return img.content
    except Exception:
        pass
    return None


def match_tracks_by_duration(
    local_durations: List[float], candidate_tracks: List[dict]
) -> Dict[int, dict]:
    """
    把本地音轨与候选专辑曲目对齐：
    - 数量一致时按顺序一一对应（最常见）；
    - 数量不一致时按「时长最接近且未被占用」贪心匹配，避免错位。
    返回 {本地音轨下标: 候选曲目字典}
    """
    mapping: Dict[int, dict] = {}
    if not candidate_tracks:
        return mapping

    if len(local_durations) == len(candidate_tracks):
        for idx, cand in enumerate(candidate_tracks):
            mapping[idx] = cand
        return mapping

    # 数量不一致时优先尝试「连续窗口顺序匹配」：
    # 典型场景是本地单碟 13 首、曲库里是 2CD 版 26 首，此时应按顺序对齐而不是逐首贪心，
    # 否则容易把第 2 首错配到第 1 首的伴奏版本上。
    n_local = len(local_durations)
    if n_local < len(candidate_tracks):
        best_start, best_avg = -1, 1e9
        for start in range(0, len(candidate_tracks) - n_local + 1):
            total, valid = 0.0, 0
            for i in range(n_local):
                cd = float(candidate_tracks[start + i].get("duration_sec") or 0)
                if cd <= 0:
                    continue
                total += abs(cd - float(local_durations[i] or 0))
                valid += 1
            if valid == 0:
                continue
            avg = total / valid
            if avg < best_avg:
                best_avg, best_start = avg, start
        if best_start >= 0 and best_avg <= 6.0:
            for i in range(n_local):
                mapping[i] = candidate_tracks[best_start + i]
            return mapping

    used = set()
    for idx, dur in enumerate(local_durations):
        best_k, best_diff = -1, 1e9
        for k, cand in enumerate(candidate_tracks):
            if k in used:
                continue
            cd = float(cand.get("duration_sec") or 0)
            if cd <= 0:
                continue
            diff = abs(cd - float(dur or 0))
            if diff < best_diff:
                best_diff, best_k = diff, k
        if best_k >= 0 and best_diff <= 8.0:
            mapping[idx] = candidate_tracks[best_k]
            used.add(best_k)
    return mapping


def lookup_album_by_name(
    album_title: str,
    artist: str = "",
    expected_track_count: int = 0,
) -> dict:
    """
    按专辑名（可选艺术家）在 iTunes 曲库检索整张专辑，自动择优并取回曲目表与高清封面。
    用于光盘信息读取失败时的手工匹配。日文/中文专辑在 JP/CN 区命中率较高。

    返回：
    {
      "matched": bool,
      "album_title": str, "artist": str, "year": str,
      "cover_bytes": bytes | None,
      "tracks": [{"title": str, "duration_sec": float}, ...],
      "source": str,
    }
    """
    title_in = (album_title or "").strip()
    artist_in = (artist or "").strip()
    result = {
        "matched": False,
        "album_title": title_in,
        "artist": artist_in,
        "year": "",
        "cover_bytes": None,
        "tracks": [],
        "source": "未匹配",
    }
    if not title_in:
        return result

    queries = []
    if artist_in:
        queries.append(f"{artist_in} {title_in}")
    queries.append(title_in)

    candidates: List[Tuple[float, dict, str]] = []
    seen = set()
    for country in ITUNES_COUNTRIES:
        for q in queries:
            for alb in _itunes_album_candidates(q, country):
                cid = alb.get("collectionId")
                if not cid or cid in seen:
                    continue
                seen.add(cid)

                c_name = (alb.get("collectionName") or "").strip()
                c_artist = (alb.get("artistName") or "").strip()
                count = int(alb.get("trackCount") or 0)

                title_sim = _title_similarity(c_name, title_in)
                artist_sim = _title_similarity(c_artist, artist_in) if artist_in else 0.5

                score = (1.0 - title_sim) * 45.0 + (1.0 - artist_sim) * 35.0
                if expected_track_count and count:
                    score += min(abs(count - expected_track_count), 10) * 3.0
                candidates.append((score, alb, country))

    if not candidates:
        return result

    candidates.sort(key=lambda item: item[0])
    best_score, best_album, best_country = candidates[0]

    songs = _itunes_album_songs(best_album.get("collectionId"), best_country)
    if not songs:
        return result

    year = ""
    release = (best_album.get("releaseDate") or "")[:4]
    if release.isdigit():
        year = release

    tracks = []
    for song in songs:
        tracks.append(
            {
                "title": (song.get("trackName") or "").strip(),
                "duration_sec": round(float(song.get("trackTimeMillis") or 0) / 1000.0, 2),
            }
        )

    cover_bytes = _itunes_cover_bytes(best_album.get("artworkUrl100") or "")

    # 曲库常把日文/中文专辑存成罗马音标题（如「平成風俗」→「Heisei Fuuzoku」）。
    # 用户手输的是 CJK 标题、而匹配结果是纯拉丁标题时，保留用户输入，避免专辑名被改成看不懂的罗马音。
    matched_title = (best_album.get("collectionName") or title_in).strip()
    if _has_cjk(title_in) and not _has_cjk(matched_title):
        matched_title = title_in

    result.update(
        {
            "matched": True,
            "album_title": matched_title,
            "artist": (best_album.get("artistName") or artist_in).strip(),
            "year": year,
            "cover_bytes": cover_bytes,
            "tracks": tracks,
            "source": f"iTunes 专辑匹配（{best_country}）",
        }
    )
    return result


# ============================================================================
# 多源歌词检索（网易云 / QQ 音乐 / LRCLIB），供「逐轨手动匹配歌词」使用
# ============================================================================

QQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://y.qq.com/",
}
LYRIC_SOURCE_LABELS = {"netease": "网易云", "qq": "QQ 音乐", "lrclib": "LRCLIB"}

_CREDIT_LINE_RE = re.compile(
    r"^(\[|--|作词|作曲|编曲|制作|混音|录音|Lyricist|Composer|Arranger|Producer)", re.IGNORECASE
)


def _primary_artist(artist: str) -> str:
    """多艺术家字符串（如「椎名林檎 / 斎藤ネコ」「A & B」）只取第一位，避免污染搜索词"""
    text = (artist or "").strip()
    for sep in (" / ", "/", " & ", "&", "、", ",", "，", " feat.", " ft."):
        if sep in text:
            text = text.split(sep)[0]
    return text.strip()


def _netease_search_songs(title: str, artist: str, limit: int = 8) -> List[dict]:
    artist = _primary_artist(artist)
    queries = [q for q in (f"{artist} {title}".strip(), title.strip()) if q]
    out: List[dict] = []
    try:
        songs_raw: List[dict] = []
        for query in queries:
            resp = requests.post(
                "https://music.163.com/api/search/get",
                data={"s": query, "type": 1, "limit": limit},
                headers=NETEASE_HEADERS,
                timeout=REQ_TIMEOUT,
            )
            if resp.status_code == 200:
                songs_raw = resp.json().get("result", {}).get("songs") or []
            if len(songs_raw) >= 3:
                break
        if songs_raw:
            for song in songs_raw:
                artists = "/".join(a.get("name", "") for a in (song.get("artists") or []) if isinstance(a, dict))
                out.append(
                    {
                        "source": "netease",
                        "song_id": str(song.get("id") or ""),
                        "title": (song.get("name") or "").strip(),
                        "artist": artists,
                        "duration_sec": round(float(song.get("duration") or 0) / 1000.0, 2),
                    }
                )
    except Exception:
        pass
    return out


def _qq_search_songs(title: str, artist: str, limit: int = 8) -> List[dict]:
    artist = _primary_artist(artist)
    queries = [q for q in (f"{artist} {title}".strip(), title.strip()) if q]
    out: List[dict] = []
    try:
        songs: List[dict] = []
        for query in queries:
            resp = requests.get(
                "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                params={"w": query, "format": "json", "n": limit, "p": 1},
                headers=QQ_HEADERS,
                timeout=REQ_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                songs = ((data.get("data") or {}).get("song") or {}).get("list") or []
            if len(songs) >= 3:
                break
        if songs:
            for song in songs:
                singers = "/".join(
                    s.get("name", "") for s in (song.get("singer") or []) if isinstance(s, dict)
                )
                out.append(
                    {
                        "source": "qq",
                        "song_id": str(song.get("songmid") or ""),
                        "title": (song.get("songname") or "").strip(),
                        "artist": singers,
                        "duration_sec": round(float(song.get("interval") or 0), 2),
                    }
                )
    except Exception:
        pass
    return out


def _lrclib_search_songs(title: str, artist: str, limit: int = 8) -> List[dict]:
    out: List[dict] = []
    try:
        resp = requests.get(
            "https://lrclib.net/api/search",
            params={"q": f"{title} {_primary_artist(artist)}".strip()},
            headers={"User-Agent": USER_AGENT},
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code == 200 and isinstance(resp.json(), list):
            for item in resp.json()[:limit]:
                if not isinstance(item, dict):
                    continue
                out.append(
                    {
                        "source": "lrclib",
                        "song_id": str(item.get("id") or ""),
                        "title": (item.get("trackName") or "").strip(),
                        "artist": (item.get("artistName") or "").strip(),
                        "duration_sec": round(float(item.get("duration") or 0), 2),
                        "_inline_synced": item.get("syncedLyrics") or "",
                    }
                )
    except Exception:
        pass
    return out


def _netease_lyrics_by_id(song_id: str) -> List[dict]:
    if not song_id:
        return []
    try:
        resp = requests.get(
            f"https://music.163.com/api/song/lyric?id={song_id}&lv=-1&tv=-1&kv=-1&yv=-1",
            headers=NETEASE_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        orig = (data.get("lrc") or {}).get("lyric", "")
        trans = (data.get("tlyric") or {}).get("lyric", "")
        yrc = (data.get("yrc") or {}).get("lyric", "")
        structured = build_structured_bilingual_lyrics(orig, trans)
        words = parse_yrc_word_lyrics(yrc)
        if words:
            structured = merge_word_lyrics(words, structured)
        return structured
    except Exception:
        return []


def _qq_lyrics_by_mid(songmid: str) -> List[dict]:
    if not songmid:
        return []
    try:
        resp = requests.get(
            "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
            params={"songmid": songmid, "format": "json", "nobase64": 0, "g_tk": 5381},
            headers=QQ_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        text = resp.text.strip()
        if text.startswith("MusicJsonCallback"):
            text = text[text.index("(") + 1: text.rindex(")")]
        data = json.loads(text)
        if int(data.get("retcode") or 0) != 0:
            return []
        orig = base64.b64decode(data.get("lyric") or "").decode("utf-8", "ignore")
        trans = base64.b64decode(data.get("trans") or "").decode("utf-8", "ignore")
        return build_structured_bilingual_lyrics(orig, trans)
    except Exception:
        return []


def _lrclib_lyrics_by_id(lrc_id: str) -> List[dict]:
    if not lrc_id:
        return []
    try:
        resp = requests.get(
            f"https://lrclib.net/api/get/{lrc_id}",
            headers={"User-Agent": USER_AGENT},
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            synced = resp.json().get("syncedLyrics") or ""
            if synced:
                return build_structured_bilingual_lyrics(synced)
    except Exception:
        pass
    return []


def fetch_lyrics_for_candidate(candidate: dict) -> List[dict]:
    """按候选条目（含 source + song_id）取回结构化歌词"""
    source = (candidate or {}).get("source") or ""
    song_id = str((candidate or {}).get("song_id") or "")
    if source == "netease":
        return _netease_lyrics_by_id(song_id)
    if source == "qq":
        return _qq_lyrics_by_mid(song_id)
    if source == "lrclib":
        inline = (candidate or {}).get("_inline_synced") or ""
        if inline:
            return build_structured_bilingual_lyrics(inline)
        return _lrclib_lyrics_by_id(song_id)
    return []


def _candidate_summary(cand: dict) -> dict:
    """补齐候选的「有无翻译 / 有无逐字 / 首行预览」信息"""
    lines = fetch_lyrics_for_candidate(cand)
    trans = sum(1 for ln in lines if ln.get("translation"))
    words = sum(1 for ln in lines if ln.get("words"))
    preview = ""
    for ln in lines:
        txt = (ln.get("text") or "").strip()
        if not txt or _CREDIT_LINE_RE.match(txt):
            continue
        tr = (ln.get("translation") or "").strip()
        preview = f"{txt}  /  {tr}" if tr else txt
        break
    out = {k: v for k, v in cand.items() if not k.startswith("_")}
    out.update(
        {
            "line_count": len(lines),
            "has_translation": trans > 0,
            "has_words": words > 0,
            "translation_ratio": round(trans / len(lines), 2) if lines else 0.0,
            "preview": preview[:120],
        }
    )
    return out


def search_lyric_candidates(
    title: str,
    artist: str = "",
    duration_sec: float = 0.0,
    limit: int = 8,
    detail_top: int = 3,
) -> List[dict]:
    """
    三源并发检索歌词候选，按「时长接近度 + 歌名相似度」排序，
    并对前 detail_top 条补齐翻译/逐字/预览信息（供用户肉眼判断是否同一首）。
    """
    title = (title or "").strip()
    if not title:
        return []

    collectors = (_netease_search_songs, _qq_search_songs, _lrclib_search_songs)
    raw: List[dict] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fn, title, artist) for fn in collectors]
        for fut in futures:
            try:
                raw.extend(fut.result() or [])
            except Exception:
                pass

    scored: List[Tuple[float, dict]] = []
    for cand in raw:
        if not cand.get("song_id") or not cand.get("title"):
            continue
        dur = float(cand.get("duration_sec") or 0)
        dur_diff = abs(dur - float(duration_sec)) if (dur > 0 and duration_sec > 0) else 12.0
        sim = _title_similarity(cand["title"], title)
        # 过滤明显不是同一首的候选：歌名既不像、时长又差得远
        if sim < 0.34 and dur_diff > 3.0:
            continue
        if duration_sec > 0 and dur > 0 and dur_diff > 60.0:
            continue
        score = dur_diff + (1.0 - sim) * 40.0
        scored.append((score, cand))

    scored.sort(key=lambda item: item[0])
    picked = [cand for _, cand in scored[:limit]]

    results: List[dict] = []
    if picked:
        with ThreadPoolExecutor(max_workers=min(4, len(picked[:detail_top]))) as pool:
            detailed = list(pool.map(_candidate_summary, picked[:detail_top]))
        results.extend(detailed)
        for cand in picked[detail_top:]:
            summary = {k: v for k, v in cand.items() if not k.startswith("_")}
            summary.update({"line_count": 0, "has_translation": False, "has_words": False,
                            "translation_ratio": 0.0, "preview": ""})
            results.append(summary)
    return results


# ============================================================================
# 三源专辑检索（QQ 音乐 / iTunes / 网易云）
# ============================================================================

QQ_ALBUM_COVER = "https://y.qq.com/music/photo_new/T002R800x800M000{}.jpg"


def _qq_album_search(query: str, limit: int = 6) -> List[dict]:
    """QQ 音乐专辑搜索（t=8），返回带曲目数与封面 URL 的轻量候选"""
    out: List[dict] = []
    if not (query or "").strip():
        return out
    try:
        resp = requests.get(
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
            params={"w": query.strip(), "t": 8, "format": "json", "n": limit, "p": 1},
            headers=QQ_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code != 200:
            return out
        albums = (((resp.json().get("data") or {}).get("album") or {}).get("list")) or []
        for alb in albums:
            mid = (alb.get("albumMID") or "").strip()
            if not mid:
                continue
            out.append(
                {
                    "source": "qq",
                    "album_id": mid,
                    "title": _sanitize_title(alb.get("albumName")),
                    "artist": _sanitize_title(alb.get("singerName")),
                    "year": "",
                    "track_count": int(alb.get("song_count") or 0),
                    "cover_url": QQ_ALBUM_COVER.format(mid),
                    "tracks": [],
                }
            )
    except Exception:
        pass
    return out


def _qq_album_detail(album_mid: str) -> Tuple[str, List[dict]]:
    """QQ 专辑详情：返回 (发行年份, [{title, duration_sec, song_id}])"""
    if not album_mid:
        return "", []
    try:
        resp = requests.get(
            "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg",
            params={"albummid": album_mid, "format": "json"},
            headers=QQ_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code != 200:
            return "", []
        data = resp.json().get("data") or {}
        year = (data.get("aDate") or "")[:4]
        if not year.isdigit():
            year = ""
        tracks = []
        for song in (data.get("list") or []):
            tracks.append(
                {
                    "title": _sanitize_title(song.get("songname")),
                    "duration_sec": round(float(song.get("interval") or 0), 2),
                    "song_id": str(song.get("songmid") or ""),
                }
            )
        return year, tracks
    except Exception:
        return "", []


def _netease_album_search(query: str, limit: int = 6) -> List[dict]:
    """网易云专辑搜索（type=10）。注意：详情接口已被风控，只能拿到封面与曲目数"""
    out: List[dict] = []
    if not (query or "").strip():
        return out
    try:
        resp = requests.post(
            "https://music.163.com/api/search/get",
            data={"s": query.strip(), "type": 10, "limit": limit},
            headers=NETEASE_HEADERS,
            timeout=REQ_TIMEOUT,
        )
        if resp.status_code != 200:
            return out
        for alb in (resp.json().get("result", {}).get("albums") or []):
            aid = alb.get("id")
            if not aid:
                continue
            art = alb.get("artist") or {}
            year = ""
            try:
                ts = float(alb.get("publishTime") or 0) / 1000.0
                if ts > 0:
                    year = str(time.localtime(ts).tm_year)
            except Exception:
                year = ""
            out.append(
                {
                    "source": "netease",
                    "album_id": str(aid),
                    "title": _sanitize_title(alb.get("name")),
                    "artist": _sanitize_title(art.get("name")) if isinstance(art, dict) else "",
                    "year": year,
                    "track_count": int(alb.get("size") or 0),
                    "cover_url": (alb.get("picUrl") or "").strip(),
                    "tracks": [],
                }
            )
    except Exception:
        pass
    return out


def _itunes_album_search_lite(query: str, country: str, limit: int = 6) -> List[dict]:
    out: List[dict] = []
    for alb in _itunes_album_candidates(query, country, limit):
        cid = alb.get("collectionId")
        if not cid:
            continue
        out.append(
            {
                "source": "itunes",
                "album_id": str(cid),
                "title": _sanitize_title(alb.get("collectionName")),
                "artist": _sanitize_title(alb.get("artistName")),
                "year": (alb.get("releaseDate") or "")[:4],
                "track_count": int(alb.get("trackCount") or 0),
                "cover_url": (alb.get("artworkUrl100") or "").replace("100x100bb", "600x600bb"),
                "tracks": [],
                "_country": country,
            }
        )
    return out


def _fill_album_tracks(cand: dict) -> dict:
    """按来源补齐候选的曲目表（含每轨可用于直取歌词的 song_id）"""
    source = cand.get("source")
    if source == "qq":
        year, tracks = _qq_album_detail(cand.get("album_id") or "")
        if year and not cand.get("year"):
            cand["year"] = year
        cand["tracks"] = tracks
    elif source == "itunes":
        songs = _itunes_album_songs(cand.get("album_id"), cand.get("_country") or "JP")
        cand["tracks"] = [
            {
                "title": _sanitize_title(s.get("trackName")),
                "duration_sec": round(float(s.get("trackTimeMillis") or 0) / 1000.0, 2),
                "song_id": "",
            }
            for s in songs
        ]
    # 网易云详情接口被风控，曲目表留空
    cand["supports_track_ids"] = any(t.get("song_id") for t in (cand.get("tracks") or []))
    cand["_cleanliness"] = _tracks_cleanliness(cand.get("tracks") or [])
    cand["_dirty_tracks"] = sum(
        1 for t in (cand.get("tracks") or []) if _looks_garbled(t.get("title") or "")
    )
    return cand


def search_album_candidates(
    album_title: str,
    artist: str = "",
    expected_track_count: int = 0,
    detail_top: int = 4,
) -> List[dict]:
    """
    三源并发检索专辑候选，按「专辑名相似度 + 艺术家相似度 + 曲目数差」排序，
    并对前 detail_top 条补齐曲目表（含可用于直取歌词的 song_id）。
    """
    title_in = (album_title or "").strip()
    artist_in = (artist or "").strip()
    if not title_in:
        return []

    artist_key = _primary_artist(artist_in)
    queries = []
    if artist_key:
        queries.append(f"{artist_key} {title_in}")
    queries.append(title_in)

    raw: List[dict] = []

    def _itunes_worker():
        acc: List[dict] = []
        for country in ITUNES_COUNTRIES:
            for q in queries:
                acc.extend(_itunes_album_search_lite(q, country))
        return acc

    def _qq_worker():
        acc: List[dict] = []
        for q in queries:
            acc.extend(_qq_album_search(q))
        return acc

    def _netease_worker():
        acc: List[dict] = []
        for q in queries:
            acc.extend(_netease_album_search(q))
        return acc

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(fn) for fn in (_qq_worker, _itunes_worker, _netease_worker)]
        for fut in futures:
            try:
                raw.extend(fut.result() or [])
            except Exception:
                pass

    seen = set()
    scored: List[Tuple[float, dict]] = []
    for cand in raw:
        key = (cand.get("source"), cand.get("album_id"))
        if key in seen or not cand.get("title"):
            continue
        seen.add(key)

        title_sim = _title_similarity(cand["title"], title_in)
        # 曲库常把 CJK 专辑存成罗马音（平成風俗 → Heisei Fuuzoku），字面相似度接近 0。
        # 这类「一边 CJK、一边纯拉丁」的情况给一个下限分，避免被同名但不同专辑的候选挤掉。
        if _has_cjk(title_in) != _has_cjk(cand["title"]):
            title_sim = max(title_sim, 0.5)
        artist_sim = _title_similarity(cand.get("artist") or "", artist_in) if artist_in else 0.5
        count = int(cand.get("track_count") or 0)
        score = (1.0 - title_sim) * 45.0 + (1.0 - artist_sim) * 35.0
        if expected_track_count and count:
            score += min(abs(count - expected_track_count), 10) * 3.0
        if title_sim < 0.34 and (not artist_in or artist_sim < 0.5):
            continue
        cand["_score"] = round(score, 1)
        cand["_title_sim"] = round(title_sim, 2)
        scored.append((score, cand))

    scored.sort(key=lambda item: item[0])
    # 多取几条，因为补齐曲目表后还要按「曲名干净度」重排，脏候选会被压下去
    picked = [cand for _, cand in scored[: max(detail_top, 6)]]

    if picked:
        with ThreadPoolExecutor(max_workers=min(3, len(picked))) as pool:
            detailed = list(pool.map(_fill_album_tracks, picked))
    else:
        detailed = []

    # ---- 曲名干净度重排 ----
    # 同一张专辑在曲库里常有多个副本（干净版 + 乱码版），专辑名/艺术家/曲目数完全一致，
    # 只能靠曲名本身判定。脏度按比例罚分：全脏 +60，半脏 +30，干净不罚。
    reranked: List[Tuple[float, dict]] = []
    for cand in detailed:
        base = float(cand.get("_score") or 0.0)
        clean = float(cand.get("_cleanliness", 1.0))
        penalty = (1.0 - clean) * 60.0
        cand["_final_score"] = round(base + penalty, 1)
        cand["_garbled"] = int(cand.get("_dirty_tracks") or 0) > 0
        reranked.append((cand["_final_score"], cand))
    reranked.sort(key=lambda item: item[0])

    results = []
    for _, cand in reranked:
        out = {k: v for k, v in cand.items() if not k.startswith("_")}
        out["score"] = cand.get("_final_score", cand.get("_score", 0))
        out["titleSimilarity"] = cand.get("_title_sim", 0)
        out["cleanliness"] = round(float(cand.get("_cleanliness", 1.0)), 2)
        out["garbled"] = bool(cand.get("_garbled", False))
        out["dirtyTracks"] = int(cand.get("_dirty_tracks") or 0)
        results.append(out)
    return results


def prefetch_lyrics_for_tracks(tracks: List[dict], max_workers: int = 4) -> Dict[int, List[dict]]:
    """
    并发预取整张专辑所有音轨的歌词，返回 {track_number: lyrics}。
    用于专辑加载后一次性把全部歌词拉齐，切歌时直接命中缓存，避免每次切歌都等网络。
    """
    results: Dict[int, List[dict]] = {}
    if not tracks:
        return results

    def _work(item: dict):
        try:
            return (
                int(item.get("track_number", 0)),
                lookup_track_lyrics(
                    item.get("title", ""),
                    item.get("artist", ""),
                    float(item.get("duration_sec") or 0.0),
                    item.get("builtin_lrc", "") or "",
                ),
            )
        except Exception:
            return (int(item.get("track_number", 0)), [])

    workers = max(1, min(max_workers, len(tracks)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for tn, lines in pool.map(_work, tracks):
            if lines:
                results[tn] = lines
    return results
