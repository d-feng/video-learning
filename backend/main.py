import os
import sys
import uuid
import asyncio
import json
import subprocess
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import aiofiles

from models.schemas import (
    SearchQuery, SearchResponse, VideoMetadata, UploadResponse,
    InstructionSet, VideoListResponse, VideoSegment
)
from video_processor import VideoProcessor
from audio_processor import AudioProcessor
from ai_service import AIService
from search_service import SearchService
from db_service import DBService

app = FastAPI(title="Video Learning", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage"))
UPLOAD_DIR = STORAGE_DIR / "uploads"
VIDEOS_DIR = STORAGE_DIR / "videos"   # per-video folders live here

for d in [UPLOAD_DIR, VIDEOS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Serve all per-video assets under /video-files/{video_id}/...
app.mount("/video-files", StaticFiles(directory=str(VIDEOS_DIR)), name="video-files")


def make_video_dirs(video_id: str) -> dict[str, Path]:
    """Create and return per-video subdirectories."""
    base = VIDEOS_DIR / video_id
    dirs = {
        "frames":      base / "frames",
        "thumbnails":  base / "thumbnails",
        "audio":       base / "audio",
        "text":        base / "text",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def append_analysis_text(video_id: str, batch_segs):
    """Append analyzed segments to analysis.txt immediately after each batch."""
    text_dir = VIDEOS_DIR / video_id / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
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
    """Write transcript.txt and instructions.txt; analysis.txt already written incrementally."""
    text_dir = VIDEOS_DIR / video_id / "text"
    text_dir.mkdir(parents=True, exist_ok=True)

    # 1. Raw transcript
    with open(text_dir / "transcript.txt", "w", encoding="utf-8") as f:
        f.write(f"TRANSCRIPT — {video_id}\n{'='*60}\n\n")
        for seg in transcript_segments:
            f.write(f"[{seg.start:.1f}s – {seg.end:.1f}s]\n{seg.text}\n\n")

    # 2. Step-by-step instructions
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

db = DBService()
ai = AIService()
search = SearchService(db, ai)



# Track active WebSocket connections per video_id
ws_connections: dict[str, WebSocket] = {}


async def process_video_task(video_id: str, video_path: str):
    """Full pipeline: extract frames + audio, analyze with AI, index for search."""
    ws = ws_connections.get(video_id)

    async def progress(pct: int, msg: str):
        print(f"[{video_id[:8]}] {pct}% — {msg}", flush=True)
        db.update_video_status(video_id, "processing", progress=pct, message=msg)
        if ws:
            try:
                await ws.send_json({"type": "progress", "pct": pct, "msg": msg})
            except Exception:
                pass

    try:
        dirs = make_video_dirs(video_id)
        dirs["responses"] = VIDEOS_DIR / video_id / "responses"
        dirs["responses"].mkdir(parents=True, exist_ok=True)

        await progress(2, "Extracting audio (ffmpeg)...")
        audio_proc = AudioProcessor(dirs["audio"])
        audio_path = await audio_proc.extract_audio(video_path)
        print(f"[{video_id[:8]}] Audio extracted → {audio_path}", flush=True)

        await progress(10, "Transcribing audio with Whisper (may take 30-60s)...")
        transcript_segments = await audio_proc.transcribe(audio_path)
        print(f"[{video_id[:8]}] Transcription done — {len(transcript_segments)} segments", flush=True)

        await progress(20, "Extracting video frames...")
        vid_proc = VideoProcessor(dirs["frames"], dirs["thumbnails"])
        frames = await vid_proc.extract_frames(video_path)

        await progress(35, "Aligning transcript to frames...")
        aligned = audio_proc.align_transcript_to_frames(transcript_segments, frames)

        from ai_service import VISION_BATCH_SIZE
        total = len(aligned)
        batch_size = max(1, VISION_BATCH_SIZE)
        await progress(40, f"Analyzing {total} segments (batch={batch_size}) with GPT-4o Vision...")

        # Write analysis.txt header before loop so file exists even if pipeline stops early
        text_dir = VIDEOS_DIR / video_id / "text"
        text_dir.mkdir(parents=True, exist_ok=True)
        with open(text_dir / "analysis.txt", "w", encoding="utf-8") as f:
            f.write(f"FRAME ANALYSIS — {video_id}\n{'='*60}\n\n")

        segments = []
        for i in range(0, total, batch_size):
            batch = aligned[i: i + batch_size]
            # Retry up to 3 times with back-off before giving up on a batch
            for attempt in range(3):
                try:
                    batch_segs = await ai.analyze_segments_batch(batch, dirs["responses"])
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"[{video_id[:8]}] Batch {i} failed after 3 attempts: {e}", flush=True)
                        # Use placeholder segments so pipeline continues
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
                        print(f"[{video_id[:8]}] Batch {i} attempt {attempt+1} failed, retrying in {wait}s: {e}", flush=True)
                        await asyncio.sleep(wait)
            for seg in batch_segs:
                seg.video_id = video_id
            segments.extend(batch_segs)
            append_analysis_text(video_id, batch_segs)
            pct = 40 + int(40 * min(i + batch_size, total) / total)
            await progress(pct, f"Analyzed {min(i+batch_size, total)}/{total} segments...")
            try:
                db.save_segments(video_id, batch_segs)
            except Exception as e:
                print(f"[{video_id[:8]}] DB save failed (batch {i}): {e}", flush=True)

        await progress(82, "Extracting step-by-step instructions...")
        instructions = await ai.extract_instructions(segments, dirs["responses"])
        db.save_instructions(video_id, instructions)

        await progress(90, "Indexing for search...")
        await search.index_video(video_id, segments)

        await progress(95, "Saving text exports...")
        save_text_exports(video_id, segments, instructions, transcript_segments)

        db.update_video_status(video_id, "completed", progress=100, message="Done")
        await progress(100, "Processing complete!")

        if ws:
            await ws.send_json({"type": "done", "video_id": video_id})

    except Exception as e:
        db.update_video_status(video_id, "failed", message=str(e))
        if ws:
            try:
                await ws.send_json({"type": "error", "msg": str(e)})
            except Exception:
                pass
        raise


@app.post("/upload", response_model=UploadResponse)
async def upload_video(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    local_path: Optional[str] = Form(None),
):
    video_id = str(uuid.uuid4())

    if file:
        dest = UPLOAD_DIR / f"{video_id}_{file.filename}"
        async with aiofiles.open(str(dest), "wb") as f:
            content = await file.read()
            await f.write(content)
        video_path = str(dest)
        original_name = file.filename

    elif url:
        import yt_dlp
        dest = str(UPLOAD_DIR / f"{video_id}.mp4")
        ydl_opts = {
            "outtmpl": dest,
            "format": "best[ext=mp4]/best",
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            original_name = info.get("title", url)
        video_path = dest

    elif local_path:
        if not Path(local_path).exists():
            raise HTTPException(400, f"File not found: {local_path}")
        video_path = local_path
        original_name = Path(local_path).name

    else:
        raise HTTPException(400, "Provide file, url, or local_path")

    # Get basic video metadata
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    duration = total_frames / fps if fps > 0 else 0
    cap.release()

    meta = VideoMetadata(
        video_id=video_id,
        original_filename=original_name,
        duration=duration,
        fps=fps,
        total_frames=total_frames,
        width=width,
        height=height,
        status="uploaded",
    )
    db.save_video_metadata(meta)

    # Launch worker as a subprocess — survives uvicorn reloads completely
    worker = Path(__file__).parent / "worker.py"
    proc = subprocess.Popen(
        [sys.executable, str(worker), video_id, video_path],
        cwd=str(Path(__file__).parent),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    print(f"[upload] Worker PID {proc.pid} started for {video_id[:8]}", flush=True)

    return UploadResponse(video_id=video_id, message="Processing started", video=meta)


@app.websocket("/ws/{video_id}")
async def websocket_progress(websocket: WebSocket, video_id: str):
    await websocket.accept()
    ws_connections[video_id] = websocket
    try:
        # Send current status immediately
        meta = db.get_video_metadata(video_id)
        if meta:
            await websocket.send_json({
                "type": "status",
                "pct": meta.get("progress", 0),
                "msg": meta.get("message", ""),
                "status": meta.get("status", ""),
            })
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        ws_connections.pop(video_id, None)


@app.post("/search", response_model=SearchResponse)
async def search_video(query: SearchQuery):
    return await search.search(query)


@app.get("/videos", response_model=VideoListResponse)
def list_videos():
    videos = db.list_videos()
    return VideoListResponse(videos=videos)


@app.get("/videos/{video_id}/instructions", response_model=InstructionSet)
def get_instructions(video_id: str):
    instructions = db.get_instructions(video_id)
    if instructions is None:
        raise HTTPException(404, "Instructions not found or still processing")
    return instructions


@app.get("/videos/{video_id}/segments")
def get_segments(video_id: str, limit: int = 50, offset: int = 0):
    segments = db.get_segments(video_id, limit=limit, offset=offset)
    return {"segments": segments, "total": db.count_segments(video_id)}


@app.get("/videos/{video_id}/api-logs")
def get_api_logs(video_id: str):
    """Return all saved API response logs for a video, highlighting any errors."""
    responses_dir = VIDEOS_DIR / video_id / "responses"
    if not responses_dir.exists():
        return {"logs": [], "error_count": 0}

    logs = []
    for f in sorted(responses_dir.glob("*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            raw = data.get("raw_response", "")
            has_error = (
                "error" in raw.lower()
                or "Error" in raw
                or not data.get("parsed_results") and not data.get("parsed")
            )
            logs.append({
                "file": f.name,
                "type": data.get("type"),
                "model": data.get("model"),
                "frame_number": data.get("frame_number"),
                "timestamp": data.get("timestamp"),
                "has_error": has_error,
                "raw_response": raw[:500],          # first 500 chars
                "usage": data.get("usage", {}),
            })
        except Exception as e:
            logs.append({"file": f.name, "has_error": True, "raw_response": str(e)})

    error_count = sum(1 for l in logs if l["has_error"])
    return {"logs": logs, "total": len(logs), "error_count": error_count}


@app.get("/videos/{video_id}/stats")
def get_video_stats(video_id: str):
    meta = db.get_video_metadata(video_id)
    if not meta:
        raise HTTPException(404, "Video not found")
    stats = db.get_video_stats(video_id)
    embeddings_ready = (VIDEOS_DIR / video_id / "text" / "embeddings.json").exists()
    return {**meta, **stats, "embeddings_ready": embeddings_ready}


@app.get("/videos/{video_id}/download/{file}")
def download_text(video_id: str, file: str):
    from fastapi.responses import FileResponse
    allowed = {"transcript.txt", "analysis.txt", "instructions.txt"}
    if file not in allowed:
        raise HTTPException(400, f"file must be one of {allowed}")
    path = VIDEOS_DIR / video_id / "text" / file
    if not path.exists():
        raise HTTPException(404, "File not ready yet")
    return FileResponse(str(path), filename=f"{video_id[:8]}_{file}", media_type="text/plain")


@app.post("/videos/{video_id}/reindex")
def reindex_video(video_id: str):
    """Load saved embeddings and push to ChromaDB. Call after processing completes."""
    emb_file = VIDEOS_DIR / video_id / "text" / "embeddings.json"
    if not emb_file.exists():
        raise HTTPException(404, "embeddings.json not found — video may not have completed processing")
    with open(emb_file, encoding="utf-8") as f:
        emb_map = {e["segment_id"]: e["embedding"] for e in json.load(f)}
    segs = db.get_segments(video_id, limit=500)
    if not segs:
        raise HTTPException(404, "No segments found for this video")
    seg_objs = [VideoSegment(**s, embedding=emb_map.get(s["segment_id"], [0.0]*1536)) for s in segs]
    try:
        db.index_segments_chroma(video_id, seg_objs)
    except Exception as e:
        raise HTTPException(500, f"ChromaDB indexing failed: {e}")
    return {"indexed": len(seg_objs), "video_id": video_id}


@app.delete("/videos/{video_id}")
def delete_video(video_id: str):
    db.delete_video(video_id)
    search.delete_video(video_id)
    return {"deleted": video_id}


@app.get("/health")
def health():
    return {"status": "ok"}
