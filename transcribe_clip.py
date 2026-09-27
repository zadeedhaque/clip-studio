from faster_whisper import WhisperModel
import os

INPUT = r"clips\clip_01.mp4"
OUTPUT_DIR = "captions"

os.makedirs(OUTPUT_DIR, exist_ok=True)

print("Loading Whisper model...")

model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

print("Transcribing clip_01...")

segments, info = model.transcribe(
    INPUT,
    beam_size=5,
    vad_filter=True
)

segments = list(segments)

output = os.path.join(OUTPUT_DIR, "clip_01.srt")

with open(output, "w", encoding="utf-8") as f:
    for i, segment in enumerate(segments, 1):
        start = segment.start
        end = segment.end
        text = segment.text.strip()

        def timestamp(seconds):
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds - int(seconds)) * 1000)

            return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

        f.write(f"{i}\n")
        f.write(f"{timestamp(start)} --> {timestamp(end)}\n")
        f.write(f"{text}\n\n")

print(f"Caption file created: {output}")