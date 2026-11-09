@echo off
chcp 65001 >nul
title 学习通刷课助手（调试模式）
cd /d "%~dp0"

if not exist "_runtime\python.exe" (
    echo [错误] 运行环境缺失。
    pause
    exit /b 1
)

echo ============================================
echo  调试模式：窗口会保留错误信息
echo ============================================
echo.
"_runtime\python.exe" "app\ui.py"
echo.
echo [退出码 %errorlevel%]
pause
