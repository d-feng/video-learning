"""
ai_service.py
Wraps OpenAI GPT-4o Vision + text-embedding-3-small.

Responsibilities:
- Analyze a video segment (frame image + transcript) → structured description
- Generate embeddings for search indexing / query
- Extract ordered instruction steps from the full segment list
"""
import os
import uuid
import base64
import asyncio
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from openai import AsyncOpenAI

from models.schemas import AlignedSegment, VideoSegment, InstructionStep, InstructionSet

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "./storage"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))
VISION_BATCH_SIZE = int(os.getenv("VISION_BATCH_SIZE", "1"))  # frames per GPT-4o call

_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=60.0)
_executor = ThreadPoolExecutor(max_workers=4)


def _load_image_b64(relative_url: str) -> str:
    """Convert /video-files/{video_id}/frames/{fname} → base64 string for GPT-4o."""
    parts = relative_url.lstrip("/").split("/")
    local_path = STORAGE_DIR / "videos" / parts[1] / parts[2] / parts[3]
    with open(str(local_path), "rb") as f:
        return base64.b64encode(f.read()).decode()


def _save_api_log(responses_dir: Path, filename: str, payload: dict):
    """Persist an API call log as JSON. Silent on errors."""
    if responses_dir is None:
        return
    try:
        responses_dir.mkdir(parents=True, exist_ok=True)
        with open(responses_dir / filename, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_SEGMENT_SYSTEM = """You are analyzing a frame from a tutorial/instructional video.
Given a video frame (image) and the spoken transcript at that moment, respond with JSON:
{
  "description": "<1-3 sentence visual description of what is shown>",
  "scene_type": "<overview|close-up|demo|diagram|text-slide|transition>",
  "objects": ["<list of visible objects or tools>"],
  "actions": ["<list of actions being performed or demonstrated>"]
}
Be concise and specific. Focus on educational content."""

_BATCH_SYSTEM = """You are analyzing multiple frames from a tutorial/instructional video in sequence.
Each frame is labeled [Frame N at Xs] with its transcript.
Respond with a JSON array — one object per frame in the same order:
[
  {
    "description": "<1-3 sentence visual description>",
    "scene_type": "<overview|close-up|demo|diagram|text-slide|transition>",
    "objects": ["<visible objects or tools>"],
    "actions": ["<actions being performed>"]
  }
]
Be concise. Focus on educational content."""

_INSTRUCTION_SYSTEM = """You are extracting step-by-step instructions from a tutorial video.
Given a list of analyzed video segments (JSON array with timestamp, description, transcript, objects, actions),
identify the distinct instructional steps in the correct order.

Respond with valid JSON:
{
  "title": "<tutorial title>",
  "summary": "<2-3 sentence overview of what is taught>",
  "steps": [
    {
      "step_number": 1,
      "title": "<short step title>",
      "description": "<clear instruction for this step>",
      "objects_needed": ["<tools/parts needed>"],
      "timestamp": <float seconds into video>
    }
  ]
}
Merge closely related actions into one step. Typically 3-15 steps for a tutorial."""


class AIService:

    async def analyze_segments_batch(
        self, items: list[AlignedSegment], responses_dir: Path = None
    ) -> list[VideoSegment]:
        """Send VISION_BATCH_SIZE frames in one GPT-4o call, return one VideoSegment per frame."""
        if VISION_BATCH_SIZE == 1 or len(items) == 1:
            return [await self.analyze_segment(items[0], responses_dir)]

        # Build multimodal content: interleave [label+image, transcript] for each frame
        content = []
        for i, item in enumerate(items):
            img_b64 = _load_image_b64(item.frame.file_path)
            content.append({
                "type": "text",
                "text": f"[Frame {i+1} at {item.frame.timestamp:.1f}s] Transcript: {item.transcript}",
            })
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "low"},
            })

        frame_nums = [it.frame.frame_number for it in items]
        raw_response = ""
        usage = {}
        try:
            response = await _client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=400 * len(items),
                messages=[
                    {"role": "system", "content": _BATCH_SYSTEM},
                    {"role": "user", "content": content},
                ],
            )
            raw_response = response.choices[0].message.content.strip()
            usage = response.usage.model_dump() if response.usage else {}
            start = raw_response.find("[")
            end = raw_response.rfind("]") + 1
            results = json.loads(raw_response[start:end]) if start >= 0 else []
        except Exception as e:
            results = []
            raw_response = str(e)

        # Save API log for the batch
        _save_api_log(responses_dir, f"batch_frames_{'_'.join(str(n) for n in frame_nums)}.json", {
            "type": "batch_vision",
            "model": OPENAI_MODEL,
            "frame_numbers": frame_nums,
            "timestamps": [it.frame.timestamp for it in items],
            "transcripts": [it.transcript for it in items],
            "raw_response": raw_response,
            "parsed_results": results,
            "usage": usage,
        })

        # Pad with fallback if GPT returned fewer items than expected
        while len(results) < len(items):
            results.append({"description": "[Analysis unavailable]", "scene_type": "unknown",
                            "objects": [], "actions": []})

        segments = []
        for item, data in zip(items, results):
            combined = f"{item.transcript} {data.get('description', '')}"
            embedding = await self.embed_text(combined)
            segments.append(VideoSegment(
                segment_id=str(uuid.uuid4()),
                video_id="",
                frame_number=item.frame.frame_number,
                timestamp=item.frame.timestamp,
                thumbnail_path=item.frame.thumbnail_path,
                transcript=item.transcript,
                description=data.get("description", ""),
                combined_text=combined,
                objects=data.get("objects", []),
                actions=data.get("actions", []),
                scene_type=data.get("scene_type", ""),
                embedding=embedding,
            ))
        return segments

    async def analyze_segment(
        self, item: AlignedSegment, responses_dir: Path = None
    ) -> VideoSegment:
        """Call GPT-4o Vision on one aligned segment → VideoSegment."""
        raw_response = ""
        usage = {}
        try:
            img_b64 = _load_image_b64(item.frame.file_path)
            response = await _client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=400,
                messages=[
                    {"role": "system", "content": _SEGMENT_SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}",
                                    "detail": "low",
                                },
                            },
                            {
                                "type": "text",
                                "text": f"Transcript at this moment:\n{item.transcript}",
                            },
                        ],
                    },
                ],
                response_format={"type": "json_object"},
            )
            raw_response = response.choices[0].message.content
            usage = response.usage.model_dump() if response.usage else {}
            data = json.loads(raw_response)
        except Exception as e:
            data = {
                "description": f"[Analysis unavailable: {e}]",
                "scene_type": "unknown",
                "objects": [],
                "actions": [],
            }
            raw_response = str(e)

        # Save API log per frame
        _save_api_log(responses_dir, f"frame_{item.frame.frame_number:06d}.json", {
            "type": "single_vision",
            "model": OPENAI_MODEL,
            "frame_number": item.frame.frame_number,
            "timestamp": item.frame.timestamp,
            "thumbnail_path": item.frame.thumbnail_path,
            "transcript": item.transcript,
            "raw_response": raw_response,
            "parsed": data,
            "usage": usage,
        })

        combined = f"{item.transcript} {data.get('description', '')}"
        embedding = await self.embed_text(combined)

        return VideoSegment(
            segment_id=str(uuid.uuid4()),
            video_id="",                          # filled in by caller
            frame_number=item.frame.frame_number,
            timestamp=item.frame.timestamp,
            thumbnail_path=item.frame.thumbnail_path,
            transcript=item.transcript,
            description=data.get("description", ""),
            combined_text=combined,
            objects=data.get("objects", []),
            actions=data.get("actions", []),
            scene_type=data.get("scene_type", ""),
            embedding=embedding,
        )

    async def embed_text(self, text: str) -> list[float]:
        """Generate a text embedding vector."""
        try:
            resp = await asyncio.wait_for(
                _client.embeddings.create(
                    model=EMBED_MODEL,
                    input=text[:8000],
                    dimensions=EMBED_DIM,
                ),
                timeout=45.0,
            )
            return resp.data[0].embedding
        except Exception as e:
            print(f"[embed_text] failed: {e}", flush=True)
            return [0.0] * EMBED_DIM

    async def embed_query(self, query: str) -> list[float]:
        return await self.embed_text(query)

    async def extract_instructions(
        self, segments: list[VideoSegment], responses_dir: Path = None
    ) -> InstructionSet:
        """Pass all analyzed segments to GPT-4o and ask for step extraction."""
        # Build compact representation to fit context
        compact = [
            {
                "timestamp": s.timestamp,
                "description": s.description,
                "transcript": s.transcript[:200],
                "objects": s.objects,
                "actions": s.actions,
            }
            for s in segments
        ]
        payload = json.dumps(compact, ensure_ascii=False)

        raw_response = ""
        usage = {}
        try:
            response = await _client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=2000,
                messages=[
                    {"role": "system", "content": _INSTRUCTION_SYSTEM},
                    {"role": "user", "content": f"Segments:\n{payload[:12000]}"},
                ],
                response_format={"type": "json_object"},
            )
            raw_response = response.choices[0].message.content
            usage = response.usage.model_dump() if response.usage else {}
            data = json.loads(raw_response)
        except Exception as e:
            data = {
                "title": "Tutorial",
                "summary": f"Instruction extraction failed: {e}",
                "steps": [],
            }
            raw_response = str(e)

        _save_api_log(responses_dir, "instructions_extraction.json", {
            "type": "instruction_extraction",
            "model": OPENAI_MODEL,
            "segment_count": len(segments),
            "raw_response": raw_response,
            "parsed": data,
            "usage": usage,
        })

        # Attach thumbnail from the nearest segment to each step
        raw_steps = data.get("steps", [])
        steps: list[InstructionStep] = []
        for raw in raw_steps:
            t = float(raw.get("timestamp", 0))
            nearest = min(segments, key=lambda s: abs(s.timestamp - t), default=None)
            steps.append(InstructionStep(
                step_number=raw.get("step_number", len(steps) + 1),
                title=raw.get("title", ""),
                description=raw.get("description", ""),
                objects_needed=raw.get("objects_needed", []),
                timestamp=t,
                thumbnail_path=nearest.thumbnail_path if nearest else "",
            ))

        video_id = segments[0].video_id if segments else ""
        return InstructionSet(
            video_id=video_id,
            title=data.get("title", "Tutorial"),
            summary=data.get("summary", ""),
            steps=steps,
            total_steps=len(steps),
        )
