# Discflow

> **把 CD 重新放回桌面。**
>
> 一个为 Windows 打造的 Hi-Fi CD 音乐播放器 —— 光驱直读、双语逐字歌词、原生 Liquid Glass 界面、无损转录归档。

![Version](https://img.shields.io/badge/version-2.1.0-ff375f)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078d4)](#系统要求)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-CC%20BY--NC%204.0-lightgrey)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-198%20suites-30d158)](#测试)

![Discflow 播放界面](docs/screenshots/player.jpg)

---

## 这个项目解决什么问题

流媒体把音乐变成了「无限供给」，代价是你不再完整地听完一张唱片，也再没有「放进去、按下播放」这个动作。

而想把 CD 重新用起来，现有的方案都不太顺手：

| 痛点 | 现状 |
|---|---|
| **想听就要先转** | 主流工具（EAC、dBpoweramp、foobar2000）都要求先整张抓轨成文件，才能播放。想听一首歌，得先等十分钟 |
| **音质不透明** | 抓轨参数（偏移校正、C2 纠错、是否重采样）藏在深层设置里，大多数人不知道自己转出来的到底是不是无损 |
| **元数据认不出来** | 冷门碟、日版碟、自刻碟的 CD-TEXT 经常是空的，MusicBrainz 也查不到，最后显示一堆 `Track 01` |
| **没有歌词** | 桌面播放器对中日文歌词的支持基本为零，更别说双语对照、逐字时间轴与偏移微调 |
| **界面停留在 2005 年** | Winamp 式的皮肤、树状播放列表、密密麻麻的按钮 |

**Discflow 的思路是：先让你听得起来，再让你留得下来。**

- **插盘即播** —— 用 Win32 的光驱指令直接读 CDDA 原始扇区，边读边放，不落盘、不转码
- **想归档时再无损转录** —— 逐扇区转成 WAV，连同封面、曲目、歌词一起存进资料库
- **元数据三源并发 + 乱码治理** —— 冷门碟也能对上，脏数据不写进库
- **双语逐字歌词** —— 原文、译文双行，跟着播放头一个字一个字点亮，支持键盘微调偏移
- **现场记忆，开盖即续** —— 记住上次播放专辑与进度位置，无盘启动也能一键续听

---

## 主要功能

### 🎧 光驱硬件直读

- 基于 Win32 `IOCTL_CDROM_READ_TOC` / `IOCTL_CDROM_RAW_READ` 直接读取 CDDA 原始扇区，`winmm.dll` waveOut 原声输出
- **自适应扇区批次**：64 → 48 → 32 → 27 → 16 自动降级，外置 USB 光驱带宽不足时也不会断音
- **Seek 世代防竞争与跨轨句柄复用**：引入 `_seek_generation` 单调代数标记消除拖动回弹；顺序切歌直接复用同一光驱 `h_drive` 句柄，零驱动卡顿
- **CD-TEXT 双字节字对齐解析**：原生支持 UTF-16BE/LE 16-bit 字对齐解析与内嵌空字节过滤，精准读取日/中文硬件内嵌碟片信息
- 放盘、退盘、托盘就绪状态实时感知

### 📝 双语逐字歌词

- 三源取词：**网易云**（译文 + 逐字）、**QQ 音乐**（中文曲库）、**LRCLIB**（欧美）
- 每一行拆成词块，按音节长度分配时长，逐字点亮
- 原文 + 译文双行对齐，当前句放大发光，其余柔焦虚化
- **纯快捷键时间轴微调**：播放界面按 `[` 延后 0.5s、`]` 提前 0.5s、`\` 归零，自动持久化到 `preferences.json` 与曲目节点，零多余界面按钮
- 专辑匹配时顺手记下每首歌的歌曲 ID，之后整张碟按 ID 直取；对不上的曲目可逐轨手动指定来源并锁定

![手动匹配歌词](docs/screenshots/lyric-match.jpg)

### 🔍 专辑元数据匹配与清洗

- **MusicBrainz DiscID** 精确匹配，TOC 时长加权兜底，另有 GNUDb / FreeDB 备选
- 匹配失败时，QQ 音乐 / iTunes / 网易云三源并发检索候选，手动挑一条套用整张（专辑信息 + 曲名 + 封面 + 歌词）
- **乱码治理**：识别 Shift-JIS 误解码产生的残次条目（例如把「ー・ママ」错成「蕙」），自动排到最后，绝不写进资料库
- 弱网自愈：元数据超时自动重试，封面图床抖动会后台补抓

![手动匹配专辑信息](docs/screenshots/album-match.jpg)

### 💾 资料库归档

- 逐扇区无损转录为 WAV（16-Bit / 44.1 kHz 立体声），附封面与元数据
- 四列黑胶唱片货架网格，封套背后露出黑胶盘面与中心圆标
- 支持按专辑名 / 歌名 / 歌手实时深度搜索
- 整库 ZIP 导出导入，换台电脑解压即用

![资料库](docs/screenshots/library.jpg)

### 🎨 原生 Liquid Glass 视觉与桌面交互

- **原生 Liquid Glass 光学渲染**：零外部框架依赖，采用 GPU 加速的原生 SVG `<feDisplacementMap>` 微距折射与双层 Specular Rim 晶莹高光内描边
- **环境光背景**：从封面提取 3 组主色调，生成缓慢流动的弥散光斑
- **高度可调的悬浮玻璃控件**：底栏宽度、透明度、毛玻璃模糊度，以及顶部圆钮质感均可实时滑块调节，支持深浅双主题与系统级 `prefers-reduced-motion` 无障碍降级
- **后台全局媒体快捷键**：基于 Win32 `WH_KEYBOARD_LL` 低级键盘钩子，在最小化、窗口遮挡或全屏游戏时也能响应播放/暂停、上下曲与停止
- **播放现场记忆（Session Resume）**：退出时自动持久化上次播放的专辑、曲目序号与音量，无盘启动时静默就绪，按空格即可起播

![系统偏好设置](docs/screenshots/settings.jpg)

---

## 安装与使用

### 系统要求

| | |
|---|---|
| 操作系统 | Windows 10 / 11（64 位） |
| 运行时 | [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win11 自带；Win10 若缺失需安装） |
| 光驱 | 内置或外置 USB CD-ROM（仅播放 CD 时需要；只播资料库则不需要） |

### 方式一：直接下载打包好的 EXE

到 [**Releases**](https://github.com/JiAng10es/Discflow/releases/latest) 下载 `Discflow.exe`，双击即可。

免安装、不写注册表、不驻留后台，删掉文件即卸载。

### 方式二：源码运行

```bash
git clone https://github.com/JiAng10es/Discflow.git
cd Discflow/win-cd-player

pip install -r requirements.txt
python main.py
```

### 方式三：自己打包

```bash
cd win-cd-player
pyinstaller --clean -y Discflow.spec
# 产物在 dist/Discflow.exe
```

### 怎么用

1. **放一张 CD 进光驱** —— 程序会自动读取曲目、匹配封面和歌词，然后开始播放
2. **想归档就点「抓轨当前光盘」** —— 逐扇区转录成 WAV，存进 `library/`，之后不用插盘也能听
3. **左上角 Discflow 菜单**可进入资料库、全局搜索或系统偏好设置；**右上角**可一键切换深浅主题
4. **歌词快了或慢了？** 直接按 `[` / `]` 微调 ±0.5s，按 `\` 归零；歌词不对可点「词」手动挑选候选
5. **后台控制**：支持键盘物理媒体键（Play/Pause、Next、Prev、Stop），最小化或全屏运行时同样有效

> 首次匹配封面和歌词需要联网。断网也能正常播放，只是没有封面和歌词。

---

## 项目目录结构

```
Discflow/
├── README.md                     本文件
├── DESIGN.md                     设计品味、Liquid Glass 参数与布局红线规范
├── PRD.md                        产品需求文档（全量设计与演进记录）
├── LICENSE                       CC BY-NC 4.0
├── .gitignore
├── docs/
│   └── screenshots/              README 用最新截图
│
└── win-cd-player/                程序本体
    ├── main.py                   主程序：控制器 + 全局媒体键 + 内联 UI（~9,100 行）
    ├── cd_engine.py              Win32 光驱读取、TOC / CD-TEXT 解析、waveOut 播放
    ├── library_service.py        资料库扫描、抓轨、偏好持久化
    ├── metadata_service.py       元数据 / 封面 / 歌词 / 专辑候选并发检索
    ├── Discflow.spec             PyInstaller 打包配置
    ├── requirements.txt
    ├── discflow.ico
    ├── docs/discflow-icon.svg
    └── test_*.py                 自动化单元测试与集成测试套件
```

**运行时目录**（不在版本库里，由程序自动生成）：

```
Discflow.exe
├── library/                     唱片资料库，每张专辑一个子目录
│   └── <disc_id>/
├── preferences.json             外观、歌词偏移、上次播放现场等偏好
```

---

## 输入与输出示例

Discflow 有两种「输入」：**一张物理 CD**，或者**一个已经存在的资料库目录**。

### 输入 A：物理音频 CD

程序通过 `IOCTL_CDROM_READ_TOC` 拿到碟片的 TOC（Table of Contents），其中包括：

- 每首曲目的起始 LBA、时长
- 碟片总长度
- 可选的 CD-TEXT（支持 ASCII 与 UTF-16BE/LE 双字节字符集）
- 由 TOC 计算出的 **MusicBrainz DiscID**（用于联网匹配）

```
G:  →  11 首音轨 / 总长 46:37 / CD-TEXT: 无
     DiscID: Oy_zcAkOjOx4tjetoyfdqY8kTKw-
```

### 输出：资料库目录

点「抓轨当前光盘」后，会产出这样的结构：

```
library/
└── Oy_zcAkOjOx4tjetoyfdqY8kTKw-/
    ├── album.json          元数据 + 曲目表 + 歌词（JSON）
    ├── cover.jpg           专辑封面（多源择优）
    ├── cover.png           原图（若来源为 PNG）
    ├── track_01.wav        逐扇区无损转录
    ├── track_02.wav
    └── ... track_11.wav
```

单个专辑目录约 **470 MB**（11 首曲目）。WAV 参数固定为：

| 参数 | 值 |
|---|---|
| 声道 | 2（立体声） |
| 位深 | 16 Bit |
| 采样率 | 44100 Hz |
| 压缩 | 无（PCM） |

### `album.json` 结构

```jsonc
{
  "disc_id": "Oy_zcAkOjOx4tjetoyfdqY8kTKw-",
  "title": "HELP EVER HURT NEVER",
  "artist": "藤井風",
  "year": "2020",
  "cover_file": "cover.png",
  "cover_data_url": "data:image/png;base64,iVBORw0KGgo...",   // 内嵌封面，供离线渲染
  "ripped_at": 1793611059.523528,                              // Unix 时间戳
  "tracks": [
    {
      "track_number": 1,
      "title": "何なんw",
      "artist": "藤井風",
      "duration_sec": 320.64,
      "file_name": "track_01.wav",
      "lyrics": [
        { "time": 0.0, "text": "作词 : 藤井風", "translation": "" },
        { "time": 12.4, "text": "何なんw", "translation": "怎么了呢" }
      ],
      "lyrics_source": "netease",       // netease / qq / lrclib / 空
      "lyrics_song_id": "1901371647",   // 来源站点上的歌曲 ID
      "lyrics_locked": false,           // true = 用户手动指定，不再自动覆盖
      "lyrics_offset": 0.0,             // 曲目级歌词时间轴微调（秒）
      "lyrics_matched_at": 0.0
    }
  ]
}
```

> `album.json` 是资料库扫描的**唯一判据** —— 没有这个文件的目录不会被识别为专辑。

### 输入 B：已有的资料库目录

把上面那个 `library/` 整个拷到任意位置，程序就能直接播放，不需要光驱。

默认查找顺序：

1. 环境变量 `DISCFLOW_LIBRARY` 指定的路径
2. 程序同级的 `library/`
3. 用户目录下的兜底位置

### 输出：偏好设置 (`preferences.json`)

```jsonc
{
  "lyricLineHeight": 1.4,
  "lyricLetterSpacing": 0,
  "dockWidth": 720,
  "dockOpacity": 2,
  "dockBlur": 4,
  "topbarIconOpacity": 65,
  "topbarIconBlur": 28,
  "themeMode": "dark",
  "volume": 80,
  "playMode": "loop",
  "showTranslation": true,
  "autoPlay": true,
  "lyricOffsets": {},
  "lastSession": {
    "disc_id": "TUY27a3ueE66IY2ppbznmCVEO5c-",
    "track_index": 1
  }
}
```

---

## 技术实现

| 层 | 技术 |
|---|---|
| 界面 | 原生 HTML / CSS / JavaScript，跑在 WebView2（pywebview）里；原生 SVG 光学滤镜驱动 Liquid Glass |
| 音频引擎 | Python `ctypes` 直接调 Win32 API：`kernel32.DeviceIoControl` + `winmm.waveOut*` |
| 全局热键 | Win32 `WH_KEYBOARD_LL` 低级键盘钩子 + 独立消息循环线程 |
| 元数据 | `requests` 并发请求 MusicBrainz / Cover Art Archive / QQ 音乐 / iTunes / 网易云 / LRCLIB |
| 打包 | PyInstaller onefile |

几个值得一提的实现细节：

- **边读边播与世代锁**：读盘线程持续把 CDDA 扇区解码成 PCM 塞进 waveOut 缓冲，并通过 `_seek_generation` 过滤旧世代数据块，任意 Seek 零回弹
- **自适应批次**：外置 USB 光驱在连续大块读取时会返回 `ERROR_NOT_READY`，程序会逐级缩小每次读取的扇区数重试
- **歌词缓存**：进程内 LRU 缓存，上限 400 条，避免长时间运行内存增长
- **封面主色提取**：从封面采样主色，生成 CSS 径向渐变叠层，切换专辑时整体换色
- **动效工程规范**：零 `transition: all`，显式声明 GPU 合成属性，完整支持 `prefers-reduced-motion`

---

## 测试

```bash
# 在仓库根目录执行
python -m unittest discover -s win-cd-player -p "test_*.py"
```

测试覆盖：

- 光驱 TOC 解析、DiscID 计算、UTF-16BE/LE CD-TEXT 解码与 Seek 世代防竞争
- 资料库扫描、抓轨流程、ZIP 导入导出
- 元数据匹配、乱码识别、歌词缓存上限、时间轴偏移持久化
- UI 结构完整性、三段对称 Dock 布局、设置中心尺寸及 Liquid Glass 样式规则
- 偏好持久化、现场记忆恢复、便携路径解析

测试会自动把资料库重定向到临时沙箱，**不会碰你的真实数据**。

---

## 已知限制

- **仅支持 Windows** —— 底层依赖 `IOCTL_CDROM_RAW_READ`、`winmm` 与 `WH_KEYBOARD_LL`，没有跨平台计划
- **需要物理光驱** —— 播放 CD 的前提；纯资料库播放不需要
- **冷门碟可能匹配不到** —— 依赖公开曲库，自刻碟、小众发行版经常查不到，此时会显示 `Track 01` 之类的兜底名
- **杀毒软件误报** —— PyInstaller 单文件打包的常见现象，加白名单即可
- **不支持 CD 以外的音源** —— 这是刻意的：它是一台 CD 播放器，不是通用音乐播放器

---

## 参与贡献

欢迎提 Issue 和 PR。提交前请确保：

1. `python -m unittest discover -s win-cd-player -p "test_*.py"` 通过
2. 涉及 UI 或动效修改时，严格遵守 [DESIGN.md](DESIGN.md) 中的布局红线与动效规范
3. 新增功能同步更新 [PRD.md](PRD.md)
4. 不要提交 `library/`、`*.wav`、`*.exe` 或任何个人偏好文件

---

## 许可证

本项目采用 **[CC BY-NC 4.0](LICENSE)** 协议授权。

- ✅ 允许自由分享、修改、复制，并在个人非商业环境下使用
- ❌ **禁止任何形式的商业使用**（包括付费销售、捆绑分发、SaaS 化）

请注意这不是一个 OSI 认可的开源许可证 —— 它禁止商业用途。

所有音乐、封面与歌词的版权归各自权利人所有。本软件仅用于播放你合法拥有的唱片，不提供任何内容下载能力。

---

**唱片还在，声音也应该在。**
