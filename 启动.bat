@echo off
chcp 65001 >nul
title Global Finance Dashboard
cd /d "%~dp0"

set "PY="
where py >nul 2>nul
if %errorlevel%==0 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if %errorlevel%==0 set "PY=python"
)
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if not defined PY (
  echo.
  echo  [ERROR] Python not found. Please install Python 3 and add it to PATH.
  echo          未找到 Python，请先安装 Python 3 并加入 PATH。
  echo.
  pause
  exit /b 1
)

echo.
echo   Starting server with: %PY%
echo   URL: http://127.0.0.1:8770/
echo.

start "gfd-open" /min cmd /c "timeout /t 2 /nobreak >nul & start "" http://127.0.0.1:8770/"
%PY% "%~dp0server.py"
echo.
echo Server stopped. / 服务已停止。
pause
