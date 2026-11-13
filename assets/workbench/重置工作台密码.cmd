@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先完成工作台安装。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "scripts\reset-password.py"
