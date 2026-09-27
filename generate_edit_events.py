import json
import os
import re

INPUT_DIR = "edit_plans"
OUTPUT_DIR = "edit_events"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def words(text):
    return re.findall(r"\b[\w'-]+\b", text)


def build_events(plan):
    events = []
    transcript = plan.get("transcript", [])
    duration = plan.get("duration", 0)

    # Opening hook
    events.append({
        "time": 0.0,
        "type": "hook",
        "action": "punch_in",
        "scale": 1.08
    })

    # Analyze each transcript segment
    for i, segment in enumerate(transcript):
        start = float(segment["start"])
        end = float(segment["end"])
        text = segment.get("text", "").strip()

        if not text:
            continue

        text_words = words(text)

        # Caption event
        events.append({
            "time": start,
            "type": "caption",
            "text": text,
            "start": start,
            "end": end
        })

        # Speaker changes get a subtle reframing
        if i > 0:
            previous = transcript[i - 1]

            if (
                segment.get("speaker")
                and previous.get("speaker")
                and segment["speaker"] != previous["speaker"]
            ):
                events.append({
                    "time": start,
                    "type": "speaker_change",
                    "action": "punch_in",
                    "scale": 1.05
                })

        # Longer pauses can be marked for cleanup
        if i > 0:
            previous_end = float(transcript[i - 1]["end"])
            pause = start - previous_end

            if pause >= 0.8:
                events.append({
                    "time": previous_end,
                    "type": "dead_air",
                    "action": "cut_or_reduce",
                    "duration": round(pause, 3)
                })

        # Emphasize potentially important words
        emphasis_words = []

        keywords = {
            "never", "always", "actually", "really",
            "love", "hate", "crazy", "bro",
            "what", "why", "how", "no", "yes",
            "seven", "crore", "million", "one",
            "best", "worst", "secret"
        }

        for word in text_words:
            if word.lower().strip(".,!?") in keywords:
                emphasis_words.append(word)

        if emphasis_words:
            events.append({
                "time": start,
                "type": "keyword_emphasis",
                "words": emphasis_words
            })

    # Stronger movement near the end/payoff
    if duration > 4:
        events.append({
            "time": round(max(duration - 2.0, 0), 3),
            "type": "ending",
            "action": "return_to_normal"
        })

    events.sort(key=lambda x: x["time"])

    return events


for filename in sorted(os.listdir(INPUT_DIR)):
    if not filename.endswith(".json"):
        continue

    path = os.path.join(INPUT_DIR, filename)

    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    events = build_events(plan)

    output = {
        "clip_id": plan["clip_id"],
        "duration": plan["duration"],
        "format": "1080x1920",
        "fps": 30,
        "events": events
    }

    output_path = os.path.join(OUTPUT_DIR, filename)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Created: {output_path}")

print("\nEdit event generation complete.")