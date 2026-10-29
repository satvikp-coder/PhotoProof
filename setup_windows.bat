@echo off
setlocal
cd /d "%~dp0"

echo [PhotoProof] Creating virtual environment...
py -m venv .venv
if errorlevel 1 goto :error

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements-dev.txt
if errorlevel 1 goto :error
python -m pip install -e .
if errorlevel 1 goto :error

echo.
echo Setup complete. Double-click run_photoproof.bat to launch PhotoProof.
pause
exit /b 0

:error
echo.
echo Setup failed. Read the error above, then check that Python and pip are installed.
pause
exit /b 1
