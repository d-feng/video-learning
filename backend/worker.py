"""
worker.py — standalone video processing pipeline.
Launched as a subprocess by main.py so it is fully independent of uvicorn.

Usage:
    python worker.py <video_id> <video_path>
"""
import sys
import os
import uuid
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

from models.schemas import VideoSegment
from video_processor import VideoProcessor
from audio_processor import AudioProcessor
from ai_service import AIService, VISION_BATCH_SIZE
from db_service import DBService

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage"))
VIDEOS_DIR = STORAGE_DIR / "videos"


def make_video_dirs(video_id: str) -> dict:
    base = VIDEOS_DIR / video_id
    dirs = {
        "frames":     base / "frames",
        "thumbnails": base / "thumbnails",
        "audio":      base / "audio",
        "text":       base / "text",
        "responses":  base / "responses",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def append_analysis_text(video_id: str, batch_segs):
    text_dir = VIDEOS_DIR / video_id / "text"
    with open(text_dir / "analysis.txt", "a", encoding="utf-8") as f:
        for seg in batch_segs:
            f.write(f"[{seg.timestamp:.1f}s | Frame {seg.frame_number}]\n")
            f.write(f"Transcript : {seg.transcript}\n")
            f.write(f"Description: {seg.description}\n")
            if seg.objects:
                f.write(f"Objects    : {', '.join(seg.objects)}\n")
            if seg.actions:
                f.write(f"Actions    : {', '.join(seg.actions)}\n")
            f.write("\n")


def save_text_exports(video_id: str, segments, instructions, transcript_segments):
    text_dir = VIDEOS_DIR / video_id / "text"

    with open(text_dir / "transcript.txt", "w", encoding="utf-8") as f:
        f.write(f"TRANSCRIPT — {video_id}\n{'='*60}\n\n")
        for seg in transcript_segments:
            f.write(f"[{seg.start:.1f}s – {seg.end:.1f}s]\n{seg.text}\n\n")

    with open(text_dir / "instructions.txt", "w", encoding="utf-8") as f:
        f.write(f"{instructions.title}\n{'='*60}\n\n")
        f.write(f"{instructions.summary}\n\n")
        for step in instructions.steps:
            f.write(f"Step {step.step_number}: {step.title}\n")
            f.write(f"  {step.description}\n")
            if step.objects_needed:
                f.write(f"  Need: {', '.join(step.objects_needed)}\n")
            f.write(f"  Time: {step.timestamp:.1f}s\n\n")

    print(f"[{video_id[:8]}] Text exports saved → {text_dir}", flush=True)


def progress(db, video_id, pct, msg):
    print(f"[{video_id[:8]}] {pct}% — {msg}", flush=True)
    db.update_video_status(video_id, "processing", progress=pct, message=msg)


async def run_pipeline(video_id: str, video_path: str):
    db = DBService()
    ai = AIService()

    try:
        dirs = make_video_dirs(video_id)

        progress(db, video_id, 2, "Extracting audio (ffmpeg)...")
        audio_proc = AudioProcessor(dirs["audio"])
        audio_path = await audio_proc.extract_audio(video_path)
        print(f"[{video_id[:8]}] Audio extracted → {audio_path}", flush=True)

        progress(db, video_id, 10, "Transcribing audio with Whisper...")
        transcript_segments = await audio_proc.transcribe(audio_path)
        print(f"[{video_id[:8]}] Transcription done — {len(transcript_segments)} segments", flush=True)

        progress(db, video_id, 20, "Extracting video frames...")
        vid_proc = VideoProcessor(dirs["frames"], dirs["thumbnails"])
        frames = await vid_proc.extract_frames(video_path)

        progress(db, video_id, 35, "Aligning transcript to frames...")
        aligned = audio_proc.align_transcript_to_frames(transcript_segments, frames)

        total = len(aligned)
        batch_size = max(1, VISION_BATCH_SIZE)
        progress(db, video_id, 40, f"Analyzing {total} segments (batch={batch_size}) with GPT-4o Vision...")

        # Create analysis.txt header
        with open(dirs["text"] / "analysis.txt", "w", encoding="utf-8") as f:
            f.write(f"FRAME ANALYSIS — {video_id}\n{'='*60}\n\n")

        segments = []
        for i in range(0, total, batch_size):
            batch = aligned[i: i + batch_size]
            frame_n = batch[0].frame.frame_number
            print(f"[{video_id[:8]}] → Starting frame {frame_n} ({i+1}/{total})", flush=True)

            for attempt in range(3):
                try:
                    print(f"[{video_id[:8]}]   calling GPT-4o for frame {frame_n}...", flush=True)
                    batch_segs = await ai.analyze_segments_batch(batch, dirs["responses"])
                    print(f"[{video_id[:8]}]   GPT-4o done for frame {frame_n}", flush=True)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"[{video_id[:8]}] Batch {i} failed after 3 attempts: {e}", flush=True)
                        batch_segs = [
                            VideoSegment(
                                segment_id=str(uuid.uuid4()),
                                video_id=video_id,
                                frame_number=item.frame.frame_number,
                                timestamp=item.frame.timestamp,
                                thumbnail_path=item.frame.thumbnail_path,
                                transcript=item.transcript,
                                description=f"[Analysis failed: {e}]",
                                combined_text=item.transcript,
                                objects=[], actions=[], scene_type="unknown",
                                embedding=[0.0] * 1536,
                            )
                            for item in batch
                        ]
                    else:
                        wait = 2 ** attempt
                        print(f"[{video_id[:8]}] Batch {i} attempt {attempt+1} failed ({e}), retrying in {wait}s", flush=True)
                        await asyncio.sleep(wait)

            for seg in batch_segs:
                seg.video_id = video_id
            segments.extend(batch_segs)
            append_analysis_text(video_id, batch_segs)

            pct = 40 + int(40 * min(i + batch_size, total) / total)
            progress(db, video_id, pct, f"Analyzed {min(i+batch_size, total)}/{total} segments...")

            print(f"[{video_id[:8]}]   saving to DB...", flush=True)
            try:
                db.save_segments(video_id, batch_segs)
                print(f"[{video_id[:8]}]   DB saved OK", flush=True)
            except Exception as e:
                print(f"[{video_id[:8]}]   DB save failed (batch {i}): {e}", flush=True)

        progress(db, video_id, 82, "Extracting step-by-step instructions...")
        instructions = await ai.extract_instructions(segments, dirs["responses"])
        db.save_instructions(video_id, instructions)

        # Save embeddings so main.py can index into ChromaDB without lock conflicts
        emb_path = dirs["text"] / "embeddings.json"
        with open(emb_path, "w", encoding="utf-8") as f:
            json.dump([{"segment_id": s.segment_id, "embedding": s.embedding} for s in segments], f)
        print(f"[{video_id[:8]}] Embeddings saved → {emb_path}", flush=True)

        progress(db, video_id, 95, "Saving text exports...")
        save_text_exports(video_id, segments, instructions, transcript_segments)

        db.update_video_status(video_id, "completed", progress=100, message="Done")
        print(f"[{video_id[:8]}] Pipeline complete!", flush=True)

    except Exception as e:
        print(f"[{video_id[:8]}] Pipeline failed: {e}", flush=True)
        db.update_video_status(video_id, "failed", message=str(e))
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python worker.py <video_id> <video_path>")
        sys.exit(1)
    asyncio.run(run_pipeline(sys.argv[1], sys.argv[2]))
