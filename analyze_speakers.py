"""
analyze_speakers.py

Speaker-aware camera tracking for 9:16 auto-reframing.

Pipeline per clip:
    shot-cut detection  ->  YuNet face detection  ->  per-shot face tracking
    ->  mouth-activity scoring  ->  active-speaker selection (with hysteresis,
    gated by transcript speech timing)  ->  smoothed camera path (spring +
    dead-zone, hard jumps at cuts)  ->  simplified keyframes for After Effects

Usage (from D:\\AI-CLIP-GENERATOR, venv active):
    python analyze_speakers.py clip_01
    python analyze_speakers.py clip_01 --preview
    python analyze_speakers.py all --preview

Outputs:
    camera_tracks/<clip>.json
    camera_tracks/previews/<clip>_debug.mp4   (with --preview)
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
CLIPS_DIR = os.path.join(ROOT, "clips")
PLANS_DIR = os.path.join(ROOT, "edit_plans")
OUT_DIR = os.path.join(ROOT, "camera_tracks")
PREVIEW_DIR = os.path.join(OUT_DIR, "previews")
MODEL_PATH = os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx")

# --------------------------------------------------
# TUNING
# --------------------------------------------------

OUT_ASPECT = 9 / 16

DET_WIDTH = 1280            # detection resolution (width)
SCORE_THRESHOLD = 0.6       # YuNet confidence
MIN_FACE_FRAC = 0.035       # face height / frame height (drops tiny background faces)
MAX_FACE_CY_FRAC = 0.78     # ignore faces centred in the bottom strip (audience)

CUT_THRESHOLD = 0.35        # HSV histogram Bhattacharyya distance for a hard cut
CUT_PIX_THRESHOLD = 12.0    # mean abs grey diff of 64x36 thumbnails (catches same-set angle changes)
MIN_SHOT_LEN = 0.3          # seconds

TRACK_MATCH_DIST = 0.6      # centre distance / face width
TRACK_MAX_GAP = 0.6         # seconds a track can go undetected

ACT_WINDOW = 0.8            # seconds, centred smoothing window for mouth activity
SWITCH_RATIO = 1.35         # challenger must beat current speaker by this factor
SWITCH_CONFIRM = 0.45       # ... for at least this long
MIN_HOLD = 1.2              # minimum seconds on a speaker before switching
SWITCH_LEAD = 0.15          # start the move slightly before the confirmed switch
GROUP_RATIO = 0.75          # second speaker this active -> frame both if they fit
GROUP_FIT = 0.75            # ... and they're within this fraction of crop width
ID_WEIGHT = 4.0             # clothing-colour identity bonus (x typical activity x relative similarity)

SPRING_OMEGA = 6.0          # camera spring stiffness (higher = snappier)
CUT_ON_SPEAKER_CHANGE = True  # hard cut (not pan) when the active speaker changes
DEADZONE_FRAC = 0.06        # ignore target drift smaller than this * crop width
HEADROOM_FRAC = 0.08        # push view centre below face centre by this * view height

WIDE_FACE_FRAC = 0.13       # median face height below this -> "wide" shot
WIDE_ZOOM = 1.15            # extra zoom on wide shots (keep modest: source is 1080p)

RDP_TOLERANCE = 1.5         # px, keyframe simplification

# --------------------------------------------------
# HELPERS
# --------------------------------------------------

TAG_RE = re.compile(r"\[[^\]]*\]")


def load_speech_segments(clip_id):
    """Relative-time speech segments from the edit plan (bracket-only lines = not speech)."""
    path = os.path.join(PLANS_DIR, f"{clip_id}.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    segs = []
    for seg in plan.get("transcript", []):
        text = TAG_RE.sub("", seg.get("text", "")).strip()
        start, end = float(seg["start"]), float(seg["end"])
        if text and end > start:
            segs.append((start, end, seg.get("speaker")))
    return segs


def speech_at(segs, t):
    for start, end, spk in segs:
        if start - 0.1 <= t <= end + 0.1:
            return True, spk
    return False, None


def frame_signature(frame):
    small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    thumb = cv2.cvtColor(cv2.resize(small, (64, 36), interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY).astype(np.float32)
    return hist, thumb


def is_hard_cut(prev_sig, sig):
    hist_d = cv2.compareHist(prev_sig[0], sig[0], cv2.HISTCMP_BHATTACHARYYA)
    pix_d = float(np.abs(prev_sig[1] - sig[1]).mean())
    return hist_d > CUT_THRESHOLD or pix_d > CUT_PIX_THRESHOLD


def norm_patch(gray, cx, cy, w, h, size=(24, 16)):
    H, W = gray.shape
    x0, x1 = int(max(0, cx - w / 2)), int(min(W, cx + w / 2))
    y0, y1 = int(max(0, cy - h / 2)), int(min(H, cy + h / 2))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    roi = cv2.resize(gray[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA).astype(np.float32)
    return (roi - roi.mean()) / (roi.std() + 5.0)


def face_patches(gray, f):
    """Mouth and eye-region patches from YuNet landmarks."""
    x, y, w, h = f[0:4]
    rex, rey, lex, ley = f[4:8]
    rmx, rmy, lmx, lmy = f[10:14]
    mx, my = (rmx + lmx) / 2, (rmy + lmy) / 2
    mouth = norm_patch(gray, mx, my + 0.05 * h, 0.6 * w, 0.35 * h)
    eyes = norm_patch(gray, (rex + lex) / 2, (rey + ley) / 2, 0.8 * w, 0.25 * h)
    return mouth, eyes


def smooth_series(values, window):
    """Centred moving average ignoring NaN. values: 1-D np.array."""
    out = np.full_like(values, np.nan)
    n = len(values)
    half = max(1, window // 2)
    for i in range(n):
        seg = values[max(0, i - half): min(n, i + half + 1)]
        seg = seg[~np.isnan(seg)]
        if len(seg):
            out[i] = seg.mean()
    return out


def rdp(points, eps):
    """Ramer-Douglas-Peucker on (t, x, y) points; returns kept indices."""
    if len(points) < 3:
        return list(range(len(points)))
    pts = np.asarray(points, dtype=np.float64)
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        ta, tb = pts[a, 0], pts[b, 0]
        seg = pts[a + 1:b]
        frac = (seg[:, 0] - ta) / max(tb - ta, 1e-9)
        interp = pts[a, 1:] + np.outer(frac, pts[b, 1:] - pts[a, 1:])
        err = np.abs(seg[:, 1:] - interp).max(axis=1)
        i = int(np.argmax(err))
        if err[i] > eps:
            m = a + 1 + i
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return list(np.nonzero(keep)[0])


# --------------------------------------------------
# PASS 1: DETECT + TRACK
# --------------------------------------------------

class Track:
    def __init__(self, tid, shot):
        self.id = tid
        self.shot = shot
        self.last_t = -1.0
        self.cx = self.cy = self.w = self.h = 0.0
        self.prev_mouth = None
        self.prev_eyes = None
        self.obs = {}  # sample index -> dict

    def last_seen_t(self, i, samples):
        seen = [j for j in self.obs if j <= i]
        return samples[max(seen)]["t"] if seen else -1e9

    def nearest_obs(self, i):
        if not self.obs:
            return None
        return self.obs[min(self.obs, key=lambda k: abs(k - i))]


def torso_hist(hsv_small, cx, cy, w, h, ds):
    """Clothing colour signature: H-S histogram of the region below the face."""
    Hs, Ws = hsv_small.shape[:2]
    x0, x1 = int(max(0, (cx - 0.9 * w) / ds)), int(min(Ws, (cx + 0.9 * w) / ds))
    y0, y1 = int(max(0, (cy + 0.8 * h) / ds)), int(min(Hs, (cy + 2.2 * h) / ds))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    hist = cv2.calcHist([hsv_small[y0:y1, x0:x1]], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 1.0, 0.0, cv2.NORM_L1)
    return hist


def analyze(clip_id, step, video_path=None):
    video_path = video_path or os.path.join(CLIPS_DIR, f"{clip_id}.mp4")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        sys.exit(f"Cannot open {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    det_w = DET_WIDTH
    det_h = int(round(H * det_w / W))
    ds = W / det_w

    detector = cv2.FaceDetectorYN.create(MODEL_PATH, "", (det_w, det_h), SCORE_THRESHOLD, 0.3, 5000)

    shots = [0]          # frame index of each shot start
    samples = []         # {frame, t, shot}
    tracks = []
    active = []          # tracks alive in current shot
    prev_sig = None
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / fps

        sig = frame_signature(frame)
        is_cut = False
        if prev_sig is not None:
            if is_hard_cut(prev_sig, sig) and (frame_idx - shots[-1]) / fps >= MIN_SHOT_LEN:
                shots.append(frame_idx)
                active = []
                is_cut = True
        prev_sig = sig

        if frame_idx % step == 0 or is_cut:
            shot = len(shots) - 1
            si = len(samples)
            samples.append({"frame": frame_idx, "t": t, "shot": shot})

            small = cv2.resize(frame, (det_w, det_h), interpolation=cv2.INTER_AREA)
            _, faces = detector.detect(small)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            hsv_small = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

            dets = []
            if faces is not None:
                for f in faces:
                    f = f.copy()
                    f[:14] *= ds
                    x, y, w, h = (float(v) for v in f[0:4])
                    cx, cy = x + w / 2, y + h / 2
                    if h < MIN_FACE_FRAC * H or cy > MAX_FACE_CY_FRAC * H:
                        continue
                    dets.append((f, cx, cy, w, h))

            # greedy nearest-centre matching
            active = [tr for tr in active if t - tr.last_t <= TRACK_MAX_GAP]
            pairs = []
            for di, (_, cx, cy, w, _) in enumerate(dets):
                for tr in active:
                    dist = math.hypot(cx - tr.cx, cy - tr.cy) / max(w, tr.w)
                    if dist < TRACK_MATCH_DIST:
                        pairs.append((dist, di, tr))
            pairs.sort(key=lambda p: p[0])
            used_d, used_t, assign = set(), set(), {}
            for _, di, tr in pairs:
                if di in used_d or tr.id in used_t:
                    continue
                used_d.add(di)
                used_t.add(tr.id)
                assign[di] = tr
            for di in range(len(dets)):
                if di not in assign:
                    tr = Track(len(tracks), shot)
                    tracks.append(tr)
                    active.append(tr)
                    assign[di] = tr

            for di, (f, cx, cy, w, h) in enumerate(dets):
                tr = assign[di]
                mouth, eyes = face_patches(gray, f)
                act = np.nan
                if (mouth is not None and tr.prev_mouth is not None
                        and eyes is not None and tr.prev_eyes is not None
                        and t - tr.last_t <= 2.5 * step / fps):
                    m = float(np.abs(mouth - tr.prev_mouth).mean())
                    e = float(np.abs(eyes - tr.prev_eyes).mean())
                    act = max(0.0, m - 0.8 * e)  # subtract head/global motion
                tr.prev_mouth, tr.prev_eyes = mouth, eyes
                tr.cx, tr.cy, tr.w, tr.h, tr.last_t = cx, cy, w, h, t
                tr.obs[si] = {"cx": cx, "cy": cy, "w": w, "h": h,
                              "conf": float(f[14]), "act": act}
                th = torso_hist(hsv_small, cx, cy, w, h, ds)
                if th is not None:
                    tr.torso = th if getattr(tr, "torso", None) is None else tr.torso + th

        frame_idx += 1
        if frame_idx % 250 == 0:
            print(f"  {clip_id}: {t:6.1f}s  shots={len(shots)}  tracks={len(tracks)}")

    cap.release()
    return {"fps": fps, "W": W, "H": H, "n_frames": frame_idx,
            "shots": shots, "samples": samples, "tracks": tracks}


# --------------------------------------------------
# PASS 2: ACTIVE SPEAKER + CAMERA PATH
# --------------------------------------------------

def plan_camera(clip_id, data, step, speech=None):
    """speech: optional list of (start, end, speaker) in clip-relative seconds."""
    fps, W, H = data["fps"], data["W"], data["H"]
    n_frames = data["n_frames"]
    samples, tracks = data["samples"], data["tracks"]
    shot_starts = data["shots"] + [n_frames]
    if speech is None:
        speech = load_speech_segments(clip_id)
    base_crop_w = H * OUT_ASPECT

    win = max(3, int(round(ACT_WINDOW * fps / step)))
    n_s = len(samples)

    # smoothed activity per track over all samples
    for tr in tracks:
        raw = np.full(n_s, np.nan)
        for si, o in tr.obs.items():
            raw[si] = o["act"]
        tr.smooth = smooth_series(raw, win)
        present = np.zeros(n_s, dtype=bool)
        present[list(tr.obs.keys())] = True
        tr.present = present

    speech_flags = [speech_at(speech, s["t"]) for s in samples]

    # pass A: mouth activity only
    chosen, group, shot_zoom, _ = select_speakers(
        samples, tracks, shot_starts, speech_flags, W, H, base_crop_w, None)

    # learn a clothing signature per transcript speaker from confident picks
    by_id_all = {tr.id: tr for tr in tracks}
    for tr in tracks:
        t = getattr(tr, "torso", None)
        tr.sig = None if t is None else t / max(float(t.sum()), 1e-9)
    models, counts = {}, {}
    for i, s in enumerate(samples):
        spk, tid = speech_flags[i][1], chosen[i]
        if not speech_flags[i][0] or spk is None or tid is None:
            continue
        tr = by_id_all[tid]
        if tr.sig is None or not tr.present[i]:
            continue
        others = [o.smooth[i] for o in tracks
                  if o.id != tid and o.shot == tr.shot and o.present[i] and not np.isnan(o.smooth[i])]
        mine = tr.smooth[i]
        if np.isnan(mine) or (others and mine < 1.8 * max(others)):
            continue
        models[spk] = tr.sig if spk not in models else models[spk] + tr.sig
        counts[spk] = counts.get(spk, 0) + 1
    models = {k: v / max(float(v.sum()), 1e-9) for k, v in models.items() if counts[k] >= 8}

    identity = None
    if models:
        pos = [tr.smooth[i] for i in range(n_s) if chosen[i] is not None
               for tr in [by_id_all[chosen[i]]] if tr.present[i] and not np.isnan(tr.smooth[i])]
        act_scale = float(np.median(pos)) if pos else 0.1
        identity = {"models": models, "scale": act_scale}
        # pass B: activity + identity bonus
        chosen, group, shot_zoom, _ = select_speakers(
            samples, tracks, shot_starts, speech_flags, W, H, base_crop_w, identity)
    data["identity_speakers"] = sorted(models.keys())

    return finish_camera(clip_id, data, step, speech_flags, chosen, group, shot_zoom)


def select_speakers(samples, tracks, shot_starts, speech_flags, W, H, base_crop_w, identity):
    n_s = len(samples)
    chosen = [None] * n_s       # active track id per sample
    group = [None] * n_s        # second track id when framing two people
    shot_zoom = {}

    for shot in range(len(shot_starts) - 1):
        idxs = [i for i, s in enumerate(samples) if s["shot"] == shot]
        if not idxs:
            continue
        s_tracks = [tr for tr in tracks if tr.shot == shot and len(tr.obs) >= 2]

        heights = [o["h"] for tr in s_tracks for o in tr.obs.values()]
        zoom = WIDE_ZOOM if heights and np.median(heights) < WIDE_FACE_FRAC * H else 1.0
        shot_zoom[shot] = zoom
        crop_w = base_crop_w / zoom

        if not s_tracks:
            continue
        by_id = {tr.id: tr for tr in s_tracks}

        # identity similarity of each track to each known speaker, centred per shot
        rel_sim = {}
        if identity:
            for spk, model in identity["models"].items():
                sims = {tr.id: 1.0 - cv2.compareHist(tr.sig.astype(np.float32), model.astype(np.float32),
                                                     cv2.HISTCMP_BHATTACHARYYA)
                        for tr in s_tracks if tr.sig is not None}
                if len(sims) >= 2:
                    mean = float(np.mean(list(sims.values())))
                    rel_sim[spk] = {tid: v - mean for tid, v in sims.items()}

        def score(tr, i):
            if not tr.present[i]:
                return 0.0
            a = tr.smooth[i]
            if np.isnan(a):
                a = 0.0
            sc = a * (0.7 + math.sqrt(tr.obs[i]["h"] / H))
            spk = speech_flags[i][1]
            if identity and speech_flags[i][0] and spk in rel_sim:
                sc = max(0.0, sc + ID_WEIGHT * identity["scale"] * rel_sim[spk].get(tr.id, 0.0))
            return sc

        # initial speaker: look ahead over the first 1.5s of the shot
        t0 = samples[idxs[0]]["t"]
        look = [i for i in idxs if samples[i]["t"] - t0 <= 1.5]
        totals = {tr.id: sum(score(tr, i) for i in look) for tr in s_tracks}
        if max(totals.values()) <= 1e-6:
            # nobody visibly talking: largest face nearest the centre
            cur = max(s_tracks, key=lambda tr: np.mean([o["h"] for o in tr.obs.values()])
                      - 0.1 * abs(np.mean([o["cx"] for o in tr.obs.values()]) - W / 2)).id
        else:
            cur = max(totals, key=totals.get)
        cur_since = t0
        challenger, ch_since = None, None

        for i in idxs:
            t = samples[i]["t"]
            is_speech = speech_flags[i][0]
            scores = {tid: score(tr, i) for tid, tr in by_id.items()}
            best = max(scores, key=scores.get)

            if not by_id[cur].present[i] and t - by_id[cur].last_seen_t(i, samples) > 0.5:
                # current speaker vanished: take the best visible face
                if by_id[best].present[i]:
                    cur, cur_since, challenger = best, t, None
            elif (best != cur and is_speech
                  and scores[best] > SWITCH_RATIO * scores[cur] + 0.02):
                if challenger != best:
                    challenger, ch_since = best, t
                elif t - ch_since >= SWITCH_CONFIRM and t - cur_since >= MIN_HOLD:
                    # anticipate: backdate the switch to when the challenger started
                    switch_t = max(cur_since + MIN_HOLD, ch_since - SWITCH_LEAD)
                    for j in idxs:
                        if switch_t <= samples[j]["t"] < t:
                            chosen[j] = best
                            group[j] = None
                    cur, cur_since, challenger = best, switch_t, None
            else:
                challenger = None

            chosen[i] = cur

            # two-shot: another person is nearly as active and fits in frame
            if is_speech and by_id[cur].present[i]:
                cx_cur = by_id[cur].obs[i]["cx"]
                for tid, sc in scores.items():
                    if tid == cur or not by_id[tid].present[i]:
                        continue
                    if (sc >= GROUP_RATIO * max(scores[cur], 1e-6) and sc > 0.05
                            and abs(by_id[tid].obs[i]["cx"] - cx_cur) < GROUP_FIT * crop_w):
                        group[i] = tid
                        break

    return chosen, group, shot_zoom, None


def finish_camera(clip_id, data, step, speech_flags, chosen, group, shot_zoom):
    fps, W, H = data["fps"], data["W"], data["H"]
    n_frames = data["n_frames"]
    samples, tracks = data["samples"], data["tracks"]
    shot_starts = data["shots"] + [n_frames]
    base_crop_w = H * OUT_ASPECT
    n_s = len(samples)

    # target per sample (hold last known position when the face drops out)
    track_by_id = {tr.id: tr for tr in tracks}
    tgt_x = np.full(n_s, np.nan)
    tgt_y = np.full(n_s, np.nan)
    for i in range(n_s):
        tid = chosen[i]
        if tid is None:
            continue
        tr = track_by_id[tid]
        o = tr.obs.get(i) or tr.nearest_obs(i)
        if o is None:
            continue
        x, y = o["cx"], o["cy"]
        g = group[i]
        if g is not None and i in track_by_id[g].obs:
            og = track_by_id[g].obs[i]
            x, y = (x + og["cx"]) / 2, (y + og["cy"]) / 2
        tgt_x[i], tgt_y[i] = x, y

    # per-frame camera with spring smoothing, reset at each cut
    cam_x = np.zeros(n_frames)
    cam_y = np.zeros(n_frames)
    cam_z = np.ones(n_frames)
    frame_to_sample = np.zeros(n_frames, dtype=int)
    si = 0
    for f in range(n_frames):
        while si + 1 < n_s and samples[si + 1]["frame"] <= f:
            si += 1
        frame_to_sample[f] = si

    dt = 1.0 / fps
    seg = np.zeros(n_frames, dtype=int)   # increments at every hard cut (shot or speaker)
    seg_id = -1
    for shot in range(len(shot_starts) - 1):
        f0, f1 = shot_starts[shot], shot_starts[shot + 1]
        zoom = shot_zoom.get(shot, 1.0)
        view_w = base_crop_w / zoom
        view_h = H / zoom
        dead = DEADZONE_FRAC * view_w

        shot_samples = [i for i in range(n_s) if samples[i]["shot"] == shot]
        xs = [tgt_x[i] for i in shot_samples if not np.isnan(tgt_x[i])]
        ys = [tgt_y[i] for i in shot_samples if not np.isnan(tgt_y[i])]
        last_x = xs[0] if xs else W / 2
        last_y = ys[0] if ys else H / 2

        px = py = None
        vx = vy = 0.0
        goal_x = goal_y = None
        prev_choice = None
        for f in range(f0, f1):
            i = frame_to_sample[f]
            if not np.isnan(tgt_x[i]):
                last_x, last_y = tgt_x[i], tgt_y[i]
            want_x = last_x
            want_y = last_y + HEADROOM_FRAC * view_h

            choice = (chosen[i], group[i])
            speaker_cut = (CUT_ON_SPEAKER_CHANGE and prev_choice is not None
                           and choice[0] != prev_choice[0])
            if goal_x is None or choice != prev_choice or abs(want_x - goal_x) > dead:
                goal_x = want_x
            if goal_y is None or choice != prev_choice or abs(want_y - goal_y) > dead:
                goal_y = want_y
            prev_choice = choice

            if px is None or speaker_cut:  # shot start or new speaker: hard cut to the goal
                px, py = goal_x, goal_y
                vx = vy = 0.0
                seg_id += 1
            else:  # critically damped spring
                w = SPRING_OMEGA
                vx += (w * w * (goal_x - px) - 2 * w * vx) * dt
                vy += (w * w * (goal_y - py) - 2 * w * vy) * dt
                px += vx * dt
                py += vy * dt

            half_w, half_h = view_w / 2, view_h / 2
            cam_x[f] = min(max(px, half_w), W - half_w)
            cam_y[f] = min(max(py, half_h), H - half_h)
            cam_z[f] = zoom
            seg[f] = seg_id

    # keyframes: simplify each shot separately; mark the last key before a cut
    keyframes = []
    n_shots = len(shot_starts) - 1
    for shot in range(n_shots):
        f0, f1 = shot_starts[shot], shot_starts[shot + 1]
        if f1 <= f0:
            continue
        pts = [(f / fps, cam_x[f], cam_y[f]) for f in range(f0, f1)]
        keep = rdp(pts, RDP_TOLERANCE)
        for k, j in enumerate(keep):
            f = f0 + j
            keyframes.append({
                "t": round(f / fps, 4),
                "x": round(float(cam_x[f]), 2),
                "y": round(float(cam_y[f]), 2),
                "zoom": round(float(cam_z[f]), 4),
                "shot": shot,
                "cut_in": k == 0 and shot > 0,
                "hold_out": k == len(keep) - 1 and shot < n_shots - 1,
            })

    # which transcript speaker was on camera for each track (for later identity linking)
    track_summ = []
    for tr in tracks:
        if len(tr.obs) < 2:
            continue
        spk_time = {}
        active_n = 0
        for i in tr.obs:
            if chosen[i] == tr.id:
                active_n += 1
                sp = speech_flags[i][1]
                if sp:
                    spk_time[sp] = spk_time.get(sp, 0) + step / fps
        track_summ.append({
            "track": tr.id,
            "shot": tr.shot,
            "samples": len(tr.obs),
            "mean_x": round(float(np.mean([o["cx"] for o in tr.obs.values()])), 1),
            "mean_face_h": round(float(np.mean([o["h"] for o in tr.obs.values()])), 1),
            "active_seconds": round(active_n * step / fps, 2),
            "transcript_speaker_overlap": {k: round(v, 2) for k, v in spk_time.items()},
        })

    sample_out = []
    for i, s in enumerate(samples):
        faces = []
        for tr in tracks:
            o = tr.obs.get(i)
            if o is None:
                continue
            faces.append({
                "track": tr.id,
                "x": round(o["cx"], 1), "y": round(o["cy"], 1),
                "w": round(o["w"], 1), "h": round(o["h"], 1),
                "mouth": None if np.isnan(tr.smooth[i]) else round(float(tr.smooth[i]), 4),
            })
        sample_out.append({
            "t": round(s["t"], 3),
            "shot": s["shot"],
            "speech": speech_flags[i][0],
            "transcript_speaker": speech_flags[i][1],
            "active_track": chosen[i],
            "group_track": group[i],
            "faces": faces,
        })

    result = {
        "clip_id": clip_id,
        "source": {"width": W, "height": H, "fps": fps, "frames": n_frames,
                   "duration": round(n_frames / fps, 3)},
        "output": {"width": 1080, "height": 1920, "aspect": "9:16",
                   "base_crop_width": round(base_crop_w, 2)},
        "method": "yunet+mouth_activity+transcript_gating+clothing_identity",
        "identity_speakers": data.get("identity_speakers", []),
        "shots": [{"shot": k, "start": round(shot_starts[k] / fps, 3),
                   "end": round(shot_starts[k + 1] / fps, 3),
                   "zoom": shot_zoom.get(k, 1.0)} for k in range(n_shots)],
        "camera_keyframes": keyframes,
        "tracks": track_summ,
        "samples": sample_out,
    }
    per_frame = {"x": cam_x, "y": cam_y, "z": cam_z, "f2s": frame_to_sample,
                 "chosen": chosen, "group": group, "seg": seg}
    return result, per_frame


# --------------------------------------------------
# DEBUG PREVIEW
# --------------------------------------------------

def render_preview(clip_id, data, per_frame):
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    video_path = os.path.join(CLIPS_DIR, f"{clip_id}.mp4")
    out_path = os.path.join(PREVIEW_DIR, f"{clip_id}_debug.mp4")
    fps, W, H = data["fps"], data["W"], data["H"]
    base_crop_w = H * OUT_ASPECT

    # left: annotated source at half size; right: vertical crop at the same height
    left_w, left_h = W // 2, H // 2
    right_w = int(round(left_h * OUT_ASPECT)) // 2 * 2
    canvas_w, canvas_h = left_w + right_w, left_h

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{canvas_w}x{canvas_h}",
           "-r", f"{fps}", "-i", "-",
           "-i", video_path, "-map", "0:v", "-map", "1:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    tracks = data["tracks"]
    samples = data["samples"]
    cap = cv2.VideoCapture(video_path)
    f = 0
    while True:
        ok, frame = cap.read()
        if not ok or f >= len(per_frame["x"]):
            break
        i = per_frame["f2s"][f]
        chosen, group = per_frame["chosen"][i], per_frame["group"][i]
        cx, cy, z = per_frame["x"][f], per_frame["y"][f], per_frame["z"][f]
        vw, vh = base_crop_w / z, H / z

        crop = frame[int(cy - vh / 2):int(cy + vh / 2), int(cx - vw / 2):int(cx + vw / 2)]
        right = cv2.resize(crop, (right_w, left_h), interpolation=cv2.INTER_AREA)

        dbg = frame.copy()
        for tr in tracks:
            o = tr.obs.get(i)
            if o is None:
                continue
            col = (0, 255, 0) if tr.id == chosen else (0, 200, 255) if tr.id == group else (160, 160, 160)
            th = 6 if tr.id == chosen else 2
            x0, y0 = int(o["cx"] - o["w"] / 2), int(o["cy"] - o["h"] / 2)
            cv2.rectangle(dbg, (x0, y0), (int(x0 + o["w"]), int(y0 + o["h"])), col, th)
            a = tr.smooth[i]
            a = 0.0 if np.isnan(a) else float(a)
            cv2.rectangle(dbg, (x0, y0 - 14), (x0 + int(min(1.0, a / 0.5) * o["w"]), y0 - 4), col, -1)
            cv2.putText(dbg, f"T{tr.id} {a:.2f}", (x0, y0 - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2)
        cv2.rectangle(dbg, (int(cx - vw / 2), int(cy - vh / 2)),
                      (int(cx + vw / 2), int(cy + vh / 2)), (255, 0, 255), 4)
        s = samples[i]
        cv2.putText(dbg, f"t={f / fps:5.2f}s shot={s['shot']} zoom={z:.2f}",
                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)

        left = cv2.resize(dbg, (left_w, left_h), interpolation=cv2.INTER_AREA)
        proc.stdin.write(np.hstack([left, right]).tobytes())
        f += 1

    cap.release()
    proc.stdin.close()
    proc.wait()
    print(f"  preview: {out_path}")


# --------------------------------------------------
# MAIN
# --------------------------------------------------

def run(clip_id, step, preview):
    print(f"Analyzing {clip_id} ...")
    data = analyze(clip_id, step)
    result, per_frame = plan_camera(clip_id, data, step)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{clip_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)

    active = {t["track"]: t["active_seconds"] for t in result["tracks"] if t["active_seconds"] > 0}
    print(f"  shots: {len(result['shots'])}  tracks: {len(result['tracks'])}  "
          f"keyframes: {len(result['camera_keyframes'])}")
    print(f"  on-camera seconds per track: {active}")
    print(f"  wrote {out_path}")

    if preview:
        render_preview(clip_id, data, per_frame)


def main():
    ap = argparse.ArgumentParser(description="Speaker-aware 9:16 camera tracking")
    ap.add_argument("clip", help="clip id (e.g. clip_01) or 'all'")
    ap.add_argument("--step", type=int, default=2, help="run face detection every N frames")
    ap.add_argument("--preview", action="store_true", help="render a side-by-side debug video")
    args = ap.parse_args()

    if not os.path.exists(MODEL_PATH):
        sys.exit(f"Missing model: {MODEL_PATH}")

    if args.clip == "all":
        ids = sorted(os.path.splitext(n)[0] for n in os.listdir(CLIPS_DIR) if n.endswith(".mp4"))
    else:
        ids = [args.clip]
    for cid in ids:
        run(cid, args.step, args.preview)


if __name__ == "__main__":
    main()
