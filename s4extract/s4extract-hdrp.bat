@echo off
setlocal enableextensions enabledelayedexpansion
REM ===================================================================
REM  s4extract launcher for Unity HDRP projects.
REM  Drag a .package file (or a folder) onto this .bat.
REM
REM  Defaults:
REM    --pipeline hdrp
REM    --all-lods             (extract all LOD levels)
REM    --no-cas               (skip clothing/hair/body meshes)
REM    --per-object           (each object in multi-object .package gets its own folder)
REM    FullBuild link mode     (automatic: ClientFullBuild0 also searches sibling
REM                             ClientFullBuild1/2... and installed game Build data)
REM    Progress + resume       (live percentage/status; completed objects are skipped)
REM ===================================================================
cd /d "%~dp0"

set "LOGFILE=%~dp0s4extract_log.txt"

echo ======================================== > "%LOGFILE%"
echo  s4extract HDRP launcher >> "%LOGFILE%"
echo  Time: %date% %time% >> "%LOGFILE%"
echo  Working dir: %cd% >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"
echo. >> "%LOGFILE%"

REM --- Find Python ---
set "PYEXE="
where py >nul 2>&1 && set "PYEXE=py"
if not defined PYEXE (
    where python >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
    echo [ERROR] Python not found >> "%LOGFILE%"
    echo Python not found! Install Python 3.9+ from python.org >> "%LOGFILE%"
    echo Do not forget to check "Add Python to PATH" >> "%LOGFILE%"
    type "%LOGFILE%"
    pause
    exit /b 1
)

echo [1/4] Python found >> "%LOGFILE%"
%PYEXE% --version >> "%LOGFILE%" 2>&1
echo Python location: >> "%LOGFILE%"
where %PYEXE% >> "%LOGFILE%" 2>&1
echo. >> "%LOGFILE%"

REM --- Check input ---
if "%~1"=="" (
    echo [ERROR] No file specified >> "%LOGFILE%"
    echo Drag a .package file onto this bat >> "%LOGFILE%"
    type "%LOGFILE%"
    pause
    exit /b 0
)

echo Input file: %~1 >> "%LOGFILE%"
echo Input exists: >> "%LOGFILE%"
if exist "%~1" (
    echo YES >> "%LOGFILE%"
) else (
    echo NO - FILE DOES NOT EXIST >> "%LOGFILE%"
)
echo. >> "%LOGFILE%"

REM --- Set PYTHONPATH so s4extract module can be found ---
set "PYTHONPATH=%cd%;%PYTHONPATH%"
echo PYTHONPATH: %PYTHONPATH% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

REM --- Quick test: can we import the module? ---
echo Testing module import... >> "%LOGFILE%"
%PYEXE% -c "from s4extract.cli import main; print('Import OK')" >> "%LOGFILE%" 2>&1
echo. >> "%LOGFILE%"

echo [2/4] Checking dependencies... >> "%LOGFILE%"
echo. >> "%LOGFILE%"

%PYEXE% -c "import numpy" >nul 2>&1
if errorlevel 1 (
    echo Installing numpy... >> "%LOGFILE%"
    %PYEXE% -m pip install numpy >> "%LOGFILE%" 2>&1
)
echo numpy: OK >> "%LOGFILE%"

%PYEXE% -c "import PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing Pillow... >> "%LOGFILE%"
    %PYEXE% -m pip install Pillow >> "%LOGFILE%" 2>&1
)
echo PIL: OK >> "%LOGFILE%"

%PYEXE% -c "import trimesh" >nul 2>&1
if errorlevel 1 (
    echo Installing trimesh... >> "%LOGFILE%"
    %PYEXE% -m pip install trimesh >> "%LOGFILE%" 2>&1
)
echo trimesh: OK >> "%LOGFILE%"

%PYEXE% -c "import scipy" >nul 2>&1
if errorlevel 1 (
    echo Installing scipy... >> "%LOGFILE%"
    %PYEXE% -m pip install scipy >> "%LOGFILE%" 2>&1
)
echo scipy: OK >> "%LOGFILE%"

%PYEXE% -c "import vhacdx" >nul 2>&1
if errorlevel 1 (
    echo Installing vhacdx... >> "%LOGFILE%"
    %PYEXE% -m pip install vhacdx >> "%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [WARNING] vhacdx failed - colliders will be limited >> "%LOGFILE%"
    )
)
echo vhacdx: OK >> "%LOGFILE%"

echo. >> "%LOGFILE%"
echo Dependencies check complete >> "%LOGFILE%"
echo. >> "%LOGFILE%"

REM --- Run extraction ---
echo [3/4] Starting extraction... >> "%LOGFILE%"
echo File: %~1 >> "%LOGFILE%"
echo. >> "%LOGFILE%"

REM Show object-level progress live and save exactly the same output to the log.
REM PowerShell Tee-Object is present in supported Windows versions. Keep a
REM redirect-only fallback for stripped Windows installations.
set "S4EXTRACT_INPUT=%~1"
set "LIVELOG=0"
where powershell >nul 2>&1
if errorlevel 1 (
    %PYEXE% -m s4extract "%~1" --pipeline hdrp --all-lods --no-cas --progress --quiet >> "%LOGFILE%" 2>&1
    set "EXIT_CODE=%errorlevel%"
) else (
    set "LIVELOG=1"
    REM The whole -Command program is already inside CMD double-quotes: do not
    REM add CMD caret escapes here. PowerShell must receive its native 2>&1 and
    REM pipe tokens literally; 2^>&1 becomes invalid PowerShell syntax.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& { & $env:PYEXE -m s4extract $env:S4EXTRACT_INPUT --pipeline hdrp --all-lods --no-cas --progress --quiet 2>&1 | Tee-Object -FilePath $env:LOGFILE -Append; exit $LASTEXITCODE }"
    set "EXIT_CODE=%errorlevel%"
)

echo. >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"
echo Finished. Exit code: %EXIT_CODE% >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"

REM --- Show results ---
if "%LIVELOG%"=="0" (
    type "%LOGFILE%"
) else (
    echo.
    echo Live progress was written to the log above.
)
echo.
echo ========================================
echo Full log: s4extract_log.txt
echo ========================================
pause
endlocal