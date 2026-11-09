@echo off
chcp 65001 >nul
title 关闭 学习通刷课助手
setlocal
cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "_runtime\python.exe" (
    echo [错误] 找不到 _runtime\python.exe，安装包不完整。
    pause
    exit /b 1
)

rem 真正的逻辑在 app\close_app.py —— 用 Python 写是因为 bat 的路径匹配
rem 在中文目录下会出假阴性（明明有进程却报没有找到）。
"_runtime\python.exe" "app\close_app.py"

echo.
pause
exit /b %ERRORLEVEL%
