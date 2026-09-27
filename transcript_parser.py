import re
import json
from pathlib import Path


INPUT_FILE = Path("input/transcript.txt")
OUTPUT_FILE = Path("transcript.json")


TIMESTAMP_PATTERN = re.compile(
    r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(\d{2}:\d{2}:\d{2},\d{3})\s+\[([^\]]+)\]"
)


def timestamp_to_seconds(timestamp):
    """
    Convert:
        HH:MM:SS,mmm
    into:
        seconds as float
    """

    hours, minutes, seconds_ms = timestamp.split(":")
    seconds, milliseconds = seconds_ms.split(",")

    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def parse_transcript():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"Could not find {INPUT_FILE}"
        )

    text = INPUT_FILE.read_text(
        encoding="utf-8-sig"
    )

    lines = text.splitlines()

    entries = []

    current = None

    for line in lines:
        line = line.strip()

        # Ignore completely empty lines
        if not line:
            continue

        match = TIMESTAMP_PATTERN.match(line)

        # New timestamp block
        if match:

            # Save previous block
            if current is not None:
                current["text"] = " ".join(current["text"]).strip()

                if current["text"]:
                    entries.append(current)

            start_timestamp = match.group(1)
            end_timestamp = match.group(2)
            speaker = match.group(3)

            current = {
                "start": timestamp_to_seconds(start_timestamp),
                "end": timestamp_to_seconds(end_timestamp),
                "speaker": speaker,
                "text": []
            }

        else:
            # This line belongs to the current timestamp block
            if current is not None:
                current["text"].append(line)

    # Save final block
    if current is not None:
        current["text"] = " ".join(current["text"]).strip()

        if current["text"]:
            entries.append(current)

    return entries


def main():
    entries = parse_transcript()

    OUTPUT_FILE.write_text(
        json.dumps(
            entries,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print(f"Parsed {len(entries)} transcript entries.")
    print(f"Saved to: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()