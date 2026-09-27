import json
import os

TRANSCRIPT_FILE = "transcript.json"
CANDIDATES_FILE = os.path.join("candidates", "candidates.json")
OUTPUT_DIR = "edit_plans"

os.makedirs(OUTPUT_DIR, exist_ok=True)

with open(TRANSCRIPT_FILE, "r", encoding="utf-8") as f:
    transcript_data = json.load(f)

with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
    candidates_data = json.load(f)

candidates = candidates_data["candidates"]


def to_seconds(value):
    if isinstance(value, (int, float)):
        return float(value)

    parts = str(value).split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)

    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)

    return float(value)


def get_transcript_items(data):
    """
    Handles the timestamped transcript structures already generated
    by the project.
    """
    if isinstance(data, list):
        return data

    for key in ["segments", "transcript", "items", "entries"]:
        if key in data and isinstance(data[key], list):
            return data[key]

    raise ValueError(
        "Could not find timestamped transcript entries in transcript.json"
    )


items = get_transcript_items(transcript_data)

for candidate in candidates:
    clip_id = candidate["id"]

    start = to_seconds(candidate["start"])
    end = to_seconds(candidate["end"])

    clip_items = []

    for item in items:
        item_start = item.get("start", item.get("start_time"))
        item_end = item.get("end", item.get("end_time"))

        if item_start is None or item_end is None:
            continue

        item_start = to_seconds(item_start)
        item_end = to_seconds(item_end)

        if item_end >= start and item_start <= end:
            text = (
                item.get("text")
                or item.get("content")
                or item.get("transcript")
                or ""
            )

            speaker = (
                item.get("speaker")
                or item.get("speaker_label")
                or ""
            )

            clip_items.append({
                "start": round(max(item_start, start) - start, 3),
                "end": round(min(item_end, end) - start, 3),
                "speaker": speaker,
                "text": text.strip()
            })

    duration = round(end - start, 3)

    plan = {
        "clip_id": clip_id,
        "source_video": "input/master.mp4",
        "source_start": candidate["start"],
        "source_end": candidate["end"],
        "duration": duration,

        "title": candidate.get("title", ""),
        "hook": candidate.get("hook", ""),
        "type": candidate.get("type", ""),
        "speakers": candidate.get("speakers", []),
        "reason": candidate.get("reason", ""),

        "transcript": clip_items,

        "editing": {
            "format": "9:16",
            "resolution": "1080x1920",
            "style": "dynamic_short_form",
            "remove_dead_air": True,
            "dynamic_captions": True,
            "punch_ins": True,
            "reaction_emphasis": True,
            "keyword_emphasis": True
        },

        "effects": {
            "punchline_zoom": True,
            "reaction_zoom": True,
            "impact_animation": True,
            "subtle_motion": True
        },

        "adobe": {
            "premiere_sequence": "1080x1920",
            "fps": 30,
            "after_effects_graphics": True
        }
    }

    output = os.path.join(
        OUTPUT_DIR,
        f"{clip_id}.json"
    )

    with open(output, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)

    print(f"Created: {output}")

print("\nEdit-plan generation complete.")