@echo off
chcp 65001 >nul
title 停止 Discflow CD 音乐播放器
cd /d "%~dp0"

echo ===================================================
echo           正在安全退出 Discflow CD 音乐播放器...
echo ===================================================

taskkill /F /IM Discflow.exe /T >nul 2>&1
taskkill /F /IM CD音乐播放器.exe /T >nul 2>&1

echo.
echo [完成] 所有 Discflow 播放器进程已安全关闭与释放。
timeout /t 2 >nul
exit /b 0
