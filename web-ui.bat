@echo off
setlocal
pushd "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo ERROR: uv is required. Install it from https://docs.astral.sh/uv/ and re-run.
  popd
  pause
  exit /b 1
)

echo Starting Pic2Acrylic (uv-managed environment)...
start "" http://127.0.0.1:5000
uv run python web_ui.py

popd
pause
endlocal
