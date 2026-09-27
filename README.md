# Clip Studio

Local Windows app that turns a long horizontal video into many vertical (9:16) shorts,
entirely on your PC: nothing is uploaded anywhere.

**Pipeline:** in-app transcription (faster-whisper) → split the video into chunks → pick
40 s windows that don't cut sentences → face detection + active-speaker tracking
(cut to the new face when the speaker changes) → word-timed pop captions
(Hindi/Hinglish written in English letters) inside the Reels/TikTok/FB safe zone →
punch-ins on laughs/applause → top text → 1080×1920 H.264, −14 LUFS.

## Use the release (other PCs, no installs)

1. Download **both** assets from the latest release: `ClipStudio-<ver>-app.zip` and
   `ClipStudio-<ver>-models.zip`.
2. Unzip both **into the same folder** (e.g. `D:\`); they share the `ClipStudio` folder.
3. Double-click `ClipStudio\Install.bat` → adds a Desktop / Start-menu shortcut.
4. Open Clip Studio, pick a video, pick the language, press **Start**.

Windows 10/11 64-bit, 16 GB RAM recommended. Works offline.

## Develop (this repo)

Requires Python 3.14 and FFmpeg on PATH.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe clip_studio.py          # the app
.\.venv\Scripts\python.exe build_shorts.py --chunks # CLI batch (uses DEFAULT_CONFIG)
```

The transcription models download on first run into `hf_cache\`.

| File | Purpose |
|---|---|
| `clip_studio.py` | Desktop app (Tkinter) |
| `build_shorts.py` | Engine: transcription, window planning, captions, punch-ins, rendering |
| `analyze_speakers.py` | Shot cuts, face tracking, active speaker, camera path |
| `make_portable.ps1` | Builds the self-contained `dist\ClipStudio` folder and zip |
| `models/` | YuNet face detector (ONNX) |
| `adobe/` | Optional After Effects scripts |
| other `*.py` | Earlier pipeline steps (candidate extraction, edit plans/events) |

## Build a release

```powershell
powershell -ExecutionPolicy Bypass -File .\make_portable.ps1
```
