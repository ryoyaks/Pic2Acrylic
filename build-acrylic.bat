@echo off
setlocal enabledelayedexpansion
REM Acrylic standee preview - one-click pipeline.
REM Usage: drag a folder of parts onto this .bat, or: build-acrylic.bat <parts_dir>

if "%~1"=="" (
  echo Drag a folder of parts onto this file, or run: build-acrylic.bat ^<parts_dir^>
  pause
  exit /b 1
)
set "SRC=%~1"
if not exist "%SRC%\" (
  echo ERROR: not a folder: %SRC%
  pause
  exit /b 1
)
set "PREP=%SRC%_prep"
set "HERE=%~dp0"

REM --- find Python -----------------------------------------------------------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo ERROR: Python not found. Install Python 3 and re-run.
  pause
  exit /b 1
)
echo Using Python: %PY%

REM --- ensure stage 1 deps ---------------------------------------------------
%PY% -c "import cv2, PIL, numpy" >nul 2>nul
if errorlevel 1 (
  echo Installing stage 1 dependencies...
  %PY% -m pip install opencv-python-headless pillow numpy
  if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
  )
)

REM --- stage 1: trace masks --------------------------------------------------
echo.
echo === Stage 1: prep_masks ===
%PY% "%HERE%prep_masks.py" "%SRC%" -o "%PREP%"
if errorlevel 1 (
  echo ERROR: prep_masks failed.
  pause
  exit /b 1
)

REM --- find Blender ----------------------------------------------------------
set "BLENDER="
if defined BLENDER_PATH if exist "%BLENDER_PATH%" set "BLENDER=%BLENDER_PATH%"
if not defined BLENDER (
  for /f "delims=" %%B in ('where blender 2^>nul') do if not defined BLENDER set "BLENDER=%%B"
)
if not defined BLENDER (
  for /f "delims=" %%B in ('dir /b /s "%ProgramFiles%\Blender Foundation\*\blender.exe" 2^>nul') do if not defined BLENDER set "BLENDER=%%B"
)
if not defined BLENDER (
  for /f "delims=" %%B in ('dir /b /s "%ProgramFiles(x86)%\Steam\steamapps\common\Blender\blender.exe" "%ProgramFiles%\Steam\steamapps\common\Blender\blender.exe" 2^>nul') do if not defined BLENDER set "BLENDER=%%B"
)
if not defined BLENDER (
  echo.
  echo Stage 1 done. Output: %PREP%
  echo Blender not found. Set BLENDER_PATH to your blender.exe and re-run, e.g.:
  echo   set "BLENDER_PATH=D:\path\to\blender.exe"
  pause
  exit /b 0
)
echo Using Blender: %BLENDER%

REM --- stage 2: build acrylic .blend ----------------------------------------
echo.
echo === Stage 2: build_acrylic ===
"%BLENDER%" --python "%HERE%build_acrylic.py" -- "%PREP%\manifest.json" "%PREP%\acrylic.blend"
if errorlevel 1 (
  echo ERROR: build_acrylic failed.
  pause
  exit /b 1
)

echo.
echo Done. Open: %PREP%\acrylic.blend
pause
endlocal
