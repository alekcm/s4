@echo off
setlocal enableextensions
REM Read COBJ Build/Buy category tags. Drag a .package onto this file.
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
if "%~1"=="" (
    echo Drag a .package file onto this diagnostic.
    pause
    exit /b 1
)

set "OUT=%~dp0diagnose_catalog_tags_output.txt"
%PYEXE% diagnose_catalog_tags.py "%~1" > "%OUT%" 2>&1
type "%OUT%"
echo.
echo Saved to: %OUT%
pause
