@echo off
chcp 65001 >nul
title 学习通刷课助手 · 环境自检
cd /d "%~dp0"

if not exist "_runtime\python.exe" (
    echo [错误] 找不到 _runtime\python.exe
    echo        请确认压缩包已完整解压，没有被杀软删掉文件。
    pause
    exit /b 1
)

"_runtime\python.exe" "app\check_env.py"
echo.
pause
