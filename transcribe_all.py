from faster_whisper import WhisperModel
import os

INPUT_DIR = "clips"
OUTPUT_DIR = "captions"

os.makedirs(OUTPUT_DIR, exist_ok=True)

print("Loading Whisper model...")
model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

for filename in sorted(os.listdir(INPUT_DIR)):
    if not filename.lower().endswith(".mp4"):
        continue

    input_path = os.path.join(INPUT_DIR, filename)
    name = os.path.splitext(filename)[0]
    output_path = os.path.join(OUTPUT_DIR, f"{name}.srt")

    if os.path.exists(output_path):
        print(f"Skipping {filename} — caption already exists.")
        continue

    print(f"\nTranscribing {filename}...")

    segments, info = model.transcribe(
        input_path,
        beam_size=5,
        vad_filter=True
    )

    segments = list(segments)

    def timestamp(seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds - int(seconds)) * 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    with open(output_path, "w", encoding="utf-8") as f:
        for i, segment in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(
                f"{timestamp(segment.start)} --> "
                f"{timestamp(segment.end)}\n"
            )
            f.write(f"{segment.text.strip()}\n\n")

    print(f"Created: {output_path}")

print("\nAll captions finished.")