@echo off
setlocal
REM ===================================================================
REM  s4extract GUI launcher (Windows). Double-click to open the window.
REM  Auto-installs numpy + Pillow into the same Python on first run.
REM ===================================================================
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python was not found on this PC.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

%PYEXE% -c "import numpy, PIL, trimesh, scipy, vhacdx" 1>nul 2>nul
if errorlevel 1 (
    echo [setup] Installing required packages numpy and Pillow ...
    %PYEXE% -m pip install --upgrade pip 1>nul 2>nul
    %PYEXE% -m pip install -r "%~dp0requirements.txt"
)

%PYEXE% -m s4extract.gui
if errorlevel 1 pause
endlocal
