# Discflow Design System & Taste Guide (`DESIGN.md`)

> 本文档沉淀了 Discflow 桌面端 CD / 本地黑胶音乐播放器的核心设计品味、视觉材质规范、动效准则与不可突破的布局红线。任何后续 UI 或动效修改均须严格遵守本规范。

---

## 1. 核心设计哲学与不可突破的红线

1. **零依赖原生美学**：坚持单文件原生 HTML / CSS / JavaScript + `pywebview` (WebView2) 架构，严禁引入 React、Tailwind CDN 或第三方 UI 组件库。
2. **禁止破坏既有 UI 布局**：
   - **底部悬浮 Dock 栏 (`.bottom-dock-player`)**：严格维持 `720px`（`--dock-custom-width`）三列对称 Grid 布局（左翼曲目信息 `minmax(160px, 236px)`、中控播控 `1fr`、右翼操作 `minmax(160px, 236px)`），水平绝对居中（`left: 50%; transform: translateX(-50%)`），禁止增删可见按钮或破坏三段对称。
   - **系统设置偏好中心 (`.settings-modal-dialog`)**：严格维持 `680px × 590px` 物理尺寸与四分类结构（外观 / 播放 / 存储 / 关于）。
   - **零界面污染**：优先通过键盘快捷键（如 `[`、`]`、`\` 微调歌词偏移，空格控制播放）承接高级微调能力，不在主视口堆砌额外按钮。

---

## 2. 光学材质与 Liquid Glass 规范

Discflow 采用“克制微距折射 + 晶莹高光内描边”的原生 Liquid Glass 方案，兼顾暗色与浅色模式：

- **SVG 原生光学着色器（GPU 加速）**：
  - 大型容器（Dock 栏、音量弹窗、模态对话框）：`url(#discflow-liquid-lens)`（`baseFrequency="0.015"`, `scale="3.5"`）配合 `blur(40px) saturate(195%)`。
  - 微型圆形按钮（顶部悬浮图标）：`url(#discflow-liquid-droplet)`（`baseFrequency="0.035"`, `scale="2.2"`）配合 `blur(28px) saturate(190%)`。
- **表面微光泽渐变 (`--dock-liquid-sheen`)**：135° 双峰半透明微光反射层；当用户将底栏透明度调至 `0%`（`.is-fully-transparent`）时，自动移除 `background-image`，保证纯净透底。
- **双层 Specular Rim 内描边**：顶部冷白微高光（暗色 `rgba(255,255,255,0.32)` / 浅色 `rgba(255,255,255,0.95)`）+ 底部暗影收边，赋予玻璃载体物理厚度。

---

## 3. 动效准则与物理手感规范

1. **禁止使用 `transition: all`**：所有交互控件必须显式声明需要过渡的属性（如 `transform`、`opacity`、`background-color`、`color`、`border-color`、`box-shadow`），避免 WebView2 监听无关属性造成性能损耗。
2. **曲线分工明确**：
   - `--spring-fluid: cubic-bezier(0.22, 1, 0.36, 1)`：用于面板展开、弹出菜单（`.discflow-popover-menu`）、音量卡片（`.dock-volume-popover`）、模态框（`.modal-dialog`）及高频按钮按压，**干净利落、无过冲回弹**。
   - `--spring-bounce: cubic-bezier(0.34, 1.56, 0.64, 1)`：仅克制用于专辑卡片悬浮、顶部图标微弹等局部趣味反馈，绝不用于功能型下拉菜单。
3. **锚定变换原点 (`transform-origin`)**：
   - `.discflow-popover-menu`：`transform-origin: top left`（从左上角图标按钮自然展开）。
   - `.dock-volume-popover.vertical`：`transform-origin: bottom center`（从底栏音量图标向上展开）。
   - `.modal-dialog`：从 `scale(0.96)` 优雅入场至 `scale(1)`，禁止从过小比例（如 `0.85` / `0.90`）突兀放大。
4. **高频按键即时按压反馈 (`:active`)**：播控按钮、胶囊切换按钮、设置 Tab、静音图标均配备 `scale(0.92)` ~ `scale(0.97)` 阻尼反馈。
5. **无障碍降级 (`prefers-reduced-motion: reduce`)**：系统开启减弱动态效果时，自动关闭背景流体飘动并把过渡时长降至 `0.01ms`，同时完整保留界面可用性。

---

## 4. 极端文本与边界防溢出准则 (`break-ui`)

- 所有 Flex / Grid 文本容器（`.player-column`、`.right-stage`、`.vinyl-card`、`.lyrics-viewport`、`.dock-track-meta`、`.track-row-info`）均必须保留 `min-width: 0`，防止超长无空格英文、日文或 Emoji 撑爆网格列宽。
- 单行信息（底栏歌名/歌手、资料库卡片标题）统一使用 `white-space: nowrap; overflow: hidden; text-overflow: ellipsis;`。
- 多行歌词（`.lyric-orig` / `.lyric-trans`）使用 `word-break: break-word;`，杜绝横向滚动条。
