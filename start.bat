@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ==========================================
echo   AstrBot x SnowLuma 集成控制台  v0.1.1
echo ==========================================
echo.

set "PYEXE="

if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYEXE=%~dp0.venv\Scripts\python.exe"
  goto :have_python
)

python --version >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=python"
  goto :have_python
)

py -3 --version >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=py -3"
  goto :have_python
)

echo [错误] 没有找到 Python。请先安装 Python 3.10 或更高版本：
echo        https://www.python.org/downloads/windows/
echo        安装时请勾选 "Add python.exe to PATH"。
echo.
pause
exit /b 1

:have_python
echo [1/3] 使用 Python：%PYEXE%
%PYEXE% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)"
if errorlevel 1 (
  echo [错误] Python 版本过低，需要 3.10 或更高。
  pause
  exit /b 1
)

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [2/3] 首次运行，正在创建虚拟环境 .venv ...
  %PYEXE% -m venv "%~dp0.venv"
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
  echo       正在安装依赖（首次约 1-3 分钟）...
  "%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
  "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 (
    echo [警告] 使用清华镜像安装失败，改用官方源重试 ...
    "%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
      echo [错误] 依赖安装失败。
      pause
      exit /b 1
    )
  )
) else (
  echo [2/3] 虚拟环境已存在。
)

if not exist "%~dp0web\dist\index.html" (
  echo [提示] 未找到前端构建产物 web\dist。
  echo        控制台 API 仍可用，但页面打不开。请先执行：
  echo            cd web
  echo            npm install
  echo            npm run build
  echo.
)

echo [3/3] 启动控制台 ...
echo.
echo   浏览器访问：http://127.0.0.1:8765
echo   按 Ctrl+C 停止。
echo.

"%~dp0.venv\Scripts\python.exe" main.py --host 127.0.0.1 --port 8765
set "EXITCODE=%ERRORLEVEL%"

echo.
if not "%EXITCODE%"=="0" (
  echo [退出码 %EXITCODE%] 控制台异常退出，请查看上面的错误信息。
)
pause
exit /b %EXITCODE%
