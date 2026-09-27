"""
build_shorts.py

End-to-end: candidates -> 40 s windows -> extract -> speaker-following camera
-> word-level pop captions (platform safe zone) -> punch-ins -> final 9:16 MP4.

Everything runs locally (OpenCV + FFmpeg/libass). No After Effects needed;
adobe/generate_ae.jsx remains available for hand-finishing.

Usage (from D:\\AI-CLIP-GENERATOR, venv active):
    python build_shorts.py              # all candidates
    python build_shorts.py clip_03      # one clip

Outputs:
    shorts_work/windows.json            chosen 40 s windows
    shorts_work/<id>.mp4                40 s horizontal source window
    shorts_work/<id>_camera.json        speaker camera track
    shorts_work/<id>.ass                caption/title subtitle script
    final/<id>_<title-slug>.mp4         finished 1080x1920 short
"""

import json
import os
import re
import shutil
import subprocess
import sys

import cv2
import numpy as np

import analyze_speakers as AS

ROOT = os.path.dirname(os.path.abspath(__file__))
MASTER = os.path.join(ROOT, "input", "master.mp4")
TRANSCRIPT = os.path.join(ROOT, "transcript.json")
CANDIDATES = os.path.join(ROOT, "candidates", "candidates.json")
WORK = os.path.join(ROOT, "shorts_work")
FINAL = os.path.join(ROOT, "final")
FONT_FILES = [r"C:\Windows\Fonts\seguibl.ttf", r"C:\Windows\Fonts\ariblk.ttf"]

WINDOW = 40.0
OUT_W, OUT_H = 1080, 1920
NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # no console flashes from the app
STEP = 2

# --------------------------------------------------
# PLATFORM SAFE ZONE (1080x1920)
# Reels / TikTok / FB Reels / Shorts overlay UI:
#   top ~220 px (status, tabs), bottom ~450 px (caption, audio, CTA),
#   right ~140 px (like/comment/share rail). Keep text inside the rest.
# --------------------------------------------------
SAFE_TOP = 250
SAFE_BOTTOM = OUT_H - 480      # 1440
SAFE_LEFT = 90
SAFE_RIGHT = OUT_W - 150       # 930
SAFE_CX = (SAFE_LEFT + SAFE_RIGHT) // 2
CAPTION_Y = 1250               # centre of the caption line (inside safe zone)
TITLE_TOP = 270

TOP_TEXT = "4 THE BOYS"        # the only text above the captions
CUT_ZOOM = 1.12                # every other speaker/shot cut punches in -> reads as a new angle

CAPTION_FONT = "Segoe UI Black"
CAPTION_SIZE = 100
TITLE_SIZE = 64
MAX_CHUNK_WORDS = 3
MAX_CHUNK_CHARS = 12

WHITE = "&H00FFFFFF&"
YELLOW = "&H0033E1FF&"         # active word  (#FFE133)
GREEN = "&H0066FF4D&"          # keyword      (#4DFF66)

KEYWORDS = {
    "NAWAZ", "NAWAZUDDIN", "SACRED", "GAMES", "BHUVAN", "SAMAY", "MUKESH", "RITWIK",
    "MAGIC", "MAGICIAN", "MAGICIANS", "COMEDIAN", "COMEDIANS", "CONDOM", "LOVE",
    "SEX", "CRORE", "LAKH", "ACTING", "ACTOR", "PATTHAR", "ROAST", "DRUGS",
    "SHARABEIN", "PROSTITUTES", "WIFE", "GIRLFRIEND", "SHAADI", "PYAAR", "PAISA",
    "PAISE", "FRIEND", "ZONE", "LIFE", "TOGETHER", "MONEY", "STONE", "CASTING",
}

LAUGH_RE = re.compile(r"laugh|cheer|clap|applau|हंस|हँस|ताली|तालि", re.I)
TAG_RE = re.compile(r"\[[^\]]*\]")
DEVA_RE = re.compile(r"[ऀ-ॿ]")

# segments longer than this with fewer words/sec get re-transcribed locally (faster-whisper)
SPARSE_MIN_DUR = 8.0
SPARSE_MAX_WPS = 1.2
WHISPER_MODEL = "small"
WHISPER_LANG = "hi"


# --------------------------------------------------
# DEVANAGARI -> HINGLISH (Latin) so every caption uses the same bold Latin font
# --------------------------------------------------

_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n", "च": "ch", "छ": "chh", "ज": "j",
    "झ": "jh", "ञ": "n", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t",
    "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh",
    "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s",
    "ह": "h", "क़": "q", "ख़": "kh", "ग़": "gh", "ज़": "z", "ड़": "d", "ढ़": "rh", "फ़": "f",
    "य़": "y",
}
_NUKTA = {"क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "d", "ढ": "rh", "फ": "f"}
_MATRA = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "े": "e",
          "ै": "ai", "ो": "o", "ौ": "au", "ॅ": "e", "ॉ": "o"}
_VOWEL = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ऋ": "ri",
          "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o"}
_DIGIT = {chr(0x0966 + i): str(i) for i in range(10)}


def _translit_word(word):
    units = []  # [consonant, vowel]  vowel: "a" inherent, "" virama, else matra/independent
    i, n = 0, len(word)
    while i < n:
        ch = word[i]
        if ch in _CONS or (ch in _NUKTA and i + 1 < n and word[i + 1] == "़"):
            cons = _CONS.get(ch, "")
            if i + 1 < n and word[i + 1] == "़":
                cons = _NUKTA.get(ch, cons)
                i += 1
            units.append([cons, "a"])
        elif ch in _MATRA and units:
            units[-1][1] = _MATRA[ch]
        elif ch == "्" and units:
            units[-1][1] = ""
        elif ch in ("ं", "ँ"):
            if units:
                units[-1].append("n")
            else:
                units.append(["", "", "n"])
        elif ch == "ः":
            units.append(["h", ""])
        elif ch in _VOWEL:
            units.append(["", _VOWEL[ch]])
        elif ch in _DIGIT:
            units.append([_DIGIT[ch], ""])
        elif ch in ("।", "॥"):
            units.append([".", ""])
        elif ch == "़":
            pass
        else:
            units.append([ch, ""])
        i += 1

    # schwa deletion: word-final, then V C(a) C V medially
    cons_units = [k for k, u in enumerate(units) if u[0] and u[0].isalpha()]
    if cons_units:
        last = cons_units[-1]
        if units[last][1] == "a" and len(units) > 1:
            units[last][1] = ""
    for k in range(1, len(units) - 1):
        u, prev, nxt = units[k], units[k - 1], units[k + 1]
        if (u[1] == "a" and u[0] and prev[1] and nxt[0] and nxt[1]
                and nxt[0].isalpha()):
            u[1] = ""

    out = []
    for k, u in enumerate(units):
        out.append(u[0] + u[1])
        if len(u) > 2:  # nasal
            nxt = units[k + 1][0] if k + 1 < len(units) else ""
            out.append("m" if nxt[:1] in ("p", "b", "m") else "n")
    s = "".join(out)
    s = re.sub(r"aa(?=[.!?,]*$)", "a", s)
    s = re.sub(r"ee(?=[.!?,]*$)", "i", s)
    s = re.sub(r"aai(?=[.!?,]*$)", "ai", s)
    # everyday Hinglish spelling: long vowels written short (bhoovan -> bhuvan, khaan -> khan)
    core = re.sub(r"[^a-z]", "", s)
    if core not in HINGLISH_KEEP and len(core) > 3:
        s = s.replace("aa", "a").replace("ee", "i").replace("oo", "u")
    return s


HINGLISH_KEEP = {"aap", "hoon", "hoo", "baat", "yaar", "saath", "aaj", "aage", "paas", "baad",
                 "raat", "jaan", "naam", "kaam", "shaam", "maar"}


def to_hinglish(text):
    if not DEVA_RE.search(text):
        return text
    return " ".join(_translit_word(w) if DEVA_RE.search(w) else w for w in text.split())

# motion
HOOK_ZOOM = 1.10               # opening zoom-out from this
HOOK_TIME = 0.7
PUNCH_ZOOM = 1.12              # punchline punch-in
PUNCH_IN = 0.18
PUNCH_OUT = 0.45
PUNCH_MAX_HOLD = 1.6
PUNCH_MIN_GAP = 2.5
PUSH_PER_SHOT = 0.035          # slow push-in across each shot


# --------------------------------------------------
# UTIL
# --------------------------------------------------

def to_sec(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def slugify(text):
    text = text.replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, creationflags=NOWIN)
    if r.returncode != 0:
        sys.exit(f"Command failed: {' '.join(cmd)}")


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True,
                         creationflags=NOWIN).stdout
    return float(out.strip())


def ease_out(x):
    return 1 - (1 - x) ** 3


def ease_in_out(x):
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


# --------------------------------------------------
# 1. WINDOW SELECTION
# --------------------------------------------------

def load_segments():
    with open(TRANSCRIPT, "r", encoding="utf-8") as f:
        raw = json.load(f)
    segs = []
    for s in raw:
        text = s.get("text", "")
        spoken = TAG_RE.sub("", text).strip()
        lead = re.match(r"\s*(\[[^\]]*\])", text)
        segs.append({
            "start": float(s["start"]), "end": float(s["end"]),
            "speaker": s.get("speaker"), "text": text, "spoken": spoken,
            "is_speech": bool(spoken), "is_laugh": bool(LAUGH_RE.search(text)) and not spoken,
            # reaction tag at the head of a spoken line = audience reacted at its start
            "laugh_at_start": bool(spoken) and bool(lead) and bool(LAUGH_RE.search(lead.group(1))),
            "words": s.get("words"),  # exact word timings when transcribed in-app
        })
    return segs


def cut_cost(t, segs, kind):
    """How badly cutting at t chops a spoken line (0 = clean)."""
    for s in segs:
        if s["is_speech"] and s["start"] + 0.05 < t < s["end"] - 0.05:
            into = (t - s["start"]) if kind == "start" else (s["end"] - t)
            return 0.5 + min(into, 3.0)
    return 0.0


def choose_window(cand, segs, total):
    s, e = to_sec(cand["start"]), to_sec(cand["end"])
    dur = e - s
    if dur <= WINDOW:
        # keep the hook near the top: allow at most 4 s of pre-roll
        lo, hi = s - min(4.0, WINDOW - dur), s
        pref = lambda st: 0.15 * (s - st)
    else:
        # long candidate: best 40 s inside it, prefer earlier (the setup/hook)
        lo, hi = s, e - WINDOW
        pref = lambda st: 0.02 * (st - s)
    lo, hi = max(0.0, lo), min(hi, total - WINDOW)
    best, best_cost = lo, 1e9
    for st in np.arange(lo, hi + 1e-6, 0.05):
        cost = 1.5 * cut_cost(st, segs, "start") + cut_cost(st + WINDOW, segs, "end") + pref(st)
        if cost < best_cost:
            best, best_cost = float(st), cost
    return round(best, 3), round(best_cost, 3)


# --------------------------------------------------
# 2. EXTRACT
# --------------------------------------------------

def extract(start, out_path):
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-i", MASTER, "-t", f"{WINDOW:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path])


def load_audio_rms(start, dur, hop=0.01):
    """RMS envelope of master audio, 10 ms hops, starting at `start`."""
    sr = 16000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-i", MASTER,
         "-t", f"{dur:.3f}", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        capture_output=True, creationflags=NOWIN).stdout
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sr * hop)
    k = len(a) // n
    return np.sqrt((a[:k * n].reshape(k, n) ** 2).mean(axis=1) + 1e-12)


# --------------------------------------------------
# 3. WORD TIMING + CAPTIONS
# --------------------------------------------------

def latin_only(word):
    """Captions are Hindi/English in English letters only: drop any non-Latin script."""
    word = word.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"[^\x20-\x7E]", "", word)


def clean_words(text):
    text = to_hinglish(TAG_RE.sub(" ", text))
    text = text.replace("--", " ").replace("…", " ").replace("...", " ")
    words = [latin_only(w) for w in text.split()]
    return [w for w in words if re.search(r"[A-Za-z0-9]", w)]


def word_timings(seg, rms, rms_t0, hop=0.01):
    """Spread words across the voiced part of a segment, weighted by length."""
    words = clean_words(seg["text"])
    if not words:
        return []
    a = int(round((seg["start"] - rms_t0) / hop))
    b = int(round((seg["end"] - rms_t0) / hop))
    a, b = max(0, a), min(len(rms), b)
    weights = np.array([len(re.sub(r"\W", "", w)) + 2 + (3 if w[-1] in ",.?!" else 0)
                        for w in words], dtype=np.float64)
    cw = np.concatenate([[0.0], np.cumsum(weights)]) / weights.sum()

    if b - a < 5:
        t = seg["start"] + cw * (seg["end"] - seg["start"])
    else:
        e = rms[a:b]
        voiced = e > max(0.25 * np.percentile(e, 90), 0.004)
        if voiced.mean() < 0.3:
            voiced[:] = True
        cum = np.cumsum(voiced)
        total = cum[-1]
        idx = np.searchsorted(cum, cw * total, side="left")
        idx = np.clip(idx, 0, len(e) - 1)
        t = seg["start"] + idx * hop
        t[-1] = seg["start"] + (np.nonzero(voiced)[0][-1] + 1) * hop

    out = []
    for k, w in enumerate(words):
        s, en = float(t[k]), float(max(t[k + 1], t[k] + 0.08))
        out.append({"word": w, "start": s, "end": en, "speaker": seg["speaker"]})
    return out


_whisper = None


def whisper_words(abs_start, abs_end, speaker):
    """Local faster-whisper word timings for spans the transcript barely covers."""
    global _whisper
    if _whisper is None:
        os.environ.setdefault("HF_HOME", os.path.join(ROOT, "hf_cache"))
        from faster_whisper import WhisperModel
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    wav = os.path.join(WORK, "_sparse.wav")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{abs_start:.3f}",
         "-i", MASTER, "-t", f"{abs_end - abs_start:.3f}", "-ac", "1", "-ar", "16000", wav])
    segments, _ = _whisper.transcribe(wav, language=WHISPER_LANG, word_timestamps=True, vad_filter=True)
    out = []
    for seg in segments:
        for w in seg.words or []:
            txt = latin_only(to_hinglish(w.word.strip()))
            if re.search(r"[A-Za-z0-9]", txt):
                out.append({"word": txt, "start": abs_start + w.start,
                            "end": abs_start + max(w.end, w.start + 0.08), "speaker": speaker})
    return out


def build_chunks(words):
    chunks, cur = [], []

    def flush():
        if cur:
            chunks.append(list(cur))
            cur.clear()

    for w in words:
        if cur:
            chars = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
            if (len(cur) >= MAX_CHUNK_WORDS or chars > MAX_CHUNK_CHARS
                    or w["speaker"] != cur[-1]["speaker"]
                    or w["start"] - cur[-1]["end"] > 0.35):
                flush()
        cur.append(w)
        if w["word"][-1] in ",.?!":
            flush()
    flush()

    out = []
    for i, c in enumerate(chunks):
        start = c[0]["start"]
        end = c[-1]["end"] + 0.15
        if i + 1 < len(chunks):
            end = min(end, chunks[i + 1][0]["start"])
        end = max(end, start + 0.3)
        out.append({"words": c, "start": start, "end": end})
    for i in range(len(out) - 1):  # never overlap
        out[i]["end"] = min(out[i]["end"], out[i + 1]["start"])
    return [c for c in out if c["end"] - c["start"] > 0.05]


def ass_time(t):
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(s):
    return s.replace("\\", "").replace("{", "(").replace("}", ")")


def is_keyword(w):
    core = re.sub(r"[^\w]", "", w.upper())
    return core in KEYWORDS or any(ch.isdigit() for ch in core)


def write_ass(path, title, chunks):
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {OUT_W}",
        f"PlayResY: {OUT_H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{CAPTION_FONT},{CAPTION_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,"
        f"0,0,0,0,100,100,1,0,1,8,5,5,{SAFE_LEFT + 30},{OUT_W - SAFE_RIGHT + 30},0,1",
        f"Style: Title,{CAPTION_FONT},{TITLE_SIZE},&H00111111,&H00111111,&H0033E1FF,&H00000000,"
        f"0,0,0,0,100,100,0,0,3,20,0,8,{SAFE_LEFT + 40},{OUT_W - SAFE_RIGHT + 40},{TITLE_TOP},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # hook title: pops in, stays for the whole short (inside the top safe area)
    lines.append(
        f"Dialogue: 1,{ass_time(0.0)},{ass_time(WINDOW)},Title,,0,0,0,,"
        "{\\fscx40\\fscy40\\t(0,160,\\fscx106\\fscy106)\\t(160,260,\\fscx100\\fscy100)}"
        + ass_escape(title.upper()))

    pos = f"\\an5\\pos({SAFE_CX},{CAPTION_Y})"
    for c in chunks:
        words = c["words"]
        for k, w in enumerate(words):
            ws = w["start"] if k > 0 else c["start"]
            we = words[k + 1]["start"] if k + 1 < len(words) else c["end"]
            if we - ws <= 0.01:
                continue
            parts = []
            for j, x in enumerate(words):
                txt = ass_escape(x["word"].upper())
                if j == k:
                    grow = 124 if is_keyword(x["word"]) else 116
                    parts.append(f"{{\\c{YELLOW}\\fscx{grow}\\fscy{grow}"
                                 f"\\t(0,110,\\fscx106\\fscy106)}}{txt}"
                                 f"{{\\c{WHITE}\\fscx100\\fscy100}}")
                elif is_keyword(x["word"]):
                    parts.append(f"{{\\c{GREEN}}}{txt}{{\\c{WHITE}}}")
                else:
                    parts.append(txt)
            lead = f"{{{pos}"
            if k == 0:  # chunk entrance: quick rise + fade
                lead += (f"\\move({SAFE_CX},{CAPTION_Y + 26},{SAFE_CX},{CAPTION_Y},0,90)"
                         "\\fad(70,0)")
            lead += "}"
            lines.append(f"Dialogue: 0,{ass_time(ws)},{ass_time(we)},Cap,,0,0,0,,"
                         + lead + " ".join(parts))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# --------------------------------------------------
# 4. PUNCH-INS
# --------------------------------------------------

def punch_events(rel_segs):
    """Punch in on the line right before the audience laughs."""
    speech = [s for s in rel_segs if s["is_speech"]]
    cues = sorted(s["start"] for s in rel_segs if s["is_laugh"] or s["laugh_at_start"])
    events, last = [], -1e9
    for cue in cues:
        prev = [s for s in speech if cue - 1.0 <= s["end"] <= cue + 0.3 and s["start"] < cue]
        if not prev:
            continue
        pe = max(s["end"] for s in prev)
        t0 = max(0.5, pe - 0.35)
        if t0 - last < PUNCH_MIN_GAP or t0 > WINDOW - 1.0:
            continue
        nxt = [s["start"] for s in speech if s["start"] > pe + 0.2]
        hold_end = min(pe + PUNCH_MAX_HOLD, nxt[0] if nxt else WINDOW)
        events.append((t0, max(hold_end, t0 + PUNCH_IN + 0.3)))
        last = t0
    return events


def audio_reaction_cues(rel, rms, rms_t0, start, hop=0.01):
    """Laughter/applause = a loud pause right after someone stops talking."""
    spans = []
    for r in rel:
        if not r["is_speech"]:
            continue
        if r.get("words"):
            for w in r["words"]:
                a, b = float(w["start"]) - start, float(w["end"]) - start
                if spans and a - spans[-1][1] < 0.3:
                    spans[-1][1] = max(spans[-1][1], b)
                else:
                    spans.append([a, b])
        else:
            spans.append([r["start"], r["end"]])
    spans = sorted([s for s in spans if s[1] > 0 and s[0] < WINDOW], key=lambda s: s[0])
    if len(spans) < 2:
        return []

    def level(a, b):
        i, j = int((a + start - rms_t0) / hop), int((b + start - rms_t0) / hop)
        seg = rms[max(0, i):max(0, j)]
        return float(seg.mean()) if len(seg) else 0.0

    speech_level = float(np.median([level(a, b) for a, b in spans if b - a > 0.3] or [0.0]))
    cues = []
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        if a2 - b1 >= 0.7 and speech_level > 0 and level(b1, min(a2, b1 + 2.0)) >= 0.55 * speech_level:
            cues.append(b1 + 0.05)
    return cues


def zoom_at(t, punches, shot_start, shot_end):
    z = 1.0
    if t < HOOK_TIME:
        z *= HOOK_ZOOM - (HOOK_ZOOM - 1.0) * ease_out(t / HOOK_TIME)
    for t0, t1 in punches:
        if t0 <= t < t0 + PUNCH_IN:
            z *= 1 + (PUNCH_ZOOM - 1) * ease_out((t - t0) / PUNCH_IN)
        elif t0 + PUNCH_IN <= t < t1:
            z *= PUNCH_ZOOM
        elif t1 <= t < t1 + PUNCH_OUT:
            z *= PUNCH_ZOOM - (PUNCH_ZOOM - 1) * ease_in_out((t - t1) / PUNCH_OUT)
    span = max(shot_end - shot_start, 1e-3)
    z *= 1 + PUSH_PER_SHOT * min(1.0, (t - shot_start) / span)
    return z


# --------------------------------------------------
# 5. RENDER
# --------------------------------------------------

def render(clip_path, ass_name, out_path, data, per_frame, punches, shots):
    fps, W, H = data["fps"], data["W"], data["H"]
    base_crop_w = H * AS.OUT_ASPECT
    n = len(per_frame["x"])

    # cut segments (camera cut or speaker cut): push-in restarts, zoom alternates
    seg = per_frame["seg"][:n]
    seg_bounds = {}
    for f_i, sid in enumerate(seg):
        a, b = seg_bounds.get(sid, (f_i, f_i))
        seg_bounds[sid] = (min(a, f_i), max(b, f_i + 1))

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{OUT_W}x{OUT_H}", "-r", f"{fps}", "-i", "-",
           "-i", os.path.basename(clip_path),
           "-filter_complex", f"[0:v]unsharp=5:5:0.45:5:5:0,ass={ass_name}:fontsdir=fonts[v]",
           "-map", "[v]", "-map", "1:a",
           "-af", f"afade=t=in:st=0:d=0.04,afade=t=out:st={WINDOW - 0.15:.2f}:d=0.15,"
                  "loudnorm=I=-14:TP=-1.5:LRA=11",
           "-ar", "48000", "-c:a", "aac", "-b:a", "192k",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-t", f"{WINDOW:.3f}", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, cwd=WORK, creationflags=NOWIN)

    cap = cv2.VideoCapture(clip_path)
    f = 0
    while f < n:
        ok, frame = cap.read()
        if not ok:
            break
        t = f / fps
        sid = seg[f]
        a, b = seg_bounds[sid]
        z = per_frame["z"][f] * zoom_at(t, punches, a / fps, b / fps)
        if sid % 2 == 1:
            z *= CUT_ZOOM
        vw, vh = base_crop_w / z, H / z
        cx = min(max(per_frame["x"][f], vw / 2), W - vw / 2)
        cy = min(max(per_frame["y"][f], vh / 2), H - vh / 2)
        s = OUT_H / vh
        M = np.float32([[s, 0, OUT_W / 2 - s * cx], [0, s, OUT_H / 2 - s * cy]])
        out = cv2.warpAffine(frame, M, (OUT_W, OUT_H), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
        proc.stdin.write(out.tobytes())
        f += 1
    cap.release()
    proc.stdin.close()
    if proc.wait() != 0:
        sys.exit(f"Render failed: {out_path}")


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def build(cand, segs, total, windows):
    cid = cand["id"]
    print(f"\n=== {cid}: {cand['title']} ===")

    start, cost = choose_window(cand, segs, total)
    windows[cid] = {"start": start, "end": round(start + WINDOW, 3), "cut_cost": cost,
                    "candidate": [cand["start"], cand["end"]], "title": cand["title"]}
    print(f"  window {start:.2f}s -> {start + WINDOW:.2f}s  (cut cost {cost})")
    out_path = os.path.join(FINAL, f"{cid}_{slugify(cand['title'])}.mp4")
    build_window(cid, start, segs, out_path)
    windows[cid]["output"] = out_path


def build_window(cid, start, segs, out_path):
    end = start + WINDOW
    clip_path = os.path.join(WORK, f"{cid}.mp4")
    extract(start, clip_path)

    # transcript relative to the window
    rel = []
    for s in segs:
        if s["end"] > start and s["start"] < end:
            r = dict(s)
            r["start"], r["end"] = s["start"] - start, s["end"] - start
            rel.append(r)
    cam_speech = [(max(0.0, r["start"]), min(WINDOW, r["end"]), r["speaker"])
                  for r in rel if r["is_speech"] and r["end"] > r["start"]]

    # speaker-following camera
    data = AS.analyze(cid, STEP, video_path=clip_path)
    result, per_frame = AS.plan_camera(cid, data, STEP, speech=cam_speech)
    with open(os.path.join(WORK, f"{cid}_camera.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    print(f"  camera: {len(result['shots'])} shots, {len(result['camera_keyframes'])} keys, "
          f"identity {result['identity_speakers']}")

    # captions
    pad = 15.0
    rms_t0 = max(0.0, start - pad)
    rms = load_audio_rms(rms_t0, WINDOW + 2 * pad)
    words = []
    for s in segs:
        if s["is_speech"] and s["end"] > start and s["start"] < end:
            dur = s["end"] - s["start"]
            if s.get("words"):
                # in-app transcription: real per-word timestamps
                seg_words = []
                for w in s["words"]:
                    for piece in clean_words(w["word"]):
                        seg_words.append({"word": piece, "start": float(w["start"]),
                                          "end": float(w["end"]), "speaker": s["speaker"]})
            elif dur > SPARSE_MIN_DUR and len(clean_words(s["text"])) / dur < SPARSE_MAX_WPS:
                seg_words = whisper_words(max(s["start"], start), min(s["end"], end), s["speaker"])
                print(f"  sparse segment {s['start'] - start:.1f}s (+{dur:.0f}s): "
                      f"whisper found {len(seg_words)} words")
            else:
                seg_words = word_timings(s, rms, rms_t0)
            for w in seg_words:
                w["start"] -= start
                w["end"] -= start
                if w["end"] > 0.05 and w["start"] < WINDOW - 0.05:
                    w["start"], w["end"] = max(0.0, w["start"]), min(WINDOW, w["end"])
                    words.append(w)
    words.sort(key=lambda w: w["start"])
    chunks = build_chunks(words)
    ass_name = f"{cid}.ass"
    write_ass(os.path.join(WORK, ass_name), TOP_TEXT, chunks)
    print(f"  captions: {len(words)} words in {len(chunks)} chunks")

    if not any(r["is_laugh"] or r["laugh_at_start"] for r in rel):
        # no [laughing]/[applause] tags (e.g. in-app transcript): hear the reactions instead
        for cue in audio_reaction_cues(rel, rms, rms_t0, start):
            rel.append({"start": cue, "end": cue + 0.5, "speaker": None, "text": "",
                        "spoken": "", "is_speech": False, "is_laugh": True,
                        "laugh_at_start": False, "words": None})
    punches = punch_events(rel)
    print(f"  punch-ins at: {[round(p[0], 2) for p in punches]}")

    render(clip_path, ass_name, out_path, data, per_frame, punches, result["shots"])
    print(f"  rendered {out_path}  ({probe_duration(out_path):.2f}s)")
    # the 40 s source window is only an intermediate
    for tmp in (clip_path,):
        if CLEAN_INTERMEDIATE and os.path.exists(tmp):
            os.remove(tmp)


# --------------------------------------------------
# CHUNK MODE: split the episode into N chunks, V shorts per chunk
# --------------------------------------------------

CHUNK_COUNT = 10
VIDEOS_PER_CHUNK = 10
CHUNK_OUT = os.path.join(ROOT, "final_100")
SNAP_RANGE = 3.0               # nudge each start up to +-3 s to avoid cutting a line
WORKERS = 4
CLEAN_INTERMEDIATE = False


def chunk_windows(segs, total):
    n_chunks = max(1, min(CHUNK_COUNT, int(total // WINDOW)))  # short videos: fewer chunks
    chunk_len = total / n_chunks
    stride = max(0.0, chunk_len - WINDOW) / max(VIDEOS_PER_CHUNK - 1, 1)
    per_chunk = VIDEOS_PER_CHUNK if stride > 0 or VIDEOS_PER_CHUNK == 1 else 1
    plan = []
    for c in range(n_chunks):
        c0 = c * chunk_len
        lo_c, hi_c = c0, max(c0, min(c0 + chunk_len, total) - WINDOW)
        for v in range(per_chunk):
            nominal = c0 + v * stride
            best, best_cost = nominal, 1e9
            for st in np.arange(nominal - SNAP_RANGE, nominal + SNAP_RANGE + 1e-6, 0.05):
                if st < max(0.0, lo_c) or st > hi_c:
                    continue
                cost = (1.5 * cut_cost(st, segs, "start") + cut_cost(st + WINDOW, segs, "end")
                        + 0.05 * abs(st - nominal))
                if cost < best_cost:
                    best, best_cost = float(st), cost
            best = min(max(best, 0.0), total - WINDOW)
            plan.append({"id": f"chunk{c + 1:02d}_v{v + 1:02d}", "chunk": c + 1,
                         "start": round(best, 3), "end": round(best + WINDOW, 3)})
    return plan


# --------------------------------------------------
# CONFIGURABLE PIPELINE (used by the Clip Studio app and --chunks)
# --------------------------------------------------

DEFAULT_CONFIG = {
    "video": MASTER,
    "transcript": "",               # "" = transcribe in-app; a .txt/.srt/.json path still works
    "language": "auto",             # auto | hindi | english
    "out_dir": CHUNK_OUT,
    "chunks": 10,
    "per_chunk": 10,
    "clip_seconds": 40,
    "top_text": "4 THE BOYS",
    "workers": 4,
    "caption_size": 100,
    "cut_on_speaker": True,
    "skip_existing": True,
    "whisper_model": "large-v3-turbo",
}

TS_LINE = re.compile(r"^(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
                     r"(?:\s*\[([^\]]+)\])?")


def _ts(s):
    h, m, rest = s.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def parse_transcript_text(path):
    """Speaker-labelled '.txt' (00:00:01,000 --> 00:00:02,000 [speaker_0]) or plain .srt."""
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    out, cur = [], None
    for line in lines:
        line = line.strip()
        m = TS_LINE.match(line)
        if m:
            if cur and cur["text"]:
                cur["text"] = " ".join(cur["text"])
                out.append(cur)
            cur = {"start": _ts(m.group(1)), "end": _ts(m.group(2)),
                   "speaker": m.group(3), "text": []}
        elif line and cur is not None and not line.isdigit():
            cur["text"].append(line)
    if cur and cur["text"]:
        cur["text"] = " ".join(cur["text"])
        out.append(cur)
    return out


LANGUAGES = {"auto": None, "hindi": "hi", "english": "en"}
HINGLISH_PROMPT = ("Haan bhai, kya haal hai? Permission toh leni chahiye na. Sacred Games kamaal "
                   "tha sir, genuinely. Main sach bol raha hoon yaar, I love this show.")


def detect_video_language(model, audio, log, samples=8, sr=16000):
    """Whisper only checks the first 30 s; sample the whole video instead.
    Any real share of Hindi -> 'hi' (Hindi mode keeps English words as spoken;
    English mode would translate the Hindi parts).
    large-v3-turbo's detector calls Hinglish 'en' ~95% of the time, so the
    small model (reliable here) is used for detection only."""
    from faster_whisper import WhisperModel
    model = WhisperModel("small", device="cpu", compute_type="int8",
                         cpu_threads=max(1, (os.cpu_count() or 4) - 2))
    n = len(audio)
    win = 30 * sr
    votes, hi_prob = {}, []
    for k in range(samples):
        a = int((n - win) * (k + 0.5) / samples) if n > win else 0
        chunk = audio[a:a + win]
        if len(chunk) < sr * 5 or float(np.abs(chunk).mean()) < 1e-3:
            continue
        lang, prob, all_probs = model.detect_language(chunk)
        votes[lang] = votes.get(lang, 0) + 1
        hi_prob.append(dict(all_probs).get("hi", 0.0) + dict(all_probs).get("ur", 0.0))
    if not votes:
        return None
    top = max(votes, key=votes.get)
    hindi_share = (votes.get("hi", 0) + votes.get("ur", 0)) / sum(votes.values())
    pick = "hi" if hindi_share >= 0.25 or (hi_prob and np.mean(hi_prob) > 0.25) else top
    log(f"Language check over {sum(votes.values())} samples: {votes} -> using '{pick}'")
    return pick


def auto_transcribe(video, model_name, language, log, progress=None):
    """Transcribe the video locally with faster-whisper, keeping per-word timestamps."""
    os.environ.setdefault("HF_HOME", os.path.join(ROOT, "hf_cache"))
    from faster_whisper import WhisperModel
    total = probe_duration(video)
    log(f"Transcribing {total / 60:.1f} min of audio (whisper {model_name}, "
        f"language: {language}) - first run downloads the model ...")
    from faster_whisper import BatchedInferencePipeline, decode_audio
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=max(1, (os.cpu_count() or 4) - 2))
    audio = decode_audio(video, sampling_rate=16000)
    lang = LANGUAGES.get(language, None)
    if lang is None:
        lang = detect_video_language(model, audio, log)
    prompt = None
    if lang in ("hi", "ur"):
        # Hindi/Hinglish: a romanised example makes Whisper write Hinglish in English
        # letters directly (and spell English words properly) instead of Devanagari.
        lang, prompt = "en", HINGLISH_PROMPT
        log("Hindi/Hinglish audio: writing captions in English letters")
    # batched inference (with built-in VAD) is ~10x faster than sequential on CPU
    segments, info = BatchedInferencePipeline(model).transcribe(
        audio, language=lang, initial_prompt=prompt, word_timestamps=True, batch_size=8)
    out, last_report = [], 0.0
    for seg in segments:
        words = [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
                 for w in (seg.words or []) if w.word.strip()]
        out.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "speaker": None,
                    "text": seg.text.strip(), "words": words})
        if progress:
            progress(seg.end, total)
        if seg.end - last_report >= 120:
            last_report = seg.end
            log(f"  transcribed {seg.end / 60:.1f} / {total / 60:.1f} min")
    return out


def prepare_transcript(cfg, log, progress=None):
    """Transcribe in-app (cached per video + language + model), or normalise a given file."""
    work = os.path.join(cfg["out_dir"], "_work")
    os.makedirs(work, exist_ok=True)
    src = (cfg.get("transcript") or "").strip()
    if src:
        data = parse_transcript_text(src) if not src.lower().endswith(".json") else \
            json.load(open(src, "r", encoding="utf-8"))
        dst = os.path.join(work, "transcript.json")
    else:
        st = os.stat(cfg["video"])
        key = f"{os.path.basename(cfg['video'])}|{st.st_size}|{int(st.st_mtime)}|" \
              f"{cfg.get('language', 'auto')}|{cfg.get('whisper_model', 'small')}"
        import hashlib
        dst = os.path.join(work, f"transcript_{hashlib.md5(key.encode()).hexdigest()[:10]}.json")
        if os.path.exists(dst):
            log("Using saved transcript for this video (already transcribed).")
            return dst
        data = auto_transcribe(cfg["video"], cfg.get("whisper_model", "small"),
                               cfg.get("language", "auto"), log, progress)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log(f"Transcript ready: {len(data)} segments")
    return dst


def configure(cfg):
    """Apply a config dict to this module (also called inside each worker process)."""
    global MASTER, TRANSCRIPT, WORK, CHUNK_OUT, WINDOW, TOP_TEXT, CAPTION_SIZE
    global CHUNK_COUNT, VIDEOS_PER_CHUNK, WORKERS, WHISPER_MODEL
    MASTER = cfg["video"]
    CHUNK_OUT = cfg["out_dir"]
    WORK = os.path.join(CHUNK_OUT, "_work")
    TRANSCRIPT = cfg.get("_transcript_json") or os.path.join(WORK, "transcript.json")
    WINDOW = float(cfg["clip_seconds"])
    TOP_TEXT = cfg["top_text"]
    CAPTION_SIZE = int(cfg["caption_size"])
    CHUNK_COUNT = int(cfg["chunks"])
    VIDEOS_PER_CHUNK = int(cfg["per_chunk"])
    WORKERS = int(cfg["workers"])
    WHISPER_MODEL = cfg.get("whisper_model", "small")
    global WHISPER_LANG
    WHISPER_LANG = LANGUAGES.get(cfg.get("language", "auto"), None)
    AS.CUT_ON_SPEAKER_CHANGE = bool(cfg["cut_on_speaker"])


def _ensure_fonts():
    fonts_dir = os.path.join(WORK, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    for src in FONT_FILES:
        dst = os.path.join(fonts_dir, os.path.basename(src))
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst)


def _chunk_job(job):
    import io
    import contextlib
    configure(job["cfg"])
    buf = io.StringIO()
    out_path = os.path.join(CHUNK_OUT, f"{job['id']}.mp4")
    try:
        with contextlib.redirect_stdout(buf):
            segs = load_segments()
            build_window(job["id"], job["start"], segs, out_path)
            tmp = os.path.join(WORK, f"{job['id']}.mp4")
            if os.path.exists(tmp):
                os.remove(tmp)
        return job["id"], True, out_path
    except BaseException as e:  # keep the batch going if one window fails
        return job["id"], False, f"{e!r}\n{buf.getvalue()[-800:]}"


def run_pipeline(cfg, log=print, progress=None, should_stop=lambda: False, only=None):
    """Whole process: transcript -> plan windows -> parallel render. Returns list of outputs."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    cfg = dict(DEFAULT_CONFIG, **cfg)
    if not os.path.exists(cfg["video"]):
        raise FileNotFoundError(cfg["video"])
    os.makedirs(cfg["out_dir"], exist_ok=True)

    cfg["_transcript_json"] = prepare_transcript(
        cfg, log, (lambda d, t: progress(d, t, None, "transcribe")) if progress else None)
    configure(cfg)
    _ensure_fonts()

    segs = load_segments()
    total = probe_duration(MASTER)
    plan = chunk_windows(segs, total)
    with open(os.path.join(CHUNK_OUT, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=1)
    todo = [j for j in plan if (not only or j["id"] in only)
            and not (cfg["skip_existing"] and os.path.exists(os.path.join(CHUNK_OUT, f"{j['id']}.mp4")))]
    log(f"Video {total / 60:.1f} min -> {len(plan)} shorts planned, {len(todo)} to render "
        f"with {WORKERS} workers")
    if progress:
        progress(0, len(todo))

    outputs, done = [], 0
    ex = ProcessPoolExecutor(max_workers=WORKERS)
    try:
        futs = [ex.submit(_chunk_job, dict(j, cfg=cfg)) for j in todo]
        for fut in as_completed(futs):
            jid, ok, info = fut.result()
            done += 1
            if ok:
                outputs.append(info)
                log(f"[{done}/{len(todo)}] {jid} OK")
            else:
                log(f"[{done}/{len(todo)}] {jid} FAILED: {info}")
            if progress:
                progress(done, len(todo), info if ok else None)
            if should_stop():
                log("Stopping: waiting for the videos already rendering to finish ...")
                for f_ in futs:
                    f_.cancel()
                break
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    log("Finished." if not should_stop() else "Stopped.")
    return outputs


def run_chunks(only=None):
    cfg = dict(DEFAULT_CONFIG)
    cfg["transcript"] = TRANSCRIPT  # CLI keeps using the project's transcript.json
    run_pipeline(cfg, log=lambda m: print(m, flush=True), only=only)


def main():
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(FINAL, exist_ok=True)
    fonts_dir = os.path.join(WORK, "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    for src in FONT_FILES:
        dst = os.path.join(fonts_dir, os.path.basename(src))
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst)

    if len(sys.argv) > 1 and sys.argv[1] == "--chunks":
        run_chunks(only=set(sys.argv[2:]) or None)
        print("\nDone.")
        return

    with open(CANDIDATES, "r", encoding="utf-8") as f:
        cands = json.load(f)["candidates"]
    if len(sys.argv) > 1:
        cands = [c for c in cands if c["id"] in sys.argv[1:]]

    segs = load_segments()
    total = probe_duration(MASTER)

    win_path = os.path.join(WORK, "windows.json")
    windows = {}
    if os.path.exists(win_path):
        with open(win_path, "r", encoding="utf-8") as f:
            windows = json.load(f)

    for cand in cands:
        build(cand, segs, total, windows)
        with open(win_path, "w", encoding="utf-8") as f:
            json.dump(windows, f, indent=1)

    print("\nDone.")


if __name__ == "__main__":
    main()
