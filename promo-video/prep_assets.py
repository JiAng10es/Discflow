import os
import shutil
import numpy as np
from PIL import Image, ImageDraw

SRC_DIR = r"F:\Discflow1.0\website\assets"
OUT_DIR = r"F:\Discflow1.0\promo-video\out\assets_clean"

PILL_FILES = [
    "hero_fujii_player_dark.png",
    "hero_fujii_player_light.png",
    "lyrics_tokyo_player_dark.png",
    "library_shelf_dark.png",
    "library_shelf_light.png",
]

COPY_FILES = [
    "hero_heisei_papaya_dark.png",
    "rip_modal_dark.png",
    "settings_modal_dark.png",
    "icon.svg",
]

COVER_SPECS = [
    ("hero_fujii_player_dark.png", "cover_fujii.png"),
    ("hero_heisei_papaya_dark.png", "cover_papaya.png"),
    ("lyrics_tokyo_player_dark.png", "cover_tokyo.png"),
]


def detect_green_pill_bbox(arr: np.ndarray):
    """在右上角 ROI 内自动探测旧版 '发现 v2.3.1 ×' 绿色胶囊像素边界。"""
    roi_x0, roi_x1 = 1050, 1340
    roi_y0, roi_y1 = 0, 75
    roi = arr[roi_y0:roi_y1, roi_x0:roi_x1].astype(np.int32)
    mask = (roi[:, :, 1] - roi[:, :, 0] > 12) & (roi[:, :, 1] - roi[:, :, 2] > 8)
    ys, xs = np.where(mask)
    if len(ys) == 0:
        raise RuntimeError("未能探测到右上角绿色胶囊像素")
    x0 = int(roi_x0 + xs.min()) - 2
    x1 = int(roi_x0 + xs.max()) + 2
    y0 = max(5, int(roi_y0 + ys.min()) - 2)
    y1 = min(arr.shape[0] - 6, int(roi_y0 + ys.max()) + 2)
    return x0, y0, x1, y1


def remove_green_pill(src_path: str, dst_path: str):
    """逐行左右背景线性插值 + 外延 5px 羽化修复右上角绿色胶囊，彻底无痕。"""
    img = Image.open(src_path).convert("RGB")
    arr = np.array(img, dtype=np.float32)
    x0, y0, x1, y1 = detect_green_pill_bbox(arr)

    feather = 5
    fy0 = max(0, y0 - feather)
    fy1 = min(arr.shape[0] - 1, y1 + feather)

    patched = arr.copy()
    # 紧贴胶囊左右各取 2 个纯净背景像素均值，避开右侧主题按钮投影
    left_ref = arr[fy0 : fy1 + 1, max(0, x0 - 2) : x0].mean(axis=1)
    right_ref = arr[fy0 : fy1 + 1, x1 + 1 : min(arr.shape[1], x1 + 3)].mean(axis=1)

    t = np.linspace(0.0, 1.0, x1 - x0 + 1, dtype=np.float32)[None, :, None]
    interp_block = left_ref[:, None, :] * (1.0 - t) + right_ref[:, None, :] * t

    # 胶囊本体区域 100% 替换，仅上下外延 5px 做渐变羽化融合
    alpha_y = np.ones((fy1 - fy0 + 1, 1, 1), dtype=np.float32)
    top_pad = y0 - fy0
    bot_pad = fy1 - y1
    for k in range(top_pad):
        alpha_y[k, 0, 0] = (k + 1) / (top_pad + 1)
    for k in range(bot_pad):
        alpha_y[-(k + 1), 0, 0] = (k + 1) / (bot_pad + 1)

    orig_block = arr[fy0 : fy1 + 1, x0 : x1 + 1]
    patched[fy0 : fy1 + 1, x0 : x1 + 1] = orig_block * (1.0 - alpha_y) + interp_block * alpha_y

    out_img = Image.fromarray(np.clip(np.round(patched), 0, 255).astype(np.uint8), mode="RGB")
    out_img.save(dst_path, optimize=True)
    print(f"[clean] {os.path.basename(src_path)} -> bbox=({x0},{y0})-({x1},{y1})")


def detect_cover_bbox(arr: np.ndarray):
    """通过行列梯度跳变精确探测左侧正方形专辑封面边框（排除 1px 玻璃描边与阴影）。"""
    # 水平方向扫描（避开上下圆角，取中段 y=340..500）
    row_band = arr[340:500, 70:540].astype(np.float32)
    row_grad = np.abs(np.diff(row_band, axis=1)).mean(axis=(0, 2))
    # 垂直方向扫描（避开左右圆角，取中段 x=200..360）
    col_band = arr[160:680, 200:360].astype(np.float32)
    col_grad = np.abs(np.diff(col_band, axis=0)).mean(axis=(1, 2))

    # 在预期左边界 [95, 130] 和右边界 [445, 515] 内找最强的边框跳变峰
    left_candidates = row_grad[25:60]  # x in [95, 130]
    right_candidates = row_grad[375:445]  # x in [445, 515]
    x_left_rim = 70 + 25 + int(np.argmax(left_candidates)) + 1
    x_right_rim = 70 + 375 + int(np.argmax(right_candidates))

    top_candidates = col_grad[40:100]  # y in [200, 260]
    bottom_candidates = col_grad[430:465]  # y in [590, 625]
    y_top_rim = 160 + 40 + int(np.argmax(top_candidates)) + 1
    y_bottom_rim = 160 + 430 + int(np.argmax(bottom_candidates))

    # 向内缩进 1px 彻底避开 1px 半透明玻璃描边
    x0 = x_left_rim + 1
    x1 = x_right_rim
    y0 = y_top_rim + 1
    y1 = y_bottom_rim

    # 强制严格正方形
    w = x1 - x0
    h = y1 - y0
    side = min(w, h)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    sx0 = cx - side // 2
    sy0 = cy - side // 2
    return sx0, sy0, sx0 + side, sy0 + side


def make_rounded_rgba(crop_img: Image.Image, radius: int = 22) -> Image.Image:
    """为圆角卡片截取的封面应用抗锯齿圆角透明蒙版，去除四角残留 UI 背景像素。"""
    w, h = crop_img.size
    scale = 4
    mask_hi = Image.new("L", (w * scale, h * scale), 0)
    draw = ImageDraw.Draw(mask_hi)
    draw.rounded_rectangle(
        [0, 0, w * scale - 1, h * scale - 1],
        radius=radius * scale,
        fill=255,
    )
    mask = mask_hi.resize((w, h), Image.Resampling.LANCZOS)
    rgba = crop_img.convert("RGBA")
    rgba.putalpha(mask)
    return rgba


def extract_cover(src_path: str, dst_path: str):
    img = Image.open(src_path).convert("RGB")
    arr = np.array(img)
    x0, y0, x1, y1 = detect_cover_bbox(arr)
    crop = img.crop((x0, y0, x1, y1))
    # 根据边长自适应圆角半径，并放大至 720x720 高清尺寸供特写渲染使用
    side = crop.size[0]
    radius = max(18, round(side * 22 / 354))
    rgba = make_rounded_rgba(crop, radius=radius)
    rgba_hi = rgba.resize((720, 720), Image.Resampling.LANCZOS)
    rgba_hi.save(dst_path, optimize=True)
    print(f"[cover] {os.path.basename(src_path)} -> crop=({x0},{y0})-({x1},{y1}) side={side} -> {os.path.basename(dst_path)} (720x720)")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for fn in PILL_FILES:
        remove_green_pill(os.path.join(SRC_DIR, fn), os.path.join(OUT_DIR, fn))
    for fn in COPY_FILES:
        shutil.copy2(os.path.join(SRC_DIR, fn), os.path.join(OUT_DIR, fn))
        print(f"[copy]  {fn}")
    for src_fn, dst_fn in COVER_SPECS:
        extract_cover(os.path.join(SRC_DIR, src_fn), os.path.join(OUT_DIR, dst_fn))
    print("Done. Clean assets saved to:", OUT_DIR)


if __name__ == "__main__":
    main()
