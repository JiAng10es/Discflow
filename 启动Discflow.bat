@echo off
chcp 65001 >nul
title 启动 Discflow CD 音乐播放器
cd /d "%~dp0"

echo ===================================================
echo           正在启动 Discflow CD 音乐播放器...
echo ===================================================

if exist "Discflow.exe" (
    start "" "Discflow.exe"
    echo [成功] 已在后台启动 Discflow.exe
) else if exist "CD音乐播放器.exe" (
    start "" "CD音乐播放器.exe"
    echo [成功] 已在后台启动 CD音乐播放器.exe
) else if exist "win-cd-player\dist\Discflow.exe" (
    start "" "win-cd-player\dist\Discflow.exe"
    echo [成功] 已在后台启动 win-cd-player\dist\Discflow.exe
) else (
    echo [错误] 未找到 Discflow.exe 可执行文件！
    pause
    exit /b 1
)

echo.
echo 播放器已进入运行状态，您可以尽情欣赏黑胶音乐。
timeout /t 3 >nul
exit /b 0
