# Discflow 产品需求文档 (PRD)

**当前基线版本**：`v2.3.1` (`APP_VERSION = "2.3.1"`，含 2026-11-17 专项修复与体验升级)  
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
├── main.py              # DiscflowController / DiscflowJsApi / 全局多媒体按键 / 应用内自更新 / DPI 逻辑窗口记忆
├── cd_engine.py         # Win32 光驱 TOC/CD-TEXT 直读 + 双缓冲内存预读与硬件时钟位置同步
├── library_service.py   # 便携资料库管理、自适应抓轨、偏好持久化 (preferences.json) 与测试沙箱隔离
├── metadata_service.py  # MusicBrainz / GNUDb / QQ音乐 / iTunes / 网易云 / LRCLIB 多源元数据与歌词服务
├── Discflow.spec        # PyInstaller 单文件打包配置 (内嵌 ui/ 与 discflow.ico)
└── ui/
    ├── index.html       # 视图骨架（含 SSR 主题/状态占位注入）与内联 SF Symbols / 重绘双语翻译 SVG 图标
    ├── styles.css       # 双主题样式 tokens、Liquid Glass 滤镜、防启动闪烁 (is-booting) 与分栏设置
    └── app.js           # 首帧水合、单调版本状态同步、自走卡拉 OK 时钟、媒体检索弹窗与交互逻辑
```

### 2.1 状态同步机制
- **首帧 SSR 水合**：`render_frontend_html(init_prefs, init_state)` 在窗口创建前直接将 `data-theme`、背景底色与 `window.__INITIAL_PREFS__` / `window.__INITIAL_STATE__` 注入 HTML，配合 `html.is-booting` 抑制首帧过渡动画，消除启动主题与渲染闪变；
- **增量同步与自愈**：后端通过 `poll_state(client_meta_ver, client_lyrics_ver, client_track_index)` 按版本号增量返回曲目、封面与歌词；关键状态变更通过 `_ui_push_worker()` 主动推送到 `window.__onBackendStatePush`，前端通过 `metaVersion`/`lyricsVersion` 单调守卫防止乱序回退，并在有碟无歌词时以 `client_lyrics_ver = -1` 触发自动补发。

---

## 3. 核心功能模块规范

### 3.1 无边框窗口与顶部悬浮栏
- **无边框窗体**：`frameless=True, easy_drag=False`，在 Windows 11 上启用原生 DWM 圆角 (`DwmSetWindowAttribute` attribute `33 = 2`)，支持 8 向边缘热区缩放 (`.win-resize-edge`) 与顶部拖拽区 (`.top-drag-spacer`)。
- **左上悬浮区**：Discflow 图标菜单（资料库、全局搜索、偏好设置）、读盘 (`#topRescanBtn`)、弹出 (`#topEjectBtn`)、三态状态灯（绿常亮就绪 / 黄闪烁读盘 / 灰无盘）。
- **右上悬浮区**：新版本提醒胶囊 (`#updateNoticePill`)、深浅主题切换 (`#topThemeBtn`) 与极简窗口三键 (`— □ ×`)。

### 3.2 主播放视图 (`#viewCd`)
- **左侧封面舞台**：1:1 专辑封面卡片 + 弥散环境散光阴影；无封面时显示极简 Discflow 矢量图标。点击封面可在右侧「双语歌词」与「音轨队列」间切换。
- **右侧双语歌词 / 队列**：
  - 支持网易云 `yrc` 逐字时间轴，普通 LRC 由前端按行时长自动合成逐字高亮，并对日文假名按音拍 (`Mora`) 合并拗音/促音/长音符、对片头 `作词 / 作曲 / 编曲` 等元信息行自动降噪显示 (`.is-credit-line`)；
  - 由硬件声卡播放位置 (`waveOutGetPosition`) + 前端 `requestAnimationFrame` 自走时钟柔和纠偏驱动；
  - 读盘后优先高优抓取当前音轨（如第 1 首）歌词，支持多歌手清洗 (`_primary_artist`) 与合辑逐轨艺人识别、网易云 → QQ 音乐 → LRCLIB 多源回退及 `yrc` 逐字补全 (`enrich_lyrics_with_word_timings`)、旧版归档缺字/缺译歌词后台自动解锁升级，以及空结果不覆盖已有有效缓存；
  - 队列页支持顺序/随机/单曲模式切换、手动「专辑匹配」（含网易云候选空曲目自动从同批次干净源借用曲目表与曲目级 ID）、队列头部一键「重刷歌词」强制全碟升级与逐轨「词」手动锁定匹配（标记未命中曲目 `.needs-lyric`）。
- **背景渲染**：4 层 `.ambient-stage`（`#artBackdrop` 模糊铺底 + `#meshCanvas` 三团取色流体光斑 + `.ambient-vignette` + `.frosted-scrim`）。

### 3.3 本地资料库视图 (`#viewLibrary`)
- **4 列黑胶卡片货架**：雾面玻璃卡片、悬停浮起与黑胶滑出动效，内置行内关键词实时搜索、本地专辑元数据编辑器、手动更换封面、单张物理删除（先停播释放句柄再清理目录）及 ZIP 整库导出/导入。
- **无损抓轨 (`#ripModal`)**：自适应 `64 -> 48 -> 32 -> 27 -> 16` 扇区批量抓取（必要时单扇区容错降级），抓轨前自动停播避免总线争抢。

### 3.4 底部常驻微播控 Dock (`.bottom-dock-player`)
- 采用三列对称 CSS Grid 布局（宽度 `480px ~ 1080px` 可调，`<= 580px` 自动启用紧凑模式 `.is-compact`）：
  - **左区**：专辑缩略图、曲名与艺术家（点击切换歌词/队列）；
  - **中区**：上一曲、播放/暂停、下一曲及可拖拽寻道进度条；
  - **右区**：重绘清晰「文 / A」双语译文开关 (`#dockTransBtn`，以根节点 `.no-translation` 与透明度状态区分开关，并与设置中心 `#prefTransSwitch` 同步) 及竖向悬浮音量胶囊。

### 3.5 macOS 分栏式偏好设置 (`.settings-modal-dialog`)
- 固定 `700px × 540px` 左侧边栏分栏导航，划分为 **外观与界面 / 播放与硬件 / 存储与数据 / 关于与更新** 四个面板，支持深浅模式统一毛玻璃质感及滑块拖拽透明预览 (`is-peeking`)：
  - **外观**：浅色/深色主题切换（`set_theme_mode` 同步刷新原生窗体 DWM 属性与底色）、歌词行间距/字间距、底栏宽度/透明度 (`0%~100%`) /模糊度；
  - **播放**：放入光盘自动播放开关、双语译文同步开关、全局多媒体按键 (`WH_KEYBOARD_LL`) 与键盘快捷键（`Space`、`←/→`、`↑/↓`、`T`、`[/]`、`\`、`Ctrl+F`、`Esc`）；点击关闭按钮直接退出并持久化状态（已移除系统托盘常驻）；
  - **存储**：动态显示便携资料库路径并一键打开目录、清空歌词缓存、红区二次确认清空资料库、ZIP 备份/恢复；
  - **关于与更新**：当前版本与硬件引擎信息、手动/启动自动检查 GitHub Releases 更新（支持点击「立即更新」在软件内流式下载 `Discflow.exe` 实时进度条展示、PE `MZ` 校验与静默批处理脚本覆盖重启，不再仅跳转浏览器）、忽略指定版本、快捷键速查与「支持作者」赞赏弹窗。

### 3.6 会话恢复与 DPI 逻辑窗口记忆
- **启动现场恢复 (`lastSession`)**：构造控制器阶段及无物理光盘时自动静默恢复上次播放的本地专辑、曲目索引与音量，按空格即可起播。
- **DPI 逻辑窗口几何记忆 (`windowBounds`)**：基于 `_enable_dpi_awareness()` 与 `_get_dpi_scale()` 以 96-DPI 逻辑像素（含 `"logical": true` 标记）记录并恢复窗口坐标、宽高与最大化状态，自动兼容迁移旧版物理像素坐标并钳制在虚拟桌面可见范围内，彻底解决高 DPI 缩放下的窗口逐次膨胀与坐标错位问题。

---

## 4. 数据存储与安全规范

1. **便携优先路径 (`resolve_library_dir`)**：
   - 优先顺序：`DISCFLOW_LIBRARY` 环境变量 > 测试运行器沙箱 (`tempfile.mkdtemp`) > 可执行文件/工程根目录下的 `library/` > `E:\Discflow\library` > `~/.discflow/library`。
2. **配置文件 (`preferences.json`)**：与生效的 `library/` 同级存放，持久化外观偏好、音量、播放现场、歌词偏移量、窗口逻辑边界及已忽略更新版本号（并自动清理历史 `closeToTray` 废弃键）。
3. **测试防误删护栏**：`is_under_test_runner()` 命中 `unittest` / `pytest` 时强制重定向到临时沙箱目录，严禁测试改动真实用户资料库。

---

## 5. 测试与发布验证

- **语法校验**：`python -m py_compile` 与 `node --check win-cd-player/ui/app.js`
- **自动化测试套件 (`win-cd-player/`)**：
  - `test_v230_fixes.py`：DPI 逻辑窗口记忆与迁移、启动主题 SSR 水合与首帧脚本防 TDZ 校验、系统托盘彻底移除、应用内更新流式下载与 PE 校验/自更新脚本、读盘首曲优先抓词与 QQ 回退、`yrc` 逐字补全、日文假名音拍分词与片头信息行降噪、网易云候选借用曲目补齐、旧版归档解锁升级与整碟强制重刷、底栏翻译图标重绘；
  - `test_all_stages.py`：音频引擎扇区安全线、布局、元信息编辑、ZIP 备份恢复；
  - `test_ambient_and_opacity.py`：浅色模式氛围与底栏 0% 透明度；
  - `test_apple_music_layout.py`：核心视图与交互函数完整性；
  - `test_boost_features.py`：Seek 代数防竞争、CD-TEXT 双字节解码、全局媒体键、会话恢复与偏移继承；
  - `test_dock_symmetry.py`：底栏三段式对称布局与窄屏适配；
  - `test_v111_features.py` / `test_v1112_clean_titles.py` / `test_portable_library_path.py`：逐字卡拉 OK 时钟、翻译开关、手动匹配、标题清洗与便携路径优先级。
- **打包同步**：通过根目录 `scripts/build_and_sync.ps1` 一键串联语法检查、全量回归、PRD 记录校验、PyInstaller 打包与双盘 `SHA256` 同步（`F:\Discflow1.0\Discflow.exe` 与 `E:\Discflow\Discflow.exe`）。
- **PRD 留痕规范**：每次功能迭代、Bug 修复或工程架构调整后，必须同步更新本 `PRD.md`（记录关键改动、影响范围与验证结论），再执行打包与本地 Git 提交。

---

## 6. 版本演进摘要

| 版本 | 里程碑内容 |
|---|---|
| `v1.0 ~ v1.4` | 完成 CD-DA 原生直读、WAV 无损抓轨与四列资料库，并从侧边栏/全屏拟物舞台收敛为双栏封面+歌词+悬浮底栏极简架构 |
| `v1.5 ~ v1.10` | 加入底栏进度/竖向音量气泡、视觉自定义参数与 `preferences.json` 磁盘持久化、全局搜索、0% 透明底栏与便携优先目录解析 |
| `v1.11.x` | 实现自适应多扇区抓轨提速、测试沙箱防误删护栏、`yrc` 逐字卡拉 OK 歌词、三源专辑/曲目手动匹配与乱码治理、Apple Music 四层环境背景 |
| `v2.0.x` | 清理历史冗余文件与死代码、三段式对称 Dock、SF Symbols 矢量图标重绘、一体化设置中心与「支持作者」弹窗 |
| `v2.1.0` | 内存预读无缝跨轨播放、CD-TEXT 硬件直读、Win32 全局多媒体按键、声卡硬件位置歌词精准同步、快捷键微调歌词时间轴与会话静默恢复 |
| `v2.2.0` | 前端 `ui/` 拆分独立、无边框 DWM 圆角窗体、macOS 分栏设置与滑块透视预览、浅色模式全弹窗磨砂统一、窗口状态记忆、GitHub 更新检查与后端事件推送 |
| `v2.2.0+ (2026-11-17 修复增强)` | 修复高 DPI 窗口坐标记忆膨胀、启动主题与背景闪烁、移除系统托盘、实现应用内流式下载与 PE 校验覆盖重启更新、修复读盘首曲漏词并新增 QQ 音乐兜底、重绘底栏「文 / A」翻译图标；移除 CPU 降级 SVG 滤镜提升帧率并确保 `yrc`/合成逐字扫词稳定生效；解锁旧归档缺词/缺字专辑自动升级与一键「重刷歌词」、补齐网易云专辑匹配空曲目列表、优化日文假名音拍分词与片头 `作词/作曲` 行视觉降噪 |
| `v2.3.2 (2026-11-19 正式发布)` | 1）按 `kubai-continuous-corners` 规范落地 `--radius-dialog/card/panel/control/chip` 五级同心圆角 Token（`28/20/12/8/4px` 对齐 4px 网格与 `r = R - p`）并加入 `@supports (corner-shape: superellipse(1.25))` 连续曲率增强（胶囊与正圆保持 `corner-shape: round`）；2）新增 512KB 自动轮转轻量运行日志 `discflow.log`；3）新增无头 Edge 真机冒烟测试并归档 16 个历史旧测试至 `tests_legacy/`，实现 172 项全量回归一键验证；4）新增 `scripts/build_and_sync.ps1` 一键脚本串联语法检查、回归、官网同步、PyInstaller 打包、双盘 SHA256 同步与可控发布 |
| `v2.3.2+ (2026-11-19 资料库播放流畅度与歌词高亮优化)` | 彻底移除资料库点击专辑 `460ms + 280ms` 两段串行延迟与重绘阴影动画，改为 `110ms` 轻盈按压反馈 + `180ms` 纯 GPU 视口淡入转场；新增 `applyOptimisticLocalAlbum()` 乐观 UI 预填消除切页旧封面闪烁；后端 `play_local_album()` 毫秒级切换内存状态并异步起播；`renderLibraryCards()` 列表签名增量复用与 `decoding="async"` 秒开不闪屏；换专辑后默认直达歌词栏（`lyrics`）而非曲目栏；移除当前歌词行背景框与过冲弹跳动效，改为整句淡柔散开光晕 + 纯线性过渡（`0.32s linear`） |
| `v2.3.2+ (2026-11-19 Phase 1 缩略图与自适应轮询)` | 新增 `generate_cover_thumbnail()` 与 `cover_thumb.jpg`（`320×320` 轻量 JPEG 缩略图），资料库与搜索列表仅传缩略图与轻量曲目摘要、播放时再加载完整高清原图与逐字歌词（历史旧专辑自动补生成）；前端 `syncBackendPollSchedule()` 按播放/读盘/空闲/隐藏动态调节轮询频率（最小化 0 次/秒，空闲降频 80%） |
| `v2.3.2+ (2026-11-19 Phase 2 后端模块化拆分)` | 将 Win32 原生 DPI/DWM 圆角主题/无边框拖拽缩放/全局多媒体热键监听从 `main.py` 剥离至 `win_native.py`，将 GitHub 更新探测、流式下载校验与自更新脚本抽离至 `updater_service.py`（保持 `main.py` 全部公共/测试接口 100% 兼容） |
| `v2.3.2+ (2026-11-19 Phase 3 FLAC 无损压缩与 WASAPI 引擎)` | 1) CD 抓轨默认改为 FLAC 无损压缩（`libsndfile` 流式写入 + `mutagen` 自动写入标准 Vorbis Comment 标签与内嵌高清封面），设置中支持切换回 WAV；2) 新增「旧 WAV 专辑无损转 FLAC」后台迁移工具（逐轨严格采样帧校验、原子更新 `album.json` 后安全清理旧 WAV）；3) 本地播放引擎无缝兼容 WAV/FLAC 直读，并新增可选音频输出模式（默认标准兼容 `waveOut`，可选 `WASAPI 共享` 与 `WASAPI 独占直通 Bit-Perfect`，异常时自动降级回退） |
| `v2.3.2+ (2026-11-19 设置面板视觉与文案精简)` | 将设置弹窗中的原生下拉菜单替换为 Apple 风格磨砂分段控制器（Segmented Control），双色适配深色/浅色主题；精简全设置面板冗长说明文案并移除底部多余提示块，提升各选项卡留白与视觉一致性 |
| `v2.3.2+ (2026-11-19 歌词对齐修复与专辑匹配稳定性增强)` | 1) 修复歌词对齐快捷键 `[` / `]` / `\` 轮询竞态覆写与实时时钟重绘问题，兼容中文输入法全角标点（`【` / `】` / `、`），并在底部控制栏新增毛玻璃「歌词对齐」微调浮层（`-0.5s` / 归零 / `+0.5s`）；2) 增强专辑匹配稳定性：iTunes 多地区并发检索、繁简汉字统一比对、引入 CD 逐轨时长吻合度打分排序及候选徽标展示，并支持 CD-TEXT/GNUDb 残缺元数据自动三源补全 |
| `v2.3.2+ (2026-11-19 启动黑屏紧急修复)` | 修复上次会话恢复高清大封面（>1MB Base64）时首帧 `NavigateToString` 超过 WebView2 字符串长度上限导致初始化失败黑屏的隐患：首帧超过 64KB 时自动切换为轻量缩略图占位并在首轮桥接同步拉取高清原图；同时修复切换音频输出引擎热重启时 `seek_seconds` 方法名笔误 |
| `v2.4.0 (2026-11-19 正式定版发布)` | 版本号正式升级至 `v2.4.0`，同步官网 `website/index.html` 下载链接与版本信息；打包发布单文件便携版（集成 FLAC 无损默认抓轨、WAV→FLAC 迁移工具、WASAPI 共享/独占直通引擎、封面缩略图秒开优化、歌词对齐底栏浮层及专辑逐轨时长精准匹配） |
| `v2.4.0+ (2026-11-19 深色模式烟熏玻璃质感优化)` | 将深色模式主底色从纯黑 `#000000` 调整为深空炭灰 `#111217` 搭配微光渐变；将所有弹窗、设置侧栏、菜单与底栏浮层统一对齐左上角图标菜单的通透玻璃配方（`rgba(44, 46, 56, 0.46)` + 高光斜向渐变 + `blur(40px) saturate(185%)`）并减弱全屏遮罩压暗，让背后封面氛围光充分透出 |
