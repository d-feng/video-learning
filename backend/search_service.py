"""
search_service.py
Hybrid search over video segments:
  - semantic  → ChromaDB cosine similarity on embeddings
  - text      → substring match on transcript + description
  - hybrid    → merge both result sets, deduplicate, re-rank by weighted score
"""
import json
import time
from typing import Optional

from db_service import DBService
from ai_service import AIService
from models.schemas import SearchQuery, SearchResult, SearchResponse


class SearchService:
    def __init__(self, db: DBService, ai: AIService):
        self.db = db
        self.ai = ai

    # ── Indexing ──────────────────────────────────────────────────────────────

    async def index_video(self, video_id: str, segments):
        """Batch-upsert all segment embeddings to ChromaDB after processing is complete."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, self.db.index_segments_chroma, video_id, segments),
                timeout=60.0,
            )
            print(f"[index_video] ChromaDB indexed {len(segments)} segments", flush=True)
        except Exception as e:
            print(f"[index_video] ChromaDB upsert failed/timeout: {e} — text search still works", flush=True)

    def delete_video(self, video_id: str):
        """Remove all Chroma documents for this video."""
        col = self.db.collection
        try:
            result = col.get(where={"video_id": video_id}, include=[])
            if result["ids"]:
                col.delete(ids=result["ids"])
        except Exception:
            pass

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(self, query: SearchQuery) -> SearchResponse:
        t0 = time.time()
        where = {"video_id": query.video_id} if query.video_id else None

        if query.search_type == "semantic":
            results = await self._semantic_search(query.query, query.top_k, where)
        elif query.search_type == "text":
            results = self._text_search(query.query, query.top_k, query.video_id)
        else:  # hybrid (default)
            sem = await self._semantic_search(query.query, query.top_k, where)
            txt = self._text_search(query.query, query.top_k, query.video_id)
            results = self._merge_hybrid(sem, txt, query.top_k)

        elapsed_ms = (time.time() - t0) * 1000
        return SearchResponse(
            query=query.query,
            results=results,
            total_results=len(results),
            processing_time_ms=round(elapsed_ms, 1),
        )

    # ── Private ───────────────────────────────────────────────────────────────

    async def _semantic_search(
        self, query: str, top_k: int, where: Optional[dict]
    ) -> list[SearchResult]:
        col = self.db.collection
        if col.count() == 0:
            return []   # nothing indexed — skip embedding API call entirely

        embedding = await self.ai.embed_query(query)

        kwargs = dict(
            query_embeddings=[embedding],
            n_results=min(top_k, max(1, col.count())),
            include=["metadatas", "distances", "documents"],
        )
        if where:
            kwargs["where"] = where

        try:
            raw = col.query(**kwargs)
        except Exception:
            return []

        results: list[SearchResult] = []
        ids = raw.get("ids", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        dists = raw.get("distances", [[]])[0]

        for seg_id, meta, dist in zip(ids, metas, dists):
            # Chroma cosine distance ∈ [0, 2]; convert to similarity %
            similarity = round((1 - dist / 2) * 100, 1)
            vid_id = meta.get("video_id", "")
            results.append(SearchResult(
                segment_id=seg_id,
                video_id=vid_id,
                video_name=self.db.get_video_name(vid_id),
                frame_number=int(meta.get("frame_number", 0)),
                timestamp=float(meta.get("timestamp", 0)),
                transcript=meta.get("transcript", ""),
                description=meta.get("description", ""),
                thumbnail_path=meta.get("thumbnail_path", ""),
                similarity_score=similarity,
                objects=json.loads(meta.get("objects", "[]")),
                actions=json.loads(meta.get("actions", "[]")),
            ))

        return results

    def _text_search(
        self, query: str, top_k: int, video_id: Optional[str]
    ) -> list[SearchResult]:
        """Simple case-insensitive substring search over TinyDB segment records."""
        from tinydb import Query as TQ
        S = TQ()

        q_lower = query.lower()
        all_segs = (
            self.db._segments_meta.search(S.video_id == video_id)
            if video_id
            else self.db._segments_meta.all()
        )

        scored: list[tuple[float, dict]] = []
        for seg in all_segs:
            text = (
                seg.get("transcript", "") + " " +
                seg.get("description", "") + " " +
                " ".join(seg.get("objects", [])) + " " +
                " ".join(seg.get("actions", []))
            ).lower()

            # Simple TF-style score: count keyword occurrences
            score = sum(text.count(word) for word in q_lower.split())
            if score > 0:
                scored.append((score, seg))

        scored.sort(key=lambda x: x[0], reverse=True)

        results: list[SearchResult] = []
        for score, seg in scored[:top_k]:
            max_score = scored[0][0] if scored else 1
            similarity = round(min(100.0, score / max_score * 85), 1)  # cap at 85 for text
            vid_id = seg.get("video_id", "")
            results.append(SearchResult(
                segment_id=seg.get("segment_id", ""),
                video_id=vid_id,
                video_name=self.db.get_video_name(vid_id),
                frame_number=seg.get("frame_number", 0),
                timestamp=seg.get("timestamp", 0.0),
                transcript=seg.get("transcript", ""),
                description=seg.get("description", ""),
                thumbnail_path=seg.get("thumbnail_path", ""),
                similarity_score=similarity,
                objects=seg.get("objects", []),
                actions=seg.get("actions", []),
            ))

        return results

    @staticmethod
    def _merge_hybrid(
        semantic: list[SearchResult],
        text: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        """
        Reciprocal Rank Fusion: combine semantic and text results.
        RRF score = 1/(k+rank), higher is better. k=60 is standard.
        """
        k = 60
        scores: dict[str, float] = {}
        best: dict[str, SearchResult] = {}

        for rank, r in enumerate(semantic):
            scores[r.segment_id] = scores.get(r.segment_id, 0) + 1 / (k + rank + 1)
            best[r.segment_id] = r

        for rank, r in enumerate(text):
            scores[r.segment_id] = scores.get(r.segment_id, 0) + 1 / (k + rank + 1)
            if r.segment_id not in best:
                best[r.segment_id] = r

        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

        merged: list[SearchResult] = []
        max_rrf = top[0][1] if top else 1
        for seg_id, rrf_score in top:
            r = best[seg_id]
            r.similarity_score = round(rrf_score / max_rrf * 100, 1)
            merged.append(r)

        return merged
