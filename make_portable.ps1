# make_portable.ps1
# Builds a self-contained Clip Studio for other Windows PCs:
#   dist\ClipStudio\      (unzip-and-run folder: Python, libraries, FFmpeg, models)
#   dist\ClipStudio.zip   (the same folder zipped, to copy to other computers)
# Run from D:\AI-CLIP-GENERATOR:   powershell -ExecutionPolicy Bypass -File .\make_portable.ps1
# Re-run it whenever the app changes.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$dist = Join-Path $root "dist\ClipStudio"
$zip  = Join-Path $root "dist\ClipStudio.zip"

$env:TEMP = Join-Path $root "temp"; $env:TMP = $env:TEMP

Write-Host "== Building portable Clip Studio in $dist"
New-Item -ItemType Directory -Force $dist | Out-Null

# 1. Python runtime (a copied CPython install runs from any folder)
$pyHome = ((Get-Content (Join-Path $root ".venv\pyvenv.cfg") | Select-String "^home\s*=").ToString() -split "=", 2)[1].Trim()
Write-Host "-- Python from $pyHome"
robocopy $pyHome (Join-Path $dist "python") /E /NFL /NDL /NJH /NJS /NP `
    /XD __pycache__ test tests idle_test Doc Tools include libs | Out-Null

# 2. All libraries from the working venv (OpenCV, NumPy, faster-whisper, ...)
Write-Host "-- Libraries from .venv"
robocopy (Join-Path $root ".venv\Lib\site-packages") (Join-Path $dist "python\Lib\site-packages") /E `
    /NFL /NDL /NJH /NJS /NP /XD __pycache__ | Out-Null

# 3. Microsoft C++ runtime next to python.exe (app-local, in case the PC lacks the redistributable)
foreach ($dll in "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll") {
    $src = Join-Path $env:WINDIR "System32\$dll"
    $dst = Join-Path $dist "python\$dll"
    if ((Test-Path $src) -and -not (Test-Path $dst)) { Copy-Item $src $dst }
}

# 4. FFmpeg (static build: the two exes are enough)
$ffDir = Split-Path (Get-Command ffmpeg).Source
Write-Host "-- FFmpeg from $ffDir"
New-Item -ItemType Directory -Force (Join-Path $dist "ffmpeg") | Out-Null
Copy-Item (Join-Path $ffDir "ffmpeg.exe"), (Join-Path $ffDir "ffprobe.exe") (Join-Path $dist "ffmpeg") -Force
Get-ChildItem $ffDir -Filter *.dll -ErrorAction SilentlyContinue | Copy-Item -Destination (Join-Path $dist "ffmpeg") -Force

# 5. App code + face model + speech models (offline use)
Write-Host "-- App files and models"
Copy-Item (Join-Path $root "clip_studio.py"), (Join-Path $root "build_shorts.py"), (Join-Path $root "analyze_speakers.py") $dist -Force
robocopy (Join-Path $root "models") (Join-Path $dist "models") /E /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy (Join-Path $root "hf_cache\hub") (Join-Path $dist "hf_cache\hub") /E /NFL /NDL /NJH /NJS /NP /XD .locks | Out-Null
New-Item -ItemType Directory -Force (Join-Path $dist "temp"), (Join-Path $dist "output") | Out-Null

# 6. Launchers
@'
@echo off
rem Clip Studio (portable) - everything runs from this folder, offline
cd /d "%~dp0"
set "PATH=%~dp0ffmpeg;%~dp0python;%PATH%"
set "TEMP=%~dp0temp"
set "TMP=%~dp0temp"
set "HF_HOME=%~dp0hf_cache"
set "HF_HUB_OFFLINE=1"
set "PYTHONNOUSERSITE=1"
if not exist "%TEMP%" mkdir "%TEMP%"
start "" "%~dp0python\pythonw.exe" "%~dp0clip_studio.py"
'@ | Set-Content -Encoding ascii (Join-Path $dist "Clip Studio.bat")

@'
@echo off
rem Same as "Clip Studio.bat" but keeps a console open so errors are visible
cd /d "%~dp0"
set "PATH=%~dp0ffmpeg;%~dp0python;%PATH%"
set "TEMP=%~dp0temp"
set "TMP=%~dp0temp"
set "HF_HOME=%~dp0hf_cache"
set "HF_HUB_OFFLINE=1"
set "PYTHONNOUSERSITE=1"
if not exist "%TEMP%" mkdir "%TEMP%"
"%~dp0python\python.exe" "%~dp0clip_studio.py"
pause
'@ | Set-Content -Encoding ascii (Join-Path $dist "Clip Studio (troubleshoot).bat")

@'
@echo off
rem Adds a "Clip Studio" shortcut to the Desktop and Start menu. Run once after unzipping.
set "APP=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$w = New-Object -ComObject WScript.Shell;" ^
  "foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {" ^
  "  $s = $w.CreateShortcut((Join-Path $d 'Clip Studio.lnk'));" ^
  "  $s.TargetPath = $env:APP + 'Clip Studio.bat'; $s.WorkingDirectory = $env:APP;" ^
  "  $s.WindowStyle = 7; $s.IconLocation = $env:APP + 'python\pythonw.exe,0';" ^
  "  $s.Description = 'Clip Studio - long video to vertical shorts'; $s.Save() }"
echo.
echo Clip Studio shortcut added to your Desktop and Start menu.
echo Keep this folder where it is - the shortcut points to it.
pause
'@ | Set-Content -Encoding ascii (Join-Path $dist "Install.bat")

@'
CLIP STUDIO - long video to vertical shorts (runs 100% on this PC, no internet needed)

SETUP (once)
  1. Unzip this folder somewhere with free space (a drive with 10+ GB free is best,
     e.g. D:\ClipStudio). Rendered videos and temp files are stored inside it.
  2. Double-click  Install.bat  -> adds "Clip Studio" to the Desktop and Start menu.

USE
  1. Open Clip Studio.
  2. Long video: Browse... to any video with English or Hindi speech.
  3. Spoken language: Auto-detect (or pick Hindi / English).
  4. Set chunks x videos per chunk, clip length, top text, then press Start.
  5. Finished shorts appear in the output folder (double-click in the list to play).

REQUIREMENTS
  Windows 10 or 11, 64-bit. 16 GB RAM recommended. Nothing to install.

TROUBLESHOOTING
  If the window does not open, run "Clip Studio (troubleshoot).bat" and read the error.
'@ | Set-Content -Encoding ascii (Join-Path $dist "README.txt")

$size = (Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1GB
Write-Host ("== Folder ready: {0:N2} GB" -f $size)

# 7. Zip (tar handles >4 GB zips; models are already compressed, so store-speed is fine)
if (Test-Path $zip) { Remove-Item $zip -Force }
Write-Host "-- Zipping to $zip (takes a few minutes)"
Push-Location (Split-Path $dist)
tar.exe -a -c -f $zip (Split-Path $dist -Leaf)
Pop-Location
Write-Host ("== Zip ready: {0:N2} GB  ->  $zip" -f ((Get-Item $zip).Length / 1GB))
