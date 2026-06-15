@echo off
setlocal
REM ===================================================================
REM  s4extract CLI launcher (Windows)
REM  Usage:
REM    - Drag a .package file (or a folder) onto this .bat
REM    - Or run from a terminal:  s4extract.bat file.package
REM  On first run it auto-installs numpy + Pillow into the same Python.
REM ===================================================================
cd /d "%~dp0"

REM --- Pick a Python interpreter (prefer the py launcher) ---
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python was not found on this PC.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo IMPORTANT: tick "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Using Python:
%PYEXE% --version

REM --- Ensure dependencies (numpy, Pillow) are installed in THIS Python ---
%PYEXE% -c "import numpy, PIL, trimesh, scipy, vhacdx" 1>nul 2>nul
if errorlevel 1 (
    echo.
    echo [setup] Installing required packages numpy and Pillow ...
    %PYEXE% -m pip install --upgrade pip 1>nul 2>nul
    %PYEXE% -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo.
        echo [WARN] Could not install dependencies automatically.
        echo The tool will still extract meshes (.fbx/.obj^), but textures
        echo may be saved as .dds instead of .png until you run:
        echo     %PYEXE% -m pip install numpy Pillow
        echo.
    )
)

if "%~1"=="" (
    echo.
    echo No file given. Drag a .package file or a folder onto this .bat,
    echo or run:  s4extract.bat path\to\file.package
    echo.
    pause
    exit /b 0
)

REM --- Run the extractor on every argument passed (files/folders) ---
%PYEXE% -m s4extract %*

echo.
pause
endlocal
