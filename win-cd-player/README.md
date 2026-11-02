# Discflow 💿

> **Discflow** 是一个运行在 Windows 上的轻量级 Hi-Fi 桌面 CD 音乐播放器，专为外置/内置光驱设计，拥有对标 Apple Music 的现代设计语言与动效体验。
>
> 当前版本：**2.0.0**（功能冻结版）

---

## ✨ 核心特性

### 光驱硬件直读
- 基于 Win32 API（`IOCTL_CDROM_READ_TOC`、`IOCTL_CDROM_RAW_READ`、`winmm.dll`）直接读取 CDDA 原始音频流，边读边播，无需预先抓轨。
- 自适应扇区批次（16 / 27 / 32 / 48 / 64 扇区自动降级），在 USB 外置光驱上也能稳定出音。
- 自动感知放盘、退盘与托盘就绪状态。

### 元数据与封面
- 硬件内嵌 **CD-TEXT** 秒级预填充。
- **MusicBrainz DiscID** 精确匹配 + TOC 时长加权兜底，另有 GNUDb / FreeDB 备选。
- 匹配不到时，可在曲目页点「匹配」手动输入专辑名，**QQ 音乐 / iTunes / 网易云三源并发**检索候选，一键整张套用。
- 自动跳过曲库中的乱码条目（Shift-JIS 误解码产生的 `━` 之类符号），保证写进资料库的曲名干净。

### 歌词
- 三源取词：网易云（译文 + 逐字）、QQ 音乐（中文曲库）、LRCLIB（欧美）。
- **逐字卡拉 OK 高亮**，译文与原文双行对齐，柔焦虚化排版，当前句放大发光。
- 底栏一键开关双语译文。
- 专辑匹配时顺手记下每首歌的歌曲 ID，之后整专辑歌词**按 ID 直取**，13 首约 8.5 秒跑完。
- 批量没对上的曲目会在曲目行标「歌词待匹配」，点行内「词」按钮可逐轨手动指定。

### 资料库
- 逐扇区无损转录为 WAV，归档后免光盘即点即播。
- 唱片货架网格，悬停有黑胶滑出微交互。
- 支持搜索（专辑 / 歌名 / 歌手）、手动换封面、整库 ZIP 导出导入。

### 视觉
- Apple Music 风格环境光背景：从封面提取主色，生成弥散光斑，随曲目流动。
- 纯黑白 SF Symbols 风格内联 SVG 图标，深浅色双主题。
- 谜底黑胶（MD Vinyl）全屏沉浸模式：拟物封套 + 旋转黑胶 + 机械唱臂落臂。

---

## 🛠️ 技术架构

| 层 | 技术 |
|---|---|
| UI | HTML5 / CSS3 / 原生 JS（WebView2 via `pywebview`） |
| 音频引擎 | Python 3 + ctypes + Win32 `winmm.dll`（waveOut） |
| 元数据 | requests（MusicBrainz / Cover Art Archive / QQ 音乐 / iTunes / 网易云 / LRCLIB） |
| 打包 | PyInstaller onefile（`Discflow.spec`） |

---

## 📦 快速开始

### 直接运行
双击 `Discflow.exe` 即可（免安装单文件）。

### 源码运行
```bash
pip install -r requirements.txt
python main.py
```

### 打包
```bash
pyinstaller --noconfirm Discflow.spec
```

### 运行测试
在**项目根目录**（`Discflow1.0/`）执行：
```bash
python -m unittest discover -s win-cd-player -p "test_*.py"
```
测试会自动把资料库重定向到临时沙箱，不会碰真实数据。

---

## 📁 目录结构

```
Discflow1.0/
├── Discflow.exe           # 打包产物
├── PRD.md                 # 产品需求文档（唯一事实来源）
├── library/               # 资料库：每张唱片一个目录（album.json + track_XX.wav + cover.jpg）
└── win-cd-player/
    ├── main.py            # 主程序 + 内联 UI（HTML/CSS/JS）
    ├── cd_engine.py       # Win32 光驱读取与 waveOut 播放
    ├── metadata_service.py# 元数据 / 封面 / 歌词 / 专辑候选检索
    ├── library_service.py # 资料库管理、抓轨、偏好持久化
    ├── Discflow.spec      # PyInstaller 配置
    └── test_*.py          # 160 项自动化测试
```

---

## 📄 开源与使用许可（非商用声明）

本项目采用 **[Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)](LICENSE)** 协议授权。

- ✅ **允许：** 自由分享、修改、复制并在个人非商业环境下使用。
- ❌ **禁止商用：** 严禁将本项目及其衍生版本用于任何商业盈利、付费销售或捆绑商用场景。
