@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 尚未安装项目环境，请先双击“安装环境.bat”。
  pause
  exit /b 1
)

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo 已创建 .env，请填写 API Key 后重新启动。
  pause
  exit /b 1
)

echo [JOTO GEO Content Hub] 正在启动...
echo 网站地址：http://127.0.0.1:8765/
start "" http://127.0.0.1:8765/
".venv\Scripts\python.exe" web_app.py

echo.
echo 网站服务已停止。
pause
