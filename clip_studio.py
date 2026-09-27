"""
Clip Studio - local desktop app for the whole shorts pipeline.

Pick a long video (+ optional transcript), choose how many shorts you want,
press Start. Everything runs on this PC:
    transcript -> 40 s windows -> speaker-following 9:16 camera (cut on speaker
    change) -> Hinglish pop captions in the platform safe zone -> top text ->
    finished MP4s in the output folder.

Launch: double-click "Clip Studio.bat" (sets temp/cache folders on D:).
"""

import json
import multiprocessing
import os
import queue
import shutil
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import build_shorts as B

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(ROOT, "clip_studio_settings.json")

BG = "#16181d"
PANEL = "#1f2229"
FG = "#e8e8ea"
MUTED = "#9aa0aa"
ACCENT = "#ffe133"

LANG_CHOICES = {"Auto-detect": "auto", "Hindi": "hindi", "English": "english"}
QUALITY_CHOICES = {
    "Best (large-v3-turbo)": "large-v3-turbo",
    "Fast, less accurate (small)": "small",
}


class ClipStudio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Clip Studio - long video to vertical shorts")
        self.geometry("1040x760")
        self.minsize(900, 640)
        self.configure(bg=BG)

        self.q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.t_start = None

        self._style()
        self._vars()
        self._layout()
        self._load_settings()
        self._refresh_outputs()
        self.after(200, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------ UI setup

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, font=("Segoe UI", 10))
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Panel.TLabel", background=PANEL, foreground=FG)
        st.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("H1.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 18))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED)
        st.configure("Sec.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI Semibold", 11))
        st.configure("Total.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI Semibold", 11))
        st.configure("TEntry", fieldbackground="#2a2e37", foreground=FG, insertcolor=FG)
        st.configure("TSpinbox", fieldbackground="#2a2e37", foreground=FG, arrowcolor=FG)
        st.configure("TCheckbutton", background=PANEL, foreground=FG)
        st.map("TCheckbutton", background=[("active", PANEL)])
        st.configure("TButton", background="#2f343f", foreground=FG, borderwidth=0, padding=(12, 6))
        st.map("TButton", background=[("active", "#3a4050"), ("disabled", "#23262d")],
               foreground=[("disabled", "#666")])
        st.configure("Go.TButton", background=ACCENT, foreground="#111", font=("Segoe UI Semibold", 11),
                     padding=(22, 8))
        st.map("Go.TButton", background=[("active", "#fff07a"), ("disabled", "#5a5530")])
        st.configure("Horizontal.TProgressbar", troughcolor="#2a2e37", background=ACCENT,
                     bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT)

    def _vars(self):
        d = B.DEFAULT_CONFIG
        # on a fresh install (other PC) the dev paths don't exist: start empty / local output
        self.v_video = tk.StringVar(value=d["video"] if os.path.exists(d["video"]) else "")
        self.v_lang = tk.StringVar(value="Auto-detect")
        self.v_quality = tk.StringVar(value=list(QUALITY_CHOICES)[0])
        self.v_out = tk.StringVar(value=d["out_dir"] if os.path.isdir(d["out_dir"])
                                  else os.path.join(ROOT, "output"))
        self.v_chunks = tk.IntVar(value=d["chunks"])
        self.v_per = tk.IntVar(value=d["per_chunk"])
        self.v_len = tk.IntVar(value=d["clip_seconds"])
        self.v_top = tk.StringVar(value=d["top_text"])
        self.v_cap = tk.IntVar(value=d["caption_size"])
        self.v_workers = tk.IntVar(value=d["workers"])
        self.v_cut = tk.BooleanVar(value=d["cut_on_speaker"])
        self.v_skip = tk.BooleanVar(value=d["skip_existing"])
        self.v_status = tk.StringVar(value="Ready.")
        self.v_total = tk.StringVar()
        for v in (self.v_chunks, self.v_per):
            v.trace_add("write", lambda *_: self._update_total())

    def _layout(self):
        head = ttk.Frame(self)
        head.pack(fill="x", padx=18, pady=(14, 6))
        ttk.Label(head, text="Clip Studio", style="H1.TLabel").pack(side="left")
        ttk.Label(head, text="   long video + transcript  ->  speaker-tracked 9:16 shorts with pop captions",
                  style="Sub.TLabel").pack(side="left", pady=(8, 0))

        top = ttk.Frame(self)
        top.pack(fill="x", padx=18)

        # ---- inputs
        inp = ttk.Frame(top, style="Panel.TFrame", padding=12)
        inp.pack(fill="x", pady=(0, 8))
        ttk.Label(inp, text="1  Inputs", style="Sec.TLabel").grid(row=0, column=0, sticky="w", columnspan=3)
        self._file_row(inp, 1, "Long video", self.v_video, self._pick_video)
        ttk.Label(inp, text="Any video with English or Hindi speech. It is transcribed here, on this PC.",
                  style="Muted.TLabel").grid(row=2, column=1, sticky="w", columnspan=2)
        self._file_row(inp, 3, "Output folder", self.v_out, self._pick_out)
        ttk.Label(inp, text="Spoken language", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=3)
        lang_row = ttk.Frame(inp, style="Panel.TFrame")
        lang_row.grid(row=4, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Combobox(lang_row, textvariable=self.v_lang, values=list(LANG_CHOICES), state="readonly",
                     width=16).pack(side="left")
        ttk.Label(lang_row, text="     Transcription", style="Panel.TLabel").pack(side="left")
        ttk.Combobox(lang_row, textvariable=self.v_quality, values=list(QUALITY_CHOICES), state="readonly",
                     width=30).pack(side="left", padx=(8, 0))
        inp.columnconfigure(1, weight=1)

        # ---- settings
        st = ttk.Frame(top, style="Panel.TFrame", padding=12)
        st.pack(fill="x", pady=(0, 8))
        ttk.Label(st, text="2  Settings", style="Sec.TLabel").grid(row=0, column=0, sticky="w", columnspan=6)
        self._spin(st, 1, 0, "Chunks (split the video into)", self.v_chunks, 1, 50)
        self._spin(st, 1, 2, "Videos per chunk", self.v_per, 1, 30)
        ttk.Label(st, textvariable=self.v_total, style="Total.TLabel").grid(row=1, column=4, sticky="w",
                                                                             padx=(18, 0))
        self._spin(st, 2, 0, "Clip length (seconds)", self.v_len, 10, 90)
        self._spin(st, 2, 2, "Caption size", self.v_cap, 60, 140)
        self._spin(st, 2, 4, "Parallel renders", self.v_workers, 1, 8)
        ttk.Label(st, text="Top text", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(st, textvariable=self.v_top, width=28).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Checkbutton(st, text="Cut to the new face when the speaker changes",
                        variable=self.v_cut).grid(row=3, column=2, columnspan=2, sticky="w", padx=(18, 0))
        ttk.Checkbutton(st, text="Skip videos already rendered",
                        variable=self.v_skip).grid(row=3, column=4, columnspan=2, sticky="w", padx=(18, 0))

        # ---- run
        run = ttk.Frame(top, style="Panel.TFrame", padding=12)
        run.pack(fill="x", pady=(0, 8))
        ttk.Label(run, text="3  Run", style="Sec.TLabel").grid(row=0, column=0, sticky="w", columnspan=4)
        self.b_start = ttk.Button(run, text="Start", style="Go.TButton", command=self._start)
        self.b_start.grid(row=1, column=0, padx=(0, 8), pady=(6, 0))
        self.b_stop = ttk.Button(run, text="Stop", command=self._stop, state="disabled")
        self.b_stop.grid(row=1, column=1, padx=(0, 8), pady=(6, 0))
        ttk.Button(run, text="Open output folder", command=self._open_out).grid(row=1, column=2, pady=(6, 0))
        self.pb = ttk.Progressbar(run, mode="determinate")
        self.pb.grid(row=1, column=3, sticky="ew", padx=(16, 0), pady=(6, 0))
        ttk.Label(run, textvariable=self.v_status, style="Muted.TLabel").grid(row=2, column=3, sticky="w",
                                                                                padx=(16, 0))
        run.columnconfigure(3, weight=1)

        # ---- log + outputs
        bottom = ttk.PanedWindow(self, orient="horizontal")
        bottom.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        lf = ttk.Frame(bottom, style="Panel.TFrame", padding=8)
        ttk.Label(lf, text="Log", style="Sec.TLabel").pack(anchor="w")
        self.log = ScrolledText(lf, height=10, bg="#101216", fg="#cfd3da", insertbackground=FG,
                                relief="flat", font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True, pady=(4, 0))
        bottom.add(lf, weight=3)
        of = ttk.Frame(bottom, style="Panel.TFrame", padding=8)
        ttk.Label(of, text="Finished shorts  (double-click to play)", style="Sec.TLabel").pack(anchor="w")
        self.lst = tk.Listbox(of, bg="#101216", fg=FG, selectbackground="#3a4050", relief="flat",
                              font=("Segoe UI", 10), activestyle="none")
        self.lst.pack(fill="both", expand=True, pady=(4, 0))
        self.lst.bind("<Double-Button-1>", self._play_selected)
        bottom.add(of, weight=2)
        self._update_total()

    def _file_row(self, parent, row, label, var, cmd):
        ttk.Label(parent, text=label, style="Panel.TLabel", width=14).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Button(parent, text="Browse...", command=cmd).grid(row=row, column=2, padx=(8, 0), pady=3)

    def _spin(self, parent, row, col, label, var, lo, hi):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=col, sticky="w", pady=4,
                                                                  padx=(0 if col == 0 else 18, 6))
        ttk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=6).grid(row=row, column=col + 1,
                                                                             sticky="w", pady=4)

    # ------------------------------------------------ helpers

    def _update_total(self):
        try:
            self.v_total.set(f"= {int(self.v_chunks.get()) * int(self.v_per.get())} shorts")
        except (tk.TclError, ValueError):
            self.v_total.set("")

    def _pick_video(self):
        p = filedialog.askopenfilename(title="Long video",
                                       filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.webm"), ("All", "*.*")])
        if p:
            self.v_video.set(os.path.normpath(p))

    def _pick_out(self):
        p = filedialog.askdirectory(title="Output folder")
        if p:
            self.v_out.set(os.path.normpath(p))
            self._refresh_outputs()

    def _open_out(self):
        out = self.v_out.get()
        os.makedirs(out, exist_ok=True)
        os.startfile(out)

    def _play_selected(self, _evt=None):
        sel = self.lst.curselection()
        if sel:
            os.startfile(os.path.join(self.v_out.get(), self.lst.get(sel[0])))

    def _refresh_outputs(self):
        self.lst.delete(0, "end")
        out = self.v_out.get()
        if os.path.isdir(out):
            for n in sorted(os.listdir(out)):
                if n.lower().endswith(".mp4"):
                    self.lst.insert("end", n)

    def _write_log(self, msg):
        self.log.insert("end", time.strftime("%H:%M:%S  ") + msg + "\n")
        self.log.see("end")

    def _config(self):
        return {
            "video": self.v_video.get().strip(),
            "transcript": "",
            "language": LANG_CHOICES.get(self.v_lang.get(), "auto"),
            "language_label": self.v_lang.get(),
            "whisper_model": QUALITY_CHOICES.get(self.v_quality.get(), "small"),
            "quality_label": self.v_quality.get(),
            "out_dir": self.v_out.get().strip(),
            "chunks": int(self.v_chunks.get()),
            "per_chunk": int(self.v_per.get()),
            "clip_seconds": int(self.v_len.get()),
            "top_text": self.v_top.get().strip(),
            "workers": int(self.v_workers.get()),
            "caption_size": int(self.v_cap.get()),
            "cut_on_speaker": bool(self.v_cut.get()),
            "skip_existing": bool(self.v_skip.get()),
        }

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            return
        m = {"video": self.v_video, "language_label": self.v_lang, "quality_label": self.v_quality,
             "out_dir": self.v_out,
             "chunks": self.v_chunks, "per_chunk": self.v_per, "clip_seconds": self.v_len,
             "top_text": self.v_top, "workers": self.v_workers, "caption_size": self.v_cap,
             "cut_on_speaker": self.v_cut, "skip_existing": self.v_skip}
        for k, var in m.items():
            if k in s:
                var.set(s[k])

    def _save_settings(self, cfg):
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=1)
        except OSError:
            pass

    # ------------------------------------------------ run

    def _start(self):
        try:
            cfg = self._config()
        except (tk.TclError, ValueError):
            messagebox.showerror("Clip Studio", "Please check the number fields.")
            return
        if not os.path.isfile(cfg["video"]):
            messagebox.showerror("Clip Studio", "Choose the long video file.")
            return
        if not cfg["out_dir"]:
            messagebox.showerror("Clip Studio", "Choose an output folder.")
            return
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            messagebox.showerror("Clip Studio", "FFmpeg was not found on PATH.")
            return
        if not os.path.exists(os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx")):
            messagebox.showerror("Clip Studio", "Face model missing: models\\face_detection_yunet_2023mar.onnx")
            return

        self._save_settings(cfg)
        self.stop_event.clear()
        self.b_start.configure(state="disabled")
        self.b_stop.configure(state="normal")
        self.pb.configure(value=0, maximum=1)
        self.v_status.set("Preparing ...")
        self.t_start = time.time()
        self._write_log(f"Start: {os.path.basename(cfg['video'])} -> {cfg['out_dir']}")

        def work():
            try:
                B.run_pipeline(
                    cfg,
                    log=lambda m: self.q.put(("log", m)),
                    progress=lambda d, t, path=None, stage="render":
                        self.q.put(("progress", (d, t, path, stage))),
                    should_stop=self.stop_event.is_set,
                )
                self.q.put(("done", None))
            except BaseException as e:
                self.q.put(("error", repr(e)))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self.b_stop.configure(state="disabled")
        self.v_status.set("Stopping after the videos currently rendering ...")

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
                        self.v_status.set(f"Step 1/2  Transcribing: {done / 60:.1f} / {total / 60:.1f} min")
                        continue
                    if done == 0:
                        self.t_start = time.time()  # ETA counts rendering only
                    if total:
                        el = time.time() - self.t_start
                        eta = (el / done) * (total - done) if done else 0
                        self.v_status.set(f"Step 2/2  Rendering: {done} / {total} shorts   "
                                          f"elapsed {el / 60:.1f} min   "
                                          f"{'ETA ' + format(eta / 60, '.1f') + ' min' if done else ''}")
                    if path:
                        self.lst.insert("end", os.path.basename(path))
                elif kind == "done":
                    self._finish("Done." if not self.stop_event.is_set() else "Stopped.")
                elif kind == "error":
                    self._write_log("ERROR: " + data)
                    self._finish("Error - see log.")
                    messagebox.showerror("Clip Studio", data)
        except queue.Empty:
            pass
        self.after(200, self._poll)

    def _finish(self, status):
        self.b_start.configure(state="normal")
        self.b_stop.configure(state="disabled")
        self.v_status.set(status)
        self._refresh_outputs()

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Clip Studio", "Rendering is running. Stop and quit?"):
                return
            self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    ClipStudio().mainloop()
