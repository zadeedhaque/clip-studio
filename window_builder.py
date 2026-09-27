import json
from pathlib import Path


INPUT_FILE = Path("transcript.json")
OUTPUT_FILE = Path("windows.json")

# Window configuration
WINDOW_LENGTH = 75       # seconds
OVERLAP = 20             # seconds
STEP = WINDOW_LENGTH - OVERLAP


def build_windows(entries):
    if not entries:
        return []

    windows = []

    # Full transcript duration
    transcript_start = entries[0]["start"]
    transcript_end = entries[-1]["end"]

    current_start = transcript_start

    while current_start < transcript_end:

        current_end = current_start + WINDOW_LENGTH

        # Find entries that overlap this window
        window_entries = []

        for entry in entries:

            # Entry ends before window starts
            if entry["end"] <= current_start:
                continue

            # Entry starts after window ends
            if entry["start"] >= current_end:
                break

            window_entries.append(entry)

        if window_entries:

            windows.append({
                "id": len(windows) + 1,
                "start": current_start,
                "end": min(current_end, transcript_end),
                "entries": window_entries
            })

        current_start += STEP

    return windows


def main():

    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {INPUT_FILE}"
        )

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        entries = json.load(f)

    windows = build_windows(entries)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            windows,
            f,
            indent=2,
            ensure_ascii=False
        )

    print(f"Created {len(windows)} windows.")
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()