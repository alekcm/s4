@echo off
setlocal enableextensions
REM Build compact AI catalog from the existing extracted folder.
cd /d "%~dp0"

set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo Python not found.
    pause
    exit /b 1
)

%PYEXE% -m s4extract --build-ai-catalog -o "extracted"
echo.
echo Created/updated: extracted\catalog_ai.json
echo Calibration setting: extracted\catalog_ai_settings.json
pause
