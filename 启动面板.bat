@echo off
rem ===========================================================================
rem  Weighted (Open-Interest) Futures Monitor  -  launcher
rem
rem  This file is intentionally pure ASCII: cmd.exe parses .bat bytes with the
rem  console code page, so a UTF-8 Chinese .bat gets mangled and the commands
rem  break. All Chinese messages live in launch.py (Python handles UTF-8 fine).
rem
rem  Set PY_EXE beforehand to pin a specific interpreter, e.g.
rem      set PY_EXE=C:\Python314\python.exe
rem ===========================================================================

chcp 65001 >nul
title Weighted Futures Monitor
cd /d "%~dp0"

set "BOOT_PY="

if defined PY_EXE if exist "%PY_EXE%" set "BOOT_PY=%PY_EXE%"

if not defined BOOT_PY (
  for %%P in (
    "python"
    "python3"
    "py"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
    "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  ) do (
    if not defined BOOT_PY (
      %%~P -c "import sys" >nul 2>nul
      if not errorlevel 1 set "BOOT_PY=%%~P"
    )
  )
)

if not defined BOOT_PY (
  echo [ERROR] No Python found. Install Python 3.9+ and add it to PATH,
  echo         or set the PY_EXE environment variable to python.exe.
  pause
  exit /b 1
)

echo Boot interpreter: %BOOT_PY%
echo.

if exist "launch.py" (
  "%BOOT_PY%" launch.py %*
) else (
  echo [ERROR] launch.py not found next to this .bat.
  echo         Please keep all project files in the same folder and run the .bat from there.
  pause
  exit /b 1
)

if errorlevel 1 (
  echo.
  echo [HINT] If the panel reported that tqsdk is missing, install it into the
  echo        interpreter that actually runs the server:
  echo          "%BOOT_PY%" -m pip install tqsdk -i https://mirrors.aliyun.com/pypi/simple/
  pause
)
