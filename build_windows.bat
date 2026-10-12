@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Building PhotoOrganizer.exe (one-time)
echo ============================================
echo.

REM Find a working Python. Try the "py" launcher first (installed by the
REM python.org installer even when PATH wasn't set), then "python".
REM The --version check also rejects the Microsoft Store placeholder
REM "python.exe" that exists on some PCs but doesn't actually run Python.
set "PY="
py --version >nul 2>nul && set "PY=py"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython

echo Using Python:
%PY% --version
echo.

%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :error

%PY% -m pip install pyinstaller
if errorlevel 1 goto :error

echo.
echo Building the standalone exe - this can take a few minutes...
%PY% -m PyInstaller --name PhotoOrganizer --windowed --onefile main.py
if errorlevel 1 goto :error

echo.
echo ============================================
echo  Done! Your app is at: dist\PhotoOrganizer.exe
echo  You can copy that one file anywhere and run
echo  it directly - no Python required on that PC.
echo ============================================
pause
exit /b 0

:nopython
echo Python was not found on this PC.
echo.
echo To fix this (one-time):
echo   1. Go to https://www.python.org/downloads/ and download Python 3.12 or newer.
echo   2. Run the installer. On the FIRST screen, check the box that says
echo      "Add python.exe to PATH" before clicking Install Now.
echo   3. When it finishes, close this window and double-click
echo      build_windows.bat again.
echo.
echo After the build, the .exe it produces does NOT need Python.
pause
exit /b 1

:error
echo.
echo Something went wrong during the build - see the messages above.
pause
exit /b 1
