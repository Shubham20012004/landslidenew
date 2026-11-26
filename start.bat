@echo off
setlocal
rem Starts the backend, the live stream and the dashboard in one process.
rem   start.bat          normal operation (only models trained on real data are used)
rem   start.bat demo     demonstration mode (allows the SYNTHETIC demonstration model)
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run setup.bat first.
  exit /b 1
)
if /i "%~1"=="demo" (
  set LANDSLIDE_DEMO_MODE=true
  echo DEMONSTRATION MODE: predictions may come from a model trained on synthetic data.
)
".venv\Scripts\python.exe" -m backend.run
endlocal
