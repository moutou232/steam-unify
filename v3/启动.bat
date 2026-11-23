@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 Steam 统译 (Steam Unify)...
python run.py
if errorlevel 1 (
    echo.
    echo 启动失败。请确认已安装 Python 3.10+，或在该目录执行：
    echo     python run.py
    pause
)
