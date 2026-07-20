@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 尚未安装项目环境，请先双击“安装环境.bat”。
  pause
  exit /b 1
)
echo 正在启动知乎发布辅助...
".venv\Scripts\python.exe" main.py publish zhihu
echo.
echo 发布辅助已结束，请查看上方提示。
pause
