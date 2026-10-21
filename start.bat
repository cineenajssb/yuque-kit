@echo off
rem ===========================================================
rem  Yuque Portable Kit - launcher
rem  Double click  ->  start + wait for login
rem  With args     ->  forwarded to yuque.py
rem  Content is ASCII only on purpose (codepage-safe).
rem ===========================================================
setlocal
chcp 65001 >nul 2>nul
cd /d "%~dp0"

set "ARGS=%*"
if "%~1"=="" set "ARGS=start --wait 300"
set "INTERACTIVE="
if "%~1"=="" set "INTERACTIVE=1"

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" "%~dp0yuque.py" %ARGS%
    goto :done
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0yuque.py" %ARGS%
    if not errorlevel 1 goto :done
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%~dp0yuque.py" %ARGS%
    if not errorlevel 1 goto :done
)

echo.
echo [X] Python 3.8+ not found on this computer.
echo.
echo   Option 1: https://www.python.org/downloads/  (check "Add python.exe to PATH")
echo   Option 2: winget install -e --id Python.Python.3.12
echo.
echo Run this file again after installing Python.

:done
if defined INTERACTIVE (
    echo.
    pause
)
endlocal
