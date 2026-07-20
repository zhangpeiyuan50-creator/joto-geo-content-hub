@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 尚未安装项目环境，请先双击“安装环境.bat”。
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$existing=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if(-not $existing){ Start-Process -FilePath '%~dp0.venv\Scripts\python.exe' -ArgumentList 'web_app.py' -WorkingDirectory '%~dp0' -WindowStyle Hidden }"
timeout /t 2 >nul
start "" http://127.0.0.1:8765/
echo 网站已在后台启动：http://127.0.0.1:8765/
