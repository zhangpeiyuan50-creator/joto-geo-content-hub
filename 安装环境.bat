@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo [JOTO GEO Content Hub] 正在检查 Python...
set "PY_EXE="
set "PY_ARGS="
where py >nul 2>nul
if not errorlevel 1 (
  set "PY_EXE=py"
  set "PY_ARGS=-3"
) 

if not defined PY_EXE (
  where python >nul 2>nul
  if not errorlevel 1 (
    set "PY_EXE=python"
  )
)

if not defined PY_EXE (
  for /f "delims=" %%P in ('powershell -NoProfile -Command "$p=Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python*\python.exe') -ErrorAction SilentlyContinue ^| Sort-Object FullName -Descending ^| Select-Object -First 1 -ExpandProperty FullName; if($p){$p}"') do set "PY_EXE=%%P"
)

if not defined PY_EXE (
  echo 未找到 Python。请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
  pause
  exit /b 1
)

"%PY_EXE%" %PY_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo Python 版本过低，请安装 Python 3.10 或更高版本。
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo 正在创建本地虚拟环境 .venv...
  "%PY_EXE%" %PY_ARGS% -m venv .venv
  if errorlevel 1 goto :failed
)

echo 正在安装 Python 依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo 正在安装发布辅助浏览器...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto :failed

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
  echo 已创建 .env，请打开并填写 Dify 与 Unsplash Key。
) else (
  echo 已保留现有 .env，不会覆盖本机密钥。
)

if not exist "data" mkdir "data"
if not exist "outputs" mkdir "outputs"

echo.
echo 安装完成。填写 .env 后，双击“启动网站.bat”。
pause
exit /b 0

:failed
echo.
echo 安装失败，请保留本窗口并查看上方错误信息。
pause
exit /b 1
