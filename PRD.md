# Discflow 产品需求文档 (PRD)

**当前基线版本**：`v2.2.0` (commit `0809b5c`)  
**更新日期**：2026-11-17  
**目标平台**：Windows 10 / Windows 11 (x64, PyWebView EdgeChromium)

---

## 1. 产品定位与核心目标

**Discflow** 是一款轻量、现代化的 Windows 原生桌面 **CD 音乐播放器与离线无损黑胶资料库**：
- **原盘无损直读**：通过 Win32 `IOCTL_CDROM_RAW_READ` + `winmm.dll waveOut` 输出 44.1kHz / 16-Bit 立体声 PCM，支持内存预读与跨轨无缝播放 (Gapless Playback)。
- **Apple Music 风格动态流体界面**：封面背景模糊铺底、取色流体光斑、Native SVG Liquid Glass 折射高光、逐字卡拉 OK 双语歌词与对称悬浮播控 Dock。
- **便携离线资料库**：自适应多扇区安全抓轨归档为本地 WAV + `album.json` + 封面，拔出光驱即可离线回放并支持 ZIP 备份恢复。

---

## 2. 系统架构与代码组织

```text
win-cd-player/
├── main.py              # DiscflowController / DiscflowJsApi / 全局多媒体按键 / 托盘 / 更新检查 / 窗口记忆
├── cd_engine.py         # Win32 光驱 TOC/CD-TEXT 直读 + 双缓冲内存预读与硬件时钟位置同步
├── library_service.py   # 便携资料库管理、自适应抓轨、偏好持久化 (preferences.json) 与测试沙箱隔离
├── metadata_service.py  # MusicBrainz / GNUDb / QQ音乐 / iTunes / 网易云 / LRCLIB 多源元数据与歌词服务
├── Discflow.spec        # PyInstaller 单文件打包配置 (内嵌 ui/ 与 discflow.ico)
└── ui/
    ├── index.html       # 视图骨架与内联 SF Symbols SVG 图标
    ├── styles.css       # 双主题样式tokens、Liquid Glass 滤镜、响应式布局与分栏设置
    └── app.js           # 状态同步、自走卡拉 OK 时钟、媒体检索弹窗与交互逻辑
```

### 2.1 状态同步机制
- 后端通过 `poll_state(client_meta_ver, client_lyrics_ver, client_track_index)` 按版本号增量返回曲目、封面与歌词；
- 关键状态变更（切歌、元数据就绪、抓轨进度、托盘操作）通过 `_ui_push_worker()` 经 `window.__onBackendStatePush` 主动推送到前端，前端保留 500ms 兜底轮询。

---

## 3. 核心功能模块规范

### 3.1 无边框窗口与顶部悬浮栏
- **无边框窗体**：`frameless=True, easy_drag=False`，在 Windows 11 上启用原生 DWM 圆角 (`DwmSetWindowAttribute` attribute `33 = 2`)，支持 8 向边缘热区缩放 (`.win-resize-edge`) 与顶部拖拽区 (`.top-drag-spacer`)。
- **左上悬浮区**：Discflow 图标菜单（资料库、全局搜索、偏好设置）、读盘 (`#topRescanBtn`)、弹出 (`#topEjectBtn`)、三态状态灯（绿常亮就绪 / 黄闪烁读盘 / 灰无盘）。
- **右上悬浮区**：新版本提醒胶囊 (`#updateNoticePill`)、深浅主题切换 (`#topThemeBtn`) 与极简窗口三键 (`— □ ×`)。

### 3.2 主播放视图 (`#viewCd`)
- **左侧封面舞台**：1:1 专辑封面卡片 + 弥散环境散光阴影；无封面时显示极简 Discflow 矢量图标。点击封面可在右侧「双语歌词」与「音轨队列」间切换。
- **右侧双语歌词 / 队列**：
  - 支持网易云 `yrc` 逐字时间轴，普通 LRC 由前端按行时长自动合成逐字高亮；
  - 由硬件声卡播放位置 (`waveOutGetPosition`) + 前端 `requestAnimationFrame` 自走时钟柔和纠偏驱动；
  - 队列页支持顺序/随机/单曲模式切换、手动「专辑匹配」与逐轨「词」手动锁定匹配（标记未命中曲目 `.needs-lyric`）。
- **背景渲染**：4 层 `.ambient-stage`（`#artBackdrop` 模糊铺底 + `#meshCanvas` 三团取色流体光斑 + `.ambient-vignette` + `.frosted-scrim`）。

### 3.3 本地资料库视图 (`#viewLibrary`)
- **4 列黑胶卡片货架**：雾面玻璃卡片、悬停浮起与黑胶滑出动效，内置行内关键词实时搜索、本地专辑元数据编辑器、手动更换封面、单张物理删除（先停播释放句柄再清理目录）及 ZIP 整库导出/导入。
- **无损抓轨 (`#ripModal`)**：自适应 `64 -> 48 -> 32 -> 27 -> 16` 扇区批量抓取（必要时单扇区容错降级），抓轨前自动停播避免总线争抢。

### 3.4 底部常驻微播控 Dock (`.bottom-dock-player`)
- 采用三列对称 CSS Grid 布局（宽度 `480px ~ 1080px` 可调，`<= 580px` 自动启用紧凑模式 `.is-compact`）：
  - **左区**：专辑缩略图、曲名与艺术家（点击切换歌词/队列）；
  - **中区**：上一曲、播放/暂停、下一曲及可拖拽寻道进度条；
  - **右区**：双语译文开关 (`#dockTransBtn`，根节点 `.no-translation` 控制) 与竖向悬浮音量胶囊。

### 3.5 macOS 分栏式偏好设置 (`.settings-modal-dialog`)
- 固定 `700px × 540px` 左侧边栏分栏导航，划分为 **外观与界面 / 播放与硬件 / 存储与数据 / 关于与更新** 四个面板，支持深浅模式统一毛玻璃质感及滑块拖拽透明预览 (`is-peeking`)：
  - **外观**：浅色/深色主题切换、歌词行间距/字间距、底栏宽度/透明度 (`0%~100%`) /模糊度；
  - **播放**：放入光盘自动播放开关、关闭窗口最小化到系统托盘开关 (`closeToTray`)、全局多媒体按键 (`WH_KEYBOARD_LL`) 与键盘快捷键（`Space`、`←/→`、`↑/↓`、`T`、`[/]`、`\`、`Ctrl+F`、`Esc`）；
  - **存储**：动态显示便携资料库路径并一键打开目录、清空歌词缓存、红区二次确认清空资料库、ZIP 备份/恢复；
  - **关于与更新**：当前版本与硬件引擎信息、手动/启动自动检查 GitHub Releases 更新（支持忽略指定版本与跳转下载页）、快捷键速查与「支持作者」赞赏弹窗。

### 3.6 系统托盘与会话记忆
- **WinForms 托盘 (`NotifyIcon`)**：支持关闭窗口驻留托盘后台播放，右键菜单显示当前曲目与播放/暂停/切歌/退出控制。
- **启动现场恢复 (`lastSession`)**：无物理光盘时自动静默恢复上次播放的本地专辑、曲目索引与音量，按空格即可起播。
- **窗口几何记忆 (`windowBounds`)**：持久化并恢复窗口位置、宽高与最大化状态。

---

## 4. 数据存储与安全规范

1. **便携优先路径 (`resolve_library_dir`)**：
   - 优先顺序：`DISCFLOW_LIBRARY` 环境变量 > 测试运行器沙箱 (`tempfile.mkdtemp`) > 可执行文件/工程根目录下的 `library/` > `E:\Discflow\library` > `~/.discflow/library`。
2. **配置文件 (`preferences.json`)**：与生效的 `library/` 同级存放，持久化外观偏好、音量、播放现场、歌词偏移量、托盘设置、窗口位置及已忽略更新版本号。
3. **测试防误删护栏**：`is_under_test_runner()` 命中 `unittest` / `pytest` 时强制重定向到临时沙箱目录，严禁测试改动真实用户资料库。

---

## 5. 测试与发布验证

- **语法校验**：`python -m py_compile` 与 `node --check win-cd-player/ui/app.js`
- **自动化测试套件 (`win-cd-player/`)**：
  - `test_all_stages.py`：音频引擎扇区安全线、布局、元信息编辑、ZIP 备份恢复；
  - `test_ambient_and_opacity.py`：浅色模式氛围与底栏 0% 透明度；
  - `test_apple_music_layout.py`：核心视图与交互函数完整性；
  - `test_boost_features.py`：Seek 代数防竞争、CD-TEXT 双字节解码、全局媒体键、会话恢复与偏移继承；
  - `test_dock_symmetry.py`：底栏三段式对称布局与窄屏适配。
- **打包同步**：在 `win-cd-player/` 执行 `pyinstaller --noconfirm Discflow.spec`，并将 `dist\Discflow.exe` 同步至 `F:\Discflow1.0\Discflow.exe` 与 `E:\Discflow\Discflow.exe`。

---

## 6. 版本演进摘要

| 版本 | 里程碑内容 |
|---|---|
| `v1.0 ~ v1.4` | 完成 CD-DA 原生直读、WAV 无损抓轨与四列资料库，并从侧边栏/全屏拟物舞台收敛为双栏封面+歌词+悬浮底栏极简架构 |
| `v1.5 ~ v1.10` | 加入底栏进度/竖向音量气泡、视觉自定义参数与 `preferences.json` 磁盘持久化、全局搜索、0% 透明底栏与便携优先目录解析 |
| `v1.11.x` | 实现自适应多扇区抓轨提速、测试沙箱防误删护栏、`yrc` 逐字卡拉 OK 歌词、三源专辑/曲目手动匹配与乱码治理、Apple Music 四层环境背景 |
| `v2.0.x` | 清理历史冗余文件与死代码、三段式对称 Dock、SF Symbols 矢量图标重绘、一体化设置中心与「支持作者」弹窗 |
| `v2.1.0` | 内存预读无缝跨轨播放、CD-TEXT 硬件直读、Win32 全局多媒体按键、声卡硬件位置歌词精准同步、快捷键微调歌词时间轴与会话静默恢复 |
| `v2.2.0` | 前端 `ui/` 拆分独立、无边框 DWM 圆角窗体、macOS 分栏设置与滑块透视预览、浅色模式全弹窗磨砂统一、WinForms 系统托盘、窗口状态记忆、GitHub 更新检查与后端事件推送 |
