@echo off
setlocal
REM Launch the local acrylic-standee web UI.
set "HERE=%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo ERROR: Python not found. Install Python 3 and re-run.
  pause
  exit /b 1
)

%PY% -c "import flask, cv2, PIL, numpy" >nul 2>nul
if errorlevel 1 (
  echo Installing dependencies...
  %PY% -m pip install flask opencv-python-headless pillow numpy
  if errorlevel 1 (
    echo ERROR: pip install failed.
    pause
    exit /b 1
  )
)

start "" http://127.0.0.1:5000
%PY% "%HERE%web_ui.py"
pause
endlocal
