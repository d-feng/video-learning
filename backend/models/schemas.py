from __future__ import annotations
from typing import Optional, List, Any
from datetime import datetime
from pydantic import BaseModel, Field


class VideoMetadata(BaseModel):
    video_id: str
    original_filename: str
    duration: float = 0.0
    fps: float = 30.0
    total_frames: int = 0
    width: int = 0
    height: int = 0
    status: str = "uploaded"          # uploaded | processing | completed | failed
    progress: int = 0                 # 0-100
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UploadResponse(BaseModel):
    video_id: str
    message: str
    video: VideoMetadata


class VideoListResponse(BaseModel):
    videos: List[dict]


# ── Frame / Segment ──────────────────────────────────────────────────────────

class FrameData(BaseModel):
    """Raw frame extracted from video."""
    frame_number: int
    timestamp: float          # seconds
    file_path: str            # full-res JPEG
    thumbnail_path: str       # 320×240 preview


class TranscriptSegment(BaseModel):
    """Whisper output segment."""
    start: float
    end: float
    text: str


class AlignedSegment(BaseModel):
    """Frame + matching transcript text, before AI analysis."""
    frame: FrameData
    transcript: str           # concatenated text covering this frame's window


class VideoSegment(BaseModel):
    """Fully analyzed segment stored in DB."""
    segment_id: str
    video_id: str
    frame_number: int
    timestamp: float
    thumbnail_path: str
    transcript: str
    description: str          # GPT-4o visual description
    combined_text: str        # transcript + description (for embedding)
    objects: List[str] = []
    actions: List[str] = []
    scene_type: str = ""
    embedding: Optional[List[float]] = None


# ── Instructions ─────────────────────────────────────────────────────────────

class InstructionStep(BaseModel):
    step_number: int
    title: str
    description: str
    objects_needed: List[str] = []
    timestamp: float          # video time reference
    thumbnail_path: str = ""  # frame image for this step


class InstructionSet(BaseModel):
    video_id: str
    title: str
    summary: str
    steps: List[InstructionStep]
    total_steps: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ── Search ───────────────────────────────────────────────────────────────────

class SearchQuery(BaseModel):
    query: str
    video_id: Optional[str] = None    # filter to one video; None = search all
    top_k: int = 8
    search_type: str = "hybrid"       # hybrid | semantic | text


class SearchResult(BaseModel):
    segment_id: str
    video_id: str
    video_name: str = ""
    frame_number: int
    timestamp: float
    transcript: str
    description: str
    thumbnail_path: str
    similarity_score: float           # 0-100
    objects: List[str] = []
    actions: List[str] = []


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResult]
    total_results: int
    processing_time_ms: float
