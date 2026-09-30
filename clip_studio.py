"""
Clip Studio v1.1 - local desktop app: long video -> vertical shorts.

Steps (sidebar):
  1 Video          choose the long video, language, where to save
  2 Look           number/length of shorts, top text, caption size (live phone preview)
  3 Music          optional: trim a song on its waveform, volume, auto-duck under speech
  4 Split screen   optional: speaker on top, your own clip on the bottom (trimmed/looped)
  5 Create         test one short, create all, progress, finished list

Everything runs on this PC. Launch: "Clip Studio.bat".
"""

import json
import multiprocessing
import os
import queue
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import numpy as np

import build_shorts as B

APP_VERSION = "1.1"
ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(ROOT, "clip_studio_settings.json")
TMP_DIR = os.path.join(ROOT, "temp", "studio")
NOWIN = B.NOWIN

# palette
BG = "#121419"
SIDE = "#0b0d11"
CARD = "#1b1f27"
FIELD = "#262b35"
FG = "#eceef2"
MUTED = "#98a0ad"
ACCENT = "#ffe133"
GREEN = "#4dff88"
LINE = "#2c323d"

LANG_CHOICES = {"Auto-detect (recommended)": "auto", "Hindi / Hinglish": "hindi", "English": "english"}
QUALITY_CHOICES = {"Best (recommended)": "large-v3-turbo", "Fast, less accurate": "small"}
STEPS = ["Video", "Look & captions", "Music", "Split screen", "Create"]
VIDEO_TYPES = [("Video", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("All files", "*.*")]
AUDIO_TYPES = [("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma"), ("All files", "*.*")]


# --------------------------------------------------------------------- helpers

def probe(path):
    """duration (s), width, height of a media file (0 if unknown)."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height,codec_type",
             "-of", "json", path], capture_output=True, text=True, creationflags=NOWIN).stdout
        info = json.loads(out)
        dur = float(info.get("format", {}).get("duration", 0) or 0)
        w = h = 0
        for s in info.get("streams", []):
            if s.get("codec_type") == "video" and s.get("width"):
                w, h = s["width"], s["height"]
                break
        return dur, w, h
    except (OSError, ValueError):
        return 0.0, 0, 0


def fmt_time(sec):
    sec = max(0.0, float(sec))
    m, s = divmod(sec, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def fmt_len(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h} h {m} min" if h else (f"{m} min {s} s" if m else f"{s} s")


def make_thumb(video, dur, width=300):
    """Grab one frame as a PNG for a Tk PhotoImage."""
    os.makedirs(TMP_DIR, exist_ok=True)
    png = os.path.join(TMP_DIR, f"thumb_{abs(hash(video)) % 10**8}.png")
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{max(0.0, dur * 0.2):.2f}", "-i", video, "-frames:v", "1",
                    "-vf", f"scale={width}:-2", png], creationflags=NOWIN)
    return png if os.path.exists(png) else None


def waveform_peaks(path, bins):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "4000",
                          "-f", "s16le", "-"], capture_output=True, creationflags=NOWIN).stdout
    a = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.float32)) / 32768.0
    if len(a) < bins:
        return np.zeros(bins)
    a = a[:len(a) // bins * bins].reshape(bins, -1).max(axis=1)
    return a / max(float(a.max()), 1e-6)


# --------------------------------------------------------------------- widgets

class PhonePreview(tk.Canvas):
    """Tiny live mock-up of the short's layout."""

    def __init__(self, parent, w=200, h=356):
        super().__init__(parent, width=w, height=h, bg=CARD, highlightthickness=0)
        self.w, self.h = w, h

    def draw(self, split=False, top_text="", caption_size=100, music=False):
        self.delete("all")
        w, h, p = self.w, self.h, 8
        x0, y0, x1, y1 = p, p, w - p, h - p
        self.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, outline="#3a404c", width=3)
        mid = (y0 + y1) / 2
        if split:
            self._scene(x0, y0, x1, mid)
            self.create_rectangle(x0, mid, x1, y1, fill="#1d3b2f", outline="")
            for k in range(-12, 16):
                xx = x0 + k * 14
                self.create_line(xx, y1, xx + (y1 - mid), mid, fill="#25503e")
            self.create_rectangle(x0, mid, x1, y1, outline="")
            self.create_text((x0 + x1) / 2, (mid + y1) / 2 + 22, text="your video",
                             fill="#8fd9b6", font=("Segoe UI", 9, "italic"))
            cap_y = mid
        else:
            self._scene(x0, y0, x1, y1)
            cap_y = y0 + (y1 - y0) * (1250 / 1920)
        if top_text.strip():
            t = top_text.strip().upper()[:22]
            tid = self.create_text((x0 + x1) / 2, y0 + (y1 - y0) * 0.155, text=t, fill="#111",
                                   font=("Segoe UI Black", 8))
            bx = self.bbox(tid)
            r = self.create_rectangle(bx[0] - 5, bx[1] - 3, bx[2] + 5, bx[3] + 3, fill=ACCENT, outline="")
            self.tag_lower(r, tid)
        size = max(8, int(11 * caption_size / 100))
        cx = (x0 + x1) / 2 - 6
        f = ("Segoe UI Black", size)
        a = self.create_text(0, cap_y, text="POPPING ", fill="white", font=f, anchor="w")
        b = self.create_text(0, cap_y, text="CAPTIONS", fill=ACCENT, font=f, anchor="w")
        wa = self.bbox(a)[2] - self.bbox(a)[0]
        wb = self.bbox(b)[2] - self.bbox(b)[0]
        self.move(a, cx - (wa + wb) / 2, 0)
        self.move(b, cx - (wa + wb) / 2 + wa, 0)
        # platform UI hints (right rail + bottom caption area)
        for k in range(4):
            self.create_oval(x1 - 20, y1 - 150 + k * 30, x1 - 8, y1 - 138 + k * 30, outline="#6b7280")
        self.create_line(x0 + 12, y1 - 34, x0 + 110, y1 - 34, fill="#6b7280", width=3)
        self.create_line(x0 + 12, y1 - 22, x0 + 80, y1 - 22, fill="#6b7280", width=3)
        if music:
            self.create_text(x0 + 14, y1 - 50, text="♪", fill=GREEN, font=("Segoe UI", 12), anchor="w")

    def _scene(self, x0, y0, x1, y1):
        self.create_rectangle(x0, y0, x1, y1, fill="#2a3140", outline="")
        half = (y1 - y0) < (x1 - x0) * 1.2  # split-screen top half: keep the face below the top text
        cx, cy = (x0 + x1) / 2, y0 + (y1 - y0) * (0.55 if half else 0.45)
        r = min(x1 - x0, y1 - y0) * (0.13 if half else 0.16)
        self.create_arc(cx - 2.2 * r, cy + 1.1 * r, cx + 2.2 * r, cy + 4.5 * r, start=0, extent=180,
                        fill="#e8e8e8", outline="")
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#c9a488", outline="")


class Waveform(tk.Canvas):
    """Song waveform with draggable start/end handles."""
    PAD = 14

    def __init__(self, parent, on_change):
        super().__init__(parent, height=120, bg="#0f1115", highlightthickness=0, cursor="sb_h_double_arrow")
        self.on_change = on_change
        self.peaks = None
        self.duration = 0.0
        self.start = 0.0
        self.end = 0.0
        self.enabled = True
        self._drag = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    def load(self, peaks, duration, start, end):
        self.peaks, self.duration = peaks, duration
        self.start, self.end = start, (end if end > start else duration)
        self.redraw()

    def _x(self, t):
        w = max(self.winfo_width(), 50)
        return self.PAD + (w - 2 * self.PAD) * (t / self.duration if self.duration else 0)

    def _t(self, x):
        w = max(self.winfo_width(), 50)
        f = (x - self.PAD) / max(w - 2 * self.PAD, 1)
        return min(max(f, 0.0), 1.0) * self.duration

    def redraw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 50), max(self.winfo_height(), 50)
        if self.peaks is None or not self.duration:
            self.create_text(w / 2, h / 2, text="Choose a song to see its waveform", fill=MUTED,
                             font=("Segoe UI", 10))
            return
        xs, xe = self._x(self.start), self._x(self.end)
        self.create_rectangle(xs, 4, xe, h - 4, fill="#2b2a14" if self.enabled else "#1a1c20", outline="")
        n = len(self.peaks)
        mid = h / 2
        on = ACCENT if self.enabled else "#6b6a4a"
        for i, p in enumerate(self.peaks):
            x = self.PAD + (w - 2 * self.PAD) * i / n
            color = on if xs <= x <= xe else "#4b5260"
            self.create_line(x, mid - p * (h / 2 - 8), x, mid + p * (h / 2 - 8), fill=color)
        for x, label in ((xs, "start"), (xe, "end")):
            self.create_line(x, 2, x, h - 2, fill="white", width=2)
            self.create_rectangle(x - 6, 2, x + 6, 16, fill="white", outline="")
            self.create_text(x, h - 8, text=label, fill="white", font=("Segoe UI", 8))

    def _press(self, e):
        if not self.duration or not self.enabled:
            return
        xs, xe = self._x(self.start), self._x(self.end)
        self._drag = "start" if abs(e.x - xs) <= abs(e.x - xe) else "end"
        self._motion(e)

    def _motion(self, e):
        if not self._drag or not self.duration:
            return
        t = self._t(e.x)
        if self._drag == "start":
            self.start = min(t, self.end - 0.5)
        else:
            self.end = max(t, self.start + 0.5)
        self.start, self.end = max(0.0, self.start), min(self.duration, self.end)
        self.redraw()
        self.on_change()


# --------------------------------------------------------------------- app

class ClipStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Clip Studio {APP_VERSION}")
        self.geometry("1180x800")
        self.minsize(1080, 740)
        self.configure(bg=BG)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.t_start = None
        self.page = 0
        self.images = {}
        self.video_info = (0.0, 0, 0)
        self.bottom_info = (0.0, 0, 0)
        self.song_duration = 0.0
        self._ready = False

        self._style()
        self._vars()
        self._build()
        self._ready = True
        self._load_settings()
        self._show(0)
        self.after(200, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------ style / vars

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=CARD, foreground=FG, fieldbackground=FIELD, font=("Segoe UI", 10),
                     bordercolor=LINE, lightcolor=CARD, darkcolor=CARD)
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=CARD, foreground=FG)
        st.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 20))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        st.configure("Q.TLabel", background=CARD, foreground=FG, font=("Segoe UI Semibold", 11))
        st.configure("Help.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("Big.TLabel", background=CARD, foreground=FG, font=("Segoe UI Semibold", 12))
        st.configure("Accent.TLabel", background=CARD, foreground=ACCENT, font=("Segoe UI Semibold", 11))
        st.configure("Summary.TLabel", background=CARD, foreground=FG, font=("Consolas", 10))
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=6)
        st.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, padding=5)
        st.configure("TCombobox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, padding=5)
        st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        st.configure("TCheckbutton", background=CARD, foreground=FG, font=("Segoe UI", 10))
        st.map("TCheckbutton", background=[("active", CARD)])
        st.configure("Switch.TCheckbutton", background=CARD, foreground=ACCENT, font=("Segoe UI Semibold", 12))
        st.map("Switch.TCheckbutton", background=[("active", CARD)])
        st.configure("TButton", background="#2f3542", foreground=FG, borderwidth=0, padding=(14, 8))
        st.map("TButton", background=[("active", "#3b4252"), ("disabled", "#23272f")],
               foreground=[("disabled", "#5d636e")])
        st.configure("Primary.TButton", background=ACCENT, foreground="#111", font=("Segoe UI Semibold", 12),
                     padding=(26, 11))
        st.map("Primary.TButton", background=[("active", "#fff07a"), ("disabled", "#4f4a24")])
        st.configure("Pick.TButton", background="#2f3542", foreground=FG, font=("Segoe UI Semibold", 11),
                     padding=(18, 12))
        st.configure("Horizontal.TProgressbar", troughcolor=FIELD, background=ACCENT,
                     bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=14)
        st.configure("Horizontal.TScale", background=CARD, troughcolor=FIELD)
        st.configure("TNotebook", background=CARD, borderwidth=0)
        st.configure("TNotebook.Tab", background=FIELD, foreground=MUTED, padding=(14, 6))
        st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", FG)])

    def _vars(self):
        d = B.DEFAULT_CONFIG
        self.v_video = tk.StringVar(value=d["video"] if os.path.exists(d["video"]) else "")
        self.v_out = tk.StringVar(value=d["out_dir"] if os.path.isdir(d["out_dir"])
                                  else os.path.join(ROOT, "output"))
        self.v_lang = tk.StringVar(value=list(LANG_CHOICES)[0])
        self.v_quality = tk.StringVar(value=list(QUALITY_CHOICES)[0])
        self.v_total = tk.IntVar(value=100)
        self.v_len = tk.IntVar(value=d["clip_seconds"])
        self.v_top = tk.StringVar(value=d["top_text"])
        self.v_cap = tk.IntVar(value=d["caption_size"])
        self.v_workers = tk.IntVar(value=d["workers"])
        self.v_cut = tk.BooleanVar(value=d["cut_on_speaker"])
        self.v_skip = tk.BooleanVar(value=d["skip_existing"])
        # music
        self.v_music_on = tk.BooleanVar(value=False)
        self.v_music = tk.StringVar(value="")
        self.v_m_start = tk.StringVar(value="0.0")
        self.v_m_end = tk.StringVar(value="0.0")
        self.v_m_vol = tk.IntVar(value=18)
        self.v_m_duck = tk.BooleanVar(value=True)
        # split
        self.v_split_on = tk.BooleanVar(value=False)
        self.v_bottom = tk.StringVar(value="")
        self.v_vary = tk.BooleanVar(value=True)
        # status
        self.v_status = tk.StringVar(value="Ready.")
        for var in (self.v_top, self.v_cap, self.v_split_on, self.v_music_on, self.v_total, self.v_len,
                    self.v_cut, self.v_lang, self.v_m_vol):
            var.trace_add("write", lambda *_: self._refresh_all())

    # ------------------------------------------------ layout

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        side = tk.Frame(self, bg=SIDE, width=230)
        side.grid(row=0, column=0, rowspan=2, sticky="ns")
        side.grid_propagate(False)
        side.pack_propagate(False)
        tk.Label(side, text="Clip Studio", bg=SIDE, fg=FG, font=("Segoe UI Semibold", 19)).pack(
            anchor="w", padx=22, pady=(24, 0))
        tk.Label(side, text=f"version {APP_VERSION}", bg=SIDE, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=22, pady=(0, 24))
        self.nav = []
        for i, name in enumerate(STEPS):
            row = tk.Frame(side, bg=SIDE, cursor="hand2")
            row.pack(fill="x", padx=12, pady=3)
            num = tk.Label(row, text=str(i + 1), width=3, bg=FIELD, fg=FG, font=("Segoe UI Semibold", 11))
            num.pack(side="left", padx=(8, 10), pady=8)
            txt = tk.Label(row, text=name, bg=SIDE, fg=FG, font=("Segoe UI", 11), anchor="w")
            txt.pack(side="left", fill="x", expand=True)
            tag = tk.Label(row, text="", bg=SIDE, fg=MUTED, font=("Segoe UI", 9))
            tag.pack(side="right", padx=10)
            for wdg in (row, num, txt, tag):
                wdg.bind("<Button-1>", lambda e, k=i: self._show(k))
            self.nav.append((row, num, txt, tag))
        tk.Label(side, text="Everything runs on this PC.\nNothing is uploaded.", bg=SIDE, fg=MUTED,
                 font=("Segoe UI", 9), justify="left").pack(side="bottom", anchor="w", padx=22, pady=20)

        main = ttk.Frame(self)
        main.grid(row=0, column=1, sticky="nsew", padx=26, pady=(20, 0))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        self.h_title = ttk.Label(main, style="Title.TLabel")
        self.h_title.grid(row=0, column=0, sticky="w")
        self.h_sub = ttk.Label(main, style="Sub.TLabel")
        self.h_sub.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.pages_host = ttk.Frame(main)
        self.pages_host.grid(row=2, column=0, sticky="nsew", pady=(14, 0))
        self.pages_host.columnconfigure(0, weight=1)
        self.pages_host.rowconfigure(0, weight=1)

        self.pages = [self._page_video(), self._page_look(), self._page_music(),
                      self._page_split(), self._page_create()]
        for p in self.pages:
            p.grid(row=0, column=0, sticky="nsew")

        bar = ttk.Frame(self)
        bar.grid(row=1, column=1, sticky="ew", padx=26, pady=16)
        self.b_back = ttk.Button(bar, text="←  Back", command=lambda: self._show(self.page - 1))
        self.b_back.pack(side="left")
        self.b_next = ttk.Button(bar, text="Next  →", style="Primary.TButton",
                                 command=lambda: self._show(self.page + 1))
        self.b_next.pack(side="right")

    def _card(self, parent, **grid):
        c = ttk.Frame(parent, style="Card.TFrame", padding=18)
        c.grid(sticky="nsew", **grid)
        return c

    def _page_video(self):
        pg = ttk.Frame(self.pages_host)
        pg.columnconfigure(0, weight=1)
        c = self._card(pg, row=0, column=0, pady=(0, 12))
        c.columnconfigure(1, weight=1)
        self.thumb_video = tk.Label(c, bg="#0f1115", width=40, height=10, text="No video yet",
                                    fg=MUTED, font=("Segoe UI", 10))
        self.thumb_video.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 20))
        ttk.Button(c, text="Choose video...", style="Pick.TButton", command=self._pick_video).grid(
            row=0, column=1, sticky="w")
        self.l_video = ttk.Label(c, text="", style="Big.TLabel", wraplength=560)
        self.l_video.grid(row=1, column=1, sticky="w", pady=(12, 2))
        self.l_video_info = ttk.Label(c, text="Pick the long video you want to cut into shorts.",
                                      style="Help.TLabel", wraplength=560)
        self.l_video_info.grid(row=2, column=1, sticky="nw")

        c2 = self._card(pg, row=1, column=0, pady=(0, 12))
        c2.columnconfigure(1, weight=1)
        ttk.Label(c2, text="What language do people speak?", style="Q.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(c2, textvariable=self.v_lang, values=list(LANG_CHOICES), state="readonly",
                     width=28).grid(row=0, column=1, sticky="w", padx=14)
        ttk.Label(c2, text="Hindi and Hinglish are written in English letters in the captions.",
                  style="Help.TLabel").grid(row=1, column=1, sticky="w", padx=14, pady=(2, 12))
        ttk.Label(c2, text="Transcription", style="Q.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Combobox(c2, textvariable=self.v_quality, values=list(QUALITY_CHOICES), state="readonly",
                     width=28).grid(row=2, column=1, sticky="w", padx=14)
        ttk.Label(c2, text="The video is transcribed on this PC (roughly 1 minute per 5 minutes of video). "
                           "It is saved, so it only happens once per video.",
                  style="Help.TLabel", wraplength=600).grid(row=3, column=1, sticky="w", padx=14, pady=(2, 0))

        c3 = self._card(pg, row=2, column=0)
        c3.columnconfigure(1, weight=1)
        ttk.Label(c3, text="Save shorts to", style="Q.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(c3, textvariable=self.v_out).grid(row=0, column=1, sticky="ew", padx=14)
        ttk.Button(c3, text="Change...", command=self._pick_out).grid(row=0, column=2)
        ttk.Button(c3, text="Open", command=self._open_out).grid(row=0, column=3, padx=(8, 0))
        ttk.Label(c3, text="Tip: use a new folder for each video.", style="Help.TLabel").grid(
            row=1, column=1, sticky="w", padx=14, pady=(4, 0))
        return pg

    def _page_look(self):
        pg = ttk.Frame(self.pages_host)
        pg.columnconfigure(0, weight=1)
        c = self._card(pg, row=0, column=0, padx=(0, 12))
        c.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(c, text="How many shorts?", style="Q.TLabel").grid(row=r, column=0, sticky="w")
        ttk.Spinbox(c, from_=1, to=500, textvariable=self.v_total, width=7).grid(row=r, column=1, sticky="w",
                                                                                 padx=14)
        r += 1
        self.l_total_help = ttk.Label(c, text="", style="Help.TLabel", wraplength=460)
        self.l_total_help.grid(row=r, column=1, sticky="w", padx=14, pady=(2, 14))
        r += 1
        ttk.Label(c, text="Length of each short", style="Q.TLabel").grid(row=r, column=0, sticky="w")
        lf = ttk.Frame(c, style="Card.TFrame")
        lf.grid(row=r, column=1, sticky="w", padx=14)
        ttk.Spinbox(lf, from_=10, to=90, textvariable=self.v_len, width=5).pack(side="left")
        ttk.Label(lf, text="seconds", style="Help.TLabel").pack(side="left", padx=6)
        r += 1
        ttk.Frame(c, style="Card.TFrame", height=14).grid(row=r, column=0)
        r += 1
        ttk.Label(c, text="Text at the top", style="Q.TLabel").grid(row=r, column=0, sticky="w")
        ttk.Entry(c, textvariable=self.v_top, width=30).grid(row=r, column=1, sticky="w", padx=14)
        r += 1
        ttk.Label(c, text="Shown on every short. Leave empty for none.", style="Help.TLabel").grid(
            row=r, column=1, sticky="w", padx=14, pady=(2, 14))
        r += 1
        ttk.Label(c, text="Caption size", style="Q.TLabel").grid(row=r, column=0, sticky="w")
        ttk.Scale(c, from_=70, to=140, variable=self.v_cap, length=260,
                  command=lambda v: self.v_cap.set(int(float(v)))).grid(row=r, column=1, sticky="w", padx=14)
        r += 1
        ttk.Frame(c, style="Card.TFrame", height=14).grid(row=r, column=0)
        r += 1
        ttk.Checkbutton(c, text="Cut to the new face when someone else starts talking",
                        variable=self.v_cut).grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        adv = ttk.Frame(c, style="Card.TFrame")
        adv.grid(row=r, column=0, columnspan=2, sticky="w", pady=(22, 0))
        ttk.Label(adv, text="Advanced:  shorts made at the same time", style="Help.TLabel").pack(side="left")
        ttk.Spinbox(adv, from_=1, to=8, textvariable=self.v_workers, width=4).pack(side="left", padx=8)
        ttk.Checkbutton(adv, text="Skip shorts already made", variable=self.v_skip).pack(side="left", padx=12)

        pc = self._card(pg, row=0, column=1)
        ttk.Label(pc, text="Preview", style="Accent.TLabel").pack(anchor="w", pady=(0, 8))
        self.phone_look = PhonePreview(pc)
        self.phone_look.pack()
        return pg

    def _page_music(self):
        pg = ttk.Frame(self.pages_host)
        pg.columnconfigure(0, weight=1)
        c = self._card(pg, row=0, column=0)
        c.columnconfigure(1, weight=1)
        ttk.Checkbutton(c, text="Add background music", style="Switch.TCheckbutton",
                        variable=self.v_music_on).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(c, text="Optional. Trim a song and it plays quietly under the speech in every short.",
                  style="Help.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 14))
        self.music_widgets = []
        b = ttk.Button(c, text="Choose song...", style="Pick.TButton", command=self._pick_music)
        b.grid(row=2, column=0, sticky="w")
        self.l_music = ttk.Label(c, text="No song chosen", style="Big.TLabel")
        self.l_music.grid(row=2, column=1, columnspan=2, sticky="w", padx=14)

        self.wave = Waveform(c, on_change=self._wave_changed)
        self.wave.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(14, 6))
        ttk.Label(c, text="Drag the white handles to choose the part of the song you want.",
                  style="Help.TLabel").grid(row=4, column=0, columnspan=3, sticky="w")

        tf = ttk.Frame(c, style="Card.TFrame")
        tf.grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))
        ttk.Label(tf, text="Start (s)", style="Q.TLabel").pack(side="left")
        e1 = ttk.Entry(tf, textvariable=self.v_m_start, width=8)
        e1.pack(side="left", padx=(6, 18))
        ttk.Label(tf, text="End (s)", style="Q.TLabel").pack(side="left")
        e2 = ttk.Entry(tf, textvariable=self.v_m_end, width=8)
        e2.pack(side="left", padx=(6, 18))
        for e in (e1, e2):
            e.bind("<Return>", lambda ev: self._times_typed())
            e.bind("<FocusOut>", lambda ev: self._times_typed())
        self.l_sel = ttk.Label(tf, text="", style="Help.TLabel")
        self.l_sel.pack(side="left", padx=(0, 18))
        b2 = ttk.Button(tf, text="▶  Play selection", command=self._play_selection)
        b2.pack(side="left")
        b3 = ttk.Button(tf, text="Whole song", command=self._whole_song)
        b3.pack(side="left", padx=8)
        self.music_widgets += [e1, e2, b2, b3]

        vf = ttk.Frame(c, style="Card.TFrame")
        vf.grid(row=6, column=0, columnspan=3, sticky="w", pady=(18, 0))
        ttk.Label(vf, text="Music volume", style="Q.TLabel").pack(side="left")
        sc = ttk.Scale(vf, from_=0, to=60, variable=self.v_m_vol, length=240,
                       command=lambda v: self.v_m_vol.set(int(float(v))))
        sc.pack(side="left", padx=12)
        self.l_vol = ttk.Label(vf, text="", style="Help.TLabel", width=6)
        self.l_vol.pack(side="left")
        ck = ttk.Checkbutton(vf, text="Lower the music while people talk (recommended)", variable=self.v_m_duck)
        ck.pack(side="left", padx=18)
        self.music_widgets += [sc, ck]
        ttk.Label(c, text="If your selection is shorter than a short, it repeats to fill it.",
                  style="Help.TLabel").grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        return pg

    def _page_split(self):
        pg = ttk.Frame(self.pages_host)
        pg.columnconfigure(0, weight=1)
        c = self._card(pg, row=0, column=0, padx=(0, 12))
        c.columnconfigure(1, weight=1)
        ttk.Checkbutton(c, text="Use split screen", style="Switch.TCheckbutton",
                        variable=self.v_split_on).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(c, text="Optional. The speaker fills the top half; a video you choose fills the bottom half "
                          "(gameplay, satisfying clips, b-roll...).", style="Help.TLabel",
                  wraplength=560).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 16))
        self.split_widgets = []
        b = ttk.Button(c, text="Choose bottom video...", style="Pick.TButton", command=self._pick_bottom)
        b.grid(row=2, column=0, sticky="w")
        self.thumb_bottom = tk.Label(c, bg="#0f1115", width=36, height=9, text="No video yet", fg=MUTED,
                                     font=("Segoe UI", 10))
        self.thumb_bottom.grid(row=3, column=0, sticky="w", pady=(14, 0))
        info = ttk.Frame(c, style="Card.TFrame")
        info.grid(row=3, column=1, sticky="nw", padx=18, pady=(14, 0))
        self.l_bottom = ttk.Label(info, text="", style="Big.TLabel", wraplength=300)
        self.l_bottom.pack(anchor="w")
        self.l_bottom_info = ttk.Label(info, text="", style="Help.TLabel", wraplength=300)
        self.l_bottom_info.pack(anchor="w", pady=(6, 0))
        ck = ttk.Checkbutton(c, text="Use a different part of this video in each short", variable=self.v_vary)
        ck.grid(row=4, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self.split_widgets.append(ck)
        ttk.Label(c, text="Longer videos are cut to fit each short; shorter ones repeat until the short ends. "
                          "The bottom video's own sound is not used.", style="Help.TLabel",
                  wraplength=560).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        pc = self._card(pg, row=0, column=1)
        ttk.Label(pc, text="Preview", style="Accent.TLabel").pack(anchor="w", pady=(0, 8))
        self.phone_split = PhonePreview(pc)
        self.phone_split.pack()
        return pg

    def _page_create(self):
        pg = ttk.Frame(self.pages_host)
        pg.columnconfigure(0, weight=1)
        pg.rowconfigure(2, weight=1)
        c = self._card(pg, row=0, column=0, pady=(0, 12))
        c.columnconfigure(0, weight=1)
        self.l_summary = ttk.Label(c, text="", style="Summary.TLabel", justify="left")
        self.l_summary.grid(row=0, column=0, sticky="w")

        a = self._card(pg, row=1, column=0, pady=(0, 12))
        a.columnconfigure(4, weight=1)
        self.b_test = ttk.Button(a, text="Test one short", command=lambda: self._start(test=True))
        self.b_test.grid(row=0, column=0, padx=(0, 10))
        self.b_go = ttk.Button(a, text="Create shorts", style="Primary.TButton", command=self._start)
        self.b_go.grid(row=0, column=1, padx=(0, 10))
        self.b_stop = ttk.Button(a, text="Stop", command=self._stop, state="disabled")
        self.b_stop.grid(row=0, column=2, padx=(0, 10))
        ttk.Button(a, text="Open folder", command=self._open_out).grid(row=0, column=3)
        self.pb = ttk.Progressbar(a, mode="determinate")
        self.pb.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(16, 4))
        ttk.Label(a, textvariable=self.v_status, style="Help.TLabel").grid(row=2, column=0, columnspan=5,
                                                                            sticky="w")
        ttk.Label(a, text="Tip: press \"Test one short\" first to check the look before making them all.",
                  style="Help.TLabel").grid(row=3, column=0, columnspan=5, sticky="w", pady=(6, 0))

        nb = ttk.Notebook(pg)
        nb.grid(row=2, column=0, sticky="nsew")
        lf = ttk.Frame(nb, style="Card.TFrame", padding=8)
        self.lst = tk.Listbox(lf, bg="#0f1115", fg=FG, selectbackground="#3b4252", relief="flat",
                              font=("Segoe UI", 10), activestyle="none", highlightthickness=0)
        self.lst.pack(fill="both", expand=True)
        self.lst.bind("<Double-Button-1>", self._play_selected)
        nb.add(lf, text="Finished shorts (double-click to play)")
        gf = ttk.Frame(nb, style="Card.TFrame", padding=8)
        self.log = ScrolledText(gf, height=8, bg="#0f1115", fg="#cfd3da", insertbackground=FG,
                                relief="flat", font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True)
        nb.add(gf, text="Details")
        return pg

    # ------------------------------------------------ navigation / refresh

    def _show(self, k):
        k = max(0, min(len(self.pages) - 1, k))
        self.page = k
        self.pages[k].tkraise()
        titles = [("Choose your video", "Pick the long video. Everything else is optional."),
                  ("How your shorts look", "Number of shorts, length, top text and captions."),
                  ("Background music", "Optional add-on. Skip this step if you don't want music."),
                  ("Split screen", "Optional add-on. Skip this step for normal full-screen shorts."),
                  ("Create your shorts", "Check the summary, test one short, then create them all.")]
        self.h_title.configure(text=titles[k][0])
        self.h_sub.configure(text=titles[k][1])
        for i, (row, num, txt, tag) in enumerate(self.nav):
            on = i == k
            bg = CARD if on else SIDE
            for wdg in (row, txt, tag):
                wdg.configure(bg=bg)
            num.configure(bg=ACCENT if on else FIELD, fg="#111" if on else FG)
            txt.configure(font=("Segoe UI Semibold", 11) if on else ("Segoe UI", 11))
        self.b_back.configure(state="normal" if k > 0 else "disabled")
        if k == len(self.pages) - 1:
            self.b_next.pack_forget()
        elif not self.b_next.winfo_ismapped():
            self.b_next.pack(side="right")
        self._refresh_all()

    def _refresh_all(self):
        if not self._ready:
            return
        try:
            total, length = int(self.v_total.get()), int(self.v_len.get())
        except (tk.TclError, ValueError):
            total, length = 0, 0
        dur = self.video_info[0]
        if dur and total:
            self.l_total_help.configure(
                text=f"Spread across the whole {fmt_len(dur)} video (a new short about every "
                     f"{fmt_len(dur / total)}).")
        else:
            self.l_total_help.configure(text="Spread evenly across the whole video.")
        split = bool(self.v_split_on.get())
        music = bool(self.v_music_on.get())
        try:
            cap = int(self.v_cap.get())
        except (tk.TclError, ValueError):
            cap = 100
        for ph in (self.phone_look, self.phone_split):
            ph.draw(split=split, top_text=self.v_top.get(), caption_size=cap, music=music)
        for w_ in self.music_widgets:
            w_.configure(state="normal" if music else "disabled")
        for w_ in self.split_widgets:
            w_.configure(state="normal" if split else "disabled")
        self.wave.enabled = music
        self.wave.redraw()
        try:
            self.l_vol.configure(text=f"{int(self.v_m_vol.get())}%")
        except (tk.TclError, ValueError):
            pass
        self.nav[0][3].configure(text="✓" if self.v_video.get() else "", fg=GREEN)
        self.nav[2][3].configure(text="On" if music else "Off", fg=GREEN if music else MUTED)
        self.nav[3][3].configure(text="On" if split else "Off", fg=GREEN if split else MUTED)
        self.l_summary.configure(text=self._summary())
        self.b_go.configure(text=f"Create {total} shorts" if total else "Create shorts")

    def _summary(self):
        v = self.v_video.get()
        rows = [("Video", os.path.basename(v) if v else "(not chosen yet)"),
                ("Language", self.v_lang.get()),
                ("Shorts", f"{self.v_total.get()} x {self.v_len.get()} seconds, 1080x1920"),
                ("Top text", self.v_top.get() or "(none)"),
                ("Captions", f"size {self.v_cap.get()}, cut on speaker change: "
                             f"{'yes' if self.v_cut.get() else 'no'}")]
        if self.v_music_on.get() and self.v_music.get():
            rows.append(("Music", f"{os.path.basename(self.v_music.get())}  ({self.v_m_start.get()}s - "
                                  f"{self.v_m_end.get()}s, volume {self.v_m_vol.get()}%)"))
        else:
            rows.append(("Music", "off"))
        if self.v_split_on.get() and self.v_bottom.get():
            rows.append(("Split screen", f"bottom = {os.path.basename(self.v_bottom.get())}"))
        else:
            rows.append(("Split screen", "off"))
        rows.append(("Save to", self.v_out.get()))
        return "\n".join(f"{k:<14}{v}" for k, v in rows)

    # ------------------------------------------------ pickers

    def _set_thumb(self, label, key, path, dur):
        png = make_thumb(path, dur, width=300)
        if png:
            img = tk.PhotoImage(file=png)
            self.images[key] = img
            label.configure(image=img, text="", width=img.width(), height=img.height())

    def _pick_video(self):
        p = filedialog.askopenfilename(title="Choose the long video", filetypes=VIDEO_TYPES)
        if p:
            self.v_video.set(os.path.normpath(p))
            self._load_video_info()

    def _load_video_info(self):
        p = self.v_video.get()
        if not p or not os.path.exists(p):
            return
        self.config(cursor="watch")
        self.update_idletasks()
        self.video_info = probe(p)
        dur, w, h = self.video_info
        self.l_video.configure(text=os.path.basename(p))
        self.l_video_info.configure(text=f"{fmt_len(dur)}   ·   {w} x {h}\n{p}")
        self._set_thumb(self.thumb_video, "video", p, dur)
        self.config(cursor="")
        self._refresh_all()

    def _pick_out(self):
        p = filedialog.askdirectory(title="Where should the shorts be saved?")
        if p:
            self.v_out.set(os.path.normpath(p))
            self._refresh_outputs()
            self._refresh_all()

    def _open_out(self):
        out = self.v_out.get()
        os.makedirs(out, exist_ok=True)
        os.startfile(out)

    def _pick_music(self):
        p = filedialog.askopenfilename(title="Choose a song", filetypes=AUDIO_TYPES)
        if p:
            self.v_music.set(os.path.normpath(p))
            self.v_music_on.set(True)
            self._load_music(reset=True)

    def _load_music(self, reset=False):
        p = self.v_music.get()
        if not p or not os.path.exists(p):
            return
        self.config(cursor="watch")
        self.update_idletasks()
        self.song_duration = probe(p)[0]
        peaks = waveform_peaks(p, 700)
        self.config(cursor="")
        if reset:
            try:
                n = float(self.v_len.get())
            except (tk.TclError, ValueError):
                n = 40.0
            self.v_m_start.set("0.0")
            self.v_m_end.set(f"{min(self.song_duration, n):.1f}")
        start, end = self._times()
        self.wave.load(peaks, self.song_duration, start, end)
        self.l_music.configure(text=f"{os.path.basename(p)}   ({fmt_time(self.song_duration)})")
        self._sel_label()

    def _times(self):
        try:
            a = float(self.v_m_start.get())
            b = float(self.v_m_end.get())
        except ValueError:
            a, b = 0.0, self.song_duration
        d = self.song_duration or max(a, b)
        a = min(max(0.0, a), d)
        b = min(max(a + 0.5, b), d) if d else b
        return a, b

    def _wave_changed(self):
        self.v_m_start.set(f"{self.wave.start:.1f}")
        self.v_m_end.set(f"{self.wave.end:.1f}")
        self._sel_label()

    def _times_typed(self):
        a, b = self._times()
        self.v_m_start.set(f"{a:.1f}")
        self.v_m_end.set(f"{b:.1f}")
        if self.wave.duration:
            self.wave.start, self.wave.end = a, b
            self.wave.redraw()
        self._sel_label()

    def _whole_song(self):
        if self.song_duration:
            self.v_m_start.set("0.0")
            self.v_m_end.set(f"{self.song_duration:.1f}")
            self._times_typed()

    def _sel_label(self):
        a, b = self._times()
        try:
            n = int(self.v_len.get())
        except (tk.TclError, ValueError):
            n = 40
        extra = "  (repeats to fill each short)" if b - a < n else ""
        self.l_sel.configure(text=f"Selection: {b - a:.1f} s{extra}")
        self._refresh_all()

    def _play_selection(self):
        p = self.v_music.get()
        if not p:
            return
        a, b = self._times()
        os.makedirs(TMP_DIR, exist_ok=True)
        out = os.path.join(TMP_DIR, "music_preview.wav")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{a:.2f}",
                        "-t", f"{b - a:.2f}", "-i", p, "-ac", "2", out], creationflags=NOWIN)
        if os.path.exists(out):
            os.startfile(out)

    def _pick_bottom(self):
        p = filedialog.askopenfilename(title="Choose the bottom video", filetypes=VIDEO_TYPES)
        if p:
            self.v_bottom.set(os.path.normpath(p))
            self.v_split_on.set(True)
            self._load_bottom_info()

    def _load_bottom_info(self):
        p = self.v_bottom.get()
        if not p or not os.path.exists(p):
            return
        self.bottom_info = probe(p)
        dur, w, h = self.bottom_info
        try:
            n = int(self.v_len.get())
        except (tk.TclError, ValueError):
            n = 40
        how = "repeats to fill" if dur < n else "is cut to fit"
        self.l_bottom.configure(text=os.path.basename(p))
        self.l_bottom_info.configure(text=f"{fmt_len(dur)}  ·  {w} x {h}\nThis video {how} each "
                                          f"{n}-second short.")
        self._set_thumb(self.thumb_bottom, "bottom", p, dur)
        self._refresh_all()

    def _refresh_outputs(self):
        self.lst.delete(0, "end")
        out = self.v_out.get()
        if os.path.isdir(out):
            for n in sorted(os.listdir(out)):
                if n.lower().endswith(".mp4"):
                    self.lst.insert("end", n)

    def _play_selected(self, _evt=None):
        sel = self.lst.curselection()
        if sel:
            os.startfile(os.path.join(self.v_out.get(), self.lst.get(sel[0])))

    # ------------------------------------------------ settings

    def _config(self):
        a, b = self._times()
        music_on = bool(self.v_music_on.get()) and bool(self.v_music.get())
        split_on = bool(self.v_split_on.get()) and bool(self.v_bottom.get())
        return {
            "video": self.v_video.get().strip(),
            "transcript": "",
            "out_dir": self.v_out.get().strip(),
            "language": LANG_CHOICES.get(self.v_lang.get(), "auto"),
            "whisper_model": QUALITY_CHOICES.get(self.v_quality.get(), "large-v3-turbo"),
            "total_shorts": int(self.v_total.get()),
            "clip_seconds": int(self.v_len.get()),
            "top_text": self.v_top.get().strip(),
            "caption_size": int(self.v_cap.get()),
            "workers": int(self.v_workers.get()),
            "cut_on_speaker": bool(self.v_cut.get()),
            "skip_existing": bool(self.v_skip.get()),
            "music_path": self.v_music.get() if music_on else "",
            "music_start": a,
            "music_end": b,
            "music_volume": int(self.v_m_vol.get()) / 100.0,
            "music_duck": bool(self.v_m_duck.get()),
            "split_screen": split_on,
            "bottom_video": self.v_bottom.get() if split_on else "",
            "bottom_vary": bool(self.v_vary.get()),
        }

    SAVED = {"video": "v_video", "out_dir": "v_out", "total": "v_total", "len": "v_len", "top": "v_top",
             "cap": "v_cap", "workers": "v_workers", "cut": "v_cut", "skip": "v_skip", "lang": "v_lang",
             "quality": "v_quality", "music_on": "v_music_on", "music": "v_music", "m_start": "v_m_start",
             "m_end": "v_m_end", "m_vol": "v_m_vol", "m_duck": "v_m_duck", "split_on": "v_split_on",
             "bottom": "v_bottom", "vary": "v_vary"}

    def _save_settings(self):
        data = {}
        for k, attr in self.SAVED.items():
            try:
                data[k] = getattr(self, attr).get()
            except tk.TclError:
                pass
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except OSError:
            pass

    def _load_settings(self):
        s = {}
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            pass
        # v1.0 settings file -> v1.1 keys
        legacy = {"clip_seconds": "len", "top_text": "top", "caption_size": "cap",
                  "cut_on_speaker": "cut", "skip_existing": "skip"}
        for old, new in legacy.items():
            if old in s and new not in s:
                s[new] = s[old]
        if "chunks" in s and "per_chunk" in s and "total" not in s:
            s["total"] = int(s["chunks"]) * int(s["per_chunk"])
        for k, attr in self.SAVED.items():
            if k not in s:
                continue
            if k == "lang" and s[k] not in LANG_CHOICES:
                continue
            if k == "quality" and s[k] not in QUALITY_CHOICES:
                continue
            try:
                getattr(self, attr).set(s[k])
            except tk.TclError:
                pass
        if self.v_video.get() and not os.path.exists(self.v_video.get()):
            self.v_video.set("")
        self._load_video_info()
        if self.v_music.get() and os.path.exists(self.v_music.get()):
            self._load_music()
        if self.v_bottom.get() and os.path.exists(self.v_bottom.get()):
            self._load_bottom_info()
        self._refresh_outputs()
        self._refresh_all()

    # ------------------------------------------------ run

    def _start(self, test=False):
        try:
            cfg = self._config()
        except (tk.TclError, ValueError):
            messagebox.showerror("Clip Studio", "Some number fields are not valid. Please check them.")
            return
        if not os.path.isfile(cfg["video"]):
            messagebox.showinfo("Clip Studio", "First choose your video (step 1).")
            self._show(0)
            return
        if self.v_music_on.get() and not cfg["music_path"]:
            messagebox.showinfo("Clip Studio", "Background music is on, but no song is chosen (step 3).\n"
                                               "Choose a song or switch music off.")
            self._show(2)
            return
        if self.v_split_on.get() and not cfg["bottom_video"]:
            messagebox.showinfo("Clip Studio", "Split screen is on, but no bottom video is chosen (step 4).\n"
                                               "Choose a video or switch split screen off.")
            self._show(3)
            return
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("Clip Studio", "FFmpeg was not found.")
            return
        if not os.path.exists(os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx")):
            messagebox.showerror("Clip Studio", "Face model missing: models\\face_detection_yunet_2023mar.onnx")
            return

        self._save_settings()
        self.stop_event.clear()
        for b in (self.b_go, self.b_test):
            b.configure(state="disabled")
        self.b_stop.configure(state="normal")
        self.pb.configure(value=0, maximum=1)
        self.v_status.set("Getting ready ...")
        self.t_start = time.time()
        self._write_log(("TEST - one short" if test else "Start") +
                        f": {os.path.basename(cfg['video'])} -> {cfg['out_dir']}")

        def work():
            try:
                outs = B.run_pipeline(
                    cfg,
                    log=lambda m: self.q.put(("log", m)),
                    progress=lambda d, t, path=None, stage="render":
                        self.q.put(("progress", (d, t, path, stage))),
                    should_stop=self.stop_event.is_set,
                    test_one=test,
                )
                self.q.put(("done", (outs, test)))
            except BaseException as e:
                self.q.put(("error", str(e) or repr(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.b_stop.configure(state="disabled")
        self.v_status.set("Stopping after the shorts that are already being made ...")

    def _write_log(self, msg):
        self.log.insert("end", time.strftime("%H:%M:%S  ") + msg + "\n")
        self.log.see("end")

    def _poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._write_log(data)
                elif kind == "progress":
                    done, total, path, stage = data
                    self.pb.configure(maximum=max(total, 1), value=done)
                    if stage == "transcribe":
                        self.v_status.set(f"Step 1 of 2  -  Listening to the video: "
                                          f"{fmt_len(done)} of {fmt_len(total)}")
                        continue
                    if done == 0:
                        self.t_start = time.time()
                    if total:
                        el = time.time() - self.t_start
                        eta = (el / done) * (total - done) if done else 0
                        self.v_status.set(f"Step 2 of 2  -  Making shorts: {done} of {total}"
                                          + (f"   (about {fmt_len(eta)} left)" if done and eta else ""))
                    if path:
                        name = os.path.basename(path)
                        if name not in self.lst.get(0, "end"):
                            self.lst.insert("end", name)
                elif kind == "done":
                    outs, test = data
                    stopped = self.stop_event.is_set()
                    self._finish("Stopped." if stopped else
                                 ("Test short ready - it is playing now." if test else
                                  f"Done! {len(outs)} shorts made."))
                    if test and outs:
                        os.startfile(outs[0])
                elif kind == "error":
                    self._write_log("ERROR: " + data)
                    self._finish("Something went wrong - see Details.")
                    messagebox.showerror("Clip Studio", data)
        except queue.Empty:
            pass
        self.after(200, self._poll)

    def _finish(self, status):
        for b in (self.b_go, self.b_test):
            b.configure(state="normal")
        self.b_stop.configure(state="disabled")
        self.v_status.set(status)
        self._refresh_outputs()

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Clip Studio", "Shorts are still being made. Stop and quit?"):
                return
            self.stop_event.set()
        try:
            self._save_settings()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    ClipStudio().mainloop()
