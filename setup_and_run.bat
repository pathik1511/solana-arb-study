@echo off
REM ============================================================
REM  Solana Arbitrage Bot - isolated environment setup & runner
REM  Double-click this file. It builds a private .venv for this
REM  project (separate from your Kaggle/Anaconda setup) and runs
REM  the bot. No internet, no dependencies, no trading.
REM ============================================================

cd /d "%~dp0"

echo.
echo === Step 1/3: Locating your Python ===
set "PYEXE="
py -3.12 --version >nul 2>&1 && set "PYEXE=py -3.12"
if not defined PYEXE ( py -3 --version >nul 2>&1 && set "PYEXE=py -3" )
if not defined PYEXE ( python --version >nul 2>&1 && set "PYEXE=python" )

if not defined PYEXE (
  echo Could not find Python automatically.
  echo Please tell Claude and paste this whole window.
  echo.
  pause
  exit /b 1
)
echo Using: %PYEXE%

echo.
echo === Step 2/3: Building isolated environment (.venv) ===
if exist ".venv\Scripts\python.exe" (
  echo .venv already exists - reusing it.
) else (
  %PYEXE% -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo Failed to create .venv. Please tell Claude and paste this window.
  echo.
  pause
  exit /b 1
)

echo Environment ready at: %cd%\.venv
echo (This project has zero third-party dependencies, so nothing to pip install.)

echo.
echo === Step 3/3: Running the bot ===
echo.
".venv\Scripts\python.exe" arb_bot.py

echo.
echo ============================================================
echo  Done. The environment lives in the .venv folder.
echo  To run again later: just double-click this file.
echo ============================================================
echo.
pause
