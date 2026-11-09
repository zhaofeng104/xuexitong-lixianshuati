@echo off
chcp 65001 >nul
title 学习通刷课助手
cd /d "%~dp0"

if not exist "_runtime\pythonw.exe" (
    echo [错误] 运行环境缺失，请重新解压完整安装包。
    pause
    exit /b 1
)

start "" "_runtime\pythonw.exe" "app\ui.py"
exit /b 0
