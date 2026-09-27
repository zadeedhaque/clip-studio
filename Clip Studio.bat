@echo off
rem Clip Studio launcher - keeps every temp/cache folder on D: (C: is nearly full)
cd /d "%~dp0"
set "TEMP=%~dp0temp"
set "TMP=%~dp0temp"
set "PIP_CACHE_DIR=%~dp0pip_cache"
set "HF_HOME=%~dp0hf_cache"
if not exist "%TEMP%" mkdir "%TEMP%"
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0clip_studio.py"
