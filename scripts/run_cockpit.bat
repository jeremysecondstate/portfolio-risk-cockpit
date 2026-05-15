@echo off
setlocal

rem Launch Portfolio Risk Cockpit from this repository.
rem The working directory is set to the repo root so .env and data files resolve correctly.

cd /d "%~dp0.."

if exist ".venv\Scripts\pythonw.exe" (
    start "Portfolio Risk Cockpit" ".venv\Scripts\pythonw.exe" -m app.main
) else if exist ".venv\Scripts\python.exe" (
    start "Portfolio Risk Cockpit" ".venv\Scripts\python.exe" -m app.main
) else (
    start "Portfolio Risk Cockpit" pythonw -m app.main
)

endlocal
