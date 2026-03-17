"""
analyze_hvac_solution.py
Secondary analysis of Gemini frame results → HVAC automation tool design report.

Reads test/gemini_all_frames/all_results.json, compacts the 449-frame dataset into
a structured digest, then asks GPT-4o to:
  1. Identify the complete HVAC workflow / algorithm taught in the video
  2. Design a Python automation tool with concrete module specs
  3. Output the tool as runnable Python code

Results saved to test/hvac_solution/:
  - digest.json        — compacted input sent to LLM
  - tool_report.md     — full analysis + tool design report
  - hvac_calculator.py — generated Python implementation

Usage:
    cd backend
    python analyze_hvac_solution.py
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from collections import Counter

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

from openai import OpenAI

# ── Config ────────────────────────────────────────────────────────────────────
IN_FILE  = Path(__file__).parent.parent / "test" / "gemini_all_frames" / "all_results.json"
OUT_DIR  = Path(__file__).parent.parent / "test" / "hvac_solution"
MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o")

# ── System prompt ─────────────────────────────────────────────────────────────
ANALYSIS_SYSTEM = """You are a senior HVAC systems engineer and Python software architect.
You will receive a structured digest of an HVAC engineering tutorial video.
The digest contains: frame-by-frame technical details, key concepts, screen text, and
translated transcripts extracted by Gemini Vision from 449 video frames.

Your task is to produce THREE outputs in a single response, each clearly delimited:

--- SECTION 1: WORKFLOW ANALYSIS ---
Identify the complete HVAC design workflow taught in this video.
For each major step, describe: what is done, what tools/data are used, what the output is.
Be specific about formulas, column names, Chinese field names seen on screen, and
any T20-HVAC AutoCAD plugin operations observed.

--- SECTION 2: AUTOMATION TOOL DESIGN ---
Design a Python automation tool that replicates this workflow computationally.
Include:
- Module breakdown with function signatures
- Data models (dataclasses or TypedDict) for Room, Zone, TerminalSpec
- Core calculation formulas with actual values observed in the video
- Terminal selection rules (H1/H2/H3) with airflow thresholds
- Air balance equations
- Validation rules
- Input/output specification (Excel in → Excel + report out)

--- SECTION 3: PYTHON IMPLEMENTATION ---
Write the complete, runnable Python implementation.
Requirements:
- Uses pandas for Excel I/O (openpyxl engine)
- Implements all calculation modules from Section 2
- Reads an input Excel with columns matching those seen in the video
- Computes all derived fields
- Validates air balance and flags errors
- Exports a completed schedule Excel + a summary report
- Include a sample __main__ block with realistic demo data matching the video
- Well-commented in English
"""

ANALYSIS_USER_TMPL = """Here is a structured digest of the HVAC tutorial video analysis.

VIDEO: {video_name}
TOTAL FRAMES ANALYZED: {total_frames}
VIDEO DURATION: ~{duration:.0f} seconds

## Most Frequent Key Concepts (across all frames)
{top_concepts}

## Most Frequent Screen Text / Field Names
{top_text}

## Timeline Digest (one entry per ~30s segment)
{timeline}

## Unique Technical Details Observed
{tech_details}

Now produce the three sections as instructed.
"""


# ── Data compaction ───────────────────────────────────────────────────────────
def compact_results(data: dict) -> dict:
    frames = data["frames"]

    # Aggregate key concepts + text_on_screen counts
    concept_counter = Counter()
    text_counter = Counter()
    tech_details_set = []
    timeline = []

    last_timeline_ts = -999
    for f in frames:
        p = f.get("parsed", {})
        for c in p.get("key_concepts", []):
            concept_counter[c.strip()] += 1
        for t in p.get("text_on_screen", []):
            t = t.strip()
            if len(t) > 1 and not t.isdigit():
                text_counter[t] += 1
        td = p.get("technical_details", "").strip()
        if td and len(td) > 30 and td not in tech_details_set:
            tech_details_set.append(td)

        # Timeline entry every ~30s
        ts = f.get("timestamp", 0)
        if ts - last_timeline_ts >= 30:
            timeline.append({
                "t": round(ts),
                "transcript": p.get("transcript_translation", "")[:120],
                "description": p.get("description", "")[:150],
                "key_concepts": p.get("key_concepts", [])[:4],
                "scene_type": p.get("scene_type", ""),
            })
            last_timeline_ts = ts

    # Limit tech_details to most informative 40
    tech_details_set = tech_details_set[:40]

    return {
        "video_name": data.get("video_name", ""),
        "total_frames": data.get("total_frames", len(frames)),
        "duration": frames[-1].get("timestamp", 0) if frames else 0,
        "top_concepts": concept_counter.most_common(30),
        "top_text": [(t, n) for t, n in text_counter.most_common(60) if n >= 3],
        "timeline": timeline,
        "tech_details": tech_details_set,
    }


def format_digest_for_prompt(digest: dict) -> str:
    top_concepts = "\n".join(f"  - {c} ({n}x)" for c, n in digest["top_concepts"])
    top_text = "\n".join(f"  - {t} ({n}x)" for t, n in digest["top_text"])
    tech_details = "\n\n".join(f"[{i+1}] {td}" for i, td in enumerate(digest["tech_details"]))
    timeline_lines = []
    for e in digest["timeline"]:
        timeline_lines.append(
            f"  [{e['t']}s | {e['scene_type']}] {e['description']}\n"
            f"    Transcript: {e['transcript']}\n"
            f"    Concepts: {', '.join(e['key_concepts'])}"
        )
    timeline = "\n".join(timeline_lines)

    return ANALYSIS_USER_TMPL.format(
        video_name=digest["video_name"],
        total_frames=digest["total_frames"],
        duration=digest["duration"],
        top_concepts=top_concepts,
        top_text=top_text,
        timeline=timeline,
        tech_details=tech_details,
    )


# ── Report splitter ───────────────────────────────────────────────────────────
def split_sections(response_text: str) -> dict:
    """Split LLM response into the three sections."""
    sections = {"workflow": "", "tool_design": "", "python_code": ""}

    # Support both --- SECTION N: ... --- and ### SECTION N: ... formats
    markers = []
    for key_marker, key in [
        ("SECTION 1: WORKFLOW ANALYSIS", "workflow"),
        ("SECTION 2: AUTOMATION TOOL DESIGN", "tool_design"),
        ("SECTION 3: PYTHON IMPLEMENTATION", "python_code"),
    ]:
        for prefix in ("--- ", "### ", "## ", "# "):
            if (prefix + key_marker) in text:
                markers.append((prefix + key_marker, key))
                break
        else:
            # fallback: search substring
            markers.append((key_marker, key))

    text = response_text
    for i, (marker, key) in enumerate(markers):
        start = text.find(marker)
        if start == -1:
            continue
        start += len(marker)
        # Find end = start of next section or end of string
        end = len(text)
        if i + 1 < len(markers):
            next_marker = markers[i + 1][0]
            next_start = text.find(next_marker)
            if next_start != -1:
                end = next_start
        sections[key] = text[start:end].strip()

    return sections


def build_report(sections: dict, digest: dict, usage: dict) -> str:
    lines = [
        f"# HVAC Automation Tool — Design Report",
        f"**Video:** {digest['video_name']}  ",
        f"**Frames analyzed:** {digest['total_frames']}  ",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  ",
        f"**Tokens used:** {usage.get('total_tokens', '?')}",
        "",
        "---",
        "## Part 1: Workflow Analysis",
        "",
        sections.get("workflow", "—"),
        "",
        "---",
        "## Part 2: Automation Tool Design",
        "",
        sections.get("tool_design", "—"),
        "",
        "---",
        "## Part 3: Python Implementation",
        "",
        "```python",
        sections.get("python_code", "# No code generated"),
        "```",
        "",
    ]
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set in .env"); sys.exit(1)
    if not IN_FILE.exists():
        print(f"ERROR: {IN_FILE} not found. Run test_gemini_all_frames.py first."); sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=api_key, timeout=120.0)

    print(f"Loading {IN_FILE.name} ...")
    with open(IN_FILE, encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {data.get('total_frames', '?')} frames loaded")

    print("Compacting data ...")
    digest = compact_results(data)
    digest_path = OUT_DIR / "digest.json"
    with open(digest_path, "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)
    print(f"  Saved digest -> {digest_path.name}")

    user_msg = format_digest_for_prompt(digest)
    print(f"  Prompt size: ~{len(user_msg):,} chars")

    print(f"\nCalling {MODEL} for secondary analysis ...")
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=8000,
        messages=[
            {"role": "system", "content": ANALYSIS_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "text"},
    )
    raw = response.choices[0].message.content
    usage = response.usage.model_dump() if response.usage else {}
    print(f"  Done. Tokens: {usage.get('total_tokens', '?')}")

    # Save raw LLM response
    raw_path = OUT_DIR / "raw_response.txt"
    raw_path.write_text(raw, encoding="utf-8")

    # Split and build report
    sections = split_sections(raw)

    report_path = OUT_DIR / "tool_report.md"
    report_path.write_text(build_report(sections, digest, usage), encoding="utf-8")
    print(f"  Saved report -> {report_path.name}")

    # Extract and save the Python code as a standalone file
    code = sections.get("python_code", "")
    if code:
        # Strip markdown code fences if LLM wrapped in ```python ... ```
        if code.startswith("```"):
            code = "\n".join(code.split("\n")[1:])
        if code.endswith("```"):
            code = "\n".join(code.split("\n")[:-1])
        code_path = OUT_DIR / "hvac_calculator.py"
        code_path.write_text(code.strip(), encoding="utf-8")
        print(f"  Saved calculator -> {code_path.name}")

    print(f"\nDone. Results in: {OUT_DIR}")
    print(f"  {report_path.name}")
    print(f"  {(OUT_DIR / 'hvac_calculator.py').name if code else '(no code generated)'}")


if __name__ == "__main__":
    main()
