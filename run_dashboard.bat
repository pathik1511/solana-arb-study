@echo off
REM ============================================================
REM  Launch the Solana Arbitrage dashboard in your browser.
REM  Double-click this file. First run installs Streamlit.
REM ============================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo No .venv found. Run setup_and_run.bat once first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -c "import streamlit, plotly" 2>nul
if errorlevel 1 (
  echo Installing dashboard dependencies ^(one-time^)...
  ".venv\Scripts\python.exe" -m pip install -r requirements-ui.txt
)

echo.
echo Launching the dashboard... a browser tab will open.
echo Close this window or press Ctrl+C to stop it.
echo.
".venv\Scripts\python.exe" -m streamlit run dashboard.py
pause
