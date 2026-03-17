"""
test_gemini_all_frames.py
Run Gemini Vision analysis on ALL frames from the WeChat HVAC tutorial.
- Skips frames already analyzed (resume support)
- Saves each frame result immediately
- Combines into all_results.json + report.md at the end
- Up to 3 concurrent requests to stay within rate limits

Usage:
    cd backend
    python test_gemini_all_frames.py
"""
import os
import sys
import json
import asyncio
import re
import time
import base64
import aiohttp
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

# ── Config ────────────────────────────────────────────────────────────────────
VIDEO_ID    = "13003e3e-d1f9-4e3e-bbc3-d4d44733b754"
VIDEO_NAME  = "WeChat_20260317004736.mp4"
FPS         = 29.93189557321226
CONCURRENCY = 3          # parallel Gemini requests

STORAGE_DIR  = Path(os.getenv("STORAGE_DIR", "./storage"))
VIDEO_DIR    = STORAGE_DIR / "videos" / VIDEO_ID
FRAMES_DIR   = VIDEO_DIR / "frames"
TEXT_DIR     = VIDEO_DIR / "text"
OUT_DIR      = Path(__file__).parent.parent / "test" / "gemini_all_frames"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL     = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

GEMINI_PROMPT = """You are a technical expert analyzing a frame from a Chinese HVAC engineering tutorial video.

Given the video frame image and the spoken transcript (in Chinese) below, provide a DEEP and COMPREHENSIVE analysis in English.

Transcript at this moment (Chinese): "{transcript}"

Respond ONLY with valid JSON:
{{
  "description": "<2-4 sentence detailed visual description of what is shown>",
  "scene_type": "<overview|close-up|demo|diagram|text-slide|transition|calculation|setup|result>",
  "objects": ["<every visible object, tool, equipment, diagram, chart, formula>"],
  "actions": ["<all actions being performed or demonstrated>"],
  "text_on_screen": ["<any visible text, labels, Chinese characters, formulas, measurements, units>"],
  "key_concepts": ["<HVAC or engineering concepts being demonstrated>"],
  "technical_details": "<specific technical information: equations, values, temperatures, air volumes, units, HVAC parameters>",
  "step_context": "<beginning|middle|end|standalone — where in a step sequence this frame appears>",
  "instructor_notes": "<expert commentary: what an HVAC engineer would highlight about this moment>",
  "transcript_translation": "<English translation of the Chinese transcript>"
}}

Be thorough and precise. Focus on technical HVAC content, formulas, and engineering accuracy."""


# ── Transcript loader ─────────────────────────────────────────────────────────
def load_transcript() -> list[dict]:
    txt_file = TEXT_DIR / "transcript.txt"
    if not txt_file.exists():
        return []
    segments = []
    pattern = re.compile(r"\[(\d+\.?\d*)s\s*[–-]\s*(\d+\.?\d*)s\]\s*\n(.+)", re.MULTILINE)
    content = txt_file.read_text(encoding="utf-8")
    for m in pattern.finditer(content):
        segments.append({"start": float(m.group(1)), "end": float(m.group(2)), "text": m.group(3).strip()})
    return segments


def get_transcript_at(timestamp: float, segments: list[dict], window: float = 6.0) -> str:
    matched = [s["text"] for s in segments
               if s["start"] <= timestamp + window and s["end"] >= timestamp - window]
    return " ".join(matched) if matched else "(no transcript at this time)"


def frame_timestamp(frame_path: Path) -> float:
    m = re.search(r"_f(\d+)\.jpg$", frame_path.name)
    return int(m.group(1)) / FPS if m else 0.0


def frame_number(frame_path: Path) -> int:
    m = re.search(r"_f(\d+)\.jpg$", frame_path.name)
    return int(m.group(1)) if m else 0


# ── Async Gemini call ─────────────────────────────────────────────────────────
async def analyze_frame(session: aiohttp.ClientSession, frame_path: Path,
                        transcript: str, semaphore: asyncio.Semaphore) -> dict:
    prompt = GEMINI_PROMPT.format(transcript=transcript)
    with open(str(frame_path), "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
            "temperature": 1.0,
            "thinkingConfig": {"thinkingBudget": 512},
        },
    }

    retries = 0
    async with semaphore:
        while True:
            try:
                async with session.post(
                    GEMINI_URL, json=payload,
                    params={"key": GEMINI_API_KEY}, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status == 429:
                        wait = 30 * (retries + 1)
                        print(f"  [rate limit] waiting {wait}s...", flush=True)
                        await asyncio.sleep(wait)
                        retries += 1
                        continue
                    body = await resp.json()
                    raw = body["candidates"][0]["content"]["parts"][0]["text"]
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"raw_text": raw, "parse_error": True}
                    um = body.get("usageMetadata", {})
                    return {
                        "parsed": data,
                        "raw_response": raw,
                        "usage": {
                            "prompt_tokens": um.get("promptTokenCount", 0),
                            "completion_tokens": um.get("candidatesTokenCount", 0),
                            "total_tokens": um.get("totalTokenCount", 0),
                        },
                    }
            except Exception as e:
                if retries >= 3:
                    return {
                        "parsed": {"description": f"[failed: {e}]", "parse_error": True},
                        "raw_response": str(e),
                        "usage": {},
                    }
                await asyncio.sleep(10)
                retries += 1


# ── Report builder ────────────────────────────────────────────────────────────
def build_markdown(results: list[dict]) -> str:
    lines = [
        f"# Gemini Vision Analysis — {VIDEO_NAME}",
        f"**Model:** {GEMINI_MODEL}  ",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Frames analyzed:** {len(results)}",
        "",
    ]
    for r in sorted(results, key=lambda x: x["frame_number"]):
        ts   = r["timestamp"]
        d    = r["parsed"]
        mins = int(ts // 60)
        secs = ts % 60
        lines += [
            f"---",
            f"## Frame {r['frame_number']} — {mins}m {secs:.1f}s",
            f"**Transcript (CN):** {r['transcript']}  ",
            f"**Translation:** {d.get('transcript_translation', '—')}",
            "",
            f"### Description",
            d.get("description", "—"),
            "",
            f"### Technical Details",
            d.get("technical_details", "—"),
            "",
            f"**Scene type:** {d.get('scene_type', '—')}  ",
            f"**Step context:** {d.get('step_context', '—')}",
            "",
        ]
        if d.get("key_concepts"):
            lines += [f"**Key concepts:** {', '.join(d['key_concepts'])}", ""]
        if d.get("instructor_notes"):
            lines += [f"**Instructor notes:** {d['instructor_notes']}", ""]
        tok = r.get("usage", {})
        if tok:
            lines += [f"*Tokens: {tok.get('total_tokens', '?')} total*", ""]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env"); sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Model: {GEMINI_MODEL}")
    print(f"Output: {OUT_DIR}")

    print("Loading transcript...")
    transcript_segs = load_transcript()
    print(f"  {len(transcript_segs)} segments")

    all_frames = sorted(FRAMES_DIR.glob("*.jpg"))
    print(f"Total frames: {len(all_frames)}")

    # Find already-done frames
    done = {int(m.group(1)) for p in OUT_DIR.glob("frame_*.json")
            if (m := re.search(r"frame_(\d+)\.json", p.name))}
    todo = [f for f in all_frames if frame_number(f) not in done]
    print(f"Already done: {len(done)}  |  Remaining: {len(todo)}")

    if not todo:
        print("All frames already analyzed.")
    else:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        completed = 0
        t_start = time.time()

        async with aiohttp.ClientSession() as session:
            async def process(frame_path: Path):
                nonlocal completed
                ts         = frame_timestamp(frame_path)
                transcript = get_transcript_at(ts, transcript_segs)
                fn         = frame_number(frame_path)

                result = await analyze_frame(session, frame_path, transcript, semaphore)
                tok    = result["usage"].get("total_tokens", "?")

                entry = {
                    "frame_number": fn,
                    "timestamp": round(ts, 2),
                    "frame_file": frame_path.name,
                    "transcript": transcript,
                    **result,
                }
                json_path = OUT_DIR / f"frame_{fn:06d}.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False, indent=2)

                completed += 1
                elapsed = time.time() - t_start
                rate = completed / elapsed if elapsed > 0 else 0
                remaining = (len(todo) - completed) / rate if rate > 0 else 0
                print(
                    f"  [{completed}/{len(todo)}] frame {fn} ({ts:.0f}s) "
                    f"| tokens={tok} | {rate:.1f} fr/s | ETA {remaining/60:.1f}m",
                    flush=True,
                )

            tasks = [asyncio.create_task(process(f)) for f in todo]
            await asyncio.gather(*tasks)

    # Collect all results and write combined files
    print("\nCollecting all results...")
    all_results = []
    for json_path in sorted(OUT_DIR.glob("frame_*.json")):
        with open(json_path, encoding="utf-8") as f:
            all_results.append(json.load(f))
    all_results.sort(key=lambda x: x["frame_number"])

    combined_path = OUT_DIR / "all_results.json"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump({
            "video_id": VIDEO_ID,
            "video_name": VIDEO_NAME,
            "model": GEMINI_MODEL,
            "generated_at": datetime.now().isoformat(),
            "total_frames": len(all_results),
            "frames": all_results,
        }, f, ensure_ascii=False, indent=2)

    md_path = OUT_DIR / "report.md"
    md_path.write_text(build_markdown(all_results), encoding="utf-8")

    total_tokens = sum(r.get("usage", {}).get("total_tokens", 0) for r in all_results)
    print(f"\nDone. {len(all_results)} frames | {total_tokens:,} total tokens")
    print(f"  {combined_path}")
    print(f"  {md_path}")


if __name__ == "__main__":
    asyncio.run(main())
