# Discflow 底部微播控 Dock 栏对称优化设计文档

**版本**：v2.0.1  
**状态**：已批准 (Approved by User)  
**设计目标**：解决底部常驻微播控 Dock 栏（`.bottom-dock-player`）中控偏右（~79px 偏移）与两端失衡问题，实现 100% 物理几何对称。

---

## 1. 意图与背景 (Intent & Why)

原设计中，底栏采用 Flex 布局（`justify-content: space-between`），左侧 `.dock-album-cluster` 固定基准宽度 `236px`，而右侧 `.dock-right-actions` 仅包含 2 个图标且宽度自适应（约 `78px`）。这导致中间核心播控区（上一曲/播放暂停/下一曲、进度条）被向右侧推挤约 79px，未处于底栏胶囊的中轴正中心，产生明显的偏心感。

用户在比较三种高保真视觉渲染方案后，明确选定 **【样式一：现代三段式 Grid 强居中】**。

---

## 2. 目标与非目标 (Goals & Non-Goals)

### 目标 (Goals)
1. **几何绝对居中**：中间播控集群（上一曲/播放暂停/下一曲 + 进度条）在整个底栏胶囊中 100% 水平对准中轴线（50% 位置），无论窗口尺寸或左右文本长短如何变化均不偏移。
2. **两翼对称约束**：左侧曲目信息区与右侧动作区采用对称的最大宽度（220px）和边界对齐方式（左翼靠左，右翼靠右）。
3. **保持现有交互功能**：保持现有功能按键（双语翻译开关、竖向音量控制）完全不变。
4. **测试与文档闭环**：全量测试套件 100% 通过，同步更新 `PRD.md`（追加 v2.0.1 章节）。

### 非目标 (Non-Goals)
- 不新增或移除底栏可见按钮（维持现有翻译 + 音量双按键）。
- 不破坏底栏的毛玻璃、动态圆角胶囊、深浅主题切换与透明度设置。

---

## 3. 备选方案与决策 (Approaches Considered)

1. **方案一（已采纳）：现代三段式 CSS Grid 强居中**
   - 采用 `display: grid; grid-template-columns: minmax(160px, 220px) 1fr minmax(160px, 220px);`。
   - 左侧 `justify-self: start;`，中控 `justify-self: center;`，右侧 `justify-self: end;`。
   - 优势：物理中轴绝对对称，不依赖两端内容长度，改动轻量安全。
2. **方案二：增加右侧功能图标（播放模式 + 歌单抽屉）**
   - 增加按键虽然使右侧视觉密度更饱满，但增加了非必要按键变更。用户选择保持精简。
3. **方案三：左右等宽镜像 Flex 占位**
   - 在 Flex 下模拟对称，但当文本溢出或缩小窗口时容易产生意外留白，不如 Grid 规范稳健。

---

## 4. 技术实施细节 (Implementation Details)

### 4.1 CSS 样式更新 (`main.py`)
```css
.bottom-dock-player {
  /* 保留原有定位、尺寸、圆角、毛玻璃与阴影 */
  display: grid;
  grid-template-columns: minmax(160px, 220px) 1fr minmax(160px, 220px);
  align-items: center;
  gap: 16px;
  padding: 0 24px;
}

.dock-album-cluster {
  justify-self: start;
  width: 100%;
  max-width: 220px;
  min-width: 132px;
  flex: 0 1 220px;
}

.dock-center-cluster {
  justify-self: center;
  width: 100%;
  max-width: 380px;
  min-width: 180px;
}

.dock-right-actions {
  justify-self: end;
  width: 100%;
  max-width: 220px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
  flex: 0 0 auto;
}
```

### 4.2 测试与回归
- 确保已有的 163 项测试不受破坏。
- 新增/完善对底栏对称三段式结构特性的断言验证。
