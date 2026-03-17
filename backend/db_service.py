"""
db_service.py
Persistent storage using two backends:
  - ChromaDB  → vector store for segment embeddings
  - TinyDB (JSON file) → lightweight metadata store for videos, instructions, segments

No cloud account required — everything is local files.
"""
import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

import chromadb
from tinydb import TinyDB, Query

from models.schemas import VideoMetadata, VideoSegment, InstructionSet

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage"))
DB_DIR = STORAGE_DIR / "db"
DB_DIR.mkdir(parents=True, exist_ok=True)

CHROMA_PATH = str(DB_DIR / "chroma")
TINYDB_PATH = str(DB_DIR / "metadata.json")

SEGMENTS_COLLECTION = "video_segments"


class DBService:
    def __init__(self):
        self._chroma = None
        self._collection = None
        self._db = TinyDB(TINYDB_PATH, indent=2)
        self._videos = self._db.table("videos")
        self._segments_meta = self._db.table("segments")
        self._instructions = self._db.table("instructions")

    def _get_collection(self):
        """Lazy-init ChromaDB — only opened when actually needed for search/indexing."""
        if self._collection is None:
            self._chroma = chromadb.PersistentClient(path=CHROMA_PATH)
            self._collection = self._chroma.get_or_create_collection(
                name=SEGMENTS_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # ── Video metadata ────────────────────────────────────────────────────────

    def save_video_metadata(self, meta: VideoMetadata):
        V = Query()
        doc = meta.model_dump()
        doc["created_at"] = doc["created_at"].isoformat()
        existing = self._videos.get(V.video_id == meta.video_id)
        if existing:
            self._videos.update(doc, V.video_id == meta.video_id)
        else:
            self._videos.insert(doc)

    def update_video_status(self, video_id: str, status: str, progress: int = 0, message: str = ""):
        V = Query()
        self._videos.update(
            {"status": status, "progress": progress, "message": message},
            V.video_id == video_id,
        )

    def get_video_metadata(self, video_id: str) -> Optional[dict]:
        V = Query()
        return self._videos.get(V.video_id == video_id)

    def list_videos(self) -> list[dict]:
        return self._videos.all()

    # ── Segments ──────────────────────────────────────────────────────────────

    def save_segments(self, video_id: str, segments: list[VideoSegment]):
        """Save segment metadata to TinyDB only (no Chroma — indexing done separately via index_video)."""
        S = Query()
        for seg in segments:
            seg.video_id = video_id
            doc = seg.model_dump(exclude={"embedding"})
            existing = self._segments_meta.get(S.segment_id == seg.segment_id)
            if existing:
                self._segments_meta.update(doc, S.segment_id == seg.segment_id)
            else:
                self._segments_meta.insert(doc)

    def index_segments_chroma(self, video_id: str, segments: list[VideoSegment]):
        """Upsert embeddings to ChromaDB. Called once after all segments are ready."""
        ids, embeddings, documents, metadatas = [], [], [], []
        for seg in segments:
            if seg.embedding:
                ids.append(seg.segment_id)
                embeddings.append(seg.embedding)
                documents.append(seg.combined_text)
                metadatas.append({
                    "video_id": video_id,
                    "timestamp": seg.timestamp,
                    "frame_number": seg.frame_number,
                    "thumbnail_path": seg.thumbnail_path,
                    "scene_type": seg.scene_type,
                    "objects": json.dumps(seg.objects),
                    "actions": json.dumps(seg.actions),
                    "transcript": seg.transcript[:500],
                    "description": seg.description[:500],
                })
        if ids:
            self._get_collection().upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def get_segments(self, video_id: str, limit: int = 50, offset: int = 0) -> list[dict]:
        S = Query()
        all_segs = self._segments_meta.search(S.video_id == video_id)
        all_segs.sort(key=lambda x: x.get("timestamp", 0))
        return all_segs[offset: offset + limit]

    def count_segments(self, video_id: str) -> int:
        S = Query()
        return len(self._segments_meta.search(S.video_id == video_id))

    # ── Instructions ──────────────────────────────────────────────────────────

    def save_instructions(self, video_id: str, instructions: InstructionSet):
        V = Query()
        doc = instructions.model_dump()
        doc["created_at"] = doc["created_at"].isoformat()
        existing = self._instructions.get(V.video_id == video_id)
        if existing:
            self._instructions.update(doc, V.video_id == video_id)
        else:
            self._instructions.insert(doc)

    def get_instructions(self, video_id: str) -> Optional[dict]:
        V = Query()
        return self._instructions.get(V.video_id == video_id)

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete_video(self, video_id: str):
        V = Query()
        self._videos.remove(V.video_id == video_id)
        self._segments_meta.remove(V.video_id == video_id)
        self._instructions.remove(V.video_id == video_id)

    # ── Chroma direct access (used by SearchService) ──────────────────────────

    @property
    def collection(self):
        return self._get_collection()

    def get_video_name(self, video_id: str) -> str:
        meta = self.get_video_metadata(video_id)
        return meta.get("original_filename", "") if meta else ""

    def get_video_stats(self, video_id: str) -> dict:
        """Return database summary for a single video."""
        S = Query()
        segs = self._segments_meta.search(S.video_id == video_id)
        instructions = self._instructions.get(S.video_id == video_id)
        chroma_count = 0
        vector_count = 0
        # Only query ChromaDB if already open — never block to open it
        if self._collection is not None:
            try:
                chroma_count = self._collection.count()
                res = self._collection.get(where={"video_id": video_id}, include=[])
                vector_count = len(res["ids"])
            except Exception:
                pass
        step_count = len(instructions.get("steps", [])) if instructions else 0
        return {
            "segments_in_db": len(segs),
            "vectors_in_chroma": vector_count,
            "embeddings_ready": vector_count > 0,
            "instructions_saved": instructions is not None,
            "instruction_steps": step_count,
            "total_vectors_all_videos": chroma_count,
        }
