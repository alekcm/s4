@echo off
setlocal enableextensions enabledelayedexpansion
REM ===================================================================
REM  Simple diagnostics for s4extract
REM ===================================================================
cd /d "%~dp0"

echo ========================================
echo  s4extract diagnostics
echo ========================================
echo.

echo [1] Check Python...
python --version
if errorlevel 1 (
    echo ERROR: Python not found or not working!
    pause
    exit /b 1
)
echo OK
echo.

echo [2] Check sys.path...
python -c "import sys; [print(p) for p in sys.path]"
echo.

echo [3] Check s4extract import...
python -c "import s4extract" 2>&1
if errorlevel 1 (
    echo ERROR: Cannot import s4extract!
    echo Make sure s4extract folder is next to this bat file.
    pause
    exit /b 1
)
echo OK
echo.

echo [4] Check cli module...
python -c "from s4extract.cli import main; print('cli OK')" 2>&1
if errorlevel 1 (
    echo ERROR: Cannot import cli!
    pause
    exit /b 1
)
echo OK
echo.

echo [5] Check extractor module...
python -c "from s4extract.extractor import Options; print('extractor OK')" 2>&1
if errorlevel 1 (
    echo ERROR: Cannot import extractor!
    pause
    exit /b 1
)
echo OK
echo.

echo ========================================
echo  All checks passed!
echo ========================================
echo.
echo If extraction still fails:
echo 1. Run test-s4extract.bat with your .package file
echo 2. Send me the s4extract_log.txt file
echo.
pause
endlocal