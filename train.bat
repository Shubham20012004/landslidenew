@echo off
setlocal
rem Reproducible Random Forest training.
rem   train.bat              real experimental data (labelled readings in the database); activates if valid
rem   train.bat synthetic    regenerate and train the SYNTHETIC demonstration model
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The virtual environment is missing. Run setup.bat first.
  exit /b 1
)
if /i "%~1"=="synthetic" (
  ".venv\Scripts\python.exe" -m ml.synthetic || exit /b 1
  ".venv\Scripts\python.exe" -m ml.train --source synthetic --activate
) else (
  ".venv\Scripts\python.exe" -m ml.train --source experimental --activate
)
endlocal
