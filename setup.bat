@echo off
setlocal
rem One-time setup: virtual environment, dependencies, configuration, database.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
  exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer is required.
  python --version
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/5] Creating virtual environment .venv
  python -m venv .venv || (echo ERROR: could not create .venv & exit /b 1)
) else (
  echo [1/5] Using existing virtual environment .venv
)

echo [2/5] Installing dependencies
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (echo ERROR: dependency installation failed & exit /b 1)

echo [3/5] Configuration (.env, device and admin tokens)
".venv\Scripts\python.exe" scripts\configure.py || exit /b 1

echo [4/5] Database migration (a backup is written first if data exists)
".venv\Scripts\python.exe" -m backend.manage migrate || exit /b 1

echo [5/5] Checking configuration and model
".venv\Scripts\python.exe" -m backend.manage check

echo.
echo Setup complete. Start the system with:  start.bat        (or  start.bat demo  for the synthetic demo model)
endlocal
