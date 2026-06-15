@echo off
setlocal
REM ===================================================================
REM  One-time setup: installs numpy + Pillow into your Python.
REM  Run this once if the auto-install in s4extract.bat failed.
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
echo.

%PYEXE% -m pip install --upgrade pip
%PYEXE% -m pip install -r "%~dp0requirements.txt"

echo.
echo Verifying ...
%PYEXE% -c "import numpy, PIL; print('OK: numpy', numpy.__version__, '| Pillow', PIL.__version__)"
if errorlevel 1 (
    echo.
    echo [ERROR] Installation could not be verified.
    echo Try running this file as Administrator, or install manually:
    echo     %PYEXE% -m pip install numpy Pillow
)
echo.
pause
endlocal
