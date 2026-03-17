"""
test_gemini_frames.py
Run Gemini Vision analysis on 5 representative frames from the WeChat HVAC tutorial.
Saves JSON + Markdown report to test/gemini_wechat_analysis/

Usage:
    cd backend
    python test_gemini_frames.py
"""
import os
import sys
import json
import asyncio
import re
from pathlib import Path
from datetime import datetime

# Load .env from project root BEFORE any other imports
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import base64
import requests
from PIL import Image

# ── Config ────────────────────────────────────────────────────────────────────
VIDEO_ID    = "13003e3e-d1f9-4e3e-bbc3-d4d44733b754"
VIDEO_NAME  = "WeChat_20260317004736.mp4"
FPS         = 29.93189557321226
N_FRAMES    = 5          # how many frames to test

STORAGE_DIR  = Path(os.getenv("STORAGE_DIR", "./storage"))
VIDEO_DIR    = STORAGE_DIR / "videos" / VIDEO_ID
FRAMES_DIR   = VIDEO_DIR / "frames"
TEXT_DIR     = VIDEO_DIR / "text"
OUT_DIR      = Path(__file__).parent.parent / "test" / "gemini_wechat_analysis"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

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
    """Parse transcript.txt into list of {start, end, text} dicts."""
    txt_file = TEXT_DIR / "transcript.txt"
    if not txt_file.exists():
        return []
    segments = []
    pattern = re.compile(r"\[(\d+\.?\d*)s\s*[–-]\s*(\d+\.?\d*)s\]\s*\n(.+)", re.MULTILINE)
    content = txt_file.read_text(encoding="utf-8")
    for m in pattern.finditer(content):
        segments.append({
            "start": float(m.group(1)),
            "end":   float(m.group(2)),
            "text":  m.group(3).strip(),
        })
    return segments


def get_transcript_at(timestamp: float, segments: list[dict], window: float = 6.0) -> str:
    """Return all transcript text within ±window seconds of the timestamp."""
    matched = [
        s["text"] for s in segments
        if s["start"] <= timestamp + window and s["end"] >= timestamp - window
    ]
    return " ".join(matched) if matched else "(no transcript at this time)"


# ── Frame picker ──────────────────────────────────────────────────────────────
def pick_frames(n: int) -> list[Path]:
    """Pick n evenly spaced frame files from the frames directory."""
    all_frames = sorted(FRAMES_DIR.glob("*.jpg"))
    if not all_frames:
        raise FileNotFoundError(f"No frames found in {FRAMES_DIR}")
    step = max(1, (len(all_frames) - 1) // (n - 1)) if n > 1 else 1
    picked = [all_frames[min(i * step, len(all_frames) - 1)] for i in range(n)]
    return picked


def frame_timestamp(frame_path: Path) -> float:
    """Extract frame number from filename and convert to seconds."""
    m = re.search(r"_f(\d+)\.jpg$", frame_path.name)
    if m:
        return int(m.group(1)) / FPS
    return 0.0


# ── Gemini call ───────────────────────────────────────────────────────────────
def analyze_frame(client: dict, frame_path: Path, transcript: str) -> dict:
    """Call Gemini REST API with image + transcript."""
    prompt = GEMINI_PROMPT.format(transcript=transcript)

    # Read and base64-encode the image
    with open(str(frame_path), "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{client['model']}:generateContent?key={client['api_key']}"
    )
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
            ]
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
            "temperature": 1.0,
            "thinkingConfig": {"thinkingBudget": 512},
        },
    }
    resp = requests.post(url, json=payload, timeout=60)
    if not resp.ok:
        raise RuntimeError(f"Gemini API {resp.status_code}: {resp.text[:300]}")
    resp.raise_for_status()
    body = resp.json()

    raw = body["candidates"][0]["content"]["parts"][0]["text"]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"raw_text": raw, "parse_error": True}

    usage = body.get("usageMetadata", {})
    return {
        "parsed": data,
        "raw_response": raw,
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }


# ── Report builder ────────────────────────────────────────────────────────────
def build_markdown(results: list[dict]) -> str:
    lines = [
        f"# Gemini Vision Analysis — {VIDEO_NAME}",
        f"**Model:** {GEMINI_MODEL}  ",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Frames analyzed:** {len(results)}",
        "",
    ]
    for r in results:
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
            f"**Objects:** {', '.join(d.get('objects', []))}  ",
            f"**Actions:** {', '.join(d.get('actions', []))}",
            "",
        ]
        if d.get("text_on_screen"):
            lines += [f"**Text on screen:** {', '.join(d['text_on_screen'])}", ""]
        if d.get("key_concepts"):
            lines += [f"**Key concepts:** {', '.join(d['key_concepts'])}", ""]
        if d.get("instructor_notes"):
            lines += [f"**Instructor notes:** {d['instructor_notes']}", ""]
        tok = r.get("usage", {})
        if tok:
            lines += [f"*Tokens: {tok.get('total_tokens', '?')} total*", ""]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env"); sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Using Gemini model: {GEMINI_MODEL} (REST API)")
    client = {"api_key": GEMINI_API_KEY, "model": GEMINI_MODEL}

    print("Loading transcript...")
    transcript_segs = load_transcript()
    print(f"  {len(transcript_segs)} transcript segments loaded")

    print(f"Picking {N_FRAMES} frames...")
    frames = pick_frames(N_FRAMES)
    for f in frames:
        ts = frame_timestamp(f)
        print(f"  {f.name}  ->  {ts:.1f}s")

    results = []
    for i, frame_path in enumerate(frames, 1):
        ts         = frame_timestamp(frame_path)
        transcript = get_transcript_at(ts, transcript_segs)
        fn_match   = re.search(r"_f(\d+)\.jpg$", frame_path.name)
        frame_num  = int(fn_match.group(1)) if fn_match else 0

        print(f"\n[{i}/{N_FRAMES}] Analyzing frame {frame_num} ({ts:.1f}s)...")
        print(f"  Transcript: {transcript[:80]}...".encode("ascii", "replace").decode())

        result = analyze_frame(client, frame_path, transcript)
        tok = result["usage"].get("total_tokens", "?")
        print(f"  Done. Tokens used: {tok}")
        desc = result['parsed'].get('description', '')[:100]
        print(f"  Description: {desc}")

        entry = {
            "frame_number": frame_num,
            "timestamp": round(ts, 2),
            "frame_file": frame_path.name,
            "transcript": transcript,
            **result,
        }
        results.append(entry)

        # Save per-frame JSON
        json_path = OUT_DIR / f"frame_{frame_num:06d}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
        print(f"  Saved -> {json_path.name}")

    # Save combined JSON
    combined_path = OUT_DIR / "all_results.json"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump({
            "video_id": VIDEO_ID,
            "video_name": VIDEO_NAME,
            "model": GEMINI_MODEL,
            "generated_at": datetime.now().isoformat(),
            "frames": results,
        }, f, ensure_ascii=False, indent=2)

    # Save Markdown report
    md_path = OUT_DIR / "report.md"
    md_path.write_text(build_markdown(results), encoding="utf-8")

    print(f"\nDone. Results saved to: {OUT_DIR}")
    print(f"  {combined_path.name}")
    print(f"  {md_path.name}")
    print(f"  + {len(results)} frame_XXXXXX.json files")


if __name__ == "__main__":
    main()
