@echo off
setlocal
REM ===================================================================
REM  s4extract launcher for Unity HDRP projects.
REM  Drag a .package file (or a folder) onto this .bat.
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

echo Using Python:
%PYEXE% --version

%PYEXE% -c "import numpy, PIL, trimesh, scipy, vhacdx" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo [setup] Installing required packages ...
    %PYEXE% -m pip install --upgrade pip 1>nul 2>nul
    %PYEXE% -m pip install -r "%~dp0requirements.txt"
)

if "%~1"=="" (
    echo.
    echo No file given. Drag a .package file or a folder onto this .bat.
    echo This launcher creates HDRP/Lit materials.
    echo.
    pause
    exit /b 0
)

%PYEXE% -m s4extract %* --pipeline hdrp

echo.
pause
endlocal
