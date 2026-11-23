# Discflow 项目协作规范

- **每次修改必提交本地 Git**：每一轮代码修改并通过验证/测试后，交付前必须同步执行一次本地 `git commit`（仅提交相关源码、测试与配置修改，不含 `.exe`、`build/`、`dist/` 或用户个人数据），方便随时按轮次精准回退。
- **打包同步路径**：构建完成后需同步更新 `F:\Discflow1.0\Discflow.exe` 与 `E:\Discflow\Discflow.exe`（仅本地替换测试，未获明确授权前不推送 GitHub Release）。
- **测试沙箱与资料库保护**：任何测试必须命中 `is_under_test_runner()` 隔离到临时目录，严禁读写真实 `library/`；任何涉及音频转码（WAV→FLAC）或合碟的操作，必须先校验新文件采样帧数可读并原子写入 `album.json`，确认无误后再删旧文件。
- **光驱与音频硬件互斥**：抓轨或切换音频输出引擎前必须先 `player.stop()` + `wait_reader_idle()`，抓轨期间暂停光驱热拔插巡检；不可双线程同时发送光驱 IOCTL；切 WASAPI 独占前须先释放 `waveOut` 句柄并具备自动回退兜底。
- **WebView2 首帧上限与防黑屏**：`main()` 注入首帧 `init_state` 时严禁直接内联 > 64 KB 的封面 Base64（必须经 `_compact_initial_state_for_html()` 降为缩略图并置 `metaVersion = -1` 异步拉取原图）；窗口高 DPI 坐标统一按 96-DPI 逻辑像素持久化，全屏沉浸态禁止覆写 `windowBounds`。
- **前端首帧无 TDZ & 状态守卫**：`ui/app.js` 顶部同步执行 `loadCustomPrefs()` 及其下游 UI 同步函数时，严禁读取后方 `let` / `const` 声明的变量；状态增量同步须遵守 `metaVersion` / `lyricsVersion` 单调递增，局部手动调节（如歌词偏移）需加防轮询覆写保护期，且键盘事件 `keydown` 头部须将 `Ctrl+F → openSearchModal()` 保持在前 400 字符窗口内以兼容回归断言。
- **JS 桥接门面隔离 & 渲染性能**：严禁将含 `Window.native` COM 对象或音频引擎实例的 Controller 直接暴露给 `pywebview`，必须通过纯方法门面 `DiscflowJsApi` 转发；禁止在动态容器上使用 `backdrop-filter: url(#svg)` 等 CPU 降级滤镜。
- **Liquid Glass UI 与文案一致性**：深/浅色模式所有弹窗、设置侧栏与底栏浮层统一对齐 `.discflow-popover-menu` 的通透玻璃配方与极轻遮罩；遵循五级同心圆角 Token（28/20/12/8/4px）；设置页 2~3 项互斥选项一律用磨砂分段控制器（`.settings-segmented`）替代原生 `<select>`，并保持单行简明文案。
