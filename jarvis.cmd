@echo off
setlocal
set "JARVIS_PY="
if exist "%~dp0.venv\Scripts\python.exe" set "JARVIS_PY=%~dp0.venv\Scripts\python.exe"
if not defined JARVIS_PY if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "JARVIS_PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined JARVIS_PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "JARVIS_PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined JARVIS_PY if exist "%ProgramFiles%\Python313\python.exe" set "JARVIS_PY=%ProgramFiles%\Python313\python.exe"
if not defined JARVIS_PY if exist "%ProgramFiles%\Python312\python.exe" set "JARVIS_PY=%ProgramFiles%\Python312\python.exe"
if not defined JARVIS_PY for /f "delims=" %%P in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined JARVIS_PY set "JARVIS_PY=%%P"
if not defined JARVIS_PY (
  echo JARVIS needs Python 3.10 or newer. Install it with:  winget install -e --id Python.Python.3.13
  exit /b 1
)
"%JARVIS_PY%" "%~dp0jarvis.py" %*
