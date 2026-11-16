# Discflow 项目协作规范

- **每次修改必提交本地 Git**：每一轮代码修改并通过验证/测试后，交付前必须同步执行一次本地 `git commit`（仅提交相关源码、测试与配置修改，不含 `.exe`、`build/`、`dist/` 或用户个人数据），方便随时按轮次精准回退。
- **打包同步路径**：构建完成后需同步更新 `F:\Discflow1.0\Discflow.exe` 与 `E:\Discflow\Discflow.exe`（仅本地替换测试，未获明确授权前不推送 GitHub Release）。
