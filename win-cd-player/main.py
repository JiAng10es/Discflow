# -*- coding: utf-8 -*-
"""
main.py
Discflow — Windows 轻量级桌面 CD 音乐播放器 (Apple Music 视觉与动效重构版)。
核心架构：
- 品牌与图标：Discflow 极简黑白 SF Symbols 风格内联 SVG 组件 (支持自适应浅色/深色模式、窗口左上角品牌栏、Favicon 及 Dock/空盘展示)
- 前端渲染：基于 Windows 11 原生 WebView2 (pywebview) 承载 Apple Music 级 CSS3 / Web Animations / Canvas 动态流体背景
- 底层硬件：cd_engine.py (Win32 IOCTL_CDROM_READ_TOC + CD-TEXT + IOCTL_CDROM_RAW_READ + winmm.dll waveOut 零延迟 PCM 流)
- 元数据与歌词：metadata_service.py (MusicBrainz DiscID/TOC 智能匹配 + 多源 Base64 高清封面 + 网易云双语同步 LRC)
"""

import os
import threading
import time
from dataclasses import asdict
from typing import Dict, List, Optional
import webview

from cd_engine import (
    CdDiscToc,
    CddaWaveOutPlayer,
    WindowsCdDriveManager,
)
from library_service import (
    CdRipperTask,
    DiscflowLibraryManager,
    LIBRARY_DIR,
    LocalAlbumInfo,
    RippingProgress,
    load_user_preferences,
    save_user_preferences,
)
from metadata_service import (
    AlbumMetadataResult,
    _bytes_to_data_url,
    lookup_album_metadata,
    fetch_lyrics_for_candidate,
    lookup_track_lyrics,
    match_tracks_by_duration,
    _has_cjk,
    _looks_garbled,
    _sanitize_title,
    download_image_bytes,
    prefetch_lyrics_for_tracks,
    refetch_album_cover,
    search_album_candidates,
    search_lyric_candidates,
)

APP_NAME = "Discflow"


# ============================================================================
# 1. Discflow 极简黑白内联 SVG 图标定义 (SF Symbols 风格 · 24x24 与 512x512 规格)
# ============================================================================
# 24x24 标准响应式内联 SVG 组件（挂载于导航栏左上角与通用图标位）
DISCFLOW_ICON_SVG_24 = """
<svg class="discflow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="Discflow Icon">
  <!-- 圆角方形唱机基座外框 (Squircle Chassis) -->
  <rect x="2.5" y="2.5" width="19" height="19" rx="5.2" />
  <!-- CD 光盘外圆轨迹 -->
  <circle cx="11.2" cy="12" r="5.6" />
  <!-- 光盘表面细腻声波流线 (半透明层次) -->
  <path d="M8.4 9.5a3.8 3.8 0 0 1 2.8-1.1" stroke-width="1.6" opacity="0.45" />
  <path d="M14.0 14.5a3.8 3.8 0 0 1-2.8 1.1" stroke-width="1.6" opacity="0.45" />
  <!-- 同心中心转轴孔 -->
  <circle cx="11.2" cy="12" r="1.6" />
  <!-- 极简穿插式拾音臂与激光唱头 (Tonearm & Pivot) -->
  <circle cx="17.8" cy="6.8" r="1.05" fill="currentColor" stroke="none" />
  <path d="M17.8 6.8v4.8l-2.5 2.2" />
</svg>
""".strip()

# 512x512 高清规格内联 SVG 组件（用于 Dock 栏、高分屏唱片空态展示与 Favicon）
DISCFLOW_ICON_SVG_512 = """
<svg class="discflow-icon-hd" viewBox="0 0 512 512" fill="none" stroke="currentColor" stroke-width="36" stroke-linecap="round" stroke-linejoin="round" aria-label="Discflow App Icon">
  <!-- 平滑连续圆角基座 (Squircle) -->
  <rect x="48" y="48" width="416" height="416" rx="112" />
  <!-- CD 光盘主圆环 -->
  <circle cx="238" cy="256" r="122" />
  <!-- 抽象声波反光弧线 -->
  <path d="M176 202a82 82 0 0 1 62-24" stroke-width="28" opacity="0.42" />
  <path d="M300 310a82 82 0 0 1-62 24" stroke-width="28" opacity="0.42" />
  <!-- 同心中心转轴孔 -->
  <circle cx="238" cy="256" r="35" />
  <!-- 唱臂轴承与拾音流线 -->
  <circle cx="382" cy="144" r="22" fill="currentColor" stroke="none" />
  <path d="M382 144v104l-54 48" />
</svg>
""".strip()


# ============================================================================
# 2. Discflow 前端 UI (Apple Music 设计语言 + 内联 SVG 品牌图标系统)
# ============================================================================
APPLE_MUSIC_HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{APP_NAME}</title>
<!-- 内联 SVG Data-URI Favicon (纯黑白极简 CD 播放器图标) -->
<link rel="icon" type="image/svg+xml" href='data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%23FFFFFF" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="20" rx="5.5" fill="%23000000" stroke="%23FFFFFF" stroke-width="1.8"/><circle cx="11.2" cy="12" r="5.6"/><circle cx="11.2" cy="12" r="1.6"/><circle cx="17.8" cy="6.8" r="1.05" fill="%23FFFFFF" stroke="none"/><path d="M17.8 6.8v4.8l-2.5 2.2"/></svg>' />
<style>
    :root {{
    --dock-bg-rgb: 22, 22, 28;
    --dock-border: rgba(255, 255, 255, 0.16);
    --dock-shadow: 0 20px 48px rgba(0, 0, 0, 0.45), inset 0 1px 0 rgba(255, 255, 255, 0.2);
    --popover-bg: rgba(26, 26, 32, 0.94);
    --popover-border: rgba(255, 255, 255, 0.18);
    --popover-shadow: 0 16px 36px rgba(0, 0, 0, 0.5);
    --scrubber-bg: rgba(255, 255, 255, 0.16);
    --scrubber-fill: var(--text-primary);
    --dock-custom-blur: 40px;
    --lyrics-line-height: 1.4;
    --lyrics-letter-spacing: 0px;
    --lyrics-margin-bottom: 12px;
    /* 逐字歌词卡拉 OK 高亮：已唱部分与未唱部分 */
    --karaoke-fill: var(--text-primary);
    --karaoke-idle: rgba(255, 255, 255, 0.34);
    --dock-custom-width: 720px;
    --dock-custom-opacity: 0.75;
    --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "PingFang SC", "Segoe UI Variable Display", "Microsoft YaHei UI", sans-serif;
    --font-mono: "SF Mono", "Cascadia Mono", "Consolas", monospace;
    --spring-fluid: cubic-bezier(0.22, 1, 0.36, 1);
    --spring-bounce: cubic-bezier(0.34, 1.56, 0.64, 1);

    /* 动态封面提取的 3 组主色调 */
    --mesh-c1: #3b2d54;
    --mesh-c2: #1f3a5f;
    --mesh-c3: #4a2545;

    /* ========================================================================
       流体自适应响应式缩放系统 (Fluid Scale Tokens)
       基准视口: 1120px * 740px，兼顾最小 880x620 与 4K / 全屏扩展
       ======================================================================== */
    --pad-shell-x: clamp(18px, 2.5vw, 42px);
    --pad-shell-y: clamp(14px, 2.2vh, 28px);
    --gap-shell: clamp(10px, 1.5vh, 22px);

    --left-col-max: clamp(340px, 35vw, 560px);
    --artwork-size: clamp(210px, min(34vw, 44vh), 460px);
    --stage-gap: clamp(24px, 3.6vw, 64px);

    --title-size: clamp(19px, 2.1vw, 30px);
    --subtitle-size: clamp(13px, 1.35vw, 19px);
    --meta-gap: clamp(4px, 0.8vh, 10px);
    --scrubber-gap: clamp(5px, 0.9vh, 12px);

    --play-btn-size: clamp(52px, 5.2vw, 76px);
    --play-icon-size: clamp(25px, 2.5vw, 36px);
    --skip-btn-size: clamp(40px, 4.0vw, 56px);
    --skip-icon-size: clamp(24px, 2.3vw, 34px);
    --sec-btn-size: clamp(32px, 3.2vw, 46px);
    --sec-icon-size: clamp(16px, 1.6vw, 24px);
    --transport-gap: clamp(16px, 2.2vw, 34px);

    --vol-icon-size: clamp(16px, 1.5vw, 22px);
    --vol-gap: clamp(10px, 1.2vw, 18px);

    --lyric-orig-size: clamp(22px, 2.4vw, 36px);
    --lyric-trans-size: clamp(14px, 1.4vw, 21px);
    --lyric-item-pad-y: clamp(10px, 1.6vh, 20px);
    --lyric-item-pad-x: clamp(14px, 1.6vw, 24px);

    --queue-title-size: clamp(17px, 1.7vw, 24px);
    --queue-item-pad-y: clamp(9px, 1.3vh, 16px);
    --queue-item-pad-x: clamp(12px, 1.4vw, 20px);
    --queue-track-name: clamp(14px, 1.4vw, 18px);
    --queue-track-sub: clamp(12px, 1.15vw, 15px);
  }}

  /* 严格跟随系统浅色/深色模式，且支持手动覆写 data-theme */
  :root,
  [data-theme="dark"] {{
    --bg-base: #000000;
    --bg-elevated-1: rgba(28, 28, 30, 0.68);
    --bg-elevated-2: rgba(44, 44, 46, 0.62);
    --bg-pill: rgba(255, 255, 255, 0.09);
    --bg-pill-hover: rgba(255, 255, 255, 0.16);
    --bg-pill-active: rgba(255, 255, 255, 0.94);
    --text-pill-active: #000000;
    --scrim-overlay: rgba(10, 10, 14, 0.56);
    --border-hairline: rgba(255, 255, 255, 0.11);
    --text-primary: rgba(255, 255, 255, 0.94);
    --text-secondary: rgba(255, 255, 255, 0.58);
    --text-tertiary: rgba(255, 255, 255, 0.36);
    --track-bar-bg: rgba(255, 255, 255, 0.22);
    --track-bar-fill: rgba(255, 255, 255, 0.92);
    --lyric-glow: rgba(255, 255, 255, 0.22);
    /* 极简黑白 App Icon 语义色 (深色模式下白线黑底) */
    --app-icon-bg: #000000;
    --app-icon-fg: #FFFFFF;
    --app-icon-border: rgba(255, 255, 255, 0.22);
  }}

  @media (prefers-color-scheme: light) {{
    :root:not([data-theme="dark"]) {{
      --bg-base: #F5F5F7;
      --bg-elevated-1: rgba(255, 255, 255, 0.72);
      --bg-elevated-2: rgba(242, 242, 247, 0.78);
      --bg-pill: rgba(0, 0, 0, 0.06);
      --bg-pill-hover: rgba(0, 0, 0, 0.11);
      --bg-pill-active: rgba(0, 0, 0, 0.88);
      --text-pill-active: #FFFFFF;
      --scrim-overlay: rgba(245, 245, 247, 0.64);
      --border-hairline: rgba(0, 0, 0, 0.08);
      --text-primary: rgba(0, 0, 0, 0.88);
      --text-secondary: rgba(60, 60, 67, 0.62);
      --text-tertiary: rgba(60, 60, 67, 0.38);
      --track-bar-bg: rgba(0, 0, 0, 0.14);
      --track-bar-fill: rgba(0, 0, 0, 0.84);
      --lyric-glow: rgba(0, 0, 0, 0.08);
      /* 极简黑白 App Icon 语义色 (浅色模式下黑线白底) */
      --app-icon-bg: #FFFFFF;
      --app-icon-fg: #000000;
      --app-icon-border: rgba(0, 0, 0, 0.14);
    }}
  }}

  [data-theme="light"] {{
    --bg-base: #F5F5F7;
    --bg-elevated-1: rgba(255, 255, 255, 0.88);
    --bg-elevated-2: rgba(242, 242, 247, 0.94);
    --bg-pill: rgba(0, 0, 0, 0.06);
    --bg-pill-hover: rgba(0, 0, 0, 0.11);
    --bg-pill-active: rgba(0, 0, 0, 0.88);
    --text-pill-active: #FFFFFF;
    --scrim-overlay: rgba(245, 245, 247, 0.64);
    --border-hairline: rgba(0, 0, 0, 0.1);
    --text-primary: #1D1D1F;
    --text-secondary: #515154;
    --text-tertiary: #86868B;
    --karaoke-idle: rgba(0, 0, 0, 0.28);
    --track-bar-bg: rgba(0, 0, 0, 0.12);
    --track-bar-fill: rgba(0, 0, 0, 0.85);
    --lyric-glow: rgba(0, 0, 0, 0.06);
    --app-icon-bg: #FFFFFF;
    --app-icon-fg: #000000;
    --app-icon-border: rgba(0, 0, 0, 0.12);

    --dock-bg-rgb: 255, 255, 255;
    --dock-border: rgba(0, 0, 0, 0.12);
    --dock-shadow: 0 16px 40px rgba(0, 0, 0, 0.12), inset 0 1px 0 rgba(255, 255, 255, 0.9);
    --popover-bg: rgba(255, 255, 255, 0.94);
    --popover-border: rgba(0, 0, 0, 0.12);
    --popover-shadow: 0 16px 36px rgba(0, 0, 0, 0.15);
    --scrubber-bg: rgba(0, 0, 0, 0.12);
    --scrubber-fill: #1D1D1F;
  }}

  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    user-select: none;
    -webkit-user-select: none;
  }}

  html, body {{
    width: 100%;
    height: 100%;
    overflow: hidden;
    background-color: var(--bg-base);
    color: var(--text-primary);
    font-family: var(--font-sans);
    -webkit-font-smoothing: antialiased;
    transition: background-color 0.6s var(--spring-fluid), color 0.4s ease;
  }}

  /* ==========================================================================
     1. Discflow 独立极简黑白 App Icon 组件样式 (支持导航栏、Dock 与空状态复用)
     ========================================================================== */
  .discflow-brand {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 3px 10px 3px 6px;
    margin-right: 4px;
    border-right: 1px solid var(--border-hairline);
    cursor: pointer;
    border-radius: 9px;
    transition: background-color 0.2s ease, transform 0.2s var(--spring-bounce);
  }}

  .discflow-brand:hover {{
    background: var(--bg-pill-hover);
    transform: scale(1.02);
  }}

  .discflow-brand:active {{
    transform: scale(0.96);
  }}

  .discflow-app-icon-badge {{
    width: 28px;
    height: 28px;
    border-radius: 7px;
    background-color: var(--app-icon-bg);
    color: var(--app-icon-fg);
    border: 1px solid var(--app-icon-border);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
    transition: transform 0.28s var(--spring-bounce), background-color 0.35s ease, color 0.35s ease;
    flex-shrink: 0;
  }}

  .discflow-brand:hover .discflow-app-icon-badge {{
    transform: scale(1.06);
  }}

  .discflow-app-icon-badge .discflow-icon {{
    width: 20px;
    height: 20px;
    display: block;
  }}

  .discflow-wordmark {{
    font-size: 15px;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: var(--text-primary);
  }}

  /* ==========================================================================
     2. Apple Music 动态专辑流体光斑背景层 (Fluid Mesh Gradient + Frosted Glass)
     ========================================================================== */
  .ambient-stage {{
    position: fixed;
    inset: 0;
    z-index: 0;
    overflow: hidden;
    pointer-events: none;
  }}

  .mesh-canvas {{
    position: absolute;
    inset: -18%;
    opacity: 0;
    filter: blur(68px) saturate(195%);
    transform: scale(1.08);
    transition: opacity 1.2s var(--spring-fluid);
  }}

  body.is-playing .mesh-canvas.has-cover {{
    opacity: 0.86;
  }}

  body:not(.is-playing) .mesh-canvas.has-cover {{
    opacity: 0.28;
  }}

  .mesh-blob {{
    position: absolute;
    border-radius: 50%;
    mix-blend-mode: screen;
    transition: background-color 1.4s ease;
    will-change: transform;
  }}

  .blob-1 {{
    width: 58vw;
    height: 58vw;
    top: -10%;
    left: -8%;
    background: radial-gradient(circle, var(--mesh-c1) 0%, transparent 72%);
    animation: floatBlob1 22s ease-in-out infinite alternate;
  }}

  .blob-2 {{
    width: 62vw;
    height: 62vw;
    bottom: -14%;
    right: -10%;
    background: radial-gradient(circle, var(--mesh-c2) 0%, transparent 72%);
    animation: floatBlob2 26s ease-in-out infinite alternate;
  }}

  .blob-3 {{
    width: 50vw;
    height: 50vw;
    top: 22%;
    left: 32%;
    background: radial-gradient(circle, var(--mesh-c3) 0%, transparent 70%);
    animation: floatBlob3 19s ease-in-out infinite alternate;
  }}

  @keyframes floatBlob1 {{
    0%   {{ transform: translate3d(0, 0, 0) scale(1) rotate(0deg); }}
    50%  {{ transform: translate3d(12%, 10%, 0) scale(1.14) rotate(18deg); }}
    100% {{ transform: translate3d(-6%, 18%, 0) scale(0.95) rotate(-12deg); }}
  }}

  @keyframes floatBlob2 {{
    0%   {{ transform: translate3d(0, 0, 0) scale(1) rotate(0deg); }}
    50%  {{ transform: translate3d(-14%, -8%, 0) scale(1.12) rotate(-22deg); }}
    100% {{ transform: translate3d(8%, -16%, 0) scale(1.04) rotate(15deg); }}
  }}

  @keyframes floatBlob3 {{
    0%   {{ transform: translate3d(0, 0, 0) scale(0.96); }}
    50%  {{ transform: translate3d(-10%, 12%, 0) scale(1.18); }}
    100% {{ transform: translate3d(14%, -10%, 0) scale(0.92); }}
  }}

  /* Apple Music 风格：把当前封面放大、重模糊后整页铺底，作为播放页的环境底色 */
  .art-backdrop {{
    position: absolute;
    inset: -14%;
    background-position: center;
    background-size: cover;
    background-repeat: no-repeat;
    filter: blur(76px) saturate(195%);
    transform: scale(1.12);
    opacity: 0;
    transition: opacity 1.1s var(--spring-fluid);
    will-change: opacity;
    pointer-events: none;
  }}

  body.has-artwork .art-backdrop {{
    opacity: 0.74;
  }}

  body.has-artwork:not(.is-playing) .art-backdrop {{
    opacity: 0.5;
  }}

  /* 上下渐变压暗 / 提亮，保证标题与底栏文字始终清晰，同时保留中央的色彩氛围 */
  .ambient-vignette {{
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.9s ease;
    background:
      radial-gradient(125% 92% at 50% 42%, rgba(0, 0, 0, 0) 36%, rgba(0, 0, 0, 0.36) 100%),
      linear-gradient(
        180deg,
        rgba(0, 0, 0, 0.3) 0%,
        rgba(0, 0, 0, 0) 24%,
        rgba(0, 0, 0, 0) 72%,
        rgba(0, 0, 0, 0.38) 100%
      );
  }}

  body.has-artwork .ambient-vignette {{
    opacity: 1;
  }}

  .frosted-scrim {{
    position: absolute;
    inset: 0;
    background: var(--scrim-overlay);
    backdrop-filter: blur(54px) saturate(180%);
    -webkit-backdrop-filter: blur(54px) saturate(180%);
    transition: background-color 0.6s var(--spring-fluid);
  }}

  /* ==========================================================================
     3. 顶层应用骨架与导航头部栏
     ========================================================================== */
  .app-shell {{
    position: relative;
    z-index: 1;
    display: flex;
    flex-direction: column;
    width: 100vw;
    height: 100vh;
    padding: var(--pad-shell-y) var(--pad-shell-x);
    gap: var(--gap-shell);
  }}

  .top-bar {{
    position: relative;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 52px;
    padding: 0 16px;
    background: transparent;
    border: none;
    box-shadow: none;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    z-index: 100;
  }}

  .top-floating-left,
  .top-floating-right {{
    display: flex;
    align-items: center;
    gap: 10px;
  }}

  .top-icon-btn {{
    width: 38px;
    height: 38px;
    border-radius: 50%;
    background: rgba(28, 28, 32, 0.65);
    backdrop-filter: blur(28px);
    -webkit-backdrop-filter: blur(28px);
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.32);
    color: var(--text-primary);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: transform 0.18s var(--spring-bounce), background-color 0.2s ease, border-color 0.2s ease;
  }}

  .top-icon-btn:hover {{
    transform: scale(1.08);
    background: rgba(44, 44, 52, 0.85);
    border-color: rgba(255, 255, 255, 0.3);
  }}

  .top-icon-btn:active {{
    transform: scale(0.94);
  }}

  /* 光驱状态指示灯（样式统一收敛到 .status-dot 一处定义，见下方） */

  /* Discflow 专属悬浮 Popover 菜单 */
  .discflow-popover-menu {{
    position: absolute;
    top: 54px;
    left: 16px;
    min-width: 190px;
    background: rgba(24, 24, 28, 0.85);
    backdrop-filter: blur(40px) saturate(190%);
    -webkit-backdrop-filter: blur(40px) saturate(190%);
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow: 0 20px 48px rgba(0, 0, 0, 0.48), inset 0 1px 0 rgba(255, 255, 255, 0.2);
    border-radius: 18px;
    padding: 6px;
    display: none;
    flex-direction: column;
    gap: 4px;
    z-index: 120;
    user-select: none;
    animation: menuFadeIn 0.22s var(--spring-bounce);
  }}

  .discflow-popover-menu.is-open {{
    display: flex;
  }}

  @keyframes menuFadeIn {{
    from {{ opacity: 0; transform: translateY(-8px) scale(0.96); }}
    to   {{ opacity: 1; transform: translateY(0) scale(1); }}
  }}

  .discflow-menu-item {{
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 13.5px;
    font-weight: 500;
    color: var(--text-primary);
    cursor: pointer;
    transition: background-color 0.16s ease, color 0.16s ease;
  }}

  .discflow-menu-item:hover {{
    background: rgba(255, 255, 255, 0.14);
    color: #fff;
  }}

  .discflow-menu-divider {{
    height: 1px;
    background: rgba(255, 255, 255, 0.1);
    margin: 4px 6px;
  }}

  .status-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #30D158;
    box-shadow: 0 0 8px rgba(48, 209, 88, 0.55);
    transition: background-color 0.3s ease, box-shadow 0.3s ease, opacity 0.3s ease;
    flex-shrink: 0;
  }}

  /* 只有真正在读盘时才呼吸闪烁，空闲时常亮，避免整屏一直在动 */
  @keyframes statusDotPulse {{
    0%, 100% {{
      transform: scale(1);
      box-shadow: 0 0 8px rgba(255, 214, 10, 0.55);
    }}
    50% {{
      transform: scale(1.18);
      box-shadow: 0 0 15px rgba(255, 214, 10, 0.95);
    }}
  }}

  .status-dot.loading {{
    background: #FFD60A;
    box-shadow: 0 0 10px rgba(255, 214, 10, 0.6);
    animation: statusDotPulse 1.4s ease-in-out infinite;
  }}

  /* 无光盘 / 无可用光驱：暗灰，明确表示「没东西可播」 */
  .status-dot.idle {{
    background: var(--text-tertiary);
    box-shadow: none;
    opacity: 0.55;
  }}

  .drive-select {{
    appearance: none;
    background: var(--bg-pill);
    color: var(--text-primary);
    border: 1px solid var(--border-hairline);
    border-radius: 10px;
    padding: 5px 28px 5px 12px;
    font-family: var(--font-sans);
    font-size: clamp(11.5px, 1.15vw, 13.5px);
    font-weight: 600;
    cursor: pointer;
    outline: none;
    transition: background-color 0.2s ease, transform 0.2s var(--spring-fluid);
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='rgba(150,150,160,0.85)' d='M1 1l4 4 4-4' stroke='rgba(150,150,160,0.85)' stroke-width='1.5' stroke-linecap='round' fill='none'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 10px center;
  }}

  .drive-select:hover {{
    background-color: var(--bg-pill-hover);
  }}

  .drive-select option {{
    background: #1C1C1E;
    color: #FFFFFF;
  }}

  .pill-btn {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: clamp(28px, 3.4vh, 34px);
    padding: 0 clamp(10px, 1.1vw, 16px);
    border-radius: 10px;
    border: 1px solid transparent;
    background: var(--bg-pill);
    color: var(--text-primary);
    font-family: var(--font-sans);
    font-size: clamp(11px, 1.1vw, 13px);
    font-weight: 600;
    cursor: pointer;
    flex-shrink: 0;
    transition: transform 0.22s var(--spring-bounce), background-color 0.2s ease, color 0.2s ease;
  }}

  .pill-btn:hover {{
    background: var(--bg-pill-hover);
  }}

  .pill-btn:active {{
    transform: scale(0.92);
  }}

  .pill-btn.active {{
    background: var(--bg-pill-active);
    color: var(--text-pill-active);
  }}

  .pill-btn svg {{
    width: 14px;
    height: 14px;
    fill: currentColor;
  }}

  .status-text {{
    font-size: clamp(11.5px, 1.15vw, 13.5px);
    font-weight: 500;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: clamp(220px, 32vw, 480px);
  }}

  /* 顶部毛玻璃下拉菜单与设置弹窗 */
  .popup-modal-overlay {{
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(0, 0, 0, 0.45);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    display: none;
    align-items: center;
    justify-content: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.28s var(--spring-fluid);
  }}

  .popup-modal-overlay.open {{
    display: flex;
    opacity: 1;
    pointer-events: auto;
  }}

  .modal-dialog {{
    width: clamp(340px, 42vw, 480px);
    background: var(--bg-elevated-1);
    border: 1px solid var(--border-hairline);
    border-radius: 20px;
    padding: clamp(18px, 2.4vw, 28px);
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.48);
    display: flex;
    flex-direction: column;
    gap: 16px;
    transform: scale(0.92);
    transition: transform 0.28s var(--spring-bounce);
  }}

  .popup-modal-overlay.open .modal-dialog {{
    transform: scale(1.0);
  }}

  .modal-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}

  .modal-title {{
    font-size: clamp(16px, 1.6vw, 20px);
    font-weight: 700;
    color: var(--text-primary);
  }}

  .modal-close-btn {{
    background: transparent;
    border: none;
    color: var(--text-secondary);
    cursor: pointer;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: background-color 0.2s ease, color 0.2s ease;
  }}

  .modal-close-btn:hover {{
    background: var(--bg-pill-hover);
    color: var(--text-primary);
  }}

  .modal-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
  }}

  .modal-item {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-radius: 12px;
    background: var(--bg-pill);
    color: var(--text-primary);
    font-size: 13.5px;
    font-weight: 500;
    cursor: pointer;
    transition: background-color 0.2s ease, transform 0.18s var(--spring-bounce);
  }}

  .modal-item:hover {{
    background: var(--bg-pill-hover);
  }}

  .modal-item:active {{
    transform: scale(0.98);
  }}

  .modal-item-sub {{
    font-size: 12px;
    color: var(--text-tertiary);
  }}

  /* ==========================================================================
     4. 主舞台布局：左侧唱片封面与控制器 + 右侧沉浸式歌词 / 音轨队列
     ========================================================================== */
  .main-stage {{
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: var(--left-col-max) 1fr;
    gap: var(--stage-gap);
    align-items: center;
    padding: 4px clamp(8px, 1.2vw, 24px);
    transition: grid-template-columns 0.55s var(--spring-fluid);
  }}

  .main-stage.single-column {{
    grid-template-columns: 1fr;
    justify-items: center;
  }}

  .main-stage.single-column .right-stage {{
    display: none;
  }}

  .player-column {{
    width: 100%;
    max-width: var(--left-col-max);
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: clamp(14px, 2.0vh, 26px);
  }}

  /* 封面容器与弥散环境光阴影 (Ambient Shadow) - 1:1 动态流体缩放与背景无缝融合 */
  .artwork-stage {{
    position: relative;
    width: var(--artwork-size);
    height: var(--artwork-size);
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }}

  /* 经典 Apple Music 专辑卡片立体弥散环境光阴影 (Ambient Shadow) - 径向羽化与无缝渐进融合 */
  .artwork-ambient-shadow {{
    position: absolute;
    inset: -28%;
    border-radius: 50%;
    background-size: cover;
    background-position: center;
    filter: blur(clamp(48px, 6.5vw, 84px)) saturate(165%);
    opacity: 0.26;
    transform: scale(0.92);
    transition: transform 0.8s var(--spring-fluid), opacity 0.8s var(--spring-fluid);
    z-index: 0;
    pointer-events: none;
    mask-image: radial-gradient(closest-side, rgba(0, 0, 0, 0.95) 25%, rgba(0, 0, 0, 0.45) 65%, transparent 100%);
    -webkit-mask-image: radial-gradient(closest-side, rgba(0, 0, 0, 0.95) 25%, rgba(0, 0, 0, 0.45) 65%, transparent 100%);
  }}

  body.is-playing .artwork-ambient-shadow {{
    opacity: 0.55;
    transform: scale(1.04);
  }}

  /* 经典 Apple Music 标志性精致悬浮专辑卡片 (带高质感圆角、超细边框与立体双层投影) */
  .artwork-card {{
    position: relative;
    z-index: 1;
    width: 100%;
    height: 100%;
    border-radius: clamp(18px, 2.4vw, 32px);
    overflow: hidden;
    background: var(--bg-elevated-2);
    border: 1px solid var(--border-hairline);
    box-shadow: 0 clamp(14px, 2.2vh, 28px) clamp(32px, 4.2vw, 64px) rgba(0, 0, 0, 0.45), 0 4px 16px rgba(0, 0, 0, 0.3);
    transform: scale(0.90);
    transition: transform 0.68s var(--spring-fluid), box-shadow 0.68s var(--spring-fluid);
  }}

  body.is-playing .artwork-card {{
    transform: scale(1.0);
    box-shadow: 0 clamp(20px, 3.0vh, 38px) clamp(44px, 5.4vw, 84px) rgba(0, 0, 0, 0.52), 0 6px 20px rgba(0, 0, 0, 0.35);
  }}

  .artwork-img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    opacity: 0;
    transition: opacity 0.45s ease;
  }}

  .artwork-img.loaded {{
    opacity: 1;
  }}

  /* 无封面时的 Discflow 512x512 极简黑白线条图标占位舞台 */
  .disc-placeholder {{
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--app-icon-bg);
    color: var(--app-icon-fg);
    transition: background-color 0.4s ease, color 0.4s ease;
  }}

  .disc-placeholder .discflow-icon-hd {{
    width: 68%;
    height: 68%;
    opacity: 0.90;
    transition: transform 0.68s var(--spring-fluid);
  }}

  body.is-playing .disc-placeholder .discflow-icon-hd {{
    transform: scale(1.04);
  }}

  /* --- 曲目与艺术家信息区 (流体伸缩) --- */

  .track-title {{
    font-size: var(--title-size);
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  /* --- Apple Music 超细弹性进度条 (Scrubber) --- */

  .scrubber-track {{
    position: relative;
    width: 100%;
    height: clamp(3.5px, 0.45vh, 5.5px);
    border-radius: 999px;
    background: var(--track-bar-bg);
    overflow: hidden;
    transition: height 0.22s var(--spring-fluid), background-color 0.2s ease;
  }}

  .scrubber-fill {{
    height: 100%;
    width: 0%;
    border-radius: 999px;
    background: var(--track-bar-fill);
    transition: width 0.1s linear;
  }}

  /* --- SF Symbols 播放控制按钮组 (等比例放大缩减) --- */

  /* --- 音量控制条与底部功能切换开关 --- */

  .vol-track {{
    width: 100%;
    height: clamp(3.5px, 0.45vh, 5.5px);
    border-radius: 999px;
    background: var(--track-bar-bg);
    overflow: hidden;
    transition: height 0.22s var(--spring-fluid);
  }}

  .vol-fill {{
    height: 100%;
    width: 80%;
    border-radius: 999px;
    background: var(--track-bar-fill);
  }}

  /* ==========================================================================
     5. 右侧：Apple Music 沉浸式柔焦滚动歌词 + 音轨列表抽屉
     ========================================================================== */
  .right-stage {{
    position: relative;
    height: 100%;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}

  .lyrics-viewport {{
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 34vh clamp(14px, 2.2vw, 32px) 38vh clamp(10px, 1.4vw, 20px);
    scroll-behavior: smooth;
    mask-image: linear-gradient(
      to bottom,
      transparent 0%,
      black 14%,
      black 84%,
      transparent 100%
    );
    -webkit-mask-image: linear-gradient(
      to bottom,
      transparent 0%,
      black 14%,
      black 84%,
      transparent 100%
    );
  }}

  .lyrics-viewport::-webkit-scrollbar,
  .queue-viewport::-webkit-scrollbar {{
    width: 0px;
    background: transparent;
  }}

  .lyric-item {{
    padding: var(--lyric-item-pad-y) var(--lyric-item-pad-x);
    margin-bottom: clamp(6px, 1.0vh, 14px);
    border-radius: clamp(12px, 1.5vh, 18px);
    cursor: pointer;
    transform-origin: left center;
    transform: scale(0.95);
    opacity: 0.32;
    filter: blur(1.35px);
    transition:
      transform 0.58s var(--spring-fluid),
      opacity 0.48s ease-out,
      filter 0.48s ease-out,
      background-color 0.25s ease;
    will-change: transform, opacity, filter;
  }}

  .lyric-item.near {{
    opacity: 0.46;
    filter: blur(0.75px);
    transform: scale(0.97);
  }}

  .lyric-item.active {{
    opacity: 1.0;
    filter: blur(0px);
    transform: scale(1.03) translateX(4px);
    text-shadow: 0 4px 26px var(--lyric-glow);
  }}

  .lyric-item:hover {{
    opacity: 0.88;
    filter: blur(0px);
    background: var(--bg-pill);
  }}

  .lyric-item:active {{
    transform: scale(0.98);
  }}

  .lyric-item {{
    margin-bottom: var(--lyrics-margin-bottom, clamp(6px, 1.0vh, 14px)) !important;
  }}

  .lyric-orig {{
    font-size: var(--lyric-orig-size);
    font-weight: 700;
    line-height: var(--lyrics-line-height, 1.34);
    letter-spacing: var(--lyrics-letter-spacing, -0.015em);
    color: var(--text-primary);
    word-break: break-word;
  }}

  /* 逐字卡拉 OK 歌词：每个字词独立 span，按播放进度用渐变裁切做填充高亮 */
  .lyric-word {{
    display: inline-block;
    white-space: pre;
  }}

  .lyric-item.active .lyric-word {{
    background-image: linear-gradient(
      90deg,
      var(--karaoke-fill) 0%,
      var(--karaoke-fill) var(--wp, 0%),
      var(--karaoke-idle) var(--wp, 0%),
      var(--karaoke-idle) 100%
    );
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    color: transparent;
  }}

  .lyric-trans {{
    margin-top: clamp(4px, 0.7vh, 8px);
    font-size: var(--lyric-trans-size);
    font-weight: 600;
    line-height: var(--lyrics-line-height, 1.4);
    letter-spacing: var(--lyrics-letter-spacing, normal);
    color: var(--text-secondary);
  }}

  .lyrics-empty {{
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    justify-content: center;
    padding-left: clamp(12px, 2vw, 28px);
    gap: 10px;
  }}

  .lyrics-empty-title {{
    font-size: clamp(20px, 2.2vw, 28px);
    font-weight: 700;
    color: var(--text-secondary);
  }}

  .lyrics-empty-sub {{
    font-size: clamp(13px, 1.3vw, 16px);
    color: var(--text-tertiary);
  }}

  .queue-viewport {{
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    padding: 12px 16px 24px 8px;
    display: none;
    flex-direction: column;
    gap: clamp(4px, 0.7vh, 8px);
  }}

  .queue-header {{
    padding: 8px 14px 12px 14px;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
  }}

  .queue-header-title {{
    font-size: var(--queue-title-size);
    font-weight: 700;
    color: var(--text-primary);
  }}

  .queue-header-meta {{
    font-size: clamp(11px, 1.1vw, 13px);
    font-family: var(--font-mono);
    color: var(--text-tertiary);
  }}

  .track-row {{
    display: flex;
    align-items: center;
    gap: clamp(10px, 1.2vw, 18px);
    padding: var(--queue-item-pad-y) var(--queue-item-pad-x);
    border-radius: clamp(10px, 1.3vh, 16px);
    cursor: pointer;
    transition: background-color 0.2s ease, transform 0.2s var(--spring-fluid);
  }}

  .track-row:hover {{
    background: var(--bg-pill);
  }}

  .track-row:active {{
    transform: scale(0.985);
  }}

  .track-row.active {{
    background: var(--bg-pill-hover);
  }}

  .track-num-col {{
    width: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: clamp(11.5px, 1.15vw, 13.5px);
    font-weight: 600;
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
  }}

  .eq-bars {{
    display: inline-flex;
    align-items: flex-end;
    gap: 2.5px;
    height: 13px;
  }}

  .eq-bar {{
    width: 3px;
    background: var(--text-primary);
    border-radius: 2px;
    animation: eqPulse 0.85s ease-in-out infinite alternate;
    animation-play-state: paused;
  }}

  body.is-playing .eq-bar {{
    animation-play-state: running;
  }}

  .eq-bar:nth-child(1) {{ height: 45%; animation-delay: 0.0s; }}
  .eq-bar:nth-child(2) {{ height: 95%; animation-delay: 0.22s; }}
  .eq-bar:nth-child(3) {{ height: 60%; animation-delay: 0.41s; }}

  @keyframes eqPulse {{
    0%   {{ height: 25%; }}
    100% {{ height: 100%; }}
  }}

  .track-row-info {{
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}

  .track-row-name {{
    font-size: var(--queue-track-name);
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .track-row-artist {{
    font-size: var(--queue-track-sub);
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .track-row-dur {{
    font-size: clamp(11.5px, 1.15vw, 13.5px);
    font-weight: 500;
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
  }}

  /* --- 3 独立互斥高亮播放模式按钮组 (参考 Apple Music) --- */
  .play-mode-group {{
    display: inline-flex;
    align-items: center;
    background: var(--bg-pill);
    border: 1px solid var(--border-hairline);
    border-radius: 999px;
    padding: 2.5px;
    gap: 3px;
    flex-shrink: 0;
  }}

  .mode-pill-btn {{
    display: inline-flex;
    align-items: center;
    gap: 5px;
    border: none;
    background: transparent;
    color: var(--text-secondary);
    padding: 3.5px 10px;
    border-radius: 999px;
    font-family: var(--font-sans);
    font-size: 11.5px;
    font-weight: 600;
    cursor: pointer;
    outline: none;
    transition: all 0.22s var(--spring-bounce);
  }}

  .mode-pill-btn:hover {{
    color: var(--text-primary);
    background: rgba(255, 255, 255, 0.08);
  }}

  .mode-pill-btn.active {{
    background: var(--bg-pill-active);
    color: var(--text-pill-active);
    box-shadow: 0 1px 8px rgba(0, 0, 0, 0.25);
  }}

  .mode-pill-btn svg {{
    width: 13px;
    height: 13px;
    fill: currentColor;
  }}

  /* 手动匹配按钮：描边图标 */
  .match-meta-btn svg {{
    fill: none;
    stroke: currentColor;
    stroke-width: 2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }}

  .match-meta-input {{
    width: 100%;
    box-sizing: border-box;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--border-hairline);
    background: var(--bg-pill);
    color: var(--text-primary);
    font-size: 13px;
    font-family: var(--font-sans);
    outline: none;
    transition: border-color 0.2s ease, background-color 0.2s ease;
  }}

  .match-meta-input:focus {{
    border-color: var(--text-tertiary);
    background: var(--bg-pill-hover);
  }}

  /* 曲目行内的「词」按钮：默认淡出，悬停或已锁定时显现 */
  .track-lyric-btn {{
    flex-shrink: 0;
    width: 24px;
    height: 24px;
    margin-left: 8px;
    border-radius: 6px;
    border: 1px solid var(--border-hairline);
    background: transparent;
    color: var(--text-tertiary);
    font-size: 11px;
    font-family: var(--font-sans);
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.18s ease, color 0.18s ease, background-color 0.18s ease, border-color 0.18s ease;
  }}

  .track-row:hover .track-lyric-btn {{
    opacity: 1;
  }}

  .track-lyric-btn:hover {{
    color: var(--text-primary);
    background: var(--bg-pill-hover);
    border-color: var(--text-tertiary);
  }}

  .track-row.is-lyric-locked .track-lyric-btn {{
    opacity: 1;
    color: var(--text-primary);
    border-color: var(--text-tertiary);
  }}

  /* 批量匹配没对上的曲目：曲目名后挂一个「歌词待匹配」小标，并让「词」按钮常亮，
     引导用户点它逐轨手动指定（专辑一键匹配优先，单曲匹配兜底） */
  .track-row.needs-lyric .track-lyric-btn {{
    opacity: 1;
    color: var(--text-primary);
    border-color: var(--text-tertiary);
    background: var(--bg-pill-hover);
  }}

  .lyric-miss-badge {{
    display: inline-block;
    margin-left: 8px;
    padding: 1px 6px;
    border-radius: 4px;
    border: 1px dashed var(--text-tertiary);
    color: var(--text-tertiary);
    font-size: 10px;
    line-height: 15px;
    vertical-align: middle;
    letter-spacing: 0.02em;
    opacity: 0.85;
  }}

  /* 歌词候选列表 */
  .lyric-candidate-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 320px;
    overflow-y: auto;
    padding-right: 2px;
  }}

  .lyric-cand {{
    border: 1px solid var(--border-hairline);
    border-radius: 10px;
    padding: 9px 11px;
    cursor: pointer;
    transition: background-color 0.16s ease, border-color 0.16s ease;
  }}

  .lyric-cand:hover {{
    background: var(--bg-pill);
    border-color: var(--text-tertiary);
  }}

  .lyric-cand-top {{
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 3px;
    min-width: 0;
  }}

  .lyric-cand-src {{
    flex-shrink: 0;
    font-size: 10.5px;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--bg-pill);
    color: var(--text-secondary);
  }}

  .lyric-cand-title {{
    font-size: 13px;
    font-weight: 500;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .lyric-cand-artist {{
    font-size: 11.5px;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .lyric-cand-dur {{
    margin-left: auto;
    flex-shrink: 0;
    font-size: 11.5px;
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
  }}

  .lyric-cand-meta {{
    font-size: 11px;
    color: var(--text-tertiary);
    margin-bottom: 2px;
  }}

  .lyric-cand-meta b {{
    font-weight: 500;
    color: var(--text-secondary);
  }}

  .lyric-cand-preview {{
    font-size: 11.5px;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  /* 专辑候选列表 */
  .album-cand-list {{
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-height: 340px;
    overflow-y: auto;
    padding-right: 2px;
  }}

  .album-cand {{
    display: flex;
    gap: 12px;
    border: 1px solid var(--border-hairline);
    border-radius: 10px;
    padding: 9px 11px;
    cursor: pointer;
    transition: background-color 0.16s ease, border-color 0.16s ease;
  }}

  .album-cand:hover {{
    background: var(--bg-pill);
    border-color: var(--text-tertiary);
  }}

  .album-cand-cover {{
    width: 48px;
    height: 48px;
    border-radius: 6px;
    object-fit: cover;
    flex-shrink: 0;
    background: var(--bg-pill);
  }}

  .album-cand-body {{
    min-width: 0;
    flex: 1;
  }}

  .album-cand-top {{
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 3px;
    min-width: 0;
  }}

  .album-cand-src {{
    flex-shrink: 0;
    font-size: 10.5px;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--bg-pill);
    color: var(--text-secondary);
  }}

  .album-cand-title {{
    font-size: 13px;
    font-weight: 500;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .album-cand-year {{
    margin-left: auto;
    flex-shrink: 0;
    font-size: 11.5px;
    color: var(--text-tertiary);
  }}

  .album-cand-meta {{
    font-size: 11px;
    color: var(--text-tertiary);
    margin-bottom: 2px;
  }}

  .album-cand-meta b {{
    font-weight: 500;
    color: var(--text-secondary);
  }}

  .album-cand-warn {{
    color: #c9803a;
    border-bottom: 1px dashed rgba(201, 128, 58, 0.55);
  }}

  .album-cand-tracks {{
    font-size: 11.5px;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .lyric-cand-empty {{
    font-size: 12px;
    color: var(--text-tertiary);
    padding: 14px 2px;
  }}

  /* 资源库搜索相关样式见下方 .library-search-box-wrap */
  /* 资源库搜索框：常驻可见（v2.0 修掉了旧版 width:0 + opacity:0 导致搜索框根本看不见的 bug） */
  .library-search-box-wrap {{
    display: flex;
    align-items: center;
    gap: 6px;
    overflow: hidden;
    transition: border-color 0.2s ease, background-color 0.2s ease;
  }}

  .library-search-box-wrap:focus-within {{
    border-color: var(--text-tertiary);
    background: rgba(255, 255, 255, 0.12);
  }}

  .library-search-input {{
    background: transparent;
    border: none;
    outline: none;
    color: var(--text-primary);
    font-size: 13px;
    width: 100%;
    font-family: inherit;
  }}

  .library-search-clear-btn {{
    background: transparent;
    border: none;
    color: var(--text-tertiary);
    font-size: 12px;
    cursor: pointer;
    padding: 2px 6px;
  }}
  .library-search-clear-btn:hover {{
    color: var(--text-primary);
  }}

  /* --- 谜底黑胶 (MD Vinyl) 本地黑胶资料库视图 --- */
  .library-top-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: clamp(52px, 6.5vh, 64px);
    padding: 0 clamp(20px, 2.5vw, 36px);
    border-bottom: 1px solid var(--border-hairline);
    background: var(--bg-elevated-1);
    backdrop-filter: blur(30px);
    flex-shrink: 0;
  }}

  .library-title-group {{
    display: flex;
    align-items: baseline;
    gap: 12px;
  }}

  .library-title {{
    font-size: clamp(17px, 1.8vw, 22px);
    font-weight: 700;
    color: var(--text-primary);
    margin: 0;
  }}

  .library-subtitle {{
    font-size: clamp(11.5px, 1.1vw, 13px);
    color: var(--text-secondary);
  }}

  .library-scroll-area {{
    flex: 1;
    overflow-y: auto;
    /* 卡片悬停时黑胶会向右滑出，最后一列会撑出横向溢出；
       必须显式裁掉横向滚动，否则底栏会冒出丑陋的白色横向滚动条 */
    overflow-x: hidden;
    /* 底部留出悬浮 Dock 的高度，避免最后一行唱片被播放条压住 */
    padding: clamp(20px, 2.8vw, 36px) clamp(24px, 3.2vw, 44px) calc(var(--dock-reserve, 108px) + 16px) clamp(24px, 3.2vw, 44px);
    scrollbar-width: thin;
    scrollbar-color: rgba(255, 255, 255, 0.16) transparent;
  }}

  /* 资源库滚动条：极细、半透明、悬停才明显，避免默认白色滚动条破坏整体质感 */
  .library-scroll-area::-webkit-scrollbar {{
    width: 10px;
    height: 0;
  }}

  .library-scroll-area::-webkit-scrollbar-track {{
    background: transparent;
  }}

  .library-scroll-area::-webkit-scrollbar-thumb {{
    background: rgba(255, 255, 255, 0.13);
    background-clip: padding-box;
    border: 2px solid transparent;
    border-radius: 999px;
  }}

  .library-scroll-area:hover::-webkit-scrollbar-thumb {{
    background: rgba(255, 255, 255, 0.26);
    background-clip: padding-box;
  }}

  [data-theme="light"] .library-scroll-area {{
    scrollbar-color: rgba(0, 0, 0, 0.18) transparent;
  }}

  [data-theme="light"] .library-scroll-area::-webkit-scrollbar-thumb {{
    background: rgba(0, 0, 0, 0.15);
    background-clip: padding-box;
  }}

  [data-theme="light"] .library-scroll-area:hover::-webkit-scrollbar-thumb {{
    background: rgba(0, 0, 0, 0.28);
    background-clip: padding-box;
  }}

  .library-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: clamp(14px, 1.8vw, 24px);
  }}

  @media (max-width: 900px) {{
    .library-grid {{
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
    }}
  }}

  @media (max-width: 640px) {{
    .library-grid {{
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
    }}
  }}

  /* 谜底黑胶卡片 (封套 + 露出 38% 黑胶盘 + 弹簧滑出动效) */
  .vinyl-card {{
    position: relative;
    background: var(--bg-card);
    border: 1px solid var(--border-hairline);
    border-radius: 14px;
    padding: clamp(10px, 1.1vw, 14px);
    display: flex;
    flex-direction: column;
    cursor: pointer;
    overflow: visible;
    transition: transform 0.32s var(--spring-bounce), box-shadow 0.32s ease, background-color 0.2s ease, opacity 0.3s ease;
  }}

  .vinyl-card:hover {{
    transform: translateY(-4px);
    box-shadow: 0 14px 32px rgba(0, 0, 0, 0.42);
    background: var(--bg-pill-hover);
  }}

  .vinyl-stage {{
    position: relative;
    width: 100%;
    aspect-ratio: 1 / 1;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    overflow: visible;
  }}

  /* 黑胶唱片纸质封套 (Sleeve / Jacket) */
  .vinyl-sleeve {{
    position: relative;
    z-index: 2;
    width: 80%;
    height: 80%;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
    background: #18181A;
    border: 1px solid rgba(255, 255, 255, 0.08);
    transition: transform 0.4s var(--spring-bounce), opacity 0.4s ease;
  }}

  .vinyl-sleeve img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }}

  /* 探出的黑胶唱片 (Vinyl Record with Grooves) */
  .vinyl-disc {{
    position: absolute;
    z-index: 1;
    right: 3%;
    top: 10%;
    width: 78%;
    height: 78%;
    border-radius: 50%;
    background: radial-gradient(circle, #2a2a2a 0%, #161616 26%, #222222 28%, #101010 46%, #1c1c1c 48%, #0a0a0a 66%, #181818 68%, #040404 100%);
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.6), inset 0 0 0 1px rgba(255, 255, 255, 0.1);
    display: flex;
    align-items: center;
    justify-content: center;
    transform: translateX(0px) rotate(0deg);
    transition: transform 0.45s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.4s ease;
  }}

  .vinyl-disc::before {{
    content: "";
    position: absolute;
    inset: 7px;
    border-radius: 50%;
    border: 1px dashed rgba(255, 255, 255, 0.08);
    pointer-events: none;
  }}

  /* 黑胶唱片中心圆标贴纸 (Center Label & Spindle Hole) */
  .vinyl-label {{
    width: 33%;
    height: 33%;
    border-radius: 50%;
    overflow: hidden;
    position: relative;
    border: 2px solid #000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #252528;
  }}

  .vinyl-label img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
  }}

  .vinyl-label::after {{
    content: "";
    position: absolute;
    width: 8px;
    height: 8px;
    background: #000;
    border-radius: 50%;
    border: 1px solid rgba(255, 255, 255, 0.4);
  }}

  /* 悬停抽出动效 (谜底黑胶灵魂交互: Slide Out & Slight Rotate) */
  .vinyl-card:hover .vinyl-disc {{
    transform: translateX(28px) rotate(26deg);
  }}

  /* 抽盘点击转场专属动效 (Vinyl Pull-out Transition) */
  .vinyl-card.is-pulling-out {{
    z-index: 120 !important;
    pointer-events: none;
    transform: scale(1.06) translateY(-8px);
    box-shadow: 0 24px 50px rgba(0, 0, 0, 0.65);
    transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.55s ease;
  }}

  .vinyl-card.is-pulling-out .vinyl-disc {{
    transform: translateX(85px) translateY(-22px) rotate(75deg) scale(1.12) !important;
    box-shadow: 0 20px 45px rgba(0, 0, 0, 0.75), 0 0 30px rgba(255, 255, 255, 0.2);
    transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1), box-shadow 0.55s ease;
  }}

  .vinyl-card.is-pulling-out .vinyl-sleeve {{
    transform: translateY(4px) scale(0.97);
    opacity: 0.85;
    transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.55s ease;
  }}

  .vinyl-info-title {{
    font-size: clamp(12px, 1.1vw, 14px);
    font-weight: 700;
    color: var(--text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}

  .vinyl-info-artist {{
    font-size: clamp(11px, 1.0vw, 12.5px);
    color: var(--text-secondary);
    margin-top: 2px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}

  .vinyl-info-footer {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 8px;
    font-size: 10.5px;
    color: var(--text-tertiary);
  }}

  .vinyl-delete-btn {{
    border: none;
    background: transparent;
    color: var(--text-tertiary);
    cursor: pointer;
    padding: 2px 6px;
    border-radius: 5px;
    font-size: 10.5px;
    transition: color 0.2s ease, background-color 0.2s ease;
  }}

  .vinyl-delete-btn:hover {{
    color: #FF453A;
    background: rgba(255, 69, 58, 0.12);
  }}

  .library-empty {{
    grid-column: 1 / -1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 80px 20px;
    text-align: center;
    gap: 12px;
  }}

  .library-empty-title {{
    font-size: 18px;
    font-weight: 700;
    color: var(--text-secondary);
  }}

  .library-empty-sub {{
    font-size: 13px;
    color: var(--text-tertiary);
    max-width: 440px;
    line-height: 1.5;
  }}

  /* ==========================================================================
     8. 沉浸式黑胶播放模式 (MD Vinyl 谜底黑胶拟物视觉)
     ========================================================================== */
  /* ==========================================================================
     8. 沉浸式黑胶播放模式 (1:1 像素级复刻「谜底黑胶 MD Vinyl」实机界面)
     ========================================================================== */
  .immersive-vinyl-stage {{
    position: fixed;
    inset: 0;
    z-index: 95;
    background: #000;
    display: none;
    flex-direction: column;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.45s var(--spring-fluid);
    overflow: hidden;
    user-select: none;
  }}

  .immersive-vinyl-stage.open {{
    display: flex;
    opacity: 1;
    pointer-events: auto;
  }}

  /* 8.1 自适应超大范围柔焦环境光漫射 (Fluid Ambient Bleed) */
  .md-ambient-bg {{
    position: absolute;
    inset: -30%;
    pointer-events: none;
    z-index: 0;
    opacity: 0.88;
    filter: blur(85px) saturate(210%);
    background: radial-gradient(circle at 35% 42%, var(--ambient-primary, #b8751e) 0%, transparent 60%),
                radial-gradient(circle at 75% 35%, var(--ambient-secondary, #dca032) 0%, transparent 65%),
                radial-gradient(circle at 50% 85%, rgba(20, 15, 10, 0.95) 0%, #000000 100%);
    transition: background 1.2s ease, opacity 0.8s ease;
  }}

  /* 8.2 极简顶栏：时钟与控制 (Top Bar) */

  /* 8.3 主舞台中央区：左侧封套 + 右侧半抽出黑胶 + 右上角悬浮机械唱臂 */
  .md-centerpiece {{
    position: relative;
    z-index: 5;
    flex: 1;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    max-width: 1100px;
    margin: 0 auto;
    padding-bottom: 70px;
  }}

  /* 黑胶唱机单元：正方形固定比例容器，黑胶与唱针在此容器内同比例绝对锁定 */
  .md-vinyl-rig {{
    position: absolute;
    left: clamp(150px, 25vh, 280px);
    width: clamp(240px, 42vh, 440px);
    height: clamp(240px, 42vh, 440px);
    aspect-ratio: 1 / 1;
    z-index: 2;
  }}

  .md-stage-wrapper {{
    position: relative;
    display: flex;
    align-items: center;
    width: clamp(520px, 68vw, 860px);
    height: clamp(340px, 50vh, 520px);
  }}

  /* 左侧：精美拟物大封面封套 (Jacket Sleeve) */
  .md-sleeve-jacket {{
    position: absolute;
    left: 20px;
    width: clamp(280px, 42vh, 460px);
    height: clamp(280px, 42vh, 460px);
    border-radius: 10px;
    z-index: 3;
    overflow: hidden;
    background: #18181a;
    box-shadow:
      0 32px 70px -12px rgba(0, 0, 0, 0.7),
      0 16px 32px -16px rgba(0, 0, 0, 0.5),
      0 0 0 1px rgba(255, 255, 255, 0.12);
    cursor: pointer;
    transition: transform 0.4s var(--spring-bounce), box-shadow 0.4s ease;
  }}

  .md-sleeve-jacket:hover {{
    transform: scale(1.015);
    box-shadow:
      0 38px 85px -12px rgba(0, 0, 0, 0.8),
      0 20px 40px -16px rgba(0, 0, 0, 0.6),
      0 0 0 1px rgba(255, 255, 255, 0.2);
  }}

  .md-sleeve-spine {{
    position: absolute;
    top: 0;
    bottom: 0;
    left: 0;
    width: 4px;
    background: linear-gradient(to right, rgba(255,255,255,0.22), transparent);
    z-index: 2;
  }}

  .md-sleeve-artwork {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
    user-drag: none;
    -webkit-user-drag: none;
  }}

  /* 右侧：半抽出旋转黑胶唱片 (Emerging Marbled Vinyl Disc) */
  /* 彩胶 (Colored Vinyl) 盘面：提取自当前专辑封面主色，自适应动态渐变 */
  .md-vinyl-disc {{
    position: absolute;
    left: clamp(140px, 24vh, 270px);
    width: clamp(240px, 40vh, 430px);
    height: clamp(240px, 40vh, 430px);
    aspect-ratio: 1 / 1;
    border-radius: 50%;
    z-index: 2;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    /* 动态取色自适应彩胶材质渐变 */
    background: radial-gradient(circle at 46% 46%,
      var(--vinyl-tint-highlight, rgba(245, 195, 75, 0.96)) 0%,
      var(--ambient-secondary, rgba(215, 135, 25, 0.92)) 28%,
      var(--ambient-primary, rgba(160, 80, 15, 0.9)) 58%,
      var(--vinyl-tint-dark, rgba(35, 16, 6, 0.98)) 86%,
      #0e0906 100%);
    box-shadow:
      0 24px 60px rgba(0, 0, 0, 0.65),
      0 0 0 1px rgba(255, 255, 255, 0.15);
    transform: rotate(0deg);
    animation: mdVinylSpin 20s linear infinite;
    animation-play-state: paused;
    will-change: transform;
    transition: transform 0.6s var(--spring-bounce);
  }}

  body.is-playing .md-vinyl-disc {{
    animation-play-state: running;
  }}

  @keyframes mdVinylSpin {{
    0%   {{ transform: rotate(0deg); }}
    100% {{ transform: rotate(360deg); }}
  }}

  .md-vinyl-grooves {{
    position: absolute;
    inset: 0;
    border-radius: 50%;
    pointer-events: none;
    background-image: repeating-radial-gradient(
      circle at center,
      rgba(255, 255, 255, 0.05) 0px,
      rgba(255, 255, 255, 0.05) 1px,
      transparent 1px,
      transparent 3.5px
    );
    opacity: 0.85;
    mix-blend-mode: overlay;
  }}

  .md-vinyl-specular {{
    position: absolute;
    inset: 0;
    border-radius: 50%;
    pointer-events: none;
    background: conic-gradient(
      from 25deg at 50% 50%,
      transparent 0deg,
      rgba(255, 255, 255, 0.35) 45deg,
      transparent 90deg,
      transparent 180deg,
      rgba(255, 255, 255, 0.35) 225deg,
      transparent 270deg,
      transparent 360deg
    );
    mix-blend-mode: soft-light;
    opacity: 0.9;
  }}

  .md-vinyl-label {{
    position: relative;
    width: 32%;
    height: 32%;
    border-radius: 50%;
    overflow: hidden;
    background: #000;
    box-shadow:
      0 0 0 2px rgba(255, 255, 255, 0.15),
      0 4px 14px rgba(0, 0, 0, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 3;
  }}

  .md-vinyl-label-art {{
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    opacity: 0.65;
    filter: brightness(0.85) contrast(1.1);
  }}

  .md-label-text-box {{
    position: relative;
    z-index: 2;
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    padding: 0 8px;
    max-width: 90%;
  }}

  .md-label-track-title {{
    font-size: 11px;
    font-weight: 700;
    color: #fff;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 80px;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.9);
  }}

  .md-label-artist {{
    font-size: 8.5px;
    color: rgba(255, 255, 255, 0.8);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 70px;
    margin-top: 1px;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.9);
  }}

  .center-spindle-hole {{
    position: absolute;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: radial-gradient(circle, #050505 0%, #1c1c1e 100%);
    box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.9), 0 0 0 1.5px rgba(255, 255, 255, 0.4);
    z-index: 4;
  }}

  /* 8.4 唱针定位修复：以舞台与黑胶盘为恒定几何基准，杜绝任何缩放下的相对位移漂移 */
  .md-tonearm-assembly {{
    position: absolute;
    top: -8%;
    right: -4%;
    width: clamp(90px, 15vh, 140px);
    height: clamp(240px, 40vh, 400px);
    z-index: 10;
    pointer-events: none;
    transform-origin: 40% 12%;
    transform: rotate(0deg);
    transition: transform 0.85s cubic-bezier(0.25, 1, 0.5, 1);
  }}

  body.is-playing .md-tonearm-assembly {{
    transform: rotate(24deg);
  }}

  .md-tonearm-glass-plate {{
    position: absolute;
    top: 6px;
    left: 8px;
    width: 68%;
    height: 28%;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.16);
    backdrop-filter: blur(24px);
    -webkit-backdrop-filter: blur(24px);
    border: 1px solid rgba(255, 255, 255, 0.32);
    box-shadow: 0 16px 32px rgba(0, 0, 0, 0.28), inset 0 1px 1px rgba(255, 255, 255, 0.45);
  }}

  .md-tonearm-pivot {{
    position: absolute;
    top: 20%;
    left: 20%;
    width: 40%;
    aspect-ratio: 1 / 1;
    border-radius: 50%;
    background: linear-gradient(135deg, #2b2b2e, #121214);
    box-shadow: 0 6px 14px rgba(0, 0, 0, 0.55), inset 0 1px 2px rgba(255, 255, 255, 0.25);
    display: flex;
    align-items: center;
    justify-content: center;
  }}

  .md-tonearm-pivot-cap {{
    width: 48%;
    height: 48%;
    border-radius: 50%;
    background: radial-gradient(circle, #8a8a8e 0%, #3a3a3c 100%);
    border: 1px solid rgba(255, 255, 255, 0.35);
  }}

  .md-tonearm-wand {{
    position: absolute;
    top: 36%;
    left: 38%;
    width: 5px;
    height: 72%;
    border-radius: 3px;
    background: linear-gradient(to right, #cfcfcf 0%, #8e8e93 35%, #ffffff 65%, #636366 100%);
    box-shadow: 2px 5px 12px rgba(0, 0, 0, 0.45);
    transform-origin: top center;
  }}

  .md-tonearm-headshell {{
    position: absolute;
    bottom: 0;
    left: 26%;
    width: 28px;
    height: 42px;
    background: linear-gradient(150deg, #323236, #141416);
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 0.22);
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.6);
    transform: rotate(20deg);
  }}

  .md-tonearm-cartridge {{
    position: absolute;
    bottom: -4px;
    left: 9px;
    width: 10px;
    height: 12px;
    background: #000;
    border-radius: 1px;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.5);
  }}

  .md-tonearm-stylus {{
    position: absolute;
    bottom: -6px;
    left: 12px;
    width: 4px;
    height: 4px;
    background: #FF9F0A;
    border-radius: 50%;
    box-shadow: 0 0 6px rgba(255, 159, 10, 0.8);
  }}

  /* 8.5 歌词浮动层 (点击···或封面展开) */
  .md-lyrics-sheet {{
    position: absolute;
    top: 80px;
    left: 50%;
    transform: translateX(-50%);
    width: clamp(340px, 50vw, 560px);
    padding: 12px 24px;
    border-radius: 16px;
    background: rgba(20, 20, 24, 0.6);
    backdrop-filter: blur(28px);
    -webkit-backdrop-filter: blur(28px);
    border: 1px solid rgba(255, 255, 255, 0.18);
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.4);
    text-align: center;
    z-index: 20;
    transition: opacity 0.35s ease, transform 0.35s var(--spring-bounce);
    opacity: 0;
    pointer-events: none;
  }}

  .md-lyrics-sheet.visible {{
    opacity: 1;
    pointer-events: auto;
    transform: translateX(-50%) translateY(0);
  }}

  .md-lyric-main {{
    font-size: 16px;
    font-weight: 700;
    color: #fff;
    line-height: 1.4;
    text-shadow: 0 2px 12px rgba(0, 0, 0, 0.6);
  }}

  .md-lyric-sub {{
    font-size: 13px;
    color: rgba(255, 255, 255, 0.75);
    margin-top: 4px;
    font-weight: 500;
  }}

  /* 8.6 底部控制栏容器与悬浮药丸 (MD Vinyl Floating Pill Dock) */
  .md-dock-container {{
    position: absolute;
    bottom: 30px;
    left: 0;
    right: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 40px;
    pointer-events: none;
  }}

  .md-dock-container > * {{
    pointer-events: auto;
  }}

  /* 底部左侧：封套/资料库微缩图按钮 */

  /* 底部中间：悬浮毛玻璃胶囊药丸栏 */
  .md-player-dock {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding: 6px 18px 6px 14px;
    background: rgba(255, 255, 255, 0.22);
    backdrop-filter: blur(36px) saturate(200%);
    -webkit-backdrop-filter: blur(36px) saturate(200%);
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 999px;
    box-shadow: 0 20px 48px rgba(0, 0, 0, 0.35), inset 0 1px 1px rgba(255, 255, 255, 0.45);
    height: 62px;
    min-width: clamp(340px, 42vw, 480px);
    transition: transform 0.2s var(--spring-bounce);
  }}

  .md-player-dock:hover {{
    transform: scale(1.015);
  }}

  .md-dock-icon-btn {{
    background: transparent;
    border: none;
    color: #fff;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 6px;
    border-radius: 50%;
    transition: transform 0.2s ease, opacity 0.2s ease;
    opacity: 0.85;
  }}

  .md-dock-icon-btn:hover {{
    opacity: 1;
    transform: scale(1.1);
  }}

  .md-dock-icon-btn:active {{
    transform: scale(0.92);
  }}

  .md-dock-track-info {{
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
    cursor: pointer;
  }}

  .md-dock-mini-label {{
    width: 32px;
    height: 32px;
    border-radius: 50%;
    object-fit: cover;
    flex-shrink: 0;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.3);
  }}

  .md-dock-text-col {{
    display: flex;
    flex-direction: column;
    min-width: 0;
  }}

  .md-dock-title {{
    font-size: 13.5px;
    font-weight: 700;
    color: #fff;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.25;
    text-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
  }}

  .md-dock-artist {{
    font-size: 11.5px;
    font-weight: 500;
    color: rgba(255, 255, 255, 0.78);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.25;
  }}

  .md-dock-controls {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}

  .md-dock-controls .play-pause-btn {{
    width: 34px;
    height: 34px;
    background: rgba(255, 255, 255, 0.25);
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 50%;
    opacity: 1;
  }}

  /* 底部右侧：··· 更多按钮 */

  /* ==========================================================================
     Apple Music 双栏架构核心布局样式
     ========================================================================== */
  .app-shell {{
    position: relative;
    z-index: 1;
    display: flex;
    flex-direction: column;
    width: 100vw;
    height: 100vh;
    padding: 10px 14px 10px 14px;
    gap: 8px;
    box-sizing: border-box;
  }}

  .app-body {{
    position: relative;
    display: flex;
    flex: 1;
    min-height: 0;
    gap: 12px;
  }}

  /* 主视口区域 */
  .app-viewport {{
    position: relative;
    flex: 1;
    min-width: 0;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: transparent;
    border: none;
    border-radius: 0;
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
    box-shadow: none;
    overflow: hidden;
  }}

  .view-page {{
    display: none;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }}

  .view-page.active {{
    display: flex;
    flex-direction: column;
  }}

  /* 视口内部实体 CD 主舞台重写 */
  #viewCd.view-page {{
    display: none;
    flex-direction: column;
    height: 100%;
  }}

  #viewCd.view-page.active {{
    display: flex;
  }}

  #viewCd .main-stage {{
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: var(--left-col-max) 1fr;
    gap: var(--stage-gap);
    align-items: center;
    padding: 8px clamp(12px, 1.8vw, 32px);
  }}

  /* 视口内部黑胶资料库视图 */
  #viewLibrary.view-page {{
    display: none;
    flex-direction: column;
    height: 100%;
  }}

  #viewLibrary.view-page.active {{
    display: flex;
  }}

  #viewLibrary .library-scroll-area {{
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 18px 24px 28px 24px;
  }}

  /* 视口内部纯享双语歌词视图 */
  #viewLyrics.view-page {{
    display: none;
    flex-direction: column;
    height: 100%;
    padding: 24px 32px;
    overflow-y: auto;
  }}

  #viewLyrics.view-page.active {{
    display: flex;
  }}

  .standalone-lyrics-container {{
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 16px;
    align-items: center;
    justify-content: center;
    text-align: center;
  }}

  /* Apple Music 原生底部常驻微播控 Dock 栏 (现代三段式对称 Grid 布局) */
  .bottom-dock-player {{
    position: absolute;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 60;
    height: 74px;
    width: var(--dock-custom-width, 720px);
    min-width: 500px;
    max-width: 92vw;
    display: grid;
    grid-template-columns: minmax(160px, 236px) 1fr minmax(160px, 236px);
    align-items: center;
    gap: 16px;
    padding: 0 24px;
    background: rgba(var(--dock-bg-rgb, 22, 22, 28), var(--dock-custom-opacity, 0.75));
    backdrop-filter: blur(var(--dock-custom-blur, 40px)) saturate(190%);
    -webkit-backdrop-filter: blur(var(--dock-custom-blur, 40px)) saturate(190%);
    border: 1px solid var(--dock-border);
    box-shadow: var(--dock-shadow);
    border-radius: 999px;
    user-select: none;
    flex-shrink: 0;
    white-space: nowrap;
    transition: width 0.25s cubic-bezier(0.16, 1, 0.3, 1), background-color 0.2s ease, opacity 0.3s ease;
  }}

  .dock-album-cluster {{
    justify-self: start;
    display: flex;
    align-items: center;
    gap: 12px;
    cursor: pointer;
    flex: 0 1 236px;
    min-width: 132px;
    max-width: 236px;
    width: 100%;
    overflow: hidden;
    transition: transform 0.18s ease;
  }}

  .dock-album-cluster:hover {{
    transform: scale(1.02);
  }}

  .dock-center-cluster {{
    justify-self: center;
    width: 100%;
    max-width: 380px;
    min-width: 180px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 3px;
  }}

  .dock-center-controls {{
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 16px;
    flex-shrink: 0;
  }}

  .dock-progress-row {{
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
  }}

  .dock-time-text {{
    font-size: 11px;
    font-variant-numeric: tabular-nums;
    color: var(--text-tertiary);
    min-width: 32px;
    text-align: center;
  }}

  .dock-scrubber-hit {{
    flex: 1;
    height: 14px;
    display: flex;
    align-items: center;
    cursor: pointer;
    position: relative;
  }}

  .dock-scrubber-track {{
    width: 100%;
    height: 4px;
    background: var(--scrubber-bg, rgba(255, 255, 255, 0.16));
    border-radius: 999px;
    overflow: hidden;
    transition: height 0.15s ease;
  }}

  .dock-scrubber-hit:hover .dock-scrubber-track {{
    height: 6px;
  }}

  .dock-scrubber-fill {{
    height: 100%;
    width: 0%;
    background: var(--scrubber-fill, var(--text-primary));
    border-radius: 999px;
    transition: width 0.08s linear;
  }}

  .dock-ctrl-btn,
  .dock-action-btn {{
    background: transparent;
    border: none;
    color: var(--text-primary);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 6px;
    border-radius: 50%;
    flex-shrink: 0;
    transition: transform 0.18s ease, color 0.18s ease;
  }}

  .dock-ctrl-btn:hover,
  .dock-action-btn:hover {{
    transform: scale(1.12);
  }}

  .dock-right-actions {{
    justify-self: end;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 14px;
    flex: 0 0 auto;
    width: 100%;
    max-width: 236px;
  }}

  /* 底栏双语翻译开关：关闭态变暗并叠加斜杠，状态一眼可辨 */
  .dock-trans-btn {{
    position: relative;
  }}

  .dock-trans-btn.is-off {{
    opacity: 0.38;
  }}

  .dock-trans-btn.is-off::after {{
    content: "";
    position: absolute;
    left: 6px;
    right: 6px;
    top: 50%;
    height: 1.6px;
    border-radius: 2px;
    background: currentColor;
    transform: rotate(-45deg);
    pointer-events: none;
  }}

  /* 翻译关闭时全局隐藏译文行，且不受歌词重新渲染影响 */
  html.no-translation .lyric-trans {{
    display: none !important;
  }}

  .dock-volume-wrap {{
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }}

  /* 竖向独立悬浮音量卡片 */
  .dock-volume-popover.vertical {{
    position: absolute;
    bottom: calc(100% + 14px);
    right: 50%;
    transform: translateX(50%) translateY(8px);
    width: 44px;
    height: 160px;
    padding: 10px 6px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    background: var(--popover-bg);
    backdrop-filter: blur(28px);
    -webkit-backdrop-filter: blur(28px);
    border: 1px solid var(--popover-border);
    box-shadow: var(--popover-shadow);
    border-radius: 22px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.22s ease, transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);
    z-index: 120;
    user-select: none;
  }}

  .dock-volume-wrap.is-open .dock-volume-popover.vertical {{
    opacity: 1;
    pointer-events: auto;
    transform: translateX(50%) translateY(0);
  }}

  .dock-vol-hit.vertical {{
    width: 28px;
    height: 96px;
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    touch-action: none;
  }}

  .dock-vol-track.vertical {{
    width: 8px;
    height: 100%;
    background: var(--scrubber-bg);
    border-radius: 999px;
    overflow: hidden;
    position: relative;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    transition: width 0.15s ease;
  }}

  .dock-vol-hit.vertical:hover .dock-vol-track.vertical {{
    width: 10px;
  }}

  .dock-vol-fill.vertical {{
    width: 100%;
    height: 80%;
    background: var(--color-accent, #FA2D48);
    border-radius: 999px;
    transition: height 0.05s linear;
  }}

  .dock-vol-val-text {{
    font-size: 11px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    color: var(--text-primary);
    text-align: center;
    line-height: 1;
  }}

  .dock-vol-popover-icon {{
    font-size: 14px;
    opacity: 0.8;
    line-height: 1;
  }}

  .dock-artwork-thumb {{
    width: 40px;
    height: 40px;
    border-radius: 8px;
    background: #18181a;
    object-fit: cover;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
    flex-shrink: 0;
  }}

  .dock-track-meta {{
    display: flex;
    flex-direction: column;
    min-width: 0;
    overflow: hidden;
  }}

  .dock-track-title {{
    font-size: 13px;
    font-weight: 700;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.3;
  }}

  .dock-artist {{
    font-size: 11.5px;
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.3;
  }}

  .dock-center {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    flex: 1;
    max-width: 540px;
  }}

  .dock-icon-btn {{
    background: transparent;
    border: none;
    color: var(--text-primary);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 4px;
    border-radius: 50%;
    transition: transform 0.18s ease, color 0.18s ease;
  }}

  .dock-icon-btn:hover {{
    color: var(--color-accent);
    transform: scale(1.08);
  }}

  .dock-play-pause-btn {{
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--text-primary);
    color: var(--bg-base);
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    cursor: pointer;
    transition: transform 0.18s var(--spring-bounce);
  }}

  .dock-play-pause-btn:hover {{
    transform: scale(1.08);
  }}

  .dock-time {{
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--text-tertiary);
    min-width: 30px;
    text-align: center;
  }}

  .dock-scrubber-hit {{
    flex: 1;
    height: 14px;
    display: flex;
    align-items: center;
    cursor: pointer;
  }}

  .dock-scrubber-track {{
    width: 100%;
    height: 4px;
    background: var(--track-bar-bg);
    border-radius: 999px;
    overflow: hidden;
    position: relative;
  }}

  .dock-scrubber-fill {{
    width: 0%;
    height: 100%;
    background: var(--track-bar-fill);
    border-radius: 999px;
  }}

  .dock-right {{
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 180px;
    justify-content: flex-end;
  }}

  .dock-vol-hit {{
    width: 80px;
    height: 14px;
    display: flex;
    align-items: center;
    cursor: pointer;
  }}

  .dock-vol-track {{
    width: 100%;
    height: 4px;
    background: var(--vol-track-bg);
    border-radius: 999px;
    overflow: hidden;
  }}

  .dock-vol-fill {{
    width: 80%;
    height: 100%;
    background: var(--vol-track-fill);
    border-radius: 999px;
  }}


  /* ===== v1.8 音量指示条修复与播放界面细节优化 ===== */
  .dock-vol-track.vertical {{
    overflow: visible;
  }}
  .dock-vol-fill.vertical {{
    transition: height 0.14s cubic-bezier(0.16, 1, 0.3, 1);
  }}
  .dock-vol-knob {{
    position: absolute;
    left: 50%;
    bottom: 80%;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: #ffffff;
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.45), 0 0 0 0.5px rgba(0, 0, 0, 0.12);
    transform: translate(-50%, 50%);
    pointer-events: none;
    transition: bottom 0.14s cubic-bezier(0.16, 1, 0.3, 1), transform 0.15s ease;
  }}
  .dock-vol-hit.vertical:hover .dock-vol-knob,
  .dock-vol-hit.vertical.is-dragging .dock-vol-knob {{
    transform: translate(-50%, 50%) scale(1.15);
  }}
  /* 拖动中取消过渡，指示条与指针 1:1 跟手 */
  .dock-vol-hit.vertical.is-dragging .dock-vol-fill.vertical,
  .dock-vol-hit.vertical.is-dragging .dock-vol-knob {{
    transition: none;
  }}
  .dock-vol-popover-icon {{
    background: transparent;
    border: none;
    padding: 2px;
    color: var(--text-primary);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .dock-volume-wrap.is-muted .dock-vol-fill.vertical {{
    opacity: 0.35;
  }}
  /* 无封面时 Dock 缩略图使用占位底色 + 极简光盘符号，避免布局跳动且不再是一块死灰方块 */
  .dock-artwork-thumb.is-empty {{
    background: linear-gradient(135deg, var(--bg-elevated-2, #2a2a2e), var(--bg-pill, #1c1c1f));
    box-shadow: inset 0 0 0 1px var(--border-hairline);
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%239a9aa2' stroke-width='1.5' stroke-linecap='round'><circle cx='12' cy='12' r='7'/><circle cx='12' cy='12' r='1.9'/><path d='M16.9 7.1a7 7 0 0 0-4.4-2.1'/></svg>");
    background-repeat: no-repeat;
    background-position: center;
    background-size: 22px 22px;
  }}
  /* 左侧封面在「Dock 以上」的可视区域内垂直居中，绝不被悬浮 Dock 遮挡 */
  #viewCd .player-column {{
    align-self: stretch;
    height: 100%;
    justify-content: center;
    padding-bottom: var(--dock-reserve, 108px);
    box-sizing: border-box;
  }}
  /* 曲目列表底部留白，最后几首不再被 Dock 盖住 */
  .queue-viewport {{
    padding-bottom: calc(var(--dock-reserve, 108px) + 16px);
  }}
  /* 歌词在接近 Dock 处提前柔和淡出 */
  .lyrics-viewport {{
    mask-image: linear-gradient(to bottom, transparent 0%, black 12%, black 70%, transparent 90%);
    -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 12%, black 70%, transparent 90%);
  }}

  /* --- iOS 液态玻璃（Fluid Morphing）弹性物理阻尼流体卡片 --- */
  :root {{
    --spring-fluid-morph: cubic-bezier(0.34, 1.56, 0.64, 1);
  }}

  .md-fluid-morph-backdrop {{
    position: absolute;
    inset: 0;
    z-index: 95;
    pointer-events: none;
    opacity: 0;
    background: rgba(0, 0, 0, 0.32);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    transition: opacity 0.3s ease;
  }}

  .md-fluid-morph-backdrop.is-open {{
    pointer-events: auto;
    opacity: 1;
  }}

  .md-fluid-morph-card {{
    position: absolute;
    bottom: 86px;
    left: 50%;
    transform: translateX(-50%) translateY(24px) scale(0.85);
    transform-origin: bottom center;
    width: clamp(340px, 42vw, 460px);
    max-height: clamp(320px, 54vh, 520px);
    display: flex;
    flex-direction: column;
    /* 微透毛玻璃、细腻边框高光与柔和漫反射投影 */
    background: rgba(22, 22, 28, 0.72);
    backdrop-filter: blur(44px) saturate(190%);
    -webkit-backdrop-filter: blur(44px) saturate(190%);
    border: 1px solid rgba(255, 255, 255, 0.16);
    box-shadow:
      0 24px 60px rgba(0, 0, 0, 0.55),
      0 4px 16px rgba(0, 0, 0, 0.35),
      inset 0 1px 0 rgba(255, 255, 255, 0.22);
    border-radius: 22px;
    padding: 16px;
    opacity: 0;
    pointer-events: none;
    filter: blur(6px);
    transition: transform 0.45s var(--spring-fluid-morph), opacity 0.3s ease, filter 0.32s ease;
  }}

  .md-fluid-morph-backdrop.is-open .md-fluid-morph-card {{
    opacity: 1;
    filter: blur(0px);
    pointer-events: auto;
    transform: translateX(-50%) translateY(0) scale(1);
  }}

  .fluid-card-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    margin-bottom: 10px;
    flex-shrink: 0;
  }}

  .fluid-mode-group {{
    display: flex;
    align-items: center;
    background: rgba(255, 255, 255, 0.08);
    border-radius: 999px;
    padding: 2px;
    gap: 2px;
  }}

  .fluid-mode-btn {{
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: transparent;
    border: none;
    color: var(--text-tertiary);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.2s ease;
  }}

  .fluid-mode-btn:hover {{
    color: var(--text-primary);
  }}

  .fluid-mode-btn.active {{
    background: rgba(255, 255, 255, 0.2);
    color: #fff;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
  }}

  .fluid-volume-group {{
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    max-width: 160px;
    justify-content: flex-end;
  }}

  .fluid-vol-hit {{
    flex: 1;
    height: 16px;
    display: flex;
    align-items: center;
    cursor: pointer;
  }}

  .fluid-vol-track {{
    width: 100%;
    height: 5px;
    background: rgba(255, 255, 255, 0.15);
    border-radius: 999px;
    overflow: hidden;
  }}

  .fluid-vol-fill {{
    width: 80%;
    height: 100%;
    background: #fff;
    border-radius: 999px;
  }}

  .fluid-track-list {{
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding-right: 4px;
  }}

  .fluid-track-list::-webkit-scrollbar {{
    width: 4px;
  }}
  .fluid-track-list::-webkit-scrollbar-thumb {{
    background: rgba(255, 255, 255, 0.2);
    border-radius: 999px;
  }}

  .fluid-track-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-radius: 12px;
    cursor: pointer;
    transition: background 0.18s ease;
  }}

  .fluid-track-row:hover {{
    background: rgba(255, 255, 255, 0.08);
  }}

  .fluid-track-row.is-current {{
    background: rgba(255, 255, 255, 0.16);
  }}

  .fluid-track-left {{
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    flex: 1;
  }}

  .fluid-track-num {{
    font-size: 12px;
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
    width: 20px;
  }}

  .fluid-track-row.is-current .fluid-track-num {{
    color: var(--color-accent);
    font-weight: 700;
  }}

  .fluid-track-title {{
    font-size: 13.5px;
    font-weight: 500;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .fluid-track-row.is-current .fluid-track-title {{
    color: #fff;
    font-weight: 700;
  }}

  .fluid-track-dur {{
    font-size: 12px;
    color: var(--text-tertiary);
    font-variant-numeric: tabular-nums;
    margin-left: 8px;
    flex-shrink: 0;
  }}

  /* 彻底移除沉浸黑胶模式 */
  #immersiveVinylStage,
  .immersive-vinyl-stage,
  .md-dock-container,
  .md-fluid-morph-backdrop {{
    display: none !important;
    pointer-events: none !important;
    visibility: hidden !important;
    opacity: 0 !important;
  }}

  /* 自定义偏好设置样式 */
  .custom-setting-group {{
    display: flex;
    flex-direction: column;
    gap: 12px;
    background: var(--bg-elevated-2, rgba(255, 255, 255, 0.04));
    padding: 14px 16px;
    border-radius: 12px;
    border: 1px solid var(--border-hairline);
  }}

  .custom-group-title {{
    font-size: 13px;
    font-weight: 700;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }}

  .custom-control-row {{
    display: flex;
    flex-direction: column;
    gap: 6px;
  }}

  .custom-label-wrap {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 13px;
    color: var(--text-primary);
  }}

  .custom-val-badge {{
    font-size: 12px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--bg-pill-hover, rgba(255, 255, 255, 0.1));
    color: var(--color-accent);
  }}

  .custom-control-row input[type="range"] {{
    appearance: none;
    -webkit-appearance: none;
    width: 100%;
    height: 6px;
    background: rgba(255, 255, 255, 0.15);
    border-radius: 999px;
    outline: none;
    cursor: pointer;
  }}

  .custom-control-row input[type="range"]::-webkit-slider-thumb {{
    -webkit-appearance: none;
    appearance: none;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: #FFFFFF;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
    cursor: pointer;
    transition: transform 0.15s ease;
  }}

  .custom-control-row input[type="range"]::-webkit-slider-thumb:hover {{
    transform: scale(1.15);
  }}

  /* Discflow 软件图标：移除外层圆形按钮背景与边框，纯净展示软件图标 */
  #discflowAppBtn.top-icon-btn {{
    width: auto !important;
    height: auto !important;
    padding: 0 !important;
    border-radius: 0 !important;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    transition: transform 0.2s var(--spring-bounce), opacity 0.2s ease;
  }}

  #discflowAppBtn.top-icon-btn:hover {{
    transform: scale(1.08);
    opacity: 0.88;
  }}

  #discflowAppBtn .discflow-app-icon-badge {{
    width: 32px;
    height: 32px;
    border-radius: 7px;
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
  }}

  /* 浅色模式全 App 统一适配微调 */
  [data-theme="light"] .top-icon-btn {{
    background: rgba(255, 255, 255, 0.85);
    border-color: rgba(0, 0, 0, 0.1);
    color: #1D1D1F;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }}

  [data-theme="light"] .top-icon-btn:hover {{
    background: #FFFFFF;
    color: #000000;
  }}

  [data-theme="light"] .discflow-popover-menu {{
    background: var(--popover-bg);
    border-color: var(--popover-border);
    box-shadow: var(--popover-shadow);
  }}

  [data-theme="light"] .discflow-menu-item {{
    color: var(--text-primary);
  }}

  [data-theme="light"] .discflow-menu-item:hover {{
    background: var(--bg-pill-hover);
    color: #000000;
  }}

  [data-theme="light"] .bottom-dock-player {{
    color: #1D1D1F;
  }}

  [data-theme="light"] .dock-ctrl-btn,
  [data-theme="light"] .dock-action-btn {{
    color: rgba(0, 0, 0, 0.72);
  }}

  [data-theme="light"] .dock-ctrl-btn:hover,
  [data-theme="light"] .dock-action-btn:hover {{
    color: #000000;
  }}

  [data-theme="light"] .dock-track-title {{
    color: #1D1D1F;
  }}

  [data-theme="light"] .dock-scrubber-fill {{
    background: #1D1D1F;
  }}

  [data-theme="light"] .dock-volume-popover {{
    background: var(--popover-bg);
    border-color: var(--popover-border);
    box-shadow: var(--popover-shadow);
    color: #1D1D1F;
  }}

  [data-theme="light"] .custom-setting-group {{
    background: rgba(0, 0, 0, 0.04);
    border-color: rgba(0, 0, 0, 0.08);
  }}

  [data-theme="light"] .custom-control-row input[type="range"] {{
    background: rgba(0, 0, 0, 0.12);
  }}

  /* 浅色模式专属环境散光柔化与卡片阴影特调 */
  [data-theme="light"] .artwork-ambient-shadow {{
    filter: blur(clamp(52px, 7.0vw, 90px)) saturate(150%) brightness(1.06);
    opacity: 0.26;
  }}

  body.is-playing [data-theme="light"] .artwork-ambient-shadow,
  [data-theme="light"] body.is-playing .artwork-ambient-shadow {{
    opacity: 0.5;
  }}

  [data-theme="light"] .artwork-card {{
    box-shadow: 0 clamp(10px, 1.8vh, 22px) clamp(24px, 3.8vw, 48px) rgba(0, 0, 0, 0.10), 0 3px 10px rgba(0, 0, 0, 0.05);
  }}

  body.is-playing [data-theme="light"] .artwork-card,
  [data-theme="light"] body.is-playing .artwork-card {{
    box-shadow: 0 clamp(16px, 2.6vh, 32px) clamp(32px, 4.8vw, 64px) rgba(0, 0, 0, 0.12), 0 4px 14px rgba(0, 0, 0, 0.06);
  }}

  [data-theme="light"] .mesh-blob {{
    mix-blend-mode: multiply;
    opacity: 0.88;
  }}

  /* 浅色模式流体背景：深色模式靠 screen 叠加发光，浅色模式必须提高存在感，
     否则只剩一层纯白底色，完全丢失 Apple Music 的通透氛围 */
  [data-theme="light"] .mesh-canvas {{
    filter: blur(clamp(58px, 7.4vw, 96px)) saturate(190%);
  }}

  [data-theme="light"] body.is-playing .mesh-canvas.has-cover {{
    opacity: 0.95;
  }}

  [data-theme="light"] body:not(.is-playing) .mesh-canvas.has-cover {{
    opacity: 0.62;
  }}

  /* 浅色模式底色不再是单一 #F5F5F7，而是带极淡垂直渐变与顶部高光，
     让界面有纵深和玻璃质感，而不是「一张白纸」 */
  [data-theme="light"] body {{
    background-image:
      radial-gradient(125% 85% at 50% -12%, rgba(255, 255, 255, 0.98) 0%, rgba(255, 255, 255, 0) 58%),
      linear-gradient(180deg, #FCFCFE 0%, #F4F4F8 46%, #ECECF2 100%);
  }}

  [data-theme="light"] .frosted-scrim {{
    background: rgba(245, 245, 247, 0.72);
  }}

  /* 浅色模式：封面底图更淡，压暗层换成白色柔光，避免整页发灰 */
  [data-theme="light"] .art-backdrop {{
    filter: blur(80px) saturate(165%) brightness(1.08);
  }}

  [data-theme="light"] body.has-artwork .art-backdrop {{
    opacity: 0.5;
  }}

  [data-theme="light"] body.has-artwork:not(.is-playing) .art-backdrop {{
    opacity: 0.34;
  }}

  [data-theme="light"] .ambient-vignette {{
    background:
      radial-gradient(125% 92% at 50% 42%, rgba(255, 255, 255, 0) 38%, rgba(255, 255, 255, 0.46) 100%),
      linear-gradient(
        180deg,
        rgba(255, 255, 255, 0.52) 0%,
        rgba(255, 255, 255, 0) 26%,
        rgba(255, 255, 255, 0) 70%,
        rgba(255, 255, 255, 0.56) 100%
      );
  }}

  /* 透明度为 0% 时底栏边框与投影自适应柔化 */
  .bottom-dock-player.is-fully-transparent {{
    border-color: rgba(255, 255, 255, 0.08);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
  }}

  [data-theme="light"] .bottom-dock-player.is-fully-transparent {{
    border-color: rgba(0, 0, 0, 0.06);
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.04);
  }}
</style>
</head>
<body class="is-playing">

  <!-- 1. 动态专辑流体背景层 (Mesh Gradient + 54px 磨砂遮罩) -->
  <div class="ambient-stage">
    <!-- Apple Music 风格环境底色：当前封面整页高斯模糊铺底 -->
    <div id="artBackdrop" class="art-backdrop"></div>
    <div id="meshCanvas" class="mesh-canvas">
      <div class="mesh-blob blob-1"></div>
      <div class="mesh-blob blob-2"></div>
      <div class="mesh-blob blob-3"></div>
    </div>
    <div class="ambient-vignette"></div>
    <div class="frosted-scrim"></div>
  </div>

  <!-- 2. 主应用界面容器 -->
  <div class="app-shell">

    <!-- 1. 顶部统一精简状态栏 -->
    <header class="top-bar">
      <!-- 悬浮控件组：4个纯图标 + 状态指示灯（悬浮于界面上方，消除生硬底栏边框） -->
      <div class="top-floating-left">
        <!-- 图标 1: Discflow 软件图标 (点击展开专属菜单：资料库、搜索、设置) -->
        <button id="discflowAppBtn" class="top-icon-btn discflow-app-btn" onclick="toggleDiscflowMenu(event)" title="Discflow 菜单">
          <span class="discflow-app-icon-badge">
            {DISCFLOW_ICON_SVG_24}
          </span>
        </button>

        <!-- 图标 2: 读盘纯图标 -->
        <button id="topRescanBtn" class="top-icon-btn" onclick="rescanDrive()" title="读取 / 扫描光驱">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <polyline points="1 20 1 14 7 14"></polyline>
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
          </svg>
        </button>

        <!-- 图标 3: 弹出纯图标 -->
        <button id="topEjectBtn" class="top-icon-btn" onclick="ejectDrive()" title="弹出光驱托盘">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="12 4 4 14 20 14 12 4"></polygon>
            <line x1="4" y1="19" x2="20" y2="19"></line>
          </svg>
        </button>

        <!-- 绿灯/黄灯状态标识 (空闲绿灯 / 读盘与忙碌黄灯呼吸微光) -->
        <span id="statusDot" class="status-dot idle" title="光驱状态指示灯"></span>

        <!-- 保持隐藏以向下兼容事件绑定 -->
        <select id="driveSelect" class="drive-select" style="display:none;"></select>
        <span id="statusText" class="status-text" style="display:none;"></span>
      </div>

      <!-- 右侧悬浮图标 4: 外观模式切换按钮 (深色/浅色一键切换) -->
      <div class="top-floating-right">
        <button id="topThemeBtn" class="top-icon-btn" onclick="cycleTheme()" title="切换外观模式 (浅色 / 深色)">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
          </svg>
        </button>
      </div>

      <!-- Discflow 图标点击弹出的悬浮 Popover 菜单 -->
      <div id="discflowAppMenu" class="discflow-popover-menu" onclick="event.stopPropagation()">
        <!-- 第一个：资料库 (抓轨过的专辑) -->
        <div class="discflow-menu-item" onclick="selectDiscflowMenu('library')">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"></rect>
            <rect x="14" y="3" width="7" height="7" rx="1.5"></rect>
            <rect x="14" y="14" width="7" height="7" rx="1.5"></rect>
            <rect x="3" y="14" width="7" height="7" rx="1.5"></rect>
          </svg>
          <span>资料库</span>
        </div>

        <!-- 第二个：搜索 (搜索资料库中的专辑、歌名、包括歌手) -->
        <div class="discflow-menu-item" onclick="selectDiscflowMenu('search')">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <span>搜索</span>
        </div>

        <div class="discflow-menu-divider"></div>

        <!-- 最下面：设置按钮 -->
        <div class="discflow-menu-item" onclick="selectDiscflowMenu('settings')">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="3"></circle>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"></path>
          </svg>
          <span>设置</span>
        </div>
      </div>
    </header>

    <!-- 2. 极简单栏主体区 -->
    <div class="app-body">
      <!-- 主视口区域：多视图瞬时平滑切换 -->
      <main class="app-viewport">
        <!-- 视图 1：实体 CD 播放主舞台 -->
        <section id="viewCd" class="view-page active">
          <div id="mainStage" class="main-stage">
            <!-- 左侧：Apple Music 播放卡片列 -->
            <section class="player-column">
              <!-- 左侧界面只保留大尺寸专辑封面与弥散光影 -->
              <div class="artwork-stage">
                <div id="ambientShadow" class="artwork-ambient-shadow"></div>
                <div class="artwork-card" onclick="toggleLyricsAndTracklist(event)" style="cursor: pointer;" title="点击切换歌词 / 曲目列表">
                  <div id="discPlaceholder" class="disc-placeholder">
                    {DISCFLOW_ICON_SVG_512}
                  </div>
                  <img id="artworkImg" class="artwork-img" alt="Album Cover" />
                </div>
              </div>

              <!-- 隐藏占位 ID 确保底层所有 JS 状态联动与测试完全兼容 -->
              <div style="display:none;">
                <span id="trackTitle"></span>
                <span id="trackSubtitle"></span>
                <div id="scrubberHit"><div id="scrubberFill"></div></div>
                <span id="elapsedTime"></span>
                <span id="remainingTime"></span>
                <button id="playPauseBtn"></button>
                <div id="playIcon"></div>
                <div id="pauseIcon"></div>
                <div id="volHit"><div id="volFill"></div></div>
                <button id="lyricsTabBtn"></button>
                <button id="queueTabBtn"></button>
                <span id="queueCountText"></span>
              </div>
            </section>

            <!-- 右侧：柔焦滚动歌词 + CD 音轨队列舞台 -->
            <section class="right-stage">
              <div id="lyricsViewport" class="lyrics-viewport"></div>
              <div id="queueViewport" class="queue-viewport"></div>
            </section>
          </div>
        </section>

        <!-- 视图 2：本地黑胶资料库视图 (常驻 4 列精致货架网格) -->
        <section id="viewLibrary" class="view-page">
          <div class="library-top-bar" style="background: transparent; border-bottom: 1px solid var(--border-hairline); display: flex; justify-content: space-between; align-items: center; padding: 16px 28px;">
            <div class="library-title-group">
              <h1 class="library-title">资源库</h1>
              <span class="library-subtitle">CD-DA 无损转录归档 · 唱片陈列 · 免光盘即点即播</span>
            </div>
            <div class="library-top-actions" style="display: flex; align-items: center; gap: 12px;">
              <!-- 资源库搜索框 (常驻且不遮挡任何按键) -->
              <div class="library-search-box-wrap" id="librarySearchBoxWrap" style="display: flex; align-items: center; background: rgba(255, 255, 255, 0.08); border: 1px solid var(--border-hairline); border-radius: 999px; padding: 4px 12px; gap: 6px;">
                <button id="librarySearchSubmitBtn" onclick="submitLibrarySearch()" title="点击搜索" style="background: transparent; border: none; outline: none; cursor: pointer; display: flex; align-items: center; color: var(--text-primary); padding: 0;">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="opacity: 0.8;">
                    <circle cx="11" cy="11" r="8"></circle>
                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                  </svg>
                </button>
                <input type="text" id="librarySearchInput" class="library-search-input" placeholder="搜索专辑、歌名或歌手..." oninput="filterLibraryCards(this.value)" onkeydown="if(event.key==='Enter') submitLibrarySearch()" style="background: transparent; border: none; outline: none; color: var(--text-primary); font-size: 13px; width: 170px;" />
                <button id="librarySearchClearBtn" class="library-search-clear-btn" onclick="clearLibrarySearch()" title="清空搜索" style="background: transparent; border: none; color: var(--text-tertiary); cursor: pointer; padding: 2px;">✕</button>
              </div>
              <!-- 打开本地目录按钮 -->
              <button id="openLibraryFolderBtn" class="pill-btn" onclick="openLibraryFolder()" title="在文件管理器中打开资源库存储目录">
                <span>📂 本地目录</span>
              </button>
              <!-- 返回播放页面按钮 -->
              <button class="pill-btn" onclick="switchMainView('cd')" title="返回常规播放界面">
                <span>◀ 返回播放</span>
              </button>
              <!-- 抓轨当前光盘按钮 -->
              <button class="pill-btn active" onclick="startRippingCurrentCd()" title="转录放入的光盘">
                <span>💿 抓轨当前光盘</span>
              </button>
            </div>
          </div>
          <div class="library-scroll-area">
            <div id="libraryGrid" class="library-grid">
              <!-- 动态渲染谜底黑胶卡片 -->
            </div>
            <!-- 手动更换专辑封面的隐藏文件选择器 -->
            <input type="file" id="coverFileInput" accept="image/*" style="display:none;" onchange="handleCoverFileSelected(event)" />
          </div>
        </section>

        <!-- 视图 3：纯享双语歌词全屏视口 -->
        <section id="viewLyrics" class="view-page">
          <div class="standalone-lyrics-container">
            <div id="standaloneLyricsViewport" class="lyrics-viewport" style="width: 100%; max-width: 640px; height: 100%;"></div>
          </div>
        </section>
      </main>
    </div>

    <!-- 3. Apple Music 底部常驻微播控 Dock 栏 -->
    <!-- 重构底部悬浮控制栏：纯图标收拢（抓轨、曲目、音量、沉浸黑胶） + 半透明毛玻璃微光边框 -->
    <!-- 居中悬浮长条播控栏 (固定比例防重叠，毛玻璃胶囊浮动质感) -->
    <footer class="bottom-dock-player" id="bottomDockPlayer">
      <!-- 1. 左侧：专辑图 + 歌名/歌手 (固定宽度 170px，点击切换歌词/曲目列表) -->
      <div class="dock-album-cluster" onclick="toggleRightPanelFromDock()" title="切换歌词 / 曲目列表">
        <img id="dockArtworkImg" class="dock-artwork-thumb" src="" alt="Album Cover" />
        <div class="dock-track-meta">
          <span id="dockTrackTitle" class="dock-track-title">等待播放</span>
          <span id="dockArtist" class="dock-artist">--</span>
        </div>
      </div>

      <!-- 2. 中间：播放控制 + 歌曲进程进度条 (固定比例绝对居中) -->
      <div class="dock-center-cluster">
        <div class="dock-center-controls">
          <button class="dock-ctrl-btn" onclick="prevTrack()" title="上一曲">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/>
            </svg>
          </button>
          <button id="dockPlayPauseBtn" class="dock-play-pause-btn" onclick="togglePlayPause()" title="播放 / 暂停">
            <svg id="dockPlayIcon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" style="display:none;">
              <path d="M8 5.14v13.72c0 .79.87 1.27 1.54.84l11-6.86c.62-.39.62-1.29 0-1.68l-11-6.86C8.87 3.87 8 4.35 8 5.14z"/>
            </svg>
            <svg id="dockPauseIcon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
              <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
            </svg>
          </button>
          <button class="dock-ctrl-btn" onclick="nextTrack()" title="下一曲">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/>
            </svg>
          </button>
        </div>
        <div class="dock-progress-row">
          <span id="dockElapsedTime" class="dock-time-text">0:00</span>
          <div id="dockScrubberHit" class="dock-scrubber-hit" title="拖动调整播放进度">
            <div class="dock-scrubber-track">
              <div id="dockScrubberFill" class="dock-scrubber-fill"></div>
            </div>
          </div>
          <span id="dockRemainingTime" class="dock-time-text">0:00</span>
        </div>
      </div>

      <!-- 3. 右侧：声音控制 (固定宽度 170px，点击展开浮动音量条，位置比例固定) -->
      <div class="dock-right-actions">
        <!-- 双语翻译开关：随时切换歌词译文显示 -->
        <button id="dockTransBtn" class="dock-action-btn dock-trans-btn" onclick="toggleBilingualLyrics()" title="双语翻译：已开启（点击关闭）" aria-pressed="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 5h8M7 3v2"></path>
            <path d="M9.5 5c0 3.6-2.6 6.8-6 8.4"></path>
            <path d="M5 9.2c1.7 2.3 4 3.9 6.8 4.6"></path>
            <path d="M12.5 21l3.9-9.5L20.3 21"></path>
            <path d="M14 17.2h4.8"></path>
          </svg>
        </button>
        <div class="dock-volume-wrap" id="dockVolumeWrap">
          <button id="dockVolBtn" class="dock-action-btn" onclick="toggleDockVolumeSlider(event)" title="音量调节（滚轮可微调）">
            <svg id="dockVolBtnIcon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>
            </svg>
          </button>
          <!-- 悬浮竖向音量调节卡片 (气泡置于按钮上方，竖向排布，不改变底栏宽度比例) -->
          <div id="dockVolSliderBox" class="dock-volume-popover vertical" onclick="event.stopPropagation()">
            <span id="dockVolValText" class="dock-vol-val-text">80%</span>
            <div id="dockVolHit" class="dock-vol-hit vertical" title="点击或上下拖动调节音量">
              <div class="dock-vol-track vertical">
                <div id="dockVolFill" class="dock-vol-fill vertical"></div>
                <div id="dockVolKnob" class="dock-vol-knob"></div>
              </div>
            </div>
            <button id="dockVolPopoverIcon" class="dock-vol-popover-icon" onclick="toggleMute()" title="静音 / 取消静音">
              <svg id="dockVolPopoverSvg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></svg>
            </button>
          </div>
        </div>
      </div>
      <!-- 隐藏兼容属性 (保留给旧测试用例或备用) -->
      <div style="display:none;">
        <button id="dockImmersiveBtn" title="已禁用黑胶"></button>
        <button id="dockRipBtn"></button>
        <button id="dockQueueBtn"></button>
        <div id="dockModeIcon"></div>
      </div>
    </footer>

  </div>

  <!-- 3. 菜单弹窗 (毛玻璃浮层) -->
  <!-- Discflow 专属综合设置模态框 -->
    <div id="settingsModal" class="popup-modal-overlay" onclick="closeModalOnBackdrop(event, 'settingsModal')">
      <div class="modal-dialog">
        <div class="modal-header">
          <h2 class="modal-title">Discflow 系统设置</h2>
          <button class="modal-close-btn" onclick="closeModal('settingsModal')">✕</button>
        </div>
        <div class="modal-list">
          <!-- 1. 关于 Discflow -->
          <div class="modal-item" onclick="openAboutModalFromSettings()">
            <span>ℹ️ 关于 Discflow</span>
            <span class="modal-item-sub">原生无损 CD 音乐播放器 · 16-Bit / 44.1kHz</span>
          </div>
          <!-- 2. 清空数据 -->
          <div class="modal-item" onclick="confirmClearAllData()">
            <span style="color: #FF453A;">🗑️ 清空资料库数据</span>
            <span class="modal-item-sub">清空所有本地已抓轨的专辑与转录音频</span>
          </div>
          <!-- 3. 自定义功能 -->
          <div class="modal-item" onclick="openCustomSettingsModal()">
            <span>🎨 自定义功能</span>
            <span class="modal-item-sub">调节歌词行间距/字间距、底部导航栏长短与透明度</span>
          </div>
          <!-- 3b. 放入光盘自动播放 -->
          <div class="modal-item" onclick="toggleAutoPlay()">
            <span>⚡ 放入光盘时自动播放</span>
            <span class="modal-item-sub" id="autoPlayLabel">已开启</span>
          </div>
          <!-- 4. 打开存储文件夹 -->
          <div class="modal-item" onclick="openLibraryFolder()">
            <span>📂 打开资源库存储文件夹</span>
            <span class="modal-item-sub" id="libraryPathSub">存储于：程序同级的 library 目录 (离线无损免光驱畅播)</span>
          </div>
          <!-- 4. 清空歌词缓存 -->
          <div class="modal-item" onclick="confirmClearLyricsCache()">
            <span>🧹 清空歌词缓存</span>
            <span class="modal-item-sub">重置已缓存的在线双语歌词，切歌时重新获取</span>
          </div>
        </div>
      </div>
    </div>


    <!-- 逐轨手动匹配歌词弹窗 -->
    <div id="lyricMatchModal" class="popup-modal-overlay" onclick="closeModalOnBackdrop(event, 'lyricMatchModal')">
      <div class="modal-dialog" style="max-width: 560px;">
        <div class="modal-header">
          <h2 class="modal-title">手动匹配歌词</h2>
          <button class="modal-close-btn" onclick="closeModal('lyricMatchModal')">✕</button>
        </div>
        <div class="modal-body" style="padding: 16px 22px; display: flex; flex-direction: column; gap: 12px;">
          <div id="lyricMatchTrackInfo" class="modal-item-sub"></div>
          <div style="display: flex; gap: 8px;">
            <input id="lyricMatchTitle" class="match-meta-input" style="flex: 1.4;" placeholder="歌名" />
            <input id="lyricMatchArtist" class="match-meta-input" style="flex: 1;" placeholder="艺术家" />
            <button id="lyricMatchSearchBtn" class="pill-btn active" onclick="searchLyricCandidates()" style="white-space: nowrap;">搜索</button>
          </div>
          <div id="lyricMatchHint" class="modal-item-sub" style="line-height: 1.6;">点击「搜索」从网易云 / QQ 音乐 / LRCLIB 同时检索。</div>
          <div id="lyricMatchResults" class="lyric-candidate-list"></div>
          <div style="display: flex; gap: 8px; justify-content: flex-end;">
            <button class="pill-btn" onclick="clearTrackLyrics()">清除歌词</button>
            <button class="pill-btn" onclick="autoMatchTrackLyrics()">重新自动匹配</button>
            <button class="pill-btn" onclick="closeModal('lyricMatchModal')">关闭</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 手动匹配专辑信息弹窗 -->
    <div id="matchMetadataModal" class="popup-modal-overlay" onclick="closeModalOnBackdrop(event, 'matchMetadataModal')">
      <div class="modal-dialog" style="max-width: 440px;">
        <div class="modal-header">
          <h2 class="modal-title">🔎 手动匹配专辑信息</h2>
          <button class="modal-close-btn" onclick="closeModal('matchMetadataModal')">✕</button>
        </div>
        <div class="modal-body" style="padding: 18px 24px; display: flex; flex-direction: column; gap: 14px;">
          <div id="matchMetaHint" class="modal-item-sub" style="line-height: 1.65;">
            填写专辑名后点「搜索」，从 QQ 音乐 / iTunes / 网易云 三源并发检索，选中一条即可整张套用。
          </div>
          <div style="display: flex; gap: 8px;">
            <input id="matchAlbumTitle" class="match-meta-input" style="flex: 1.4;" placeholder="专辑名称（必填）" />
            <input id="matchAlbumArtist" class="match-meta-input" style="flex: 1;" placeholder="艺术家（可选）" />
            <button id="matchMetaSubmitBtn" class="pill-btn active" onclick="submitMatchMetadata()" style="white-space: nowrap;">搜索</button>
          </div>
          <div id="matchAlbumResults" class="album-cand-list"></div>
          <div style="display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px;">
            <button class="pill-btn" onclick="closeModal('matchMetadataModal')">取消</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 自定义显示偏好模态弹窗 -->
    <div id="customSettingsModal" class="popup-modal-overlay" onclick="closeModalOnBackdrop(event, 'customSettingsModal')">
      <div class="modal-dialog" style="max-width: 460px;">
        <div class="modal-header">
          <h2 class="modal-title">🎨 自定义显示偏好</h2>
          <button class="modal-close-btn" onclick="closeModal('customSettingsModal')">✕</button>
        </div>
        <div class="modal-body" style="padding: 18px 24px; display: flex; flex-direction: column; gap: 16px;">
          
          <!-- 歌词排版自定义 -->
          <div class="custom-setting-group">
            <div class="custom-group-title">歌词排版</div>
            <div class="custom-control-row">
              <div class="custom-label-wrap">
                <label for="lyricLineHeightInput">行间距</label>
                <span id="lyricLineHeightVal" class="custom-val-badge">1.4</span>
              </div>
              <input type="range" id="lyricLineHeightInput" min="1.0" max="2.6" step="0.1" value="1.4" oninput="onLyricLineHeightChange(this.value)" />
            </div>
            <div class="custom-control-row">
              <div class="custom-label-wrap">
                <label for="lyricLetterSpacingInput">字间距</label>
                <span id="lyricLetterSpacingVal" class="custom-val-badge">0px</span>
              </div>
              <input type="range" id="lyricLetterSpacingInput" min="-1" max="10" step="0.5" value="0" oninput="onLyricLetterSpacingChange(this.value)" />
            </div>
          </div>

          <!-- 导航栏自定义 -->
          <div class="custom-setting-group">
            <div class="custom-group-title">底部导航栏</div>
            <div class="custom-control-row">
              <div class="custom-label-wrap">
                <label for="dockWidthInput">长短（宽度）</label>
                <span id="dockWidthVal" class="custom-val-badge">720px</span>
              </div>
              <input type="range" id="dockWidthInput" min="480" max="1080" step="20" value="720" oninput="onDockWidthChange(this.value)" />
            </div>
            <div class="custom-control-row">
              <div class="custom-label-wrap">
                <label for="dockOpacityInput">透明度</label>
                <span id="dockOpacityVal" class="custom-val-badge">75%</span>
              </div>
              <input type="range" id="dockOpacityInput" min="0" max="100" step="5" value="75" oninput="onDockOpacityChange(this.value)" />
            </div>
            <div class="custom-control-row">
              <div class="custom-label-wrap">
                <label for="dockBlurInput">模糊度 (毛玻璃)</label>
                <span id="dockBlurVal" class="custom-val-badge">40px</span>
              </div>
              <input type="range" id="dockBlurInput" min="0" max="60" step="2" value="40" oninput="onDockBlurChange(this.value)" />
            </div>
          </div>

          <!-- 操作行 -->
          <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
            <button class="pill-btn" onclick="resetCustomSettings()" style="font-size: 12px; padding: 6px 14px; opacity: 0.85;">恢复默认</button>
            <button class="pill-btn active" onclick="closeModal('customSettingsModal')" style="font-size: 13px; padding: 7px 20px;">完成</button>
          </div>

        </div>
      </div>
    </div>

  <!-- 4. 外观设置已合并进 #customSettingsModal（v2.0 移除重复的 appearanceModal，消除重复 ID） -->

  <!-- 5. 关于 Discflow 弹窗 (精美毛玻璃卡片，杜绝系统阻塞 alert) -->
  <div id="aboutModal" class="popup-modal-overlay" onclick="closeModalOnBackdrop(event, 'aboutModal')">
    <div class="modal-dialog">
      <div class="modal-header">
        <div class="discflow-brand">
          <div class="discflow-app-icon-badge">
            {DISCFLOW_ICON_SVG_24}
          </div>
          <span class="discflow-wordmark">{APP_NAME}</span>
        </div>
        <button class="modal-close-btn" onclick="closeModal('aboutModal')">✕</button>
      </div>
      <div class="modal-list" style="margin-top: 4px;">
        <div class="modal-item" style="cursor: default;">
          <span>版本与架构</span>
          <span class="modal-item-sub">v3.2 · Windows x64 Native</span>
        </div>
        <div class="modal-item" style="cursor: default;">
          <span>音频引擎</span>
          <span class="modal-item-sub">CD-DA 44.1kHz / 16-Bit PCM 零损直读</span>
        </div>
        <div class="modal-item" style="cursor: default;">
          <span>元数据与歌词</span>
          <span class="modal-item-sub">MusicBrainz / FreeDB / 双语滚动 LRC</span>
        </div>
        <div class="modal-item" style="cursor: default;">
          <span>设计理念</span>
          <span class="modal-item-sub">Apple Music 级动态流体光斑与极简黑白</span>
        </div>
      </div>
      <div style="display: flex; justify-content: flex-end; margin-top: 6px;">
        <button class="pill-btn active" onclick="closeModal('aboutModal')">完成</button>
      </div>
    </div>
  </div>

  <!-- 6. 抓轨归档进度弹窗 (#ripModal) -->
  <div id="ripModal" class="popup-modal-overlay">
    <div class="modal-dialog" style="max-width: 480px;">
      <div class="modal-header">
        <h2 class="modal-title">CD 无损抓轨归档</h2>
        <button id="ripModalCloseBtn" class="modal-close-btn" onclick="cancelRippingAndClose()">✕</button>
      </div>
      <div style="padding: 10px 0 6px 0; display: flex; flex-direction: column; gap: 14px;">
        <div style="display: flex; gap: 14px; align-items: center;">
          <img id="ripCoverImg" src="" style="width: 60px; height: 60px; border-radius: 8px; object-fit: cover; background: #222;" />
          <div style="flex: 1; min-width: 0;">
            <div id="ripAlbumTitle" style="font-weight: 700; font-size: 14.5px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">--</div>
            <div id="ripArtist" style="font-size: 12.5px; color: var(--text-secondary); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">--</div>
          </div>
        </div>

        <div>
          <div style="display: flex; justify-content: space-between; font-size: 12px; color: var(--text-secondary); margin-bottom: 5px;">
            <span id="ripStatusText">正在准备抓轨...</span>
            <span id="ripOverallPctText" style="font-family: var(--font-mono); font-weight: 600;">0%</span>
          </div>
          <div style="width: 100%; height: 7px; background: var(--track-bar-bg); border-radius: 999px; overflow: hidden;">
            <div id="ripOverallBar" style="height: 100%; width: 0%; background: #30D158; border-radius: 999px; transition: width 0.2s ease;"></div>
          </div>
        </div>

        <div>
          <div style="display: flex; justify-content: space-between; font-size: 11px; color: var(--text-tertiary); margin-bottom: 4px;">
            <span id="ripTrackTitle">等待音轨...</span>
            <span id="ripTrackPctText" style="font-family: var(--font-mono);">0%</span>
          </div>
          <div style="width: 100%; height: 4px; background: var(--track-bar-bg); border-radius: 999px; overflow: hidden;">
            <div id="ripTrackBar" style="height: 100%; width: 0%; background: var(--track-bar-fill); border-radius: 999px; transition: width 0.2s ease;"></div>
          </div>
        </div>

        <div style="display: flex; justify-content: flex-end; gap: 10px; margin-top: 6px;">
          <button id="ripCancelBtn" class="pill-btn" onclick="cancelRippingAndClose()">取消抓轨</button>
          <button id="ripDoneBtn" class="pill-btn active" style="display: none;" onclick="finishRippingAndShowLibrary()">确认 (回到资源库)</button>
        </div>
      </div>
    </div>
  </div>

  <!-- 7. 全局搜索弹窗 (#searchModal) -->
  <div id="searchModal" class="popup-modal-overlay">
    <div class="modal-dialog" style="max-width: 540px; border-radius: 18px; padding: 22px 24px;">
      <div class="modal-header" style="margin-bottom: 14px;">
        <div style="display: flex; align-items: center; gap: 8px;">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="opacity: 0.85;">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <h2 class="modal-title" style="font-size: 17px; font-weight: 700;">搜索资源库</h2>
        </div>
        <button class="modal-close-btn" onclick="closeModal('searchModal')">✕</button>
      </div>

      <!-- 搜索输入条 -->
      <div style="display: flex; gap: 8px; align-items: center; background: rgba(255, 255, 255, 0.08); border: 1px solid var(--border-hairline); border-radius: 12px; padding: 8px 14px;">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="opacity: 0.5;">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        <input type="text" id="modalSearchInput" placeholder="搜索已抓轨专辑、歌名或歌手..."
               oninput="onModalSearchInput(this.value)"
               onkeydown="if(event.key==='Enter') executeModalSearch()"
               style="flex: 1; background: transparent; border: none; outline: none; color: var(--text-primary); font-size: 14px;" />
        <button id="modalSearchClearBtn" onclick="clearModalSearch()" style="background: transparent; border: none; color: var(--text-tertiary); cursor: pointer; padding: 2px 6px; font-size: 13px; display: none;">✕</button>
        <button class="pill-btn active" onclick="executeModalSearch()" style="padding: 4px 14px; font-size: 12px;">搜索</button>
      </div>

      <!-- 搜索结果列表容器 -->
      <div id="modalSearchResults" style="max-height: 280px; overflow-y: auto; margin-top: 14px; display: flex; flex-direction: column; gap: 8px; min-height: 100px;">
        <div style="text-align: center; color: var(--text-tertiary); padding: 32px 12px; font-size: 13px;">
          输入专辑、歌名或歌手关键词进行搜索
        </div>
      </div>

      <!-- 底部跳转按钮 -->
      <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 14px; border-top: 1px solid var(--border-hairline); padding-top: 12px;">
        <span id="modalSearchCountText" style="font-size: 12px; color: var(--text-tertiary);">准备就绪</span>
        <div style="display: flex; gap: 8px;">
          <button class="pill-btn" onclick="jumpToLibraryFromSearch()">在资源库中浏览</button>
          <button class="pill-btn active" onclick="closeModal('searchModal')">关闭</button>
        </div>
      </div>
    </div>
  </div>

  <!-- 8. 谜底黑胶 (MD Vinyl) 1:1 实机像素级全屏沉浸播放舞台 (#immersiveVinylStage) -->
  <div id="immersiveVinylStage" class="immersive-vinyl-stage">
    <!-- 自适应动态柔焦环境光漫射层 -->
    <div id="mdAmbientBg" class="md-ambient-bg"></div>

    <!-- 顶部极简状态栏：左侧时钟，右侧设置与退出 -->
    <!-- 沉浸黑胶模式：移除顶部干扰项（时间、设置与关闭叉号），界面极致沉浸纯粹 -->

    <!-- 主舞台中央区：左侧大封面封套 + 右侧半露琥珀黑胶 + 右上角悬浮机械金属唱臂 -->
    <div class="md-centerpiece">
      <div class="md-stage-wrapper">
        <!-- 左侧：拟物大封面封套 (Jacket Sleeve) -->
        <div class="md-sleeve-jacket" id="mdSleeveJacket" onclick="toggleMdLyricsSheet()" title="点击切换歌词与唱片详情">
          <div class="md-sleeve-spine"></div>
          <img id="immersiveCoverImg" class="md-sleeve-artwork" src="" alt="Album Cover" />
        </div>

        <!-- 右侧：固定比例黑胶唱机组件 (黑胶盘 + 唱臂基准绝对锁定，杜绝任何缩放下的位移漂移) -->
        <div class="md-vinyl-rig" id="mdVinylRig">
          <!-- 旋转彩胶唱片 -->
          <div id="immersiveVinylDisc" class="md-vinyl-disc">
            <div class="md-vinyl-grooves"></div>
            <div class="md-vinyl-specular"></div>
            <div class="md-vinyl-label">
              <img id="immersiveLabelImg" class="md-vinyl-label-art" src="" alt="Label" />
              <div class="md-label-text-box">
                <span id="mdLabelTrackTitle" class="md-label-track-title">Discflow</span>
                <span id="mdLabelArtist" class="md-label-artist">Hi-Fi</span>
              </div>
              <div class="center-spindle-hole"></div>
            </div>
          </div>

          <!-- 机械金属唱臂：直接相对于 .md-vinyl-rig 锚定，针尖无论缩放到何种尺寸均恒定在黑胶音轨上 -->
          <div id="immersiveTonearm" class="md-tonearm-assembly">
            <div class="md-tonearm-glass-plate"></div>
            <div class="md-tonearm-pivot">
              <div class="md-tonearm-pivot-cap"></div>
            </div>
            <div class="md-tonearm-wand"></div>
            <div class="md-tonearm-headshell">
              <div class="md-tonearm-cartridge"></div>
              <div class="md-tonearm-stylus"></div>
            </div>
          </div>
        </div></div>
      </div>
    </div>

    <!-- 悬浮双语歌词浮动层 -->
    <div id="mdLyricsSheet" class="md-lyrics-sheet">
      <div id="mdLyricsActiveLine" class="md-lyric-main">纯享 CD 无损音轨</div>
      <div id="mdLyricsActiveSub" class="md-lyric-sub"></div>
    </div>

    <!-- 底栏「播放列表与控制面板」iOS 液态玻璃弹性阻尼流体浮动卡片 -->
    <div id="mdFluidMorphOverlay" class="md-fluid-morph-backdrop" onclick="closeFluidPlaylistMorph(event)">
      <div id="mdFluidMorphCard" class="md-fluid-morph-card" onclick="event.stopPropagation()">
        <!-- 顶部控制行：三态播放模式切换 + 紧凑音量滑块 -->
        <div class="fluid-card-header">
          <div class="fluid-mode-group">
            <button id="fluidModeRepeatAll" class="fluid-mode-btn active" onclick="setFluidPlayMode('repeat_all')" title="列表循环">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>
              </svg>
            </button>
            <button id="fluidModeShuffle" class="fluid-mode-btn" onclick="setFluidPlayMode('shuffle')" title="随机播放">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                <path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/>
              </svg>
            </button>
            <button id="fluidModeRepeatOne" class="fluid-mode-btn" onclick="setFluidPlayMode('repeat_one')" title="单曲循环">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor">
                <path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>
                <circle cx="12" cy="12" r="2.5" fill="currentColor"/>
              </svg>
            </button>
          </div>

          <div class="fluid-volume-group">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" opacity="0.8">
              <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
              <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
            </svg>
            <div id="fluidVolHit" class="fluid-vol-hit" title="滑动调节音量">
              <div class="fluid-vol-track">
                <div id="fluidVolFill" class="fluid-vol-fill"></div>
              </div>
            </div>
          </div>
        </div>

        <!-- 曲目列表区：垂直展示当前专辑曲目（音轨号、歌名、时长、正在播放高亮、快速切歌） -->
        <div id="fluidTrackList" class="fluid-track-list"></div>
      </div>
    </div>

    <!-- 底部悬浮控制药丸胶囊 (MD Vinyl Floating Pill Dock) -->
    <div class="md-dock-container">
      <!-- 居中主药丸胶囊 (去除左下角/右下角按钮及播放键旁的循环播放键) -->
      <div class="md-player-dock">
        <!-- 播放列表与控制面板触发锚点 (液态玻璃弹性形变) -->
        <button id="mdPlaylistMorphBtn" class="md-dock-icon-btn" onclick="toggleFluidPlaylistMorph(event)" title="播放列表与控制面板">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
            <path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"/>
          </svg>
        </button>

        <!-- 中间：微缩唱片圆标 + 歌曲/歌手信息 -->
        <div class="md-dock-track-info" onclick="toggleMdLyricsSheet()" title="点击切换歌词">
          <img id="mdDockMiniLabel" class="md-dock-mini-label" src="" alt="Track Art" />
          <div class="md-dock-text-col">
            <span id="immersiveTrackTitle" class="md-dock-title">等待播放</span>
            <span id="immersiveArtistName" class="md-dock-artist">--</span>
          </div>
        </div>

        <!-- 右侧：播放/暂停按钮 -->
        <div class="md-dock-controls">
          <button id="mdDockPlayPauseBtn" class="md-dock-icon-btn play-pause-btn" onclick="togglePlayPause()" title="播放 / 暂停">
            <svg id="mdDockPlayIcon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor" style="display:none;">
              <path d="M8 5.14v13.72c0 .79.87 1.27 1.54.84l11-6.86c.62-.39.62-1.29 0-1.68l-11-6.86C8.87 3.87 8 4.35 8 5.14z"/>
            </svg>
            <svg id="mdDockPauseIcon" viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
              <path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
            </svg>
          </button>
        </div>
      </div>

      <!-- 隐藏占位以向下兼容旧测试及脚本引用 -->
      <div style="display:none;">
        <img id="mdDockThumbImg" src="" alt="" />
        <button id="mdDockMoreBtn"></button>
        <button id="mdDockModeBtn"></button>
      </div>
    </div>
  </div>

<script>

  // ================= 自定义功能：歌词行间距/字间距、导航栏长短/透明度/模糊度与主题持久化 =================
  const DEFAULT_CUSTOM_PREFS = {{
    lyricLineHeight: 1.4,
    lyricLetterSpacing: 0,
    dockWidth: 720,
    dockOpacity: 75,
    dockBlur: 40,
    themeMode: "dark",
    showTranslation: true
  }};

  let currentCustomPrefs = {{ ...DEFAULT_CUSTOM_PREFS }};

  function loadCustomPrefs() {{
    try {{
      const saved = localStorage.getItem("discflow_custom_prefs");
      if (saved) {{
        currentCustomPrefs = {{ ...DEFAULT_CUSTOM_PREFS, ...JSON.parse(saved) }};
      }}
    }} catch (e) {{}}
    applyCustomPrefs(currentCustomPrefs);

    try {{
      const savedTheme = localStorage.getItem("discflow_theme");
      if (savedTheme) {{
        applyTheme(savedTheme);
      }}
    }} catch (e) {{}}
  }}

  function saveCustomPrefs() {{
    try {{
      localStorage.setItem("discflow_custom_prefs", JSON.stringify(currentCustomPrefs));
    }} catch (e) {{}}
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_preferences) {{
      window.pywebview.api.save_preferences(currentCustomPrefs).catch(() => {{}});
    }}
  }}

  async function syncPreferencesFromBackend() {{
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_preferences) return;
    try {{
      const prefs = await window.pywebview.api.get_preferences();
      if (prefs && typeof prefs === "object") {{
        currentCustomPrefs = {{ ...DEFAULT_CUSTOM_PREFS, ...currentCustomPrefs, ...prefs }};
        applyCustomPrefs(currentCustomPrefs);
        if (prefs.themeMode) {{
          applyTheme(prefs.themeMode);
        }}
        if (typeof prefs.autoPlay === "boolean") {{
          appState.autoPlayDisc = prefs.autoPlay;
          const label = document.getElementById("autoPlayLabel");
          if (label) label.textContent = prefs.autoPlay ? "已开启" : "已关闭";
        }}
      }}
    }} catch (e) {{
      console.warn("syncPreferencesFromBackend error:", e);
    }}
  }}

  // 页面脚本初始化时立即应用本地已保存的自定义外观设置
  loadCustomPrefs();

  function applyCustomPrefs(prefs) {{
    const root = document.documentElement;
    root.style.setProperty("--lyrics-line-height", prefs.lyricLineHeight);
    const mb = Math.round(6 + (prefs.lyricLineHeight - 1.0) * 8);
    root.style.setProperty("--lyrics-margin-bottom", `${{mb}}px`);
    root.style.setProperty("--lyrics-letter-spacing", `${{prefs.lyricLetterSpacing}}px`);
    root.style.setProperty("--dock-custom-width", `${{prefs.dockWidth}}px`);
    const op = prefs.dockOpacity !== undefined ? Number(prefs.dockOpacity) : 75;
    root.style.setProperty("--dock-custom-opacity", (op / 100).toFixed(2));
    root.style.setProperty("--dock-custom-blur", `${{prefs.dockBlur !== undefined ? prefs.dockBlur : 40}}px`);
    const dockEl = document.getElementById("bottomDockPlayer");
    if (dockEl) {{
      dockEl.classList.toggle("is-fully-transparent", op === 0);
    }}
    applyTranslationVisibility(prefs.showTranslation !== false);
    syncCustomSettingsInputs(prefs);
  }}

  function syncCustomSettingsInputs(prefs) {{
    const lhInput = document.getElementById("lyricLineHeightInput");
    const lhVal = document.getElementById("lyricLineHeightVal");
    if (lhInput) lhInput.value = prefs.lyricLineHeight;
    if (lhVal) lhVal.textContent = prefs.lyricLineHeight;

    const lsInput = document.getElementById("lyricLetterSpacingInput");
    const lsVal = document.getElementById("lyricLetterSpacingVal");
    if (lsInput) lsInput.value = prefs.lyricLetterSpacing;
    if (lsVal) lsVal.textContent = `${{prefs.lyricLetterSpacing}}px`;

    const dwInput = document.getElementById("dockWidthInput");
    const dwVal = document.getElementById("dockWidthVal");
    if (dwInput) dwInput.value = prefs.dockWidth;
    if (dwVal) dwVal.textContent = `${{prefs.dockWidth}}px`;

    const doInput = document.getElementById("dockOpacityInput");
    const doVal = document.getElementById("dockOpacityVal");
    const op = prefs.dockOpacity !== undefined ? Number(prefs.dockOpacity) : 75;
    if (doInput) doInput.value = op;
    if (doVal) doVal.textContent = `${{op}}%`;

    const dbInput = document.getElementById("dockBlurInput");
    const dbVal = document.getElementById("dockBlurVal");
    if (dbInput) dbInput.value = prefs.dockBlur !== undefined ? prefs.dockBlur : 40;
    if (dbVal) dbVal.textContent = `${{prefs.dockBlur !== undefined ? prefs.dockBlur : 40}}px`;
  }}

  function onDockBlurChange(val) {{
    currentCustomPrefs.dockBlur = parseInt(val, 10);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }}

  // --- 手动匹配专辑信息（应对光盘信息读取失败） ---
  function openMatchMetadataModal() {{
    const t = document.getElementById("matchAlbumTitle");
    const a = document.getElementById("matchAlbumArtist");
    const hint = document.getElementById("matchMetaHint");
    const cur = appState.albumTitle || "";
    const curArtist = appState.artist || "";
    if (t) t.value = (cur && cur !== "Audio CD" && !cur.startsWith("等待")) ? cur : "";
    if (a) a.value = (curArtist && !curArtist.startsWith("未检测到") && !curArtist.startsWith("放入")) ? curArtist : "";
    if (hint) hint.textContent = "光盘信息读取失败时，在这里填写专辑名，Discflow 会自动在线检索并套用封面、年份与曲目名。";
    openModal("matchMetadataModal");
    setTimeout(() => {{ if (t) {{ t.focus(); t.select(); }} }}, 120);
  }}

  // 等待后端整专辑歌词批量任务完成，期间把进度写进提示文案
  async function waitForLyricsBatch(maxMs = 180000) {{
    const hint = document.getElementById("matchMetaHint");
    const startTs = Date.now();
    while (Date.now() - startTs < maxMs) {{
      const b = appState.lyricsBatch || {{}};
      if (b.running) {{
        if (hint) hint.textContent = `正在批量匹配歌词 ${{b.done}}/${{b.total}}，已命中 ${{b.matched}} 首…`;
      }} else if ((b.total || 0) > 0) {{
        return b;
      }}
      await new Promise(r => setTimeout(r, 350));
    }}
    return appState.lyricsBatch || {{}};
  }}

  let albumCandidateCache = [];

  async function submitMatchMetadata() {{
    const tEl = document.getElementById("matchAlbumTitle");
    const aEl = document.getElementById("matchAlbumArtist");
    const btn = document.getElementById("matchMetaSubmitBtn");
    const hint = document.getElementById("matchMetaHint");
    const box = document.getElementById("matchAlbumResults");
    const title = tEl ? tEl.value.trim() : "";
    const artist = aEl ? aEl.value.trim() : "";
    if (!title) {{
      if (hint) hint.textContent = "请先填写专辑名称。";
      return;
    }}
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.search_album_candidates_for_current) return;

    if (btn) {{ btn.disabled = true; btn.textContent = "检索中…"; }}
    if (hint) hint.textContent = "正在从 QQ 音乐 / iTunes / 网易云 三源并发检索…";
    if (box) box.innerHTML = "";
    albumCandidateCache = [];
    try {{
      const list = await window.pywebview.api.search_album_candidates_for_current(title, artist);
      albumCandidateCache = Array.isArray(list) ? list : [];
      renderAlbumCandidates(albumCandidateCache);
      if (hint) {{
        hint.textContent = albumCandidateCache.length
          ? "点任意一条即可整张套用（专辑信息 + 曲名 + 封面 + 歌词）。"
          : "没有找到匹配的专辑，试试换个更准确的专辑名或补上艺术家。";
      }}
    }} catch (e) {{
      console.error("search_album_candidates error:", e);
      if (hint) hint.textContent = "检索失败：" + e;
    }} finally {{
      if (btn) {{ btn.disabled = false; btn.textContent = "搜索"; }}
    }}
  }}

  function renderAlbumCandidates(list) {{
    const box = document.getElementById("matchAlbumResults");
    if (!box) return;
    if (!list || !list.length) {{
      box.innerHTML = '<div class="lyric-cand-empty">暂无候选结果</div>';
      return;
    }}
    box.innerHTML = list.map((c, i) => {{
      const label = LYRIC_SOURCE_LABEL[c.source] || c.source || "未知来源";
      const ids = (c.tracks || []).filter(t => t.song_id).length;
      const trackCount = (c.tracks || []).length || c.track_count || 0;
      const canDirect = ids > 0;
      const metaParts = [];
      metaParts.push(trackCount ? `<b>${{trackCount}}</b> 首` : "曲目数未知");
      if (canDirect) metaParts.push(`<b>${{ids}}</b> 首可直取歌词`);
      else if (c.source === "netease") metaParts.push("仅封面与专辑信息");
      else metaParts.push("歌词需按曲名搜索");
      // 曲库里有不少 Shift-JIS 误解码的残次条目，标出来避免用户选中后把乱码写进曲名
      if (c.garbled) metaParts.push(`<span class="album-cand-warn">⚠ ${{c.dirtyTracks || 0}} 首曲名疑似乱码</span>`);
      const trackPreview = (c.tracks || []).slice(0, 4).map(t => t.title).join(" / ");
      const cover = c.cover_url
        ? `<img class="album-cand-cover" src="${{escapeHtml(c.cover_url)}}" alt="" loading="lazy" />`
        : '<div class="album-cand-cover"></div>';
      return `
        <div class="album-cand" onclick="applyAlbumCandidate(${{i}})">
          ${{cover}}
          <div class="album-cand-body">
            <div class="album-cand-top">
              <span class="album-cand-src">${{escapeHtml(label)}}</span>
              <span class="album-cand-title">${{escapeHtml(c.title || "")}}</span>
              <span class="album-cand-year">${{escapeHtml(c.year || "")}} · ${{escapeHtml(c.artist || "")}}</span>
            </div>
            <div class="album-cand-meta">${{metaParts.join(" · ")}}</div>
            ${{trackPreview ? `<div class="album-cand-tracks">${{escapeHtml(trackPreview)}}</div>` : ""}}
          </div>
        </div>
      `;
    }}).join("");
  }}

  async function applyAlbumCandidate(index) {{
    const cand = albumCandidateCache[index];
    if (!cand) return;
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.apply_album_candidate) return;
    const hint = document.getElementById("matchMetaHint");
    if (hint) hint.textContent = "正在套用专辑信息与封面…";
    try {{
      const res = await window.pywebview.api.apply_album_candidate(cand);
      if (res && res.ok) {{
        if (res.lyricsBatchStarted) {{
          if (hint) hint.textContent = "专辑已套用，正在为整张专辑匹配歌词…";
          const b = await waitForLyricsBatch();
          const miss = (b.missed || []).length;
          if (hint) {{
            hint.textContent = miss
              ? `整专辑歌词匹配完成：${{b.matched || 0}}/${{b.total || 0}} 首已套用，${{miss}} 首没对上——关掉弹窗后点曲目行上的「词」逐轨指定。`
              : `整专辑歌词匹配完成：${{b.matched || 0}}/${{b.total || 0}} 首已套用。`;
          }}
          await new Promise(r => setTimeout(r, miss ? 1600 : 800));
        }}
        closeModal("matchMetadataModal");
        triggerImmediatePoll();
        renderQueueList();
        if (res.skippedGarbled > 0) {{
          alertToast(`已跳过 ${{res.skippedGarbled}} 个乱码曲名，保留本地曲名`);
        }}
      }} else if (hint) {{
        hint.textContent = (res && res.reason) || "套用失败，换一条候选试试。";
      }}
    }} catch (e) {{
      console.error("apply_album_candidate error:", e);
      if (hint) hint.textContent = "套用失败：" + e;
    }}
  }}

  // --- 逐轨手动匹配歌词 ---
  let lyricMatchTrackNumber = null;
  let lyricMatchDuration = 0;
  let lyricCandidateCache = [];

  const LYRIC_SOURCE_LABEL = {{ netease: "网易云", qq: "QQ 音乐", lrclib: "LRCLIB" }};

  function openLyricMatchModal(event, idx) {{
    if (event) event.stopPropagation();
    const t = (appState.tracks || [])[idx];
    if (!t) return;
    lyricMatchTrackNumber = t.track_number;
    lyricMatchDuration = Number(t.duration_sec) || 0;
    lyricCandidateCache = [];

    const info = document.getElementById("lyricMatchTrackInfo");
    if (info) {{
      info.textContent = `第 ${{t.track_number}} 轨 · ${{t.title}} · ${{t.artist}} · ${{formatTime(t.duration_sec)}}`;
    }}
    const tEl = document.getElementById("lyricMatchTitle");
    const aEl = document.getElementById("lyricMatchArtist");
    if (tEl) tEl.value = t.title || "";
    if (aEl) aEl.value = t.artist || "";
    const box = document.getElementById("lyricMatchResults");
    if (box) box.innerHTML = "";
    const hint = document.getElementById("lyricMatchHint");
    if (hint) hint.textContent = "点击「搜索」从网易云 / QQ 音乐 / LRCLIB 同时检索。";

    openModal("lyricMatchModal");
    searchLyricCandidates();
  }}

  async function searchLyricCandidates() {{
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.search_track_lyric_candidates) return;
    const hint = document.getElementById("lyricMatchHint");
    const btn = document.getElementById("lyricMatchSearchBtn");
    const tEl = document.getElementById("lyricMatchTitle");
    const aEl = document.getElementById("lyricMatchArtist");
    const box = document.getElementById("lyricMatchResults");
    const title = tEl ? tEl.value.trim() : "";
    const artist = aEl ? aEl.value.trim() : "";

    if (btn) {{ btn.disabled = true; btn.textContent = "检索中…"; }}
    if (hint) hint.textContent = "正在同时检索网易云 / QQ 音乐 / LRCLIB…";
    if (box) box.innerHTML = "";
    try {{
      const list = await window.pywebview.api.search_track_lyric_candidates(lyricMatchTrackNumber, title, artist);
      lyricCandidateCache = Array.isArray(list) ? list : [];
      renderLyricCandidates(lyricCandidateCache);
      if (hint) {{
        hint.textContent = lyricCandidateCache.length
          ? "点任意一条即可套用并锁定，锁定后自动匹配不会再覆盖。"
          : "没有找到候选，试试把歌名改得更简短或换一种写法。";
      }}
    }} catch (e) {{
      console.error("search_track_lyric_candidates error:", e);
      if (hint) hint.textContent = "检索失败：" + e;
    }} finally {{
      if (btn) {{ btn.disabled = false; btn.textContent = "搜索"; }}
    }}
  }}

  function renderLyricCandidates(list) {{
    const box = document.getElementById("lyricMatchResults");
    if (!box) return;
    if (!list || !list.length) {{
      box.innerHTML = '<div class="lyric-cand-empty">暂无候选结果</div>';
      return;
    }}
    box.innerHTML = list.map((c, i) => {{
      const label = LYRIC_SOURCE_LABEL[c.source] || c.source || "未知来源";
      const dur = Number(c.duration_sec) || 0;
      const diff = (dur > 0 && lyricMatchDuration > 0) ? Math.abs(dur - lyricMatchDuration) : 0;
      const parts = [];
      if (c.line_count) parts.push(`${{c.line_count}} 行`);
      parts.push(c.has_translation ? "有翻译" : "无翻译");
      parts.push(c.has_words ? "有逐字" : "无逐字");
      if (diff > 0.15) parts.push(`时长差 ${{diff.toFixed(1)}}s`);
      const meta = parts.map(p => (p === "有翻译" || p === "有逐字") ? `<b>${{p}}</b>` : p).join(" · ");
      return `
        <div class="lyric-cand" onclick="applyLyricCandidate(${{i}})">
          <div class="lyric-cand-top">
            <span class="lyric-cand-src">${{escapeHtml(label)}}</span>
            <span class="lyric-cand-title">${{escapeHtml(c.title || "")}}</span>
            <span class="lyric-cand-artist">${{escapeHtml(c.artist || "")}}</span>
            <span class="lyric-cand-dur">${{dur > 0 ? formatTime(dur) : "--"}}</span>
          </div>
          <div class="lyric-cand-meta">${{meta}}</div>
          ${{c.preview ? `<div class="lyric-cand-preview">${{escapeHtml(c.preview)}}</div>` : ""}}
        </div>
      `;
    }}).join("");
  }}

  async function applyLyricCandidate(index) {{
    const cand = lyricCandidateCache[index];
    if (!cand) return;
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.apply_track_lyrics) return;
    const hint = document.getElementById("lyricMatchHint");
    if (hint) hint.textContent = "正在套用并锁定这首歌的歌词…";
    try {{
      const res = await window.pywebview.api.apply_track_lyrics(
        lyricMatchTrackNumber, cand.source, String(cand.song_id || ""),
        cand.title || "", cand.artist || "", Number(cand.duration_sec) || 0
      );
      if (res && res.ok) {{
        if (hint) hint.textContent = `已套用 ${{res.lineCount}} 行歌词，并锁定该曲目。`;
        closeModal("lyricMatchModal");
        triggerImmediatePoll();
        renderQueueList();
      }} else if (hint) {{
        hint.textContent = (res && res.reason) || "套用失败，换一条候选试试。";
      }}
    }} catch (e) {{
      console.error("apply_track_lyrics error:", e);
      if (hint) hint.textContent = "套用失败：" + e;
    }}
  }}

  async function clearTrackLyrics() {{
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.clear_track_lyrics) return;
    const hint = document.getElementById("lyricMatchHint");
    try {{
      await window.pywebview.api.clear_track_lyrics(lyricMatchTrackNumber);
      if (hint) hint.textContent = "已清除该曲目的歌词并解除锁定。";
      triggerImmediatePoll();
      renderQueueList();
    }} catch (e) {{
      if (hint) hint.textContent = "清除失败：" + e;
    }}
  }}

  async function autoMatchTrackLyrics() {{
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.auto_match_track_lyrics) return;
    const hint = document.getElementById("lyricMatchHint");
    if (hint) hint.textContent = "正在按曲名重新自动匹配…";
    try {{
      const res = await window.pywebview.api.auto_match_track_lyrics(lyricMatchTrackNumber);
      if (hint) {{
        hint.textContent = (res && res.ok)
          ? `自动匹配到 ${{res.lineCount}} 行歌词，已解除锁定。`
          : "自动匹配没有找到歌词，可手动搜索选择。";
      }}
      triggerImmediatePoll();
      renderQueueList();
    }} catch (e) {{
      if (hint) hint.textContent = "自动匹配失败：" + e;
    }}
  }}

  function openCustomSettingsModal() {{
    closeModal("settingsModal");
    syncCustomSettingsInputs(currentCustomPrefs);
    openModal("customSettingsModal");
  }}

  function onLyricLineHeightChange(val) {{
    currentCustomPrefs.lyricLineHeight = parseFloat(val);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }}

  function onLyricLetterSpacingChange(val) {{
    currentCustomPrefs.lyricLetterSpacing = parseFloat(val);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }}

  function onDockWidthChange(val) {{
    currentCustomPrefs.dockWidth = parseInt(val, 10);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }}

  function onDockOpacityChange(val) {{
    currentCustomPrefs.dockOpacity = parseInt(val, 10);
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }}

  function resetCustomSettings() {{
    currentCustomPrefs = {{ ...DEFAULT_CUSTOM_PREFS }};
    applyCustomPrefs(currentCustomPrefs);
    saveCustomPrefs();
  }}
  // ================= Discflow 专属弹出菜单交互 =================
  let isDiscflowMenuOpen = false;

    // 底部控制栏音量滑块展开/收起
  let isDockVolumeSliderOpen = false;

  function toggleDockVolumeSlider(event) {{
    if (event) {{
      event.preventDefault();
      event.stopPropagation();
    }}
    isDockVolumeSliderOpen = !isDockVolumeSliderOpen;
    const wrap = document.getElementById("dockVolumeWrap");
    if (wrap) {{
      wrap.classList.toggle("is-open", isDockVolumeSliderOpen);
    }}
  }}

  function closeDockVolumeSlider() {{
    isDockVolumeSliderOpen = false;
    const wrap = document.getElementById("dockVolumeWrap");
    if (wrap) wrap.classList.remove("is-open");
  }}

  // 封面与底栏缩略图点击：切换歌词与曲目列表
  function toggleLyricsAndTracklist(event) {{
    if (event) {{
      event.preventDefault();
      event.stopPropagation();
    }}
    switchMainView("cd");
    if (appState.rightPanelMode === "queue") {{
      switchRightPanel("lyrics");
    }} else {{
      switchRightPanel("queue");
    }}
  }}
  const toggleRightPanelFromDock = toggleLyricsAndTracklist;

function toggleDiscflowMenu(event) {{
    if (event) {{
      event.preventDefault();
      event.stopPropagation();
    }}
    isDiscflowMenuOpen = !isDiscflowMenuOpen;
    const menu = document.getElementById("discflowAppMenu");
    if (menu) {{
      if (isDiscflowMenuOpen) {{
        menu.classList.add("is-open");
      }} else {{
        menu.classList.remove("is-open");
      }}
    }}
  }}

  function closeDiscflowMenu() {{
    isDiscflowMenuOpen = false;
    const menu = document.getElementById("discflowAppMenu");
    if (menu) menu.classList.remove("is-open");
  }}

  document.addEventListener("click", (e) => {{
    const btn = document.getElementById("discflowAppBtn");
    const menu = document.getElementById("discflowAppMenu");
    if (!(btn && btn.contains(e.target)) && !(menu && menu.contains(e.target))) {{
      if (isDiscflowMenuOpen) closeDiscflowMenu();
    }}
    // 音量弹层同理：点空白处收起，避免一直挂在底栏上方
    const volWrap = document.getElementById("dockVolumeWrap");
    if (isDockVolumeSliderOpen && volWrap && !volWrap.contains(e.target)) {{
      closeDockVolumeSlider();
    }}
  }});

  function selectDiscflowMenu(action) {{
    closeDiscflowMenu();
    if (action === "library") {{
      switchMainView("library");
    }} else if (action === "search") {{
      openSearchModal();
    }} else if (action === "settings") {{
      openModal("settingsModal");
    }}
  }}

  function openAboutModalFromSettings() {{
    closeModal("settingsModal");
    openModal("aboutModal");
  }}

  async function confirmClearAllData() {{
    if (!confirm("⚠️ 确定要清空 Discflow 本地资料库数据吗？\\n所有已转录归档的唱片音频将被彻底删除！此操作不可恢复。")) {{
      return;
    }}
    try {{
      if (window.pywebview && window.pywebview.api && window.pywebview.api.clear_all_library_data) {{
        await window.pywebview.api.clear_all_library_data();
      }}
      await renderLibraryCards();
      alert("本地资料库已完全清空。");
    }} catch (e) {{
      console.error("clear_all_library_data error:", e);
      alert("清空数据失败: " + e);
    }}
  }}

  async function confirmClearLyricsCache() {{
    try {{
      if (window.pywebview && window.pywebview.api && window.pywebview.api.clear_lyrics_cache) {{
        await window.pywebview.api.clear_lyrics_cache();
      }}
      alert("双语歌词缓存已全部清空，将在切歌时重新从源头匹配。");
    }} catch (e) {{
      console.error("clear_lyrics_cache error:", e);
    }}
  }}

  // 打开资源库物理存储目录（便携优先策略解析出的实际路径）
  async function openLibraryFolder() {{
    try {{
      if (window.pywebview && window.pywebview.api && window.pywebview.api.open_library_folder) {{
        const res = await window.pywebview.api.open_library_folder();
        if (res && res.ok === false) {{
          alert("无法打开资源库目录：\\n" + (res.path || "路径不可用"));
        }}
        return;
      }}
      alert("资源库目录功能尚未就绪，请稍后重试。");
    }} catch (e) {{
      console.error("open_library_folder error:", e);
    }}
  }}

  // 将后端解析出的真实资源库路径回填到设置菜单与按钮提示
  async function syncLibraryPathText() {{
    try {{
      if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.get_library_dir) return;
      const dir = await window.pywebview.api.get_library_dir();
      if (!dir) return;
      const sub = document.getElementById("libraryPathSub");
      if (sub) sub.textContent = "存储于：" + dir + " (离线无损免光驱畅播)";
      const btn = document.getElementById("openLibraryFolderBtn");
      if (btn) btn.title = "在文件管理器中打开 " + dir;
    }} catch (e) {{
      console.warn("get_library_dir error:", e);
    }}
  }}

  // 可编程复用的 Discflow 极简黑白 SVG 图标生成函数 (支持 24x24 或任意尺寸挂载)
  function createDiscflowIconMarkup(size = 24) {{
    return `
      <svg class="discflow-icon" width="${{size}}" height="${{size}}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-label="Discflow">
        <rect x="2.5" y="2.5" width="19" height="19" rx="5.2" />
        <circle cx="11.2" cy="12" r="5.6" />
        <path d="M8.4 9.5a3.8 3.8 0 0 1 2.8-1.1" stroke-width="1.6" opacity="0.45" />
        <path d="M14.0 14.5a3.8 3.8 0 0 1-2.8 1.1" stroke-width="1.6" opacity="0.45" />
        <circle cx="11.2" cy="12" r="1.6" />
        <circle cx="17.8" cy="6.8" r="1.05" fill="currentColor" stroke="none" />
        <path d="M17.8 6.8v4.8l-2.5 2.2" />
      </svg>
    `;
  }}

  let appState = {{
    drive: "",
    drives: [],
    discId: "",
    freedbId: "",
    albumTitle: "等待放入音频 CD",
    artist: "未检测到音频光盘 · 放入 CD 后将自动识别",
    year: "",
    source: "",
    coverDataUrl: "",
    tracks: [],
    currentTrackIndex: 0,
    isPlaying: false,
    elapsedSec: 0,
    durationSec: 0,
    volume: 80,
    playMode: "order", // "order" | "shuffle" | "one"
    rightPanelMode: "lyrics",
    themeMode: "dark",
    lyrics: [],
    activeLyricIndex: -1,
    lastCoverSignature: "",
    showTranslation: currentCustomPrefs.showTranslation !== false,
    autoPlayDisc: true
  }};

  function openModal(id) {{
    const el = document.getElementById(id);
    if (el) el.classList.add("open");
  }}

  function closeModal(id) {{
    const el = document.getElementById(id);
    if (el) el.classList.remove("open");
  }}

  function closeModalOnBackdrop(e, id) {{
    if (e.target && e.target.id === id) {{
      closeModal(id);
    }}
  }}

  const PLAY_MODES = ["order", "shuffle", "one"];
  const PLAY_MODE_INFO = {{
    order: {{
      title: "当前：顺序播放 (列表循环)",
      icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>`
    }},
    shuffle: {{
      title: "当前：随机播放",
      icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>`
    }},
    one: {{
      title: "当前：单曲循环",
      icon: `<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/><text x="12" y="15" font-size="9" font-weight="800" text-anchor="middle" fill="currentColor">1</text></svg>`
    }}
  }};

  function cyclePlayMode() {{
    const curIdx = PLAY_MODES.indexOf(appState.playMode);
    const nextMode = PLAY_MODES[(curIdx + 1) % PLAY_MODES.length];
    appState.playMode = nextMode;
    applyPlayModeUI(nextMode);
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.set_play_mode(nextMode);
    }}
  }}

  function applyPlayModeUI(mode) {{
    const btn = document.getElementById("playModeBtn");
    if (!btn) return;
    const info = PLAY_MODE_INFO[mode] || PLAY_MODE_INFO.order;
    btn.innerHTML = info.icon;
    btn.title = info.title;
  }}

  // 双语翻译显示开关（底部导航栏按钮 + 菜单项共用）
  // 用 <html> 上的 no-translation 类控制，歌词重新渲染后依然生效；
  // 注意：本函数在脚本初始化阶段也会被调用，此时 appState 尚未声明，
  // 因此这里只操作 DOM，不直接读写 appState，避免 TDZ 报错。
  function applyTranslationVisibility(show) {{
    document.documentElement.classList.toggle("no-translation", !show);
    document.querySelectorAll("#transToggleLabel").forEach(el => {{
      el.textContent = show ? "已开启" : "已关闭";
    }});
    const btn = document.getElementById("dockTransBtn");
    if (btn) {{
      btn.classList.toggle("is-off", !show);
      btn.setAttribute("aria-pressed", show ? "true" : "false");
      btn.title = show ? "双语翻译：已开启（点击关闭）" : "双语翻译：已关闭（点击开启）";
    }}
    document.querySelectorAll(".lyric-trans").forEach(el => {{
      el.style.display = show ? "" : "none";
    }});
  }}

  function toggleBilingualLyrics() {{
    const next = !appState.showTranslation;
    appState.showTranslation = next;
    currentCustomPrefs.showTranslation = next;
    applyTranslationVisibility(next);
    saveCustomPrefs();
  }}

  function toggleAutoPlay() {{
    appState.autoPlayDisc = !appState.autoPlayDisc;
    const label = document.getElementById("autoPlayLabel");
    if (label) label.textContent = appState.autoPlayDisc ? "已开启" : "已关闭";
    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_preferences) {{
      window.pywebview.api.save_preferences({{ autoPlay: !!appState.autoPlayDisc }}).catch(() => {{}});
    }}
  }}

  let isDraggingScrubber = false;
  let isDraggingVolume = false;
  let userScrollLockUntil = 0;

  const lyricsViewport = document.getElementById("lyricsViewport");
  lyricsViewport.addEventListener("wheel", () => {{
    userScrollLockUntil = Date.now() + 2600;
  }}, {{ passive: true }});

  function formatTime(sec) {{
    const s = Math.max(0, Math.floor(sec || 0));
    const m = Math.floor(s / 60);
    const r = s % 60;
    return `${{m}}:${{r < 10 ? "0" : ""}}${{r}}`;
  }}

  function extractPaletteFromDataUrl(dataUrl) {{
    const meshEl = document.getElementById("meshCanvas");
    const ambientEl = document.getElementById("ambientShadow");
    const imgEl = document.getElementById("artworkImg");
    const placeholderEl = document.getElementById("discPlaceholder");

    const backdropEl = document.getElementById("artBackdrop");

    if (!dataUrl) {{
      imgEl.classList.remove("loaded");
      placeholderEl.style.display = "flex";
      meshEl.classList.remove("has-cover");
      ambientEl.style.backgroundImage = "none";
      if (backdropEl) backdropEl.style.backgroundImage = "none";
      document.body.classList.remove("has-artwork");
      return;
    }}

    const tempImg = new Image();
    tempImg.onload = () => {{
      imgEl.src = dataUrl;
      imgEl.classList.add("loaded");
      placeholderEl.style.display = "none";
      ambientEl.style.backgroundImage = `url("${{dataUrl}}")`;
      if (backdropEl) backdropEl.style.backgroundImage = `url("${{dataUrl}}")`;
      document.body.classList.add("has-artwork");
      meshEl.classList.add("has-cover");

      try {{
        const canvas = document.createElement("canvas");
        const size = 48;
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(tempImg, 0, 0, size, size);
        const data = ctx.getImageData(0, 0, size, size).data;

        const buckets = [
          {{ r: 0, g: 0, b: 0, count: 0 }},
          {{ r: 0, g: 0, b: 0, count: 0 }},
          {{ r: 0, g: 0, b: 0, count: 0 }}
        ];

        for (let i = 0; i < data.length; i += 16) {{
          const r = data[i], g = data[i + 1], b = data[i + 2];
          const max = Math.max(r, g, b), min = Math.min(r, g, b);
          const luma = 0.299 * r + 0.587 * g + 0.114 * b;
          const sat = max === 0 ? 0 : (max - min) / max;

          if (luma < 22 || luma > 240) continue;
          const weight = 1 + sat * 2.5;
          const bucketIdx = (i / 16) % 3;
          buckets[bucketIdx].r += r * weight;
          buckets[bucketIdx].g += g * weight;
          buckets[bucketIdx].b += b * weight;
          buckets[bucketIdx].count += weight;
        }}

        const colors = buckets.map((bk, idx) => {{
          if (bk.count <= 0) {{
            return idx === 0 ? "rgb(68, 52, 110)" : idx === 1 ? "rgb(36, 78, 115)" : "rgb(95, 45, 82)";
          }}
          const r = Math.min(235, Math.max(36, Math.round(bk.r / bk.count)));
          const g = Math.min(235, Math.max(36, Math.round(bk.g / bk.count)));
          const b = Math.min(235, Math.max(36, Math.round(bk.b / bk.count)));
          return `rgb(${{r}}, ${{g}}, ${{b}})`;
        }});

        document.documentElement.style.setProperty("--mesh-c1", colors[0]);
        document.documentElement.style.setProperty("--mesh-c2", colors[1]);
        document.documentElement.style.setProperty("--mesh-c3", colors[2]);
        // 动态自适应彩胶盘面与沉浸背景色彩提取
        document.documentElement.style.setProperty("--ambient-primary", colors[0]);
        document.documentElement.style.setProperty("--ambient-secondary", colors[1]);
        document.documentElement.style.setProperty("--vinyl-tint-highlight", colors[1]);
        document.documentElement.style.setProperty("--vinyl-tint-base", colors[0]);
        document.documentElement.style.setProperty("--vinyl-tint-dark", colors[2]);
      }} catch (e) {{
        console.warn("Palette extraction skipped:", e);
      }}
    }};
    tempImg.src = dataUrl;
  }}

  // 逐字歌词分词：中文按单字、英文数字按整词（含尾随空格），标点单独成词
  const KARAOKE_TOKEN_RE = /[A-Za-z0-9'’\\-]+\\s*|[\\u4e00-\\u9fff]|[^\\sA-Za-z0-9\\u4e00-\\u9fff]/g;

  function tokenizeKaraokeLine(text) {{
    const tokens = [];
    KARAOKE_TOKEN_RE.lastIndex = 0;
    let m;
    while ((m = KARAOKE_TOKEN_RE.exec(text)) !== null) {{
      tokens.push(m[0]);
    }}
    return tokens;
  }}

  // 为缺少逐字时间轴的行合成按字词长度加权的均匀时间轴，
  // 保证任何有同步歌词的歌都能获得逐字卡拉 OK 效果（解决「有的歌有、有的歌没有」）
  function ensureWordTimings(lyrics) {{
    if (!Array.isArray(lyrics) || !lyrics.length) return lyrics;
    for (let i = 0; i < lyrics.length; i++) {{
      const line = lyrics[i];
      if (!line) continue;
      if (Array.isArray(line.words) && line.words.length) continue;
      const text = String(line.text || "");
      if (!text.trim()) continue;

      const start = Number(line.time) || 0;
      let end;
      if (i + 1 < lyrics.length) {{
        end = Number(lyrics[i + 1].time) || (start + 3);
      }} else {{
        end = start + Math.min(6, Math.max(2, text.length * 0.3));
      }}
      const dur = Math.max(0.6, Math.min(12, end - start));

      const tokens = tokenizeKaraokeLine(text);
      if (!tokens.length) continue;
      const weights = tokens.map(tk => Math.max(1, (tk.trim() || tk).length));
      const totalW = weights.reduce((a, b) => a + b, 0) || 1;

      let cursor = start;
      line.words = tokens.map((tk, idx) => {{
        const d = dur * (weights[idx] / totalW);
        const item = {{
          t: Number(cursor.toFixed(3)),
          d: Number(Math.max(0.05, d).toFixed(3)),
          w: tk
        }};
        cursor += d;
        return item;
      }});
    }}
    return lyrics;
  }}

  function renderLyricsList(lyrics) {{
    lyrics = ensureWordTimings(lyrics);
    lyricsViewport.innerHTML = "";
    appState.activeLyricIndex = -1;

    if (!lyrics || lyrics.length === 0) {{
      // 区分两种空：根本没放光盘（引导语），还是放了但这首歌没歌词（说明性文案）
      const hasDisc = (appState.tracks || []).length > 0;
      lyricsViewport.innerHTML = hasDisc
        ? `
        <div class="lyrics-empty">
          <div class="lyrics-empty-title">纯享 CD 原始无损音轨</div>
          <div class="lyrics-empty-sub">当前曲目为纯音乐或暂无时间轴歌词</div>
        </div>
      `
        : `
        <div class="lyrics-empty">
          <div class="lyrics-empty-title">等待放入音频 CD</div>
          <div class="lyrics-empty-sub">插入光盘后会自动读取曲目与歌词，也可以从「资料库」直接播放已归档的唱片</div>
        </div>
      `;
      return;
    }}

    lyrics.forEach((line, idx) => {{
      const div = document.createElement("div");
      div.className = "lyric-item";
      div.dataset.index = idx;

      const orig = document.createElement("div");
      orig.className = "lyric-orig";
      if (Array.isArray(line.words) && line.words.length) {{
        line.words.forEach(w => {{
          const span = document.createElement("span");
          span.className = "lyric-word";
          span.dataset.t = String(typeof w.t === "number" ? w.t : line.time);
          span.dataset.d = String(typeof w.d === "number" && w.d > 0 ? w.d : 0.25);
          span.textContent = w.w || "";
          orig.appendChild(span);
        }});
      }} else {{
        orig.textContent = line.text;
      }}
      div.appendChild(orig);

      if (line.translation) {{
        const trans = document.createElement("div");
        trans.className = "lyric-trans";
        trans.textContent = line.translation;
        div.appendChild(trans);
      }}

      div.addEventListener("click", () => {{
        userScrollLockUntil = 0;
        if (window.pywebview && window.pywebview.api) {{
          window.pywebview.api.seek_to(line.time);
        }}
      }});

      lyricsViewport.appendChild(div);
    }});
  }}

  function updateActiveLyric(elapsedSec) {{
    const lyrics = appState.lyrics;
    if (!lyrics || lyrics.length === 0) return;

    // 加 60ms 前向偏置：避免轮询时间在行边界抖动时导致高亮在两行之间来回跳
    const probe = (Number(elapsedSec) || 0) + 0.06;

    let activeIdx = 0;
    for (let i = lyrics.length - 1; i >= 0; i--) {{
      if (probe >= lyrics[i].time) {{
        activeIdx = i;
        break;
      }}
    }}

    if (activeIdx === appState.activeLyricIndex) return;
    appState.activeLyricIndex = activeIdx;

    const items = lyricsViewport.querySelectorAll(".lyric-item");
    items.forEach((el, idx) => {{
      el.classList.remove("active", "near");
      if (idx === activeIdx) {{
        el.classList.add("active");
      }} else if (Math.abs(idx - activeIdx) === 1) {{
        el.classList.add("near");
      }}
    }});

    if (Date.now() > userScrollLockUntil && items[activeIdx]) {{
      const targetEl = items[activeIdx];
      const viewportHeight = lyricsViewport.clientHeight;
      const targetTop = targetEl.offsetTop - viewportHeight * 0.36 + targetEl.clientHeight * 0.5;
      lyricsViewport.scrollTo({{
        top: Math.max(0, targetTop),
        behavior: "smooth"
      }});
    }}
  }}

  // --- 逐字歌词卡拉 OK 渲染循环（rAF 自走时钟 + 轻微纠偏，避免高亮来回跳动） ---
  let karaokeRafId = null;
  let karaokeClock = 0;      // 当前播放位置的连续估计值（秒）
  let karaokeLastTs = 0;     // 上一帧时间戳
  let karaokeTrackKey = "";  // 碟片 + 曲目标识，用于切歌时硬重置

  function syncKaraokeAnchor() {{
    const backend = Number(appState.elapsedSec) || 0;
    const key = `${{appState.discId || ""}}#${{appState.currentTrackIndex || 0}}`;

    if (key !== karaokeTrackKey) {{
      karaokeTrackKey = key;
      karaokeClock = backend;
      karaokeLastTs = 0;
      return;
    }}

    if (!appState.isPlaying) {{
      karaokeClock = backend;
      karaokeLastTs = 0;
      return;
    }}

    const drift = backend - karaokeClock;
    if (Math.abs(drift) > 0.45) {{
      // 拖动进度条 / 跳转：必须硬同步
      karaokeClock = backend;
    }} else {{
      // 常规轮询：只做 12% 的柔性纠偏，肉眼不可见，杜绝「跳来跳去」
      karaokeClock += drift * 0.12;
    }}
  }}

  function startKaraokeLoop() {{
    if (karaokeRafId) return;
    const step = () => {{
      karaokeRafId = requestAnimationFrame(step);
      const now = performance.now();
      if (appState.isPlaying) {{
        const dt = karaokeLastTs ? (now - karaokeLastTs) / 1000 : 0;
        karaokeClock += Math.min(Math.max(dt, 0), 0.25);
      }}
      karaokeLastTs = now;

      if (!appState.isPlaying) return;
      const lyrics = appState.lyrics;
      if (!lyrics || !lyrics.length) return;
      const idx = appState.activeLyricIndex;
      const line = lyrics[idx];
      if (!line || !Array.isArray(line.words) || !line.words.length) return;
      const item = lyricsViewport.querySelector(`.lyric-item[data-index="${{idx}}"]`);
      if (!item) return;
      const sec = karaokeClock;
      item.querySelectorAll(".lyric-word").forEach(wEl => {{
        const t = parseFloat(wEl.dataset.t || "0");
        const d = Math.max(0.08, parseFloat(wEl.dataset.d || "0.25"));
        const p = Math.max(0, Math.min(1, (sec - t) / d));
        wEl.style.setProperty("--wp", (p * 100).toFixed(1) + "%");
      }});
    }};
    karaokeRafId = requestAnimationFrame(step);
  }}

  function setPlayMode(mode) {{
    if (["order", "shuffle", "one"].includes(mode)) {{
      appState.playMode = mode;
      applyPlayModeUI(mode);
      if (window.pywebview && window.pywebview.api) {{
        window.pywebview.api.set_play_mode(mode);
      }}
    }}
  }}

  function applyPlayModeUI(mode) {{
    const btns = document.querySelectorAll(".mode-pill-btn");
    btns.forEach(btn => {{
      btn.classList.toggle("active", btn.dataset.mode === mode);
    }});
  }}

  function renderQueueList() {{
    const qEl = document.getElementById("queueViewport");
    const tracks = appState.tracks || [];
    document.getElementById("queueCountText").textContent =
      tracks.length > 0 ? `曲目 (${{tracks.length}})` : "曲目列表";

    const curMode = appState.playMode || "order";

    let html = `
      <div class="queue-header">
        <div style="min-width: 0; flex: 1;">
          <div class="queue-header-title">${{escapeHtml(appState.albumTitle || "Discflow 曲目队列")}}</div>
          <div class="queue-header-meta">DiscID: ${{escapeHtml((appState.discId || "--").slice(0, 10))}} · 共 ${{tracks.length}} 首</div>
        </div>
        <div class="play-mode-group" role="radiogroup" aria-label="播放模式">
          <button class="mode-pill-btn ${{curMode === 'order' ? 'active' : ''}}" data-mode="order" onclick="setPlayMode('order')" title="顺序播放 (列表循环)">
            <svg viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/></svg>
            <span>顺序</span>
          </button>
          <button class="mode-pill-btn ${{curMode === 'shuffle' ? 'active' : ''}}" data-mode="shuffle" onclick="setPlayMode('shuffle')" title="随机播放">
            <svg viewBox="0 0 24 24"><path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>
            <span>随机</span>
          </button>
          <button class="mode-pill-btn ${{curMode === 'one' ? 'active' : ''}}" data-mode="one" onclick="setPlayMode('one')" title="单曲循环">
            <svg viewBox="0 0 24 24"><path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/><path d="M12 9v6m-1-5l1-1" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
            <span>单曲</span>
          </button>
        </div>
        <button class="mode-pill-btn match-meta-btn" onclick="openMatchMetadataModal()" title="手动匹配专辑信息（封面 / 曲名 / 歌词）">
          <svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>
          <span>匹配</span>
        </button>
      </div>
    `;

    tracks.forEach((t, idx) => {{
      const isAct = idx === appState.currentTrackIndex;
      const numCol = isAct
        ? `<div class="eq-bars"><span class="eq-bar"></span><span class="eq-bar"></span><span class="eq-bar"></span></div>`
        : String(t.track_number).padStart(2, "0");

      html += `
        <div class="track-row ${{isAct ? "active" : ""}} ${{t.lyricsLocked ? "is-lyric-locked" : ""}} ${{t.lyricsMissed ? "needs-lyric" : ""}}" onclick="selectTrack(${{idx}})">
          <div class="track-num-col">${{numCol}}</div>
          <div class="track-row-info">
            <div class="track-row-name">${{escapeHtml(t.title)}}${{t.lyricsMissed ? '<span class="lyric-miss-badge">歌词待匹配</span>' : ""}}</div>
            <div class="track-row-artist">${{escapeHtml(t.artist)}}</div>
          </div>
          <div class="track-row-dur">${{formatTime(t.duration_sec)}}</div>
          <button class="track-lyric-btn" onclick="openLyricMatchModal(event, ${{idx}})" title="匹配这首歌的歌词">词</button>
        </div>
      `;
    }});

    qEl.innerHTML = html;
  }}

  // ================= 资料库与抓轨服务 =================
  let ripPollTimer = null;

  async function startRippingCurrentCd() {{
    if (!appState.discId || !appState.tracks || appState.tracks.length === 0) {{
      alertToast("请先放入实体音频 CD 识别完成后再抓轨");
      return;
    }}

    document.getElementById("ripAlbumTitle").textContent = appState.albumTitle || "音频 CD";
    document.getElementById("ripArtist").textContent = appState.artist || "未知歌手";
    document.getElementById("ripCoverImg").src = appState.coverDataUrl || "";
    document.getElementById("ripOverallBar").style.width = "0%";
    document.getElementById("ripOverallPctText").textContent = "0%";
    document.getElementById("ripTrackBar").style.width = "0%";
    document.getElementById("ripTrackPctText").textContent = "0%";
    document.getElementById("ripStatusText").textContent = "正在启动抓轨引擎...";
    document.getElementById("ripTrackTitle").textContent = "准备读取第一首音轨...";
    document.getElementById("ripDoneBtn").style.display = "none";
    document.getElementById("ripCancelBtn").style.display = "inline-flex";

    openModal("ripModal");

    if (window.pywebview && window.pywebview.api) {{
      const res = await window.pywebview.api.start_ripping();
      if (!res || !res.success) {{
        document.getElementById("ripStatusText").textContent = res ? res.message : "抓轨启动失败";
        return;
      }}
    }}

    if (ripPollTimer) clearInterval(ripPollTimer);
    ripPollTimer = setInterval(pollRippingProgress, 280);
  }}

  async function pollRippingProgress() {{
    if (!window.pywebview || !window.pywebview.api) return;
    try {{
      const p = await window.pywebview.api.get_ripping_progress();
      if (!p) return;

      document.getElementById("ripOverallBar").style.width = `${{p.overall_progress_pct || 0}}%`;
      document.getElementById("ripOverallPctText").textContent = `${{p.overall_progress_pct || 0}}%`;
      document.getElementById("ripTrackBar").style.width = `${{p.track_progress_pct || 0}}%`;
      document.getElementById("ripTrackPctText").textContent = `${{p.track_progress_pct || 0}}%`;
      document.getElementById("ripStatusText").textContent = p.status_text || "";
      document.getElementById("ripTrackTitle").textContent = p.track_title ? `正在转录: ${{p.track_title}}` : "";

      if (p.is_completed) {{
        clearInterval(ripPollTimer);
        ripPollTimer = null;
        document.getElementById("ripDoneBtn").style.display = "inline-flex";
        document.getElementById("ripCancelBtn").style.display = "none";
      }} else if (!p.is_ripping && p.error_message) {{
        clearInterval(ripPollTimer);
        ripPollTimer = null;
        document.getElementById("ripStatusText").textContent = `错误: ${{p.error_message}}`;
      }}
    }} catch (e) {{
      console.warn("pollRippingProgress error:", e);
    }}
  }}

  function cancelRippingAndClose() {{
    if (ripPollTimer) {{
      clearInterval(ripPollTimer);
      ripPollTimer = null;
    }}
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.cancel_ripping();
    }}
    closeModal("ripModal");
  }}

  function finishRippingAndShowLibrary() {{
    closeModal("ripModal");
    switchMainView("library");
    renderLibraryCards();
  }}

  // ================= 谜底黑胶 (MD Vinyl) 资料库交互 =================
  let cachedLocalAlbums = [];

  function openSearchModal() {{
    closeModal("settingsModal");
    closeModal("customSettingsModal");
    closeModal("aboutModal");
    openModal("searchModal");
    const input = document.getElementById("modalSearchInput");
    if (input) {{
      input.value = "";
      setTimeout(() => input.focus(), 120);
    }}
    const clearBtn = document.getElementById("modalSearchClearBtn");
    if (clearBtn) clearBtn.style.display = "none";
    const resEl = document.getElementById("modalSearchResults");
    if (resEl) {{
      resEl.innerHTML = `
        <div style="text-align: center; color: var(--text-tertiary); padding: 36px 12px; font-size: 13px;">
          输入专辑、歌名或歌手关键词进行搜索
        </div>
      `;
    }}
    const countEl = document.getElementById("modalSearchCountText");
    if (countEl) countEl.textContent = "准备就绪";

    if (window.pywebview && window.pywebview.api && window.pywebview.api.list_local_albums) {{
      window.pywebview.api.list_local_albums().then(albs => {{
        cachedLocalAlbums = albs || [];
      }}).catch(() => {{}});
    }}
  }}

  function onModalSearchInput(val) {{
    const clearBtn = document.getElementById("modalSearchClearBtn");
    if (clearBtn) {{
      clearBtn.style.display = val ? "inline-block" : "none";
    }}
    executeModalSearch();
  }}

  function clearModalSearch() {{
    const input = document.getElementById("modalSearchInput");
    if (input) {{
      input.value = "";
      input.focus();
    }}
    const clearBtn = document.getElementById("modalSearchClearBtn");
    if (clearBtn) clearBtn.style.display = "none";
    executeModalSearch();
  }}

  async function executeModalSearch() {{
    const input = document.getElementById("modalSearchInput");
    const term = (input ? input.value : "").trim().toLowerCase();
    const resEl = document.getElementById("modalSearchResults");
    const countEl = document.getElementById("modalSearchCountText");
    if (!resEl) return;

    if (!term) {{
      resEl.innerHTML = `
        <div style="text-align: center; color: var(--text-tertiary); padding: 36px 12px; font-size: 13px;">
          输入专辑、歌名或歌手关键词进行搜索
        </div>
      `;
      if (countEl) countEl.textContent = "准备就绪";
      return;
    }}

    if (!cachedLocalAlbums || cachedLocalAlbums.length === 0) {{
      if (window.pywebview && window.pywebview.api && window.pywebview.api.list_local_albums) {{
        try {{
          cachedLocalAlbums = (await window.pywebview.api.list_local_albums()) || [];
        }} catch(e) {{}}
      }}
    }}

    const matched = [];
    (cachedLocalAlbums || []).forEach(alb => {{
      const matchAlbum = (alb.title || "").toLowerCase().includes(term) || (alb.artist || "").toLowerCase().includes(term);
      const matchedTracks = (alb.tracks || []).filter(t => (t.title || "").toLowerCase().includes(term) || (t.artist || "").toLowerCase().includes(term));
      if (matchAlbum || matchedTracks.length > 0) {{
        matched.push({{
          album: alb,
          matchedTracks: matchedTracks,
          matchedAlbum: matchAlbum
        }});
      }}
    }});

    if (countEl) {{
      countEl.textContent = `找到 ${{matched.length}} 张匹配唱片`;
    }}

    if (matched.length === 0) {{
      resEl.innerHTML = `
        <div style="text-align: center; color: var(--text-tertiary); padding: 36px 12px; font-size: 13px;">
          未找到与 “<span style="color:var(--text-primary); font-weight:600;">${{escapeHtml(term)}}</span>” 相关的专辑或歌曲
        </div>
      `;
      return;
    }}

    let html = "";
    matched.forEach(item => {{
      const alb = item.album;
      const coverSrc = alb.cover_data_url || "";
      const discIdSafe = alb.disc_id.replace(/'/g, "\\'");
      const trackHighlight = item.matchedTracks.length > 0
        ? `· 命中歌曲: ${{item.matchedTracks.map(t => escapeHtml(t.title)).join(", ")}}`
        : "";

      html += `
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 8px 12px; border-radius: 10px; background: rgba(255, 255, 255, 0.04); border: 1px solid var(--border-hairline);">
          <div style="display: flex; align-items: center; gap: 12px; min-width: 0; flex: 1;">
            ${{coverSrc
              ? `<img src="${{coverSrc}}" style="width: 44px; height: 44px; border-radius: 6px; object-fit: cover; flex-shrink: 0;" />`
              : `<div style="width: 44px; height: 44px; border-radius: 6px; background: #222; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">💿</div>`
            }}
            <div style="min-width: 0; flex: 1;">
              <div style="font-weight: 600; font-size: 13.5px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                ${{escapeHtml(alb.title)}}
              </div>
              <div style="font-size: 12px; color: var(--text-secondary); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                ${{escapeHtml(alb.artist)}} <span style="opacity: 0.75; font-size: 11px;">${{trackHighlight}}</span>
              </div>
            </div>
          </div>
          <div style="display: flex; gap: 6px; margin-left: 10px; flex-shrink: 0;">
            <button class="pill-btn active" onclick="playLocalAlbumFromSearch('${{discIdSafe}}')" style="padding: 3px 12px; font-size: 12px;">▶ 播放</button>
          </div>
        </div>
      `;
    }});
    resEl.innerHTML = html;
  }}

  async function playLocalAlbumFromSearch(discId) {{
    closeModal("searchModal");
    await playLocalAlbum(discId);
  }}

  function jumpToLibraryFromSearch() {{
    const input = document.getElementById("modalSearchInput");
    const query = input ? input.value : "";
    closeModal("searchModal");
    switchMainView("library");
    const libInput = document.getElementById("librarySearchInput");
    if (libInput) {{
      libInput.value = query;
      filterLibraryCards(query);
      libInput.focus();
    }}
  }}

  let isLibrarySearchOpen = false;

  function submitLibrarySearch() {{
    const input = document.getElementById("librarySearchInput");
    if (input) {{
      filterLibraryCards(input.value);
      input.focus();
    }}
  }}

  function clearLibrarySearch() {{
    const input = document.getElementById("librarySearchInput");
    if (input) {{
      input.value = "";
      input.focus();
      filterLibraryCards("");
    }}
  }}

  function filterLibraryCards(keyword) {{
    const term = (keyword || "").trim().toLowerCase();
    const cards = document.querySelectorAll("#libraryGrid .vinyl-card");
    let visibleCount = 0;
    cards.forEach(card => {{
      const title = card.querySelector(".vinyl-info-title")?.textContent?.toLowerCase() || "";
      const artist = card.querySelector(".vinyl-info-artist")?.textContent?.toLowerCase() || "";
      const tracks = (card.getAttribute("data-tracks") || "").toLowerCase();
      // 支持按专辑名、歌手名、歌曲名（歌名）全维度搜索
      if (!term || title.includes(term) || artist.includes(term) || tracks.includes(term)) {{
        card.style.display = "";
        visibleCount++;
      }} else {{
        card.style.display = "none";
      }}
    }});

    let emptyPrompt = document.getElementById("librarySearchEmptyPrompt");
    if (cards.length > 0 && visibleCount === 0 && term) {{
      if (!emptyPrompt) {{
        emptyPrompt = document.createElement("div");
        emptyPrompt.id = "librarySearchEmptyPrompt";
        emptyPrompt.style.cssText = "grid-column: 1/-1; text-align: center; color: var(--text-tertiary); padding: 50px 20px; font-size: 14px;";
        const grid = document.getElementById("libraryGrid");
        if (grid) grid.appendChild(emptyPrompt);
      }}
      emptyPrompt.innerHTML = `未找到与 “<span style="color:var(--text-primary); font-weight:600;">${{escapeHtml(term)}}</span>” 相关的唱片或歌曲<br><button class="pill-btn" style="margin-top:14px;" onclick="clearLibrarySearch()">清空搜索</button>`;
      emptyPrompt.style.display = "block";
    }} else if (emptyPrompt) {{
      emptyPrompt.style.display = "none";
    }}
  }}

  async function renderLibraryCards() {{
    const grid = document.getElementById("libraryGrid");
    grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: var(--text-tertiary); padding: 40px;">正在加载黑胶唱片...</div>`;

    if (!window.pywebview || !window.pywebview.api) return;
    try {{
      const albums = await window.pywebview.api.list_local_albums();
      if (!albums || albums.length === 0) {{
        grid.innerHTML = `
          <div class="library-empty">
            <div style="width: 72px; height: 72px; opacity: 0.35; margin-bottom: 8px;">
              ${{createDiscflowIconMarkup(72)}}
            </div>
            <div class="library-empty-title">资源库为空</div>
            <div class="library-empty-sub">
              放入音频 CD 后，点击顶部菜单中的「💿 抓轨当前 CD 到本地资料库」，
              即可将整张唱片逐扇区无损转录归档至此处，以后无需插入光盘即可即点即播。
            </div>
          </div>
        `;
        return;
      }}

      let html = "";
      albums.forEach(alb => {{
        const coverSrc = alb.cover_data_url || "";
        const durFormatted = formatTime(alb.total_duration_sec);
        const trackCount = alb.tracks ? alb.tracks.length : 0;
        const discIdSafe = alb.disc_id.replace(/'/g, "\\'");
        const trackNamesSafe = (alb.tracks || []).map(t => (t.title || '').toLowerCase()).join(' ');

        html += `
          <div class="vinyl-card" data-tracks="${{escapeHtml(trackNamesSafe)}}" onclick="playLocalAlbumWithAnimation(this, '${{discIdSafe}}')" title="点击免光驱无损播放《${{escapeHtml(alb.title)}}》">
            <div class="vinyl-stage">
              <!-- 纸质唱片封套 -->
              <div class="vinyl-sleeve">
                ${{coverSrc ? `<img src="${{coverSrc}}" alt="Cover" />` : createDiscflowIconMarkup(140)}}
              </div>
              <!-- 抽出的实体黑胶唱片 (带沟槽纹理与中心圆标) -->
              <div class="vinyl-disc">
                <div class="vinyl-label">
                  ${{coverSrc ? `<img src="${{coverSrc}}" alt="Label" />` : ""}}
                </div>
              </div>
            </div>
            <div class="vinyl-info-title">${{escapeHtml(alb.title)}}</div>
            <div class="vinyl-info-artist">${{escapeHtml(alb.artist)}}</div>
            <div class="vinyl-info-footer">
              <span>${{trackCount}} 首音轨 · ${{durFormatted}}</span>
              <button class="vinyl-delete-btn" onclick="pickAlbumCover(event, '${{discIdSafe}}')" title="为这张唱片更换封面">🖼️ 封面</button>
              <button class="vinyl-delete-btn" onclick="deleteLocalAlbum(event, '${{discIdSafe}}')" title="从资料库移除唱片">🗑️ 删除</button>
            </div>
          </div>
        `;
      }});
      grid.innerHTML = html;
    }} catch (e) {{
      console.error("renderLibraryCards failed:", e);
      grid.innerHTML = `<div style="grid-column: 1/-1; text-align: center; color: #FF453A; padding: 40px;">加载资料库失败: ${{e}}</div>`;
    }}
  }}

  async function playLocalAlbumWithAnimation(cardEl, discId) {{
    if (cardEl) {{
      cardEl.classList.add("is-pulling-out");
    }}
    let playPromise = null;
    if (window.pywebview && window.pywebview.api) {{
      playPromise = window.pywebview.api.play_local_album(discId);
    }}

    // 等待 460ms 享受拟物抽盘微交互
    await new Promise(r => setTimeout(r, 460));

    if (playPromise) {{
      try {{
        await playPromise;
      }} catch (e) {{
        console.warn("play_local_album error:", e);
      }}
    }}

    setTimeout(() => {{
      if (cardEl) cardEl.classList.remove("is-pulling-out");
      triggerImmediatePoll();
      // 路由修复：默认平滑导航至常规双栏播放界面，并确保关闭沉浸全屏
      if (typeof isImmersiveMode !== "undefined" && isImmersiveMode) {{
        toggleImmersiveVinylMode(false);
      }}
      switchMainView("cd");
      // 从资料库进入时默认展开曲目栏，方便直接查看曲目并手动匹配专辑信息
      applyRightPanelMode("queue");
      renderQueueList();
    }}, 280);
  }}

  async function playLocalAlbum(discId) {{
    if (!window.pywebview || !window.pywebview.api) return;
    try {{
      const ok = await window.pywebview.api.play_local_album(discId);
      if (ok) {{
        triggerImmediatePoll();
        if (typeof isImmersiveMode !== "undefined" && isImmersiveMode) {{
          toggleImmersiveVinylMode(false);
        }}
        switchMainView("cd");
      }}
    }} catch (e) {{
      console.warn("playLocalAlbum error:", e);
    }}
  }}

  async function deleteLocalAlbum(e, discId) {{
    e.stopPropagation();
    if (!confirm("确定要从资源库删除这张唱片吗？\\n\\n专辑音频文件（WAV）、封面与元数据将被一并物理删除，且不可恢复。")) {{
      return;
    }}
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.delete_local_album) return;
    try {{
      const res = await window.pywebview.api.delete_local_album(discId);
      if (res && res.ok) {{
        appState.currentTrackIndex = 0;
        await renderLibraryCards();
        return;
      }}
      const failedList = (res && res.failed) ? res.failed : [];
      let detail = "";
      if (failedList.length) {{
        detail = "\\n\\n以下文件仍被占用，未能删除：\\n" + failedList.slice(0, 5).join("\\n");
      }} else if (res && res.reason) {{
        detail = "\\n\\n" + res.reason;
      }}
      alert("删除失败：唱片文件未能完全移除。" + detail + "\\n\\n请先停止播放后重试。");
      await renderLibraryCards();
    }} catch (err) {{
      console.error("delete_local_album error:", err);
      alert("删除失败：" + err);
    }}
  }}

  // --- 手动更换专辑封面（自动匹配失败时的兜底） ---
  let pendingCoverDiscId = null;

  function pickAlbumCover(e, discId) {{
    if (e) e.stopPropagation();
    pendingCoverDiscId = discId;
    const input = document.getElementById("coverFileInput");
    if (!input) return;
    input.value = "";
    input.click();
  }}

  function handleCoverFileSelected(ev) {{
    const file = ev.target.files && ev.target.files[0];
    if (!file || !pendingCoverDiscId) return;
    const discId = pendingCoverDiscId;
    pendingCoverDiscId = null;

    const reader = new FileReader();
    reader.onload = async () => {{
      try {{
        if (!window.pywebview || !window.pywebview.api) return;
        const ok = await window.pywebview.api.update_album_cover(discId, reader.result);
        if (ok) {{
          await renderLibraryCards();
        }} else {{
          alert("封面更新失败：唱片元数据缺失或图片不可写。");
        }}
      }} catch (err) {{
        console.error("update_album_cover error:", err);
        alert("封面更新失败：" + err);
      }}
    }};
    reader.readAsDataURL(file);
  }}

  // --- 液态玻璃 (Fluid Morphing) 播放列表与控制面板状态管理 ---
  let isFluidPlaylistOpen = false;

  function toggleFluidPlaylistMorph(event) {{
    if (event) event.stopPropagation();
    isFluidPlaylistOpen = !isFluidPlaylistOpen;
    const overlay = document.getElementById("mdFluidMorphOverlay");
    if (!overlay) return;

    if (isFluidPlaylistOpen) {{
      overlay.classList.add("is-open");
      renderFluidTrackList();
      syncFluidControls();
    }} else {{
      overlay.classList.remove("is-open");
    }}
  }}

  function closeFluidPlaylistMorph(event) {{
    if (event) event.stopPropagation();
    isFluidPlaylistOpen = false;
    const overlay = document.getElementById("mdFluidMorphOverlay");
    if (overlay) overlay.classList.remove("is-open");
  }}

  function renderFluidTrackList() {{
    const container = document.getElementById("fluidTrackList");
    if (!container) return;

    if (!appState.tracks || appState.tracks.length === 0) {{
      container.innerHTML = `<div style="text-align: center; color: var(--text-tertiary); padding: 32px 0; font-size: 13px;">暂无当前专辑曲目</div>`;
      return;
    }}

    let html = "";
    appState.tracks.forEach((tr, idx) => {{
      const isCurrent = idx === appState.currentTrackIndex;
      const title = tr.title || `音轨 ${{tr.track_number}}`;
      const dur = formatTime(tr.duration_sec);
      html += `
        <div class="fluid-track-row ${{isCurrent ? 'is-current' : ''}}" onclick="playTrackIndex(${{idx}})" title="播放 ${{escapeHtml(title)}}">
          <div class="fluid-track-left">
            <span class="fluid-track-num">${{isCurrent ? '▶' : (idx + 1)}}</span>
            <span class="fluid-track-title">${{escapeHtml(title)}}</span>
          </div>
          <span class="fluid-track-dur">${{dur}}</span>
        </div>
      `;
    }});
    container.innerHTML = html;
  }}

  function syncFluidControls() {{
    // 1. 同步音量滑块与指示条
    const fill = document.getElementById("fluidVolFill");
    if (fill) fill.style.width = `${{currentVolumePercent}}%`;

    // 2. 同步三态播放模式高亮
    const btnAll = document.getElementById("fluidModeRepeatAll");
    const btnShuff = document.getElementById("fluidModeShuffle");
    const btnOne = document.getElementById("fluidModeRepeatOne");
    if (btnAll && btnShuff && btnOne) {{
      btnAll.classList.toggle("active", currentPlayMode === "repeat_all" || currentPlayMode === "order");
      btnShuff.classList.toggle("active", currentPlayMode === "shuffle");
      btnOne.classList.toggle("active", currentPlayMode === "repeat_one" || currentPlayMode === "one");
    }}
  }}

  function setFluidPlayMode(mode) {{
    currentPlayMode = mode;
    if (window.pywebview && window.pywebview.api && window.pywebview.api.set_play_mode) {{
      window.pywebview.api.set_play_mode(mode).then(() => triggerImmediatePoll()).catch(() => {{}});
    }}
    syncFluidControls();
    updateModeIconUi();
  }}

  // 绑定液态面板内的音量调节
  const fluidVolHit = document.getElementById("fluidVolHit");
  if (fluidVolHit) {{
    let isDraggingFluidVol = false;
    function handleFluidVolPointer(e) {{
      const rect = fluidVolHit.getBoundingClientRect();
      const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
      const pct = Math.round((x / rect.width) * 100);
      setVolumePercent(pct);
      syncFluidControls();
    }}
    fluidVolHit.addEventListener("pointerdown", (e) => {{
      isDraggingFluidVol = true;
      fluidVolHit.setPointerCapture(e.pointerId);
      handleFluidVolPointer(e);
    }});
    fluidVolHit.addEventListener("pointermove", (e) => {{
      if (isDraggingFluidVol) handleFluidVolPointer(e);
    }});
    fluidVolHit.addEventListener("pointerup", () => {{
      isDraggingFluidVol = false;
    }});
  }}

  // ================= 谜底黑胶 (MD Vinyl) 沉浸播放模式交互 =================
  let isImmersiveMode = false;
  let isMdLyricsVisible = false;

  function toggleImmersiveVinylMode(forceOpen = null) {{
    const stage = document.getElementById("immersiveVinylStage");
    if (forceOpen === true) isImmersiveMode = true;
    else if (forceOpen === false) isImmersiveMode = false;
    else isImmersiveMode = !isImmersiveMode;

    stage.classList.toggle("open", isImmersiveMode);
    if (isImmersiveMode) {{
      syncImmersiveState();
    }}
  }}

  function toggleMdLyricsSheet(forceState = null) {{
    const sheet = document.getElementById("mdLyricsSheet");
    if (!sheet) return;
    if (forceState === true) isMdLyricsVisible = true;
    else if (forceState === false) isMdLyricsVisible = false;
    else isMdLyricsVisible = !isMdLyricsVisible;
    sheet.classList.toggle("visible", isMdLyricsVisible);
  }}

  function cyclePlayMode() {{
    let nextMode = "order";
    if (appState.playMode === "order") nextMode = "shuffle";
    else if (appState.playMode === "shuffle") nextMode = "one";
    else nextMode = "order";
    setPlayMode(nextMode);
  }}

    // 全局键盘快捷键与多媒体键全面支持 (Stage 4)
  window.addEventListener("keydown", (e) => {{
    if ((e.ctrlKey || e.metaKey) && (e.key === "f" || e.key === "F")) {{
      e.preventDefault();
      openSearchModal();
      return;
    }}
    // 忽略在输入框或可编辑区域内的按键
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable)) {{
      return;
    }}

    if (e.key === "Escape") {{
      if (isImmersiveMode) {{
        toggleImmersiveVinylMode(false);
      }} else if (currentMainView === "library") {{
        // 资源库页按 Esc 直接回到播放页
        switchMainView("cd");
      }}
    }} else if (e.key === " " || e.code === "Space") {{
      e.preventDefault();
      togglePlayPause();
    }} else if (e.key === "ArrowRight" || e.code === "ArrowRight") {{
      // 快进 5 秒
      e.preventDefault();
      const targetSec = Math.min((appState.durationSec || 0), (appState.elapsedSec || 0) + 5);
      if (window.pywebview && window.pywebview.api) {{
        window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {{}});
      }}
    }} else if (e.key === "ArrowLeft" || e.code === "ArrowLeft") {{
      // 快退 5 秒
      e.preventDefault();
      const targetSec = Math.max(0, (appState.elapsedSec || 0) - 5);
      if (window.pywebview && window.pywebview.api) {{
        window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {{}});
      }}
    }} else if (e.key === "ArrowUp" || e.key === "ArrowDown") {{
      e.preventDefault();
      const cur = typeof appState.volume === "number" ? appState.volume : 80;
      setVolumePercent(cur + (e.key === "ArrowUp" ? 5 : -5), true);
    }} else if (e.key === "MediaTrackNext") {{
      nextTrack();
    }} else if (e.key === "MediaTrackPrevious") {{
      prevTrack();
    }} else if (e.key === "MediaPlayPause") {{
      togglePlayPause();
    }}
  }});

  function syncImmersiveState() {{
    if (!isImmersiveMode) return;
    const curTrack = (appState.tracks || [])[appState.currentTrackIndex];
    const trackTitle = curTrack ? curTrack.title : (appState.albumTitle || "等待放入音频 CD");
    const artistName = (curTrack && curTrack.artist) || appState.artist || "--";
    const albumName = appState.albumTitle ? (appState.year ? `${{appState.albumTitle}} (${{appState.year}})` : appState.albumTitle) : "--";

    // 封套大图
    const covImg = document.getElementById("immersiveCoverImg");
    if (covImg) {{
      if (appState.coverDataUrl) {{
        covImg.src = appState.coverDataUrl;
        covImg.style.display = "block";
      }} else {{
        covImg.style.display = "none";
      }}
    }}

    // 黑胶中心圆标
    const labelImg = document.getElementById("immersiveLabelImg");
    if (labelImg) {{
      if (appState.coverDataUrl) {{
        labelImg.src = appState.coverDataUrl;
        labelImg.style.display = "block";
      }} else {{
        labelImg.style.display = "none";
      }}
    }}

    // 贴纸文字
    const lblTitle = document.getElementById("mdLabelTrackTitle");
    if (lblTitle) lblTitle.textContent = trackTitle;
    const lblArtist = document.getElementById("mdLabelArtist");
    if (lblArtist) lblArtist.textContent = artistName;

    // 底部胶囊微缩图
    const miniLbl = document.getElementById("mdDockMiniLabel");
    if (miniLbl) {{
      if (appState.coverDataUrl) {{
        miniLbl.src = appState.coverDataUrl;
        miniLbl.style.display = "block";
      }} else {{
        miniLbl.style.display = "none";
      }}
    }}

    // 底部左角资料库缩略图
    const thumbImg = document.getElementById("mdDockThumbImg");
    if (thumbImg) {{
      if (appState.coverDataUrl) {{
        thumbImg.src = appState.coverDataUrl;
        thumbImg.style.display = "block";
      }} else {{
        thumbImg.style.display = "none";
      }}
    }}

    // 底部胶囊曲目信息
    const titleEl = document.getElementById("immersiveTrackTitle");
    if (titleEl) titleEl.textContent = trackTitle;
    const artistEl = document.getElementById("immersiveArtistName");
    if (artistEl) artistEl.textContent = artistName;

    // 底部胶囊播放/暂停图标
    const playIcon = document.getElementById("mdDockPlayIcon");
    const pauseIcon = document.getElementById("mdDockPauseIcon");
    if (playIcon && pauseIcon) {{
      playIcon.style.display = appState.isPlaying ? "none" : "block";
      pauseIcon.style.display = appState.isPlaying ? "block" : "none";
    }}

    // 底部胶囊播放模式图标
    const modeBtn = document.getElementById("mdDockModeBtn");
    if (modeBtn) {{
      let iconSvg = '<path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z"/>';
      if (appState.playMode === "shuffle") {{
        iconSvg = '<path d="M10.59 9.17L5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41l-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/>';
      }} else if (appState.playMode === "one") {{
        iconSvg = '<path d="M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z"/>';
      }}
      modeBtn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">${{iconSvg}}</svg>`;
    }}

    // 同步当前单句高光歌词
    const lyrics = appState.lyrics || [];
    const actLine = document.getElementById("mdLyricsActiveLine");
    const actSub = document.getElementById("mdLyricsActiveSub");
    if (actLine && actSub) {{
      if (lyrics.length > 0 && appState.activeLyricIndex >= 0 && appState.activeLyricIndex < lyrics.length) {{
        const curL = lyrics[appState.activeLyricIndex];
        actLine.textContent = curL.text || "";
        actSub.textContent = (appState.showTranslation && curL.translation) ? curL.translation : "";
      }} else {{
        actLine.textContent = "纯享 CD 无损音轨";
        actSub.textContent = "";
      }}
    }}
  }}

  function alertToast(msg) {{
    const dot = document.getElementById("statusText");
    if (dot) {{
      const old = dot.textContent;
      dot.textContent = msg;
      setTimeout(() => {{ dot.textContent = old; }}, 3200);
    }}
  }}

  function escapeHtml(str) {{
    return String(str || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }}

  let localMetaVer = -1;
  let localLyricsVer = -1;
  let pollInFlight = false;

  window.applyBackendState = function(payload) {{
    if (!payload) return;
    const prevTrackIdx = appState.currentTrackIndex;
    const prevDiscId = appState.discId;
    const hasMetaUpdate = ("tracks" in payload) || ("albumTitle" in payload);
    const hasLyricsUpdate = ("lyrics" in payload);

    Object.assign(appState, payload);

    document.body.classList.toggle("is-playing", !!appState.isPlaying);
    document.getElementById("playIcon").style.display = appState.isPlaying ? "none" : "block";
    document.getElementById("pauseIcon").style.display = appState.isPlaying ? "block" : "none";

    const dot = document.getElementById("statusDot");
    if (dot) {{
      dot.classList.toggle("loading", !!appState.isLoading);
      dot.classList.toggle("idle", !appState.isLoading && !(appState.discId && (appState.tracks || []).length));
      dot.title = appState.statusText || "光驱状态指示灯";
    }}
    const statusTextEl = document.getElementById("statusText");
    if (statusTextEl) statusTextEl.textContent = appState.statusText || "";

    if ("drives" in payload) {{
      const sel = document.getElementById("driveSelect");
      const curOptions = Array.from(sel.options).map(o => o.value);
      if (JSON.stringify(curOptions) !== JSON.stringify(appState.drives || [])) {{
        sel.innerHTML = "";
        (appState.drives || []).forEach(d => {{
          const opt = document.createElement("option");
          opt.value = d;
          opt.textContent = d;
          sel.appendChild(opt);
        }});
      }}
      if (appState.drive && sel.value !== appState.drive) {{
        sel.value = appState.drive;
      }}
    }}

    if (hasMetaUpdate || prevTrackIdx !== appState.currentTrackIndex) {{
      const curTrack = (appState.tracks || [])[appState.currentTrackIndex];
      document.getElementById("trackTitle").textContent =
        curTrack ? curTrack.title : (appState.albumTitle || "等待放入音频 CD");

      const subParts = [];
      if (curTrack && curTrack.artist) subParts.push(curTrack.artist);
      else if (appState.artist) subParts.push(appState.artist);
      if (appState.albumTitle && appState.albumTitle !== "等待放入音频 CD") {{
        subParts.push(appState.year ? `${{appState.albumTitle}} (${{appState.year}})` : appState.albumTitle);
      }}
      document.getElementById("trackSubtitle").textContent =
        subParts.join(" — ") || "{APP_NAME} · 插入外置光驱并放入音频 CD 开始播放";
    }}

    if ("coverDataUrl" in payload && appState.coverDataUrl !== appState.lastCoverSignature) {{
      appState.lastCoverSignature = appState.coverDataUrl;
      extractPaletteFromDataUrl(appState.coverDataUrl);
    }}

    if (!isDraggingScrubber) {{
      const dur = Math.max(0.01, appState.durationSec || 0);
      const pct = appState.durationSec > 0 ? Math.min(100, Math.max(0, (appState.elapsedSec / dur) * 100)) : 0;
      document.getElementById("scrubberFill").style.width = `${{pct}}%`;
      document.getElementById("elapsedTime").textContent = formatTime(appState.elapsedSec);
      const rem = Math.max(0, (appState.durationSec || 0) - (appState.elapsedSec || 0));
      document.getElementById("remainingTime").textContent = `-${{formatTime(rem)}}`;
    }}

    if (hasLyricsUpdate) {{
      // 判重签名只取稳定字段并带上曲目索引：
      // ensureWordTimings 会给行对象挂 words 字段，用 JSON.stringify 判重会因自身副作用而误判「未变化」，
      // 导致歌词已下发却不重绘（表现为首曲没歌词、切走再切回才出现）。
      // 另外必须带上 discId：换碟时歌词可能都是空的，只靠曲目索引无法触发重绘，
      // 空状态文案就会一直停留在「等待放入音频 CD」。
      const sig = `${{appState.discId || ""}}#${{appState.currentTrackIndex || 0}}#${{(appState.tracks || []).length}}#` +
        (appState.lyrics || [])
          .map(l => `${{l.time}}|${{l.text}}|${{l.translation || ""}}`)
          .join("\\n");
      if (window._lastLyricsSig !== sig) {{
        window._lastLyricsSig = sig;
        renderLyricsList(appState.lyrics || []);
      }}
    }}
    updateActiveLyric(appState.elapsedSec || 0);

    if (hasMetaUpdate || prevTrackIdx !== appState.currentTrackIndex || prevDiscId !== appState.discId) {{
      renderQueueList();
    }}

    // 同步底部常驻 Dock 栏状态
    const dockArt = document.getElementById("dockArtworkImg");
    if (dockArt) {{
      const nextSrc = appState.coverDataUrl || "";
      if (dockArt.dataset.coverSrc !== nextSrc) {{
        dockArt.dataset.coverSrc = nextSrc;
        dockArt.src = nextSrc || "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
      }}
      dockArt.classList.toggle("is-empty", !nextSrc);
      dockArt.style.display = "block";
    }}
    const dockTitle = document.getElementById("dockTrackTitle");
    if (dockTitle) {{
      const curTrk = (appState.tracks || [])[appState.currentTrackIndex];
      dockTitle.textContent = curTrk ? curTrk.title : (appState.albumTitle || "等待放入音频 CD");
    }}
    const dockArtst = document.getElementById("dockArtist");
    if (dockArtst) {{
      const curTrk = (appState.tracks || [])[appState.currentTrackIndex];
      dockArtst.textContent = (curTrk && curTrk.artist) || appState.artist || "--";
    }}
    const dockPlay = document.getElementById("dockPlayIcon");
    const dockPause = document.getElementById("dockPauseIcon");
    if (dockPlay && dockPause) {{
      dockPlay.style.display = appState.isPlaying ? "none" : "block";
      dockPause.style.display = appState.isPlaying ? "block" : "none";
    }}
    if (!isDraggingScrubber) {{
      const dur = Math.max(0.01, appState.durationSec || 0);
      const pct = appState.durationSec > 0 ? Math.min(100, Math.max(0, (appState.elapsedSec / dur) * 100)) : 0;
      const dFill = document.getElementById("dockScrubberFill");
      if (dFill) dFill.style.width = `${{pct}}%`;
      const dElap = document.getElementById("dockElapsedTime");
      if (dElap) dElap.textContent = formatTime(appState.elapsedSec);
      const dRem = document.getElementById("dockRemainingTime");
      if (dRem) dRem.textContent = `-${{formatTime(Math.max(0, (appState.durationSec || 0) - (appState.elapsedSec || 0)))}}`;
    }}
    // 音量：仅在启动首次从后端同步（恢复上次音量），之后以用户操作为准；拖动中不覆盖
    if (!volumeSyncedFromBackend && typeof payload.volume === "number") {{
      volumeSyncedFromBackend = true;
      appState.volume = payload.volume;
      if (payload.volume > 0) lastNonZeroVolume = payload.volume;
    }}
    if (!isDraggingDockVol) renderVolumeUI(appState.volume);

    if ("playMode" in payload && payload.playMode !== appState.playMode) {{
      appState.playMode = payload.playMode;
      applyPlayModeUI(appState.playMode);
    }}

    syncImmersiveState();
  }};

  async function pollBackendState() {{
    if (pollInFlight || !window.pywebview || !window.pywebview.api) return;
    pollInFlight = true;
    try {{
      const res = await window.pywebview.api.poll_state(
        localMetaVer,
        localLyricsVer,
        (typeof appState.currentTrackIndex === "number" ? appState.currentTrackIndex : -1)
      );
      if (res) {{
        if (typeof res.metaVersion === "number") localMetaVer = res.metaVersion;
        if (typeof res.lyricsVersion === "number") localLyricsVer = res.lyricsVersion;
        window.applyBackendState(res);
        syncKaraokeAnchor();
      }}
    }} catch (err) {{
      // 忽略窗口关闭瞬间的桥接异常
    }} finally {{
      pollInFlight = false;
    }}
  }}

  function triggerImmediatePoll() {{
    setTimeout(pollBackendState, 16);
  }}

  const scrubberHit = document.getElementById("scrubberHit");
  function handleScrubberPointer(e, commit) {{
    const rect = scrubberHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetSec = ratio * (appState.durationSec || 0);
    document.getElementById("scrubberFill").style.width = `${{ratio * 100}}%`;
    document.getElementById("elapsedTime").textContent = formatTime(targetSec);
    document.getElementById("remainingTime").textContent = `-${{formatTime((appState.durationSec || 0) - targetSec)}}`;
    if (commit && window.pywebview && window.pywebview.api) {{
      window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {{}});
    }}
  }}

  scrubberHit.addEventListener("pointerdown", (e) => {{
    isDraggingScrubber = true;
    scrubberHit.classList.add("dragging");
    scrubberHit.setPointerCapture(e.pointerId);
    handleScrubberPointer(e, false);
  }});

  scrubberHit.addEventListener("pointermove", (e) => {{
    if (isDraggingScrubber) handleScrubberPointer(e, false);
  }});

  scrubberHit.addEventListener("pointerup", (e) => {{
    if (!isDraggingScrubber) return;
    isDraggingScrubber = false;
    scrubberHit.classList.remove("dragging");
    handleScrubberPointer(e, true);
  }});

  const immScrubberHit = document.getElementById("immScrubberHit");
  if (immScrubberHit) {{
    immScrubberHit.addEventListener("pointerdown", (e) => {{
      isDraggingScrubber = true;
      immScrubberHit.setPointerCapture(e.pointerId);
      handleImmScrubberPointer(e, false);
    }});
    immScrubberHit.addEventListener("pointermove", (e) => {{
      if (isDraggingScrubber) handleImmScrubberPointer(e, false);
    }});
    immScrubberHit.addEventListener("pointerup", (e) => {{
      if (!isDraggingScrubber) return;
      isDraggingScrubber = false;
      handleImmScrubberPointer(e, true);
    }});
  }}

  function handleImmScrubberPointer(e, commit) {{
    if (!immScrubberHit) return;
    const rect = immScrubberHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetSec = ratio * (appState.durationSec || 0);
    const fillEl = document.getElementById("immScrubberFill");
    if (fillEl) fillEl.style.width = `${{ratio * 100}}%`;
    const elTime = document.getElementById("immElapsedTime");
    if (elTime) elTime.textContent = formatTime(targetSec);
    const remTime = document.getElementById("immRemainingTime");
    if (remTime) remTime.textContent = `-${{formatTime((appState.durationSec || 0) - targetSec)}}`;
    if (commit && window.pywebview && window.pywebview.api) {{
      window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {{}});
    }}
  }}

  const volHit = document.getElementById("volHit");
  function handleVolPointer(e) {{
    const rect = volHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setVolumePercent(Math.round(ratio * 100));
  }}

  volHit.addEventListener("pointerdown", (e) => {{
    isDraggingVolume = true;
    volHit.setPointerCapture(e.pointerId);
    handleVolPointer(e);
  }});
  volHit.addEventListener("pointermove", (e) => {{
    if (isDraggingVolume) handleVolPointer(e);
  }});
  volHit.addEventListener("pointerup", () => {{
    isDraggingVolume = false;
  }});

  // ---------- 音量状态（var 声明避免渲染循环早于此处执行时的 TDZ 报错） ----------
  var isDraggingDockVol = false;
  var volumeSyncedFromBackend = false;
  var lastNonZeroVolume = 80;
  var volumePersistTimer = null;

  function volumeIconMarkup(v) {{
    const speaker = '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>';
    if (v <= 0) {{
      return speaker + '<line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line>';
    }}
    if (v < 50) {{
      return speaker + '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>';
    }}
    return speaker + '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path>';
  }}

  function renderVolumeUI(vol) {{
    const v = Math.round(Math.min(100, Math.max(0, typeof vol === "number" ? vol : 80)));
    const vf = document.getElementById("volFill");
    if (vf) vf.style.width = `${{v}}%`;
    const dvf = document.getElementById("dockVolFill");
    if (dvf) {{
      dvf.style.height = `${{v}}%`;
      dvf.style.width = "100%";
    }}
    const knob = document.getElementById("dockVolKnob");
    if (knob) knob.style.bottom = `${{v}}%`;
    const dvt = document.getElementById("dockVolValText");
    if (dvt) dvt.textContent = `${{v}}%`;
    const level = v <= 0 ? "mute" : (v < 50 ? "low" : "high");
    const btnIcon = document.getElementById("dockVolBtnIcon");
    if (btnIcon && btnIcon.dataset.level !== level) {{
      btnIcon.dataset.level = level;
      btnIcon.innerHTML = volumeIconMarkup(v);
    }}
    const popIcon = document.getElementById("dockVolPopoverSvg");
    if (popIcon && popIcon.dataset.level !== level) {{
      popIcon.dataset.level = level;
      popIcon.innerHTML = volumeIconMarkup(v);
    }}
    const wrap = document.getElementById("dockVolumeWrap");
    if (wrap) wrap.classList.toggle("is-muted", v <= 0);
  }}

  function schedulePersistVolume() {{
    if (volumePersistTimer) clearTimeout(volumePersistTimer);
    volumePersistTimer = setTimeout(() => {{
      volumePersistTimer = null;
      if (window.pywebview && window.pywebview.api && window.pywebview.api.save_preferences) {{
        window.pywebview.api.save_preferences({{ volume: appState.volume }}).catch(() => {{}});
      }}
    }}, 400);
  }}

  function setVolumePercent(pct, persist = false) {{
    const v = Math.round(Math.min(100, Math.max(0, Number(pct) || 0)));
    appState.volume = v;
    volumeSyncedFromBackend = true;
    if (v > 0) lastNonZeroVolume = v;
    renderVolumeUI(v);
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.set_volume(v);
    }}
    if (persist) schedulePersistVolume();
  }}

  function toggleMute() {{
    const cur = typeof appState.volume === "number" ? appState.volume : 80;
    setVolumePercent(cur > 0 ? 0 : (lastNonZeroVolume || 80), true);
  }}

  document.getElementById("driveSelect").addEventListener("change", (e) => {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.select_drive(e.target.value).then(() => triggerImmediatePoll());
    }}
  }});

  function togglePlayPause() {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.toggle_play_pause().then(() => triggerImmediatePoll());
    }}
  }}
  function prevTrack() {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.prev_track().then(() => triggerImmediatePoll());
    }}
  }}
  function nextTrack() {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.next_track().then(() => triggerImmediatePoll());
    }}
  }}
  function playTrackIndex(idx) {{
    appState.currentTrackIndex = idx;
    if (appState.tracks && appState.tracks[idx]) {{
      const title = appState.tracks[idx].title || `音轨 ${{appState.tracks[idx].track_number}}`;
      const titleEl = document.getElementById("immersiveTrackTitle");
      if (titleEl) titleEl.textContent = title;
      const lblEl = document.getElementById("mdLabelTrackTitle");
      if (lblEl) lblEl.textContent = title;
    }}

    // 立即高亮选中歌曲行
    const rows = document.querySelectorAll("#fluidTrackList .fluid-track-row");
    rows.forEach((r, i) => {{
      const numSpan = r.querySelector(".fluid-track-num");
      if (i === idx) {{
        r.classList.add("is-current");
        if (numSpan) numSpan.textContent = "▶";
      }} else {{
        r.classList.remove("is-current");
        if (numSpan) numSpan.textContent = String(i + 1);
      }}
    }});

    selectTrack(idx);
  }}

  function selectTrack(idx) {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.play_track_index(idx).then(() => triggerImmediatePoll());
    }}
  }}
  function rescanDrive() {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.rescan_drive().then(() => triggerImmediatePoll());
    }}
  }}
  function ejectDrive() {{
    if (window.pywebview && window.pywebview.api) {{
      window.pywebview.api.eject_drive().then(() => triggerImmediatePoll());
    }}
  }}

  // 强制设定右侧面板模式（不做「同模式再点即收起」的切换语义）
  function applyRightPanelMode(mode) {{
    const stage = document.getElementById("mainStage");
    const lyrBtn = document.getElementById("lyricsTabBtn");
    const queBtn = document.getElementById("queueTabBtn");
    const lyrVp = document.getElementById("lyricsViewport");
    const queVp = document.getElementById("queueViewport");
    if (!stage) return;

    if (mode === "hidden") {{
      appState.rightPanelMode = "hidden";
      stage.classList.add("single-column");
      if (lyrBtn) lyrBtn.classList.remove("active");
      if (queBtn) queBtn.classList.remove("active");
      return;
    }}

    appState.rightPanelMode = mode;
    stage.classList.remove("single-column");
    if (lyrBtn) lyrBtn.classList.toggle("active", mode === "lyrics");
    if (queBtn) queBtn.classList.toggle("active", mode === "queue");
    if (lyrVp) lyrVp.style.display = mode === "lyrics" ? "block" : "none";
    if (queVp) queVp.style.display = mode === "queue" ? "flex" : "none";
    if (mode === "queue") renderQueueList();
  }}

  function switchRightPanel(mode) {{
    applyRightPanelMode(appState.rightPanelMode === mode ? "hidden" : mode);
  }}

  function applyTheme(mode) {{
    appState.themeMode = mode;
    currentCustomPrefs.themeMode = mode;
    document.documentElement.setAttribute("data-theme", mode);
    try {{
      localStorage.setItem("discflow_theme", mode);
    }} catch (e) {{}}
    const sub = document.getElementById("settingsThemeSub");
    if (sub) {{
      sub.textContent = mode === "light" ? "当前：浅色模式 (点击切换深色)" : "当前：深色模式 (点击切换浅色)";
    }}
    saveCustomPrefs();
  }}

  function cycleTheme() {{
    appState.themeMode = appState.themeMode === "light" ? "dark" : "light";
    applyTheme(appState.themeMode);
  }}

  let bridgeInitialized = false;
  function initPywebviewBridge() {{
    if (bridgeInitialized) return true;
    if (window.pywebview && window.pywebview.api) {{
      bridgeInitialized = true;
      try {{
        window.pywebview.api.ui_ready();
      }} catch (e) {{
        console.warn("ui_ready error:", e);
      }}
      syncPreferencesFromBackend();
      syncLibraryPathText();
      startKaraokeLoop();
      pollBackendState();
      setInterval(pollBackendState, 150);
      return true;
    }}
    return false;
  }}

  if (!initPywebviewBridge()) {{
    window.addEventListener("pywebviewready", initPywebviewBridge);
    let bridgeCheckCount = 0;
    const bridgePollTimer = setInterval(() => {{
      bridgeCheckCount++;
      if (initPywebviewBridge() || bridgeCheckCount > 40) {{
        clearInterval(bridgePollTimer);
      }}
    }}, 60);
  }}

  // ================= Apple Music 双栏主视图切换逻辑 =================
  let currentMainView = "cd"; // "cd" | "library" | "lyrics"

  function switchMainView(viewName) {{
    currentMainView = viewName;
    const map = {{ cd: "viewCd", library: "viewLibrary", lyrics: "viewLyrics" }};
    document.querySelectorAll(".view-page").forEach(page => page.classList.remove("active"));
    const target = document.getElementById(map[viewName] || "viewCd");
    if (target) target.classList.add("active");

    if (viewName === "library") {{
      renderLibraryCards();
    }} else if (viewName === "lyrics") {{
      renderStandaloneLyrics();
    }}
  }}

  function renderStandaloneLyrics() {{
    const vp = document.getElementById("standaloneLyricsViewport");
    const orig = document.getElementById("lyricsViewport");
    if (vp && orig) {{
      vp.innerHTML = orig.innerHTML;
    }}
  }}

  // 底部 Dock 栏进度条拖拽绑定
  const dockScrubberHit = document.getElementById("dockScrubberHit");
  if (dockScrubberHit) {{
    dockScrubberHit.addEventListener("pointerdown", (e) => {{
      isDraggingScrubber = true;
      dockScrubberHit.setPointerCapture(e.pointerId);
      handleDockScrubberPointer(e, false);
    }});
    dockScrubberHit.addEventListener("pointermove", (e) => {{
      if (isDraggingScrubber) handleDockScrubberPointer(e, false);
    }});
    dockScrubberHit.addEventListener("pointerup", (e) => {{
      if (!isDraggingScrubber) return;
      isDraggingScrubber = false;
      handleDockScrubberPointer(e, true);
    }});
  }}

  function handleDockScrubberPointer(e, commit) {{
    if (!dockScrubberHit) return;
    const rect = dockScrubberHit.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const targetSec = ratio * (appState.durationSec || 0);
    const fill = document.getElementById("dockScrubberFill");
    if (fill) fill.style.width = `${{ratio * 100}}%`;
    const elTime = document.getElementById("dockElapsedTime");
    if (elTime) elTime.textContent = formatTime(targetSec);
    const remTime = document.getElementById("dockRemainingTime");
    if (remTime) remTime.textContent = `-${{formatTime((appState.durationSec || 0) - targetSec)}}`;
    if (commit && window.pywebview && window.pywebview.api) {{
      window.pywebview.api.seek_to(targetSec).then(() => triggerImmediatePoll()).catch(() => {{}});
    }}
  }}

  // 底部 Dock 音量拖拽
  const dockVolHit = document.getElementById("dockVolHit");
  if (dockVolHit) {{
    dockVolHit.addEventListener("pointerdown", (e) => {{
      e.preventDefault();
      dockVolHit.setPointerCapture(e.pointerId);
      isDraggingDockVol = true;
      dockVolHit.classList.add("is-dragging");
      handleDockVolPointer(e);
    }});
    dockVolHit.addEventListener("pointermove", (e) => {{
      if (isDraggingDockVol) handleDockVolPointer(e);
    }});
    const endDockVolDrag = () => {{
      if (!isDraggingDockVol) return;
      isDraggingDockVol = false;
      dockVolHit.classList.remove("is-dragging");
      schedulePersistVolume();
    }};
    dockVolHit.addEventListener("pointerup", endDockVolDrag);
    dockVolHit.addEventListener("pointercancel", endDockVolDrag);
    dockVolHit.addEventListener("lostpointercapture", endDockVolDrag);
  }}

  // 在音量按钮 / 音量卡片上滚动鼠标滚轮，每格 ±5%
  const dockVolumeWrapEl = document.getElementById("dockVolumeWrap");
  if (dockVolumeWrapEl) {{
    dockVolumeWrapEl.addEventListener("wheel", (e) => {{
      e.preventDefault();
      const cur = typeof appState.volume === "number" ? appState.volume : 80;
      setVolumePercent(cur + (e.deltaY < 0 ? 5 : -5), true);
    }}, {{ passive: false }});
  }}

  function handleDockVolPointer(e) {{
    if (!dockVolHit) return;
    const rect = dockVolHit.getBoundingClientRect();
    // 竖向计算：从下到上为 0% 到 100%
    const ratio = Math.min(1, Math.max(0, (rect.bottom - e.clientY) / rect.height));
    const pct = ratio * 100;
    setVolumePercent(pct);
  }}

</script>
</body>
</html>
"""


# ============================================================================
# 3. Python <-> WebView2 无锁增量状态桥接控制器与后台光驱热插拔巡检服务
# ============================================================================
class DiscflowController:
    def __init__(self):
        self._window: Optional[webview.Window] = None
        self._lock = threading.Lock()
        self._closed = False
        self._booted = False

        # 状态版本号：前端轮询仅在版本号变更时才拉取大型 Base64 封面或歌单/歌词，彻底消除跨线程 evaluate_js 死锁
        self._meta_version: int = 1
        self._lyrics_version: int = 1
        self._load_token: int = 0
        self._watcher_miss_count: Dict[str, int] = {}

        self._cached_drives: List[str] = []
        self.current_toc: Optional[CdDiscToc] = None
        self.current_drive: str = ""
        self.current_track_index: int = 0
        self.elapsed_sec: float = 0.0
        self.duration_sec: float = 0.0
        self.is_loading: bool = True
        self.status_text: str = f"正在扫描 {APP_NAME} 光驱设备..."

        self.album_title: str = "等待放入音频 CD"
        self.artist: str = "未检测到音频光盘 · 放入 CD 后将自动识别"
        self.year: str = ""
        self.source: str = ""
        self.cover_data_url: str = ""

        self.play_mode: str = "order"  # "order" | "shuffle" | "one"

        self.library_manager = DiscflowLibraryManager()
        self.ripper_task: Optional[CdRipperTask] = None

        self.lyrics_cache: Dict[int, List[dict]] = {}
        # 被用户手动指定并锁定的曲目编号：自动预取不得覆盖
        self.lyrics_locked_tracks: set = set()
        # 批量匹配未命中的曲目编号：前端在曲目行标出「待匹配」，引导逐轨手动指定
        self.lyrics_missed_tracks: set = set()
        # 整专辑歌词批量套用进度（供前端展示）
        self._lyrics_batch = {"running": False, "done": 0, "total": 0, "matched": 0, "missed": []}
        # 每轨对应的歌曲 ID（来源 + id），用于歌词直取
        self.track_song_ids: Dict[int, dict] = {}
        self.current_lyrics: List[dict] = []

        self.player = CddaWaveOutPlayer(
            on_position=self._on_player_position,
            on_finished=self._on_player_finished,
            on_error=self._on_player_error,
        )

        # 启动时恢复上次保存的音量（preferences.json），并立即作用到播放引擎
        try:
            self.volume_pct: int = int(max(0, min(100, int(load_user_preferences().get("volume", 80)))))
        except Exception:
            self.volume_pct = 80
        self.player.set_volume(self.volume_pct / 100.0)

    def bind_window(self, win: webview.Window):
        self._window = win

    def shutdown(self):
        self._closed = True
        self.player.stop()

    def _bump_meta(self):
        self._meta_version += 1

    def _bump_lyrics(self):
        self._lyrics_version += 1

    def poll_state(
        self,
        client_meta_ver: int = -1,
        client_lyrics_ver: int = -1,
        client_track_index: int = -1,
    ) -> dict:
        """
        供前端 WebView2 定时异步拉取的高性能增量状态接口 (< 0.1ms，无阻塞 I/O、无跨线程 Invoke 死锁)：
        - 每次仅传输 ~180 字节的轻量播放进度与状态字
        - 仅当 _meta_version 变化时才下发 Base64 封面、光驱列表与曲目表
        - 仅当 _lyrics_version 变化时才下发双语 LRC 数组
        """
        meta_ver = self._meta_version
        lyrics_ver = self._lyrics_version

        state = {
            "currentTrackIndex": self.current_track_index,
            "isPlaying": self.player.is_playing(),
            "elapsedSec": round(self.elapsed_sec, 2),
            "durationSec": round(self.duration_sec, 2),
            "isLoading": self.is_loading,
            "statusText": self.status_text,
            "playMode": self.play_mode,
            "volume": self.volume_pct,
            "metaVersion": meta_ver,
            "lyricsVersion": lyrics_ver,
            "lyricsBatch": dict(self._lyrics_batch),
        }

        if int(client_meta_ver) != meta_ver:
            tracks_payload = []
            toc_ref = self.current_toc
            if toc_ref:
                for t in toc_ref.tracks:
                    tracks_payload.append(
                        {
                            "track_number": t.track_number,
                            "title": t.title,
                            "artist": t.artist,
                            "duration_sec": t.duration_sec,
                            "lyricsLocked": t.track_number in self.lyrics_locked_tracks,
                            "lyricsMissed": t.track_number in self.lyrics_missed_tracks,
                        }
                    )
            state.update(
                {
                    "drive": self.current_drive,
                    "drives": list(self._cached_drives),
                    "discId": toc_ref.disc_id if toc_ref else "",
                    "freedbId": toc_ref.freedb_id if toc_ref else "",
                    "albumTitle": self.album_title,
                    "artist": self.artist,
                    "year": self.year,
                    "source": self.source,
                    "coverDataUrl": self.cover_data_url,
                    "tracks": tracks_payload,
                }
            )

        # 歌词下发条件（v1.11.4 修复「首曲歌词不显示、切走再切回才有」）：
        # 除版本号变化外，只要客户端上次渲染的曲目索引与当前曲目不一致，也必须补发一次。
        # 这样即使版本号在轮询间隙被连续自增、前端已提前 ack 到新版本，切歌后也一定会重新拿到歌词。
        if int(client_lyrics_ver) != lyrics_ver or int(client_track_index) != self.current_track_index:
            state["lyrics"] = list(self.current_lyrics)

        return state

    def ui_ready(self):
        with self._lock:
            if self._booted:
                return
            self._booted = True
        threading.Thread(target=self._initial_boot, daemon=True).start()
        threading.Thread(target=self._hardware_watcher_loop, daemon=True).start()

    def select_drive(self, drive_letter: str):
        if drive_letter:
            threading.Thread(target=self._load_drive_async, args=(drive_letter,), daemon=True).start()

    def rescan_drive(self):
        target = self.current_drive
        if not target and self._cached_drives:
            target = self._cached_drives[0]
        self.current_toc = None
        self._bump_meta()
        threading.Thread(target=self._load_drive_async, args=(target,), daemon=True).start()

    def eject_drive(self):
        drv = self.current_drive
        if not drv:
            return
        self._load_token += 1
        self.player.stop()
        self.current_toc = None
        self.is_loading = False
        self.elapsed_sec = 0.0
        self.duration_sec = 0.0
        self.status_text = f"正在弹出光驱 {drv} 托盘..."
        self.album_title = f"光驱 {drv} 托盘已弹出"
        self.artist = "放入新的音频 CD 后将自动识别并播放"
        self.cover_data_url = ""
        self.current_lyrics = []
        self._bump_meta()
        self._bump_lyrics()

        def _eject_worker(target_drv: str):
            WindowsCdDriveManager.eject_disc(target_drv)
            self.status_text = f"已弹出光驱 {target_drv} 托盘 · 等待放入音频 CD"

        threading.Thread(target=_eject_worker, args=(drv,), daemon=True).start()

    def toggle_play_pause(self):
        if not self.current_toc or not self.current_toc.tracks:
            return
        if not self.player.is_running():
            self._start_track(self.current_track_index)
            return
        playing = self.player.is_playing()
        self.player.set_paused(playing)

    def prev_track(self):
        if not self.current_toc or not self.current_toc.tracks:
            return
        idx = (self.current_track_index - 1) % len(self.current_toc.tracks)
        self._start_track(idx)

    def next_track(self):
        if not self.current_toc or not self.current_toc.tracks:
            return
        if self.play_mode == "shuffle" and len(self.current_toc.tracks) > 1:
            import random
            choices = [i for i in range(len(self.current_toc.tracks)) if i != self.current_track_index]
            idx = random.choice(choices)
        else:
            idx = (self.current_track_index + 1) % len(self.current_toc.tracks)
        self._start_track(idx)

    def play_track_index(self, index: int):
        if self.current_toc and 0 <= int(index) < len(self.current_toc.tracks):
            self._start_track(int(index))

    def seek_to(self, seconds: float):
        target = max(0.0, float(seconds))
        self.elapsed_sec = target
        self.player.seek_seconds(target)

    def set_volume(self, pct: int):
        try:
            v = int(round(max(0.0, min(100.0, float(pct)))))
        except (TypeError, ValueError):
            return
        self.volume_pct = v
        self.player.set_volume(v / 100.0)

    def set_play_mode(self, mode: str):
        if mode in ("order", "shuffle", "one"):
            self.play_mode = mode

    def set_shuffle(self, enabled: bool):
        self.play_mode = "shuffle" if enabled else "order"

    def set_repeat_mode(self, mode: str):
        self.play_mode = "one" if mode == "one" else "order"

    def start_ripping(self) -> dict:
        with self._lock:
            if not self.current_toc or not self.current_toc.tracks:
                return {"success": False, "message": "当前未放入音频 CD，无法执行抓轨"}
            if getattr(self.current_toc, "is_local", False):
                return {"success": False, "message": "当前已是本地资料库曲目，无需重复抓轨"}
            if self.ripper_task and self.ripper_task.get_progress().is_ripping:
                return {"success": False, "message": "当前已有抓轨任务正在进行中"}

            # 抓轨前安全停播，彻底释放硬件激光头寻道与独占锁定
            self.player.stop()

            task = CdRipperTask(
                toc=self.current_toc,
                album_title=self.album_title,
                artist=self.artist,
                year=self.year,
                cover_data_url=self.cover_data_url,
                lyrics_cache=dict(self.lyrics_cache),
                library_mgr=self.library_manager,
            )
            self.ripper_task = task
            threading.Thread(target=task.run, daemon=True).start()
            return {"success": True, "message": "抓轨任务已启动"}

    def cancel_ripping(self) -> dict:
        if self.ripper_task:
            self.ripper_task.cancel()
            return {"success": True, "message": "已取消抓轨"}
        return {"success": False, "message": "当前无抓轨任务"}

    def get_ripping_progress(self) -> dict:
        if not self.ripper_task:
            return asdict(RippingProgress())
        return asdict(self.ripper_task.get_progress())

    def list_local_albums(self) -> list:
        albums = self.library_manager.list_albums(force_refresh=True)
        return [asdict(a) for a in albums]

    def delete_local_album(self, disc_id: str) -> dict:
        """
        从资源库物理删除整张唱片（WAV / 封面 / 元数据一并清除）。
        若该唱片正在播放，先停止播放并等待音频文件句柄释放，避免 Windows 下 rmtree 半途失败留下孤儿文件。
        """
        if not disc_id:
            return {"ok": False, "existed": False, "path": "", "failed": [], "reason": "缺少唱片标识"}

        is_current = bool(
            self.current_toc and getattr(self.current_toc, "disc_id", "") == disc_id
        )
        if is_current:
            try:
                self.player.stop()
            except Exception:
                pass
            # 播放线程为非阻塞复位，需给硬件句柄与 WAV 读取器留出释放时间
            time.sleep(0.35)
            self.current_toc = None
            self.current_track_index = 0
            self.current_lyrics = []
            self.elapsed_sec = 0.0
            self.duration_sec = 0.0
            self._bump_lyrics()

        res = self.library_manager.delete_album_verbose(disc_id)
        if res.get("ok"):
            self._meta_version += 1
        return res

    def update_album_metadata(self, disc_id: str, title: str = "", artist: str = "", year: str = "") -> bool:
        res = self.library_manager.update_album_metadata(disc_id, title=title or None, artist=artist or None, year=year or None)
        if res:
            self._meta_version += 1
        return res

    def clear_lyrics_cache(self) -> bool:
        """清空当前内存歌词缓存并让前端重绘（磁盘上的锁定歌词不动）"""
        self.lyrics_cache.clear()
        self.lyrics_missed_tracks.clear()
        self.current_lyrics = []
        self._bump_lyrics()
        self._bump_meta()
        return True

    def clear_all_library_data(self) -> bool:
        """清空整个资料库（磁盘文件一并删除），并重置内存中的播放状态"""
        albums = self.library_manager.list_albums()
        for alb in albums:
            try:
                self.library_manager.delete_album(alb.disc_id)
            except Exception:
                pass
        with self._lock:
            self.player.stop()
            self.current_toc = None
            self.lyrics_cache.clear()
            self.lyrics_locked_tracks = set()
            self.lyrics_missed_tracks = set()
            self.track_song_ids = {}
            self.current_lyrics = []
            self.album_title = "等待放入音频 CD"
            self.artist = ""
            self.year = ""
            self.cover_data_url = ""
            self.status_text = "资料库已清空"
            self._meta_version += 1
        self._bump_lyrics()
        return True

    def update_album_cover(self, disc_id: str, cover_data_url: str) -> bool:
        res = self.library_manager.update_album_cover(disc_id, cover_data_url)
        if res:
            self._meta_version += 1
        return res

    def export_library(self, target_zip_path: str) -> bool:
        return self.library_manager.export_library_zip(target_zip_path)

    def import_library(self, source_zip_path: str) -> bool:
        res = self.library_manager.import_library_zip(source_zip_path)
        if res:
            self._meta_version += 1
        return res

    def get_preferences(self) -> dict:
        return load_user_preferences()

    def save_preferences(self, prefs: dict) -> bool:
        return save_user_preferences(prefs)

    def search_album_candidates_for_current(self, title: str = "", artist: str = "") -> list:
        """按输入（缺省用当前专辑名/艺术家）三源并发检索专辑候选"""
        toc_ref = self.current_toc
        count = len(toc_ref.tracks) if toc_ref else 0
        use_title = (title or "").strip() or (self.album_title or "")
        use_artist = (artist or "").strip() or (self.artist or "")
        if not use_title or use_title in ("Audio CD",) or use_title.startswith("等待"):
            return []
        return search_album_candidates(use_title, use_artist, count)

    def apply_album_candidate(self, cand: dict) -> dict:
        """
        应用用户选定的专辑候选：
        1. 套用专辑名 / 艺术家 / 年份 / 封面；
        2. 按顺序 + 时长对齐写入逐轨曲名；
        3. 记录每轨的歌曲 ID（QQ / 网易云来源），供歌词按 ID 直取；
        4. 本地唱片同步写回 album.json，随后启动整专辑歌词批量套用。
        """
        if not isinstance(cand, dict):
            return {"ok": False, "reason": "候选数据无效"}

        toc_ref = self.current_toc
        # 曲库返回的标题先过一遍清洗：反转义 HTML 实体、去零宽/控制字符、折叠空白
        title = _sanitize_title(cand.get("title"))
        artist = _sanitize_title(cand.get("artist"))
        year = (cand.get("year") or "").strip()
        source = (cand.get("source") or "").strip()
        tracks = cand.get("tracks") or []
        if not title:
            return {"ok": False, "reason": "候选缺少专辑名"}

        disc_id = getattr(toc_ref, "disc_id", "") if toc_ref else ""
        is_local = bool(toc_ref and getattr(toc_ref, "is_local", False))

        track_titles: Dict[int, str] = {}
        track_artists: Dict[int, str] = {}
        song_ids: Dict[int, dict] = {}
        skipped_garbled = 0
        # 本地同名曲目（同一张碟出现两首一模一样的名字）说明本地元数据本身就不可信，
        # 这种情况下不做 CJK 保护，让曲库的准确曲名覆盖掉
        local_name_counts: Dict[str, int] = {}
        if toc_ref:
            for t in toc_ref.tracks:
                key = (t.title or "").strip()
                local_name_counts[key] = local_name_counts.get(key, 0) + 1
        if toc_ref and tracks:
            durations = [t.duration_sec for t in toc_ref.tracks]
            mapping = match_tracks_by_duration(durations, tracks)
            for idx, c in mapping.items():
                if not (0 <= idx < len(toc_ref.tracks)):
                    continue
                trk = toc_ref.tracks[idx]
                nm = _sanitize_title(c.get("title"))
                local_nm = trk.title or ""
                # 曲库常把日文曲名存成英文（茎 → Stem）：本地是 CJK、候选是纯拉丁时保留本地曲名。
                # 但如果本地曲名本身就是乱码（ソ━膝）、或者本地同名曲目出现多次（元数据不可信），
                # 就别再护着它了，宁可换成拉丁名。
                if (
                    nm
                    and not _looks_garbled(local_nm)
                    and local_name_counts.get(local_nm.strip(), 0) <= 1
                    and _has_cjk(local_nm)
                    and not _has_cjk(nm)
                ):
                    nm = ""
                # 候选曲名本身是乱码（━ 之类的误解码产物）时一律不覆盖本地曲名
                if nm and _looks_garbled(nm):
                    nm = ""
                    skipped_garbled += 1
                if nm:
                    track_titles[trk.track_number] = nm
                # 专辑艺术家变更时同步曲目艺术家，保证「专辑作者 / 曲目作者」一致
                if artist:
                    track_artists[trk.track_number] = artist
                sid = (c.get("song_id") or "").strip()
                if sid:
                    song_ids[trk.track_number] = {"source": source, "song_id": sid}

        cover_url_remote = (cand.get("cover_url") or "").strip()
        cover_data_url = ""
        if cover_url_remote:
            cover_bytes = download_image_bytes(cover_url_remote)
            if cover_bytes:
                cover_data_url = _bytes_to_data_url(cover_bytes)

        with self._lock:
            self.album_title = title
            if artist:
                self.artist = artist
            self.year = year
            self.source = f"{source or '手动'} 专辑匹配"
            if cover_data_url:
                self.cover_data_url = cover_data_url
            if toc_ref:
                for trk in toc_ref.tracks:
                    if trk.track_number in track_titles:
                        trk.title = track_titles[trk.track_number]
                    if trk.track_number in track_artists:
                        trk.artist = track_artists[trk.track_number]
            self.status_text = f"已套用专辑：《{self.album_title}》— {self.artist}"
            self._bump_meta()

        if song_ids:
            self.track_song_ids.update(song_ids)

        if is_local and disc_id:
            try:
                self.library_manager.update_album_metadata(
                    disc_id,
                    title=self.album_title,
                    artist=self.artist or None,
                    year=self.year or None,
                    track_titles=track_titles or None,
                    track_artists=track_artists or None,
                )
            except Exception:
                pass
            if cover_data_url:
                try:
                    self.library_manager.update_album_cover(disc_id, cover_data_url)
                except Exception:
                    pass
            for tn, info in song_ids.items():
                try:
                    self.library_manager.update_track_song_id(disc_id, tn, info["source"], info["song_id"])
                except Exception:
                    pass

        # 曲名已变：未锁定的歌词全部作废，锁定的保留
        for tn in list(self.lyrics_cache.keys()):
            if tn not in self.lyrics_locked_tracks:
                self.lyrics_cache.pop(tn, None)
        self._bump_lyrics()

        try:
            batch_info = self.start_album_lyrics_batch()
        except Exception:
            batch_info = {"ok": False}

        return {
            "ok": True,
            "albumTitle": self.album_title,
            "artist": self.artist,
            "year": self.year,
            "trackCount": len(track_titles),
            "songIdCount": len(song_ids),
            "skippedGarbled": skipped_garbled,
            "lyricsBatchStarted": bool(batch_info.get("ok")),
            "source": self.source,
        }

    def apply_manual_metadata(self, title: str, artist: str = "", year: str = "") -> dict:
        """兼容入口：按专辑名检索并自动应用最佳候选"""
        title = (title or "").strip()
        artist = (artist or "").strip()
        if not title:
            return {"ok": False, "reason": "专辑名称不能为空"}
        cands = self.search_album_candidates_for_current(title, artist)
        if not cands:
            return {
                "ok": False,
                "matched": False,
                "reason": "没有找到匹配的专辑，请换用更准确的专辑名，或补上艺术家后再试。",
            }
        # 优先取曲名干净的候选；全部带乱码时才退回第一条
        best = next((c for c in cands if not c.get("garbled")), cands[0])
        res = self.apply_album_candidate(best)
        res["matched"] = True
        return res

    # ------------------------------------------------------------------
    # 逐轨歌词手动匹配
    # ------------------------------------------------------------------
    def _find_track(self, track_number: int):
        if not self.current_toc:
            return None
        try:
            want = int(track_number)
        except Exception:
            return None
        for t in self.current_toc.tracks:
            if int(t.track_number) == want:
                return t
        return None

    def _persist_track_lyrics(self, track_number: int, lines: List[dict], source: str = "",
                              song_id: str = "", locked: bool = False, clear_lock: bool = False) -> bool:
        toc_ref = self.current_toc
        if not toc_ref or not getattr(toc_ref, "is_local", False):
            return False
        disc_id = getattr(toc_ref, "disc_id", "")
        if not disc_id:
            return False
        try:
            return self.library_manager.update_track_lyrics(
                disc_id, track_number, lines,
                source=source, song_id=song_id, locked=locked, clear_lock=clear_lock,
            )
        except Exception:
            return False

    def _is_current_track(self, track_number: int) -> bool:
        toc_ref = self.current_toc
        if not toc_ref or not (0 <= self.current_track_index < len(toc_ref.tracks)):
            return False
        return int(toc_ref.tracks[self.current_track_index].track_number) == int(track_number)

    def search_track_lyric_candidates(
        self, track_number: int, title: str = "", artist: str = ""
    ) -> list:
        """三源检索指定曲目的歌词候选，供用户挑选"""
        trk = self._find_track(track_number)
        if not trk:
            return []
        use_title = (title or "").strip() or (trk.title or "")
        use_artist = (artist or "").strip() or (trk.artist or "")
        return search_lyric_candidates(use_title, use_artist, trk.duration_sec, limit=8)

    def apply_track_lyrics(
        self, track_number: int, source: str, song_id: str,
        title: str = "", artist: str = "", duration: float = 0.0,
    ) -> dict:
        """应用用户选定的歌词，并锁定该曲目，自动流程不再覆盖"""
        trk = self._find_track(track_number)
        if not trk:
            return {"ok": False, "reason": "找不到该曲目"}

        cand = {
            "source": source,
            "song_id": str(song_id or ""),
            "title": (title or trk.title or "").strip(),
            "artist": (artist or trk.artist or "").strip(),
            "duration_sec": float(duration or trk.duration_sec or 0),
        }
        lines = fetch_lyrics_for_candidate(cand)
        if not lines:
            return {"ok": False, "reason": "该来源没有返回可用歌词，换一条候选试试。"}

        tn = int(trk.track_number)
        self.lyrics_cache[tn] = lines
        self.lyrics_locked_tracks.add(tn)
        self.lyrics_missed_tracks.discard(tn)
        self._persist_track_lyrics(tn, lines, source=source, song_id=str(song_id or ""), locked=True)

        if self._is_current_track(tn):
            self.current_lyrics = lines
            self._bump_lyrics()
        self._bump_meta()

        return {
            "ok": True,
            "lineCount": len(lines),
            "source": source,
            "hasTranslation": any(ln.get("translation") for ln in lines),
        }

    def clear_track_lyrics(self, track_number: int) -> dict:
        """清空指定曲目的歌词并解除锁定"""
        trk = self._find_track(track_number)
        if not trk:
            return {"ok": False, "reason": "找不到该曲目"}
        tn = int(trk.track_number)
        self.lyrics_cache.pop(tn, None)
        self.lyrics_locked_tracks.discard(tn)
        self._persist_track_lyrics(tn, [], clear_lock=True)
        if self._is_current_track(tn):
            self.current_lyrics = []
            self._bump_lyrics()
        return {"ok": True}

    def auto_match_track_lyrics(self, track_number: int) -> dict:
        """解除锁定并按曲名重新自动匹配一次"""
        trk = self._find_track(track_number)
        if not trk:
            return {"ok": False, "reason": "找不到该曲目"}
        tn = int(trk.track_number)
        self.lyrics_locked_tracks.discard(tn)
        self.lyrics_cache.pop(tn, None)

        lines = lookup_track_lyrics(
            trk.title, trk.artist, trk.duration_sec, getattr(trk, "builtin_lrc", "") or ""
        )
        if lines:
            self.lyrics_cache[tn] = lines
            self.lyrics_missed_tracks.discard(tn)
            self._persist_track_lyrics(tn, lines, clear_lock=True)
        else:
            self._persist_track_lyrics(tn, [], clear_lock=True)

        if self._is_current_track(tn):
            self.current_lyrics = lines
            self._bump_lyrics()
        self._bump_meta()

        return {"ok": bool(lines), "lineCount": len(lines)}

    # ------------------------------------------------------------------
    # 整专辑歌词批量套用
    # ------------------------------------------------------------------
    def start_album_lyrics_batch(self) -> dict:
        """按当前专辑每一轨的曲名，批量检索并套用歌词（后台执行，带进度）"""
        if self._lyrics_batch.get("running"):
            return {"ok": False, "reason": "已有批量任务在进行中"}
        toc_ref = self.current_toc
        if not toc_ref or not toc_ref.tracks:
            return {"ok": False, "reason": "当前没有可处理的曲目"}

        tracks = [t for t in toc_ref.tracks if not getattr(t, "is_data_track", False)]
        if not tracks:
            return {"ok": False, "reason": "当前没有音频曲目"}

        self._lyrics_batch = {"running": True, "done": 0, "total": len(tracks), "matched": 0, "missed": []}
        threading.Thread(target=self._run_album_lyrics_batch, args=(toc_ref, tracks), daemon=True).start()
        return {"ok": True, "total": len(tracks)}

    def _run_album_lyrics_batch(self, toc_ref, tracks) -> None:
        """
        并发为每轨取词：
        - 若该轨已绑定歌曲 ID（来自专辑匹配），直接按 ID 取词，不再搜索（快且不会配错）；
        - 否则退回三源检索，按时长差 ≤ 3 秒的可信度自动套用。
        """
        from concurrent.futures import ThreadPoolExecutor

        disc_id = getattr(toc_ref, "disc_id", "")
        is_local = bool(getattr(toc_ref, "is_local", False))
        total = len(tracks)
        done = 0
        matched = 0
        missed: List[int] = []

        def _fetch_lines(trk):
            sid = self.track_song_ids.get(trk.track_number) or {}
            if sid.get("song_id"):
                lines = fetch_lyrics_for_candidate(
                    {"source": sid.get("source") or "", "song_id": sid["song_id"]}
                )
                if lines:
                    return lines, {"source": sid.get("source") or "", "song_id": sid["song_id"]}
            try:
                cands = search_lyric_candidates(
                    trk.title, trk.artist, trk.duration_sec, limit=3, detail_top=1
                )
            except Exception:
                return [], {}
            for cand in cands:
                dur = float(cand.get("duration_sec") or 0)
                if dur <= 0:
                    continue
                if abs(dur - float(trk.duration_sec or 0)) <= 3.0:
                    lines = fetch_lyrics_for_candidate(cand)
                    if lines:
                        return lines, {"source": str(cand.get("source") or ""), "song_id": str(cand.get("song_id") or "")}
                    break
            return [], {}

        def _work(trk):
            if trk.track_number in self.lyrics_locked_tracks:
                return (trk, [], {}, "locked")
            existing = self.lyrics_cache.get(trk.track_number)
            if existing and self._lyrics_quality_ok(existing):
                return (trk, [], {}, "ok")
            lines, info = _fetch_lines(trk)
            return (trk, lines, info, "hit" if lines else "miss")

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for trk, lines, info, status in pool.map(_work, tracks):
                    if status == "ok":
                        matched += 1
                    if status == "hit" and lines:
                        tn = int(trk.track_number)
                        self.lyrics_cache[tn] = lines
                        self.lyrics_locked_tracks.add(tn)
                        if info.get("song_id"):
                            self.track_song_ids[tn] = info
                        if is_local and disc_id:
                            self._persist_track_lyrics(
                                tn, lines,
                                source=str(info.get("source") or ""),
                                song_id=str(info.get("song_id") or ""),
                                locked=True,
                            )
                        matched += 1
                        if self._is_current_track(tn):
                            self.current_lyrics = lines
                            self._bump_lyrics()
                    elif status == "miss":
                        # 批量对不上的轨道单独记账，前端会在曲目行上标出来，
                        # 引导用户用行内「词」按钮逐轨手动指定（专辑优先、单曲兜底）
                        missed.append(int(trk.track_number))
                    done += 1
                    self._lyrics_batch = {
                        "running": done < total,
                        "done": done,
                        "total": total,
                        "matched": matched,
                        "missed": list(missed),
                    }
        finally:
            self.lyrics_missed_tracks = set(missed)
            self._lyrics_batch = {
                "running": False,
                "done": done,
                "total": total,
                "matched": matched,
                "missed": list(missed),
            }
            if missed:
                self.status_text = (
                    f"整专辑歌词匹配完成：{matched}/{total} 首，"
                    f"{len(missed)} 首未命中，可点曲目行的「词」逐轨指定"
                )
            else:
                self.status_text = f"整专辑歌词匹配完成：{matched}/{total} 首"
            self._bump_meta()
            self._bump_lyrics()

    def get_library_dir(self) -> str:
        try:
            return os.path.abspath(self.library_manager.base_dir)
        except Exception:
            return os.path.abspath(LIBRARY_DIR)

    def open_library_folder(self) -> dict:
        """在 Windows 资源管理器中打开资源库存储目录"""
        target = self.get_library_dir()
        try:
            os.makedirs(target, exist_ok=True)
        except Exception:
            return {"ok": False, "path": target}
        try:
            if hasattr(os, "startfile"):
                os.startfile(target)  # type: ignore[attr-defined]
                return {"ok": True, "path": target}
        except Exception:
            pass
        try:
            import subprocess

            subprocess.Popen(["explorer", os.path.normpath(target)])
            return {"ok": True, "path": target}
        except Exception:
            return {"ok": False, "path": target}

    def play_local_album(self, disc_id: str) -> bool:
        toc = self.library_manager.get_album_toc(disc_id)
        if not toc:
            return False

        albums = self.library_manager.list_albums()
        target_album: Optional[LocalAlbumInfo] = None
        for a in albums:
            if a.disc_id == disc_id:
                target_album = a
                break

        with self._lock:
            self._load_token += 1
            self.player.stop()
            self.current_toc = toc
            self.current_drive = ""
            self.album_title = target_album.title if target_album else (toc.cdtext_album or "Local Album")
            self.artist = target_album.artist if target_album else (toc.cdtext_artist or "Local Artist")
            self.year = target_album.year if target_album else ""
            self.cover_data_url = target_album.cover_data_url if target_album else ""
            self.lyrics_cache.clear()
            self.lyrics_locked_tracks = set()
            self.lyrics_missed_tracks = set()
            self.track_song_ids = {}
            if target_album:
                for t in target_album.tracks:
                    if t.lyrics:
                        self.lyrics_cache[t.track_number] = t.lyrics
                    if getattr(t, "lyrics_locked", False):
                        self.lyrics_locked_tracks.add(t.track_number)
                    sid = getattr(t, "lyrics_song_id", "") or ""
                    if sid:
                        self.track_song_ids[t.track_number] = {
                            "source": getattr(t, "lyrics_source", "") or "",
                            "song_id": sid,
                        }
            self.status_text = f"正在离线播放黑胶资料库: 《{self.album_title}》"
            self.is_loading = False
            self._bump_meta()
            self._bump_lyrics()

        self._start_track(0)
        threading.Thread(
            target=self._prefetch_lyrics_async,
            args=(self._load_token,),
            daemon=True,
        ).start()
        return True

    def _refresh_cached_drives(self) -> List[str]:
        phys = WindowsCdDriveManager.list_physical_cdrom_drives()
        if phys != self._cached_drives:
            self._cached_drives = phys
            self._bump_meta()
        return phys

    def _initial_boot(self):
        phys = self._refresh_cached_drives()
        target = ""
        for drv in phys:
            if WindowsCdDriveManager.is_disc_ready(drv):
                target = drv
                break
        else:
            if phys:
                target = phys[0]
        if target:
            threading.Thread(target=self._load_drive_async, args=(target,), daemon=True).start()
        else:
            with self._lock:
                self.is_loading = False
                self.current_toc = None
                self.current_drive = ""
                self.status_text = "未检测到可用光驱 · 请连接外置或内置 CD 光驱"
                self.album_title = "等待放入音频 CD"
                self.artist = "连接外置 USB 光驱并放入音频 CD 后将自动识别并播放"
                self.cover_data_url = ""
                self._bump_meta()

    def _hardware_watcher_loop(self):
        while not self._closed:
            time.sleep(1.8)
            if self._closed:
                break
            # 若当前正在高优先级加载读盘，暂缓该轮巡检，避免并发冲突
            if self.is_loading:
                continue

            try:
                # 关键保护：当前若正在播放本地黑胶资料库专辑（is_local=True），
                # 绝不受物理光驱有盘/无盘巡检影响，绝不误触退盘或强行打断！
                if self.current_toc is not None and getattr(self.current_toc, "is_local", False):
                    continue

                phys = self._refresh_cached_drives()
                if not phys:
                    if self.current_toc and not getattr(self.current_toc, "is_local", False):
                        self._handle_disc_removed(self.current_drive)
                    continue

                for p_drv in phys:
                    # 关键保护：当物理光驱正在活跃读取 CD-DA 原始 PCM 扇区播放时，
                    # 绝不开启新句柄发送 IOCTL 检查，防止外置 USB 光驱因总线争夺产生杂音或卡顿
                    if (
                        self.current_toc is not None
                        and not self.current_toc.is_simulated
                        and self.current_toc.drive_letter == p_drv
                        and self.player.is_running()
                    ):
                        self._watcher_miss_count[p_drv] = 0
                        continue

                    ready = WindowsCdDriveManager.is_disc_ready(p_drv)
                    if ready:
                        self._watcher_miss_count[p_drv] = 0
                        if (
                            self.current_toc is None
                            or self.current_toc.drive_letter != p_drv
                        ):
                            # 如果该光驱正在异步加载，不要重复发起触发
                            if self.current_drive == p_drv and self.is_loading:
                                continue
                            threading.Thread(
                                target=self._load_drive_async,
                                args=(p_drv,),
                                daemon=True,
                            ).start()
                            break
                    else:
                        # 物理防抖：外置 USB 光驱在寻道或刚放入时可能偶发瞬态未就绪
                        # 只有连续 3 次巡检 (约 5.4 秒) 均明确未就绪，且当前载入了该光驱，才判定为退盘
                        self._watcher_miss_count[p_drv] = self._watcher_miss_count.get(p_drv, 0) + 1
                        if (
                            self.current_toc is not None
                            and self.current_toc.drive_letter == p_drv
                            and self._watcher_miss_count[p_drv] >= 3
                        ):
                            self._handle_disc_removed(p_drv)
                            break
            except Exception:
                pass

    def _handle_disc_removed(self, drive_letter: str):
        self.player.stop()
        with self._lock:
            self.current_toc = None
            self.is_loading = False
            self.elapsed_sec = 0.0
            self.duration_sec = 0.0
            self.status_text = f"光驱 {drive_letter} 中光盘已取出 · 等待放入 CD"
            self.album_title = f"等待光驱 {drive_letter} 放入 CD"
            self.artist = "放入音频 CD 后约 3~5 秒自动识别专辑、封面与双语歌词"
            self.cover_data_url = ""
            self.current_lyrics = []
            self._bump_meta()
            self._bump_lyrics()

    def _load_drive_async(self, drive_letter: str):
        with self._lock:
            self._load_token += 1
            my_token = self._load_token
            self.is_loading = True
            self.current_drive = drive_letter

        self.player.stop()
        self.lyrics_cache.clear()
        self.lyrics_locked_tracks = set()
        self.lyrics_missed_tracks = set()
        self.track_song_ids = {}
        self.current_lyrics = []
        self.status_text = f"正在读取光驱 {drive_letter} 引导区 TOC..."
        self._bump_meta()
        self._bump_lyrics()

        toc: Optional[CdDiscToc] = None
        try:
            max_attempts = 6
            for attempt in range(max_attempts):
                if my_token != self._load_token or self._closed:
                    return

                # 优先直接尝试读 TOC (外置光驱旋转就绪时直接命中，省去冗余就绪等待)
                toc = WindowsCdDriveManager.read_toc(drive_letter)
                if toc and toc.tracks:
                    break

                if attempt < max_attempts - 1:
                    self.status_text = f"光驱 {drive_letter} 正在旋转启动 ({attempt + 1}/{max_attempts})..."
                    time.sleep(0.75)

            if my_token != self._load_token or self._closed:
                return

            if not toc or not toc.tracks:
                with self._lock:
                    if my_token == self._load_token:
                        self.current_toc = None
                        self.is_loading = False
                        self.elapsed_sec = 0.0
                        self.duration_sec = 0.0
                        self.status_text = f"光驱 {drive_letter} 已就绪 · 请放入音频 CD 光盘"
                        self.album_title = f"等待光驱 {drive_letter} 放入 CD"
                        self.artist = "放入音频 CD 后约 3~5 秒自动识别专辑、封面与双语歌词"
                        self.cover_data_url = ""
                        self._bump_meta()
                return

            with self._lock:
                if my_token != self._load_token or self._closed:
                    return
                self.current_toc = toc
                self.album_title = toc.cdtext_album or "Audio CD"
                self.artist = toc.cdtext_artist or f"DiscID {toc.disc_id[:10]}"
                self.year = ""
                self.cover_data_url = ""
                self.status_text = f"已就绪 ({drive_letter}) · {len(toc.tracks)} 首音轨 · 正在匹配封面与元数据..."
                self.is_loading = False
                self._bump_meta()

            # 「放入光盘自动播放」可在设置里关掉；关掉时只读元数据不出声
            try:
                auto_play = bool(load_user_preferences().get("autoPlay", True))
            except Exception:
                auto_play = True
            self._start_track(0, autoplay=auto_play)
            threading.Thread(target=self._fetch_metadata_async, args=(toc, my_token), daemon=True).start()
        except Exception as e:
            with self._lock:
                if my_token == self._load_token:
                    self.is_loading = False
                    self.status_text = f"读取光驱 {drive_letter} 异常: {e}"
                    self._bump_meta()
        finally:
            # 终极安全网：确保无论发生什么，is_loading 绝不会永久处于 True
            with self._lock:
                if my_token == self._load_token and self.is_loading:
                    self.is_loading = False
                    self._bump_meta()

    def _apply_metadata_to_state(self, meta: AlbumMetadataResult, my_token: int):
        if self._closed or my_token != self._load_token:
            return
        if not self.current_toc or self.current_toc.disc_id != meta.disc_id:
            return

        self.album_title = meta.album_title
        self.artist = meta.artist
        self.year = meta.year
        self.source = meta.matched_source
        if meta.cover_data_url:
            self.cover_data_url = meta.cover_data_url
        self.status_text = f"{meta.album_title} — {meta.artist} ({meta.matched_source})"

        for trk in self.current_toc.tracks:
            if trk.track_number in meta.track_titles:
                trk.title = meta.track_titles[trk.track_number]
            if trk.track_number in meta.track_artists:
                trk.artist = meta.track_artists[trk.track_number]

        self._bump_meta()

    def _fetch_metadata_async(self, toc: CdDiscToc, my_token: int):
        lyrics_triggered = [False]

        def _on_partial(partial_meta: AlbumMetadataResult):
            self._apply_metadata_to_state(partial_meta, my_token)
            if not lyrics_triggered[0] and self.current_toc:
                lyrics_triggered[0] = True
                threading.Thread(
                    target=self._prefetch_lyrics_async,
                    args=(my_token,),
                    daemon=True,
                ).start()

        meta: AlbumMetadataResult = lookup_album_metadata(toc, on_partial=_on_partial)

        # 弱网下 MusicBrainz 偶发超时，首次没匹配上就补一次重试。
        # 否则界面会一直停在「Audio CD / Track 01」，用户会以为光盘读不出来。
        # 失败结果不会写进 _METADATA_CACHE，所以重试是真正重新联网查询。
        if (
            (not meta.album_title or meta.album_title == "Audio CD")
            and not self._closed
            and my_token == self._load_token
        ):
            time.sleep(2.5)
            if not self._closed and my_token == self._load_token:
                meta = lookup_album_metadata(toc, on_partial=_on_partial)

        self._apply_metadata_to_state(meta, my_token)

        # 封面补抓：标题匹配上了但封面源超时，界面会一直停在占位图标上。
        # 这里异步再试一次，成功了就悄悄补上。
        if (
            not meta.cover_data_url
            and meta.album_title
            and meta.album_title != "Audio CD"
            and not self._closed
            and my_token == self._load_token
        ):
            threading.Thread(
                target=self._retry_cover_async,
                args=(meta.disc_id, meta.album_title, meta.artist, my_token),
                daemon=True,
            ).start()

        if not lyrics_triggered[0] and self.current_toc:
            lyrics_triggered[0] = True
            threading.Thread(
                target=self._prefetch_lyrics_async,
                args=(my_token,),
                daemon=True,
            ).start()

    def _retry_cover_async(self, disc_id: str, album_title: str, artist: str, my_token: int):
        """封面补抓：稍等片刻再联网取一次，成功就更新封面"""
        time.sleep(2.0)
        if self._closed or my_token != self._load_token:
            return
        data_url = refetch_album_cover(album_title, artist)
        if not data_url or self._closed or my_token != self._load_token:
            return
        if not self.current_toc or self.current_toc.disc_id != disc_id:
            return
        if self.cover_data_url:
            return  # 期间已经被别的路径补上了
        self.cover_data_url = data_url
        self._bump_meta()

    def _start_track(self, index: int, autoplay: bool = True):
        if not self.current_toc or not (0 <= index < len(self.current_toc.tracks)):
            return

        self.current_track_index = index
        trk = self.current_toc.tracks[index]
        self.duration_sec = trk.duration_sec
        self.elapsed_sec = 0.0

        if trk.track_number in self.lyrics_cache:
            self.current_lyrics = self.lyrics_cache[trk.track_number]
            self._bump_lyrics()
        else:
            self.current_lyrics = []
            self._bump_lyrics()
            # 切到任何没有歌词的曲目都立即补取一次（CD 与本地唱片一致）：
            # 命中进程内歌词缓存时零网络开销，未命中才真正请求。
            # 这样即使预取因换碟/切歌而错过当前曲目，也不会出现「首曲永远没歌词」。
            threading.Thread(
                target=self._fetch_lyrics_async,
                args=(trk, False, self._load_token),
                daemon=True,
            ).start()

        # 关闭「放入光盘自动播放」时只准备好曲目与歌词，等用户点播放键再出声
        if autoplay:
            self.player.load_and_play(self.current_toc.drive_letter, trk)
        else:
            self.status_text = f"已就绪：《{self.album_title}》· 按播放键开始"
            self._bump_meta()

    def _fetch_lyrics_async(self, trk, force_refresh: bool = False, my_token: int = 0):
        if self._closed or (my_token and my_token != self._load_token):
            return
        if not force_refresh and trk.track_number in self.lyrics_cache:
            return
        lines = lookup_track_lyrics(trk.title, trk.artist, trk.duration_sec, trk.builtin_lrc)
        if self._closed or (my_token and my_token != self._load_token):
            return
        if lines:
            self.lyrics_cache[trk.track_number] = lines
        if (
            self.current_toc
            and 0 <= self.current_track_index < len(self.current_toc.tracks)
            and self.current_toc.tracks[self.current_track_index].track_number == trk.track_number
        ):
            self.current_lyrics = lines
            self._bump_lyrics()

    @staticmethod
    def _lyrics_quality_ok(lines) -> bool:
        """
        判断一份歌词是否「够好」：行数够 + 翻译覆盖率达标。
        逐字时间轴不参与判定——缺失时由前端按行时长合成，所以只需关注翻译是否残缺。
        不达标时后台会重新抓取，用于修复历史抓轨时用旧对齐逻辑存下的「有的句子有翻译、有的没有」。
        """
        if not lines or len(lines) < 4:
            return False
        n = len(lines)
        with_trans = sum(1 for ln in lines if isinstance(ln, dict) and ln.get("translation"))
        return with_trans >= n * 0.7

    def _prefetch_lyrics_async(self, my_token: int = 0):
        """
        并发预取整张专辑所有音轨的歌词（v1.11 提速）：
        专辑加载后一次性把全部歌词拉齐，切歌时直接命中缓存，避免每切一首都要等 1~3 秒网络。
        对于已缓存但质量不达标的歌词（缺逐字/缺翻译），会重新抓取并热更新当前曲目。
        """
        if self._closed:
            return
        toc_ref = self.current_toc
        if not toc_ref or not toc_ref.tracks:
            return

        pending = []
        for trk in toc_ref.tracks:
            if getattr(trk, "is_data_track", False):
                continue
            if trk.track_number in self.lyrics_locked_tracks:
                continue
            existing = self.lyrics_cache.get(trk.track_number)
            if existing and self._lyrics_quality_ok(existing):
                continue
            pending.append(
                {
                    "track_number": trk.track_number,
                    "title": trk.title,
                    "artist": trk.artist,
                    "duration_sec": trk.duration_sec,
                    "builtin_lrc": getattr(trk, "builtin_lrc", "") or "",
                }
            )
        if not pending:
            return

        fetched = prefetch_lyrics_for_tracks(pending)
        if self._closed or (my_token and my_token != self._load_token):
            return

        changed = False
        improved = {}
        for tn, lines in fetched.items():
            if not lines:
                continue
            current = self.lyrics_cache.get(tn)
            if current is None or not self._lyrics_quality_ok(current):
                self.lyrics_cache[tn] = lines
                improved[tn] = lines
                changed = True

        # 本地唱片：把重新抓到的优质歌词回写 album.json，让修复长期生效
        if improved and getattr(toc_ref, "is_local", False):
            disc_id = getattr(toc_ref, "disc_id", "")
            if disc_id:
                def _persist():
                    for tn, lyr in improved.items():
                        try:
                            self.library_manager.update_track_lyrics(disc_id, tn, lyr)
                        except Exception:
                            pass

                threading.Thread(target=_persist, daemon=True).start()

        if (
            changed
            and self.current_toc
            and 0 <= self.current_track_index < len(self.current_toc.tracks)
        ):
            cur = self.current_toc.tracks[self.current_track_index]
            refreshed = self.lyrics_cache.get(cur.track_number)
            if refreshed and refreshed is not self.current_lyrics:
                self.current_lyrics = refreshed
                self._bump_lyrics()

    def _on_player_position(self, elapsed: float):
        self.elapsed_sec = elapsed

    def _on_player_finished(self):
        if self._closed:
            return
        if self.play_mode == "one":
            self._start_track(self.current_track_index)
        else:
            self.next_track()

    def _on_player_error(self, msg: str):
        if self._closed:
            return
        self.status_text = msg
        if (
            self.current_toc
            and not getattr(self.current_toc, "is_local", False)
            and not self.current_toc.is_simulated
            and not WindowsCdDriveManager.is_disc_ready(self.current_toc.drive_letter)
        ):
            self._handle_disc_removed(self.current_toc.drive_letter)


class DiscflowJsApi:
    """
    轻量纯方法 JS 桥接门面（所有内部属性均以 _ 开头）：
    防止 pywebview.util.get_functions 递归反射遍历 Window.native (WinForms COM 对象)
    或 CddaWaveOutPlayer 实例，彻底消除启动期的递归风暴与 COM 跨线程异常。
    """

    def __init__(self, controller: DiscflowController):
        self._ctrl = controller

    def poll_state(
        self,
        client_meta_ver: int = -1,
        client_lyrics_ver: int = -1,
        client_track_index: int = -1,
    ) -> dict:
        return self._ctrl.poll_state(client_meta_ver, client_lyrics_ver, client_track_index)

    def ui_ready(self):
        self._ctrl.ui_ready()

    def select_drive(self, drive_letter: str):
        self._ctrl.select_drive(drive_letter)

    def rescan_drive(self):
        self._ctrl.rescan_drive()

    def eject_drive(self):
        self._ctrl.eject_drive()

    def toggle_play_pause(self):
        self._ctrl.toggle_play_pause()

    def prev_track(self):
        self._ctrl.prev_track()

    def next_track(self):
        self._ctrl.next_track()

    def play_track_index(self, index: int):
        self._ctrl.play_track_index(index)

    def seek_to(self, seconds: float):
        self._ctrl.seek_to(seconds)

    def set_volume(self, pct: int):
        self._ctrl.set_volume(pct)

    def set_play_mode(self, mode: str):
        self._ctrl.set_play_mode(mode)

    def set_shuffle(self, enabled: bool):
        self._ctrl.set_shuffle(enabled)

    def set_repeat_mode(self, mode: str):
        self._ctrl.set_repeat_mode(mode)

    def start_ripping(self) -> dict:
        return self._ctrl.start_ripping()

    def cancel_ripping(self) -> dict:
        return self._ctrl.cancel_ripping()

    def get_ripping_progress(self) -> dict:
        return self._ctrl.get_ripping_progress()

    def list_local_albums(self) -> list:
        return self._ctrl.list_local_albums()

    def delete_local_album(self, disc_id: str) -> dict:
        return self._ctrl.delete_local_album(disc_id)

    def update_album_metadata(self, disc_id: str, title: str = "", artist: str = "", year: str = "") -> bool:
        return self._ctrl.update_album_metadata(disc_id, title, artist, year)

    def update_album_cover(self, disc_id: str, cover_data_url: str) -> bool:
        return self._ctrl.update_album_cover(disc_id, cover_data_url)

    def export_library(self, target_zip_path: str) -> bool:
        return self._ctrl.export_library(target_zip_path)

    def import_library(self, source_zip_path: str) -> bool:
        return self._ctrl.import_library(source_zip_path)

    def play_local_album(self, disc_id: str) -> bool:
        return self._ctrl.play_local_album(disc_id)

    def clear_lyrics_cache(self) -> bool:
        return self._ctrl.clear_lyrics_cache()

    def clear_all_library_data(self) -> bool:
        return self._ctrl.clear_all_library_data()

    def get_preferences(self) -> dict:
        return self._ctrl.get_preferences()

    def save_preferences(self, prefs: dict) -> bool:
        return self._ctrl.save_preferences(prefs)

    def get_library_dir(self) -> str:
        return self._ctrl.get_library_dir()

    def open_library_folder(self) -> dict:
        return self._ctrl.open_library_folder()

    def apply_manual_metadata(self, title: str, artist: str = "", year: str = "") -> dict:
        return self._ctrl.apply_manual_metadata(title, artist, year)

    def search_track_lyric_candidates(self, track_number: int, title: str = "", artist: str = "") -> list:
        return self._ctrl.search_track_lyric_candidates(track_number, title, artist)

    def apply_track_lyrics(self, track_number: int, source: str, song_id: str,
                           title: str = "", artist: str = "", duration: float = 0.0) -> dict:
        return self._ctrl.apply_track_lyrics(track_number, source, song_id, title, artist, duration)

    def clear_track_lyrics(self, track_number: int) -> dict:
        return self._ctrl.clear_track_lyrics(track_number)

    def auto_match_track_lyrics(self, track_number: int) -> dict:
        return self._ctrl.auto_match_track_lyrics(track_number)

    def start_album_lyrics_batch(self) -> dict:
        return self._ctrl.start_album_lyrics_batch()

    def search_album_candidates_for_current(self, title: str = "", artist: str = "") -> list:
        return self._ctrl.search_album_candidates_for_current(title, artist)

    def apply_album_candidate(self, cand: dict) -> dict:
        return self._ctrl.apply_album_candidate(cand)


# Backwards compatibility alias
AppleMusicCdController = DiscflowController


def main():
    controller = DiscflowController()
    js_api = DiscflowJsApi(controller)
    win = webview.create_window(
        title=APP_NAME,
        html=APPLE_MUSIC_HTML,
        js_api=js_api,
        width=1120,
        height=740,
        min_size=(880, 620),
        background_color="#000000",
    )
    controller.bind_window(win)
    win.events.closed += controller.shutdown
    win.events.loaded += lambda: controller.ui_ready()
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
