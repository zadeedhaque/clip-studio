import json
import os
import subprocess

INPUT_VIDEO = os.path.join("input", "master.mp4")
CANDIDATES_FILE = os.path.join("candidates", "candidates.json")
OUTPUT_DIR = "clips"

os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

candidates = data["candidates"]


def to_seconds(ts):
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


print(f"Found {len(candidates)} candidates.")
print(f"Input: {INPUT_VIDEO}")
print(f"Output: {OUTPUT_DIR}")
print()

for clip in candidates:
    clip_id = clip["id"]
    start = clip["start"]
    end = clip["end"]

    output_file = os.path.join(
        OUTPUT_DIR,
        f"{clip_id}.mp4"
    )

    # With -ss before -i, output timestamps restart at 0, so "-to end" would
    # be treated as a duration. Use an explicit duration instead.
    duration = to_seconds(end) - to_seconds(start)

    print(f"Extracting {clip_id}: {start} -> {end} ({duration:.2f}s)")

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", start,
        "-i", INPUT_VIDEO,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_file
    ]

    result = subprocess.run(cmd)

    if result.returncode == 0:
        print(f"✓ Created: {output_file}")
    else:
        print(f"✗ Failed: {clip_id}")

    print()

print("Finished.")