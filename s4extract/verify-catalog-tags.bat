@echo off
setlocal enableextensions
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

set "OUT=%~dp0verify_catalog_tags_output.txt"
%PYEXE% verify_catalog_tag_install.py "%~1" > "%OUT%" 2>&1
type "%OUT%"
echo.
echo Saved to: %OUT%
pause
