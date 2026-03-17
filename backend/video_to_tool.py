"""
video_to_tool.py
================
General pipeline: any analyzed video → domain tool + Excel output.

Given a video's all_results.json (from test_gemini_all_frames.py or the main
pipeline), this script:
  1. Compacts 449+ frames into a structured digest
  2. Asks GPT-4o to identify the domain and design a calculator
  3. Generates a runnable Python tool + blank Excel template for that domain

Works for ANY instructional video:
  - HVAC design          → room airflow schedule + terminal selection
  - Electrical wiring    → circuit schedule + cable sizing
  - Cooking / recipe     → ingredient list + scaling calculator
  - Carpentry / woodwork → cut list + material estimator
  - Lab protocol         → reagent table + step tracker
  - Software tutorial    → config template + checklist
  - Manufacturing / QC   → inspection checklist + defect log
  - ... anything else    → GPT figures it out

Usage:
    cd backend
    python video_to_tool.py <path_to_all_results.json> [output_dir]

    # Example — the HVAC video:
    python video_to_tool.py ../test/gemini_all_frames/all_results.json ../test/auto_tool
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


# ── Prompts ───────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a senior software engineer and domain expert.
You will receive a structured digest of an instructional video analyzed frame-by-frame.

Your job:
1. Identify the domain and core workflow being taught.
2. Design a general-purpose Python tool that automates or assists with that workflow.
3. Write the complete, runnable Python implementation.

Output exactly three sections separated by these headers (include the headers verbatim):

### SECTION 1: DOMAIN & WORKFLOW
Identify:
- Domain (e.g. HVAC design, electrical wiring, cooking, woodworking, lab protocol, ...)
- Core workflow steps with inputs, outputs, and any formulas/rules observed
- Key data fields seen in the video (column names, measurements, units)
- What problem does the video teach how to solve?

### SECTION 2: TOOL DESIGN
Design a Python tool that solves the same problem computationally:
- Tool name and purpose (1 sentence)
- Data model: what are the main entities (rows in a table)? What fields does each have?
- Calculation logic: what formulas, selection rules, or decision logic apply?
- Validation rules: what makes a row invalid or incomplete?
- Excel input schema: list every column the input template should have
- Excel output schema: what computed columns does the tool add?

### SECTION 3: PYTHON IMPLEMENTATION
Write complete, runnable Python code:
- Uses only stdlib + pandas + openpyxl (no other dependencies)
- Reads an input Excel with the schema from Section 2
- Computes all derived fields
- Validates and flags errors
- Exports a completed Excel schedule (input columns + computed columns + validation)
- Exports a plain-text summary report
- Includes a `demo_data()` function with 6-10 realistic rows matching the video
- Includes a `run(input_excel=None, output_excel=None)` function as main entry point
- Has `if __name__ == "__main__":` block accepting optional CLI args
- Well-commented; uses English column names
Do NOT use any domain-specific library. Keep it self-contained."""

USER_TEMPLATE = """Video digest below. Identify the domain and generate the tool.

VIDEO FILE: {video_name}
TOTAL FRAMES: {total_frames}
DURATION: ~{duration:.0f} seconds

## Top Key Concepts (most frequent across all frames)
{top_concepts}

## Most Frequent On-Screen Text / Field Names
{top_text}

## Timeline (one entry per ~45 seconds)
{timeline}

## Unique Technical Details
{tech_details}
"""


# ── Digest builder ─────────────────────────────────────────────────────────────
def build_digest(data: dict) -> dict:
    frames = data["frames"]
    concept_counter = Counter()
    text_counter    = Counter()
    tech_details    = []
    timeline        = []
    last_ts         = -999

    for f in frames:
        p = f.get("parsed", {})
        for c in p.get("key_concepts", []):
            concept_counter[c.strip()] += 1
        for t in p.get("text_on_screen", []):
            t = t.strip()
            if len(t) > 1 and not t.isdigit():
                text_counter[t] += 1
        td = p.get("technical_details", "").strip()
        if td and len(td) > 30 and td not in tech_details:
            tech_details.append(td)

        ts = f.get("timestamp", 0)
        if ts - last_ts >= 45:
            timeline.append({
                "t":           round(ts),
                "scene":       p.get("scene_type", ""),
                "description": p.get("description", "")[:160],
                "translation": p.get("transcript_translation", "")[:120],
                "concepts":    p.get("key_concepts", [])[:4],
            })
            last_ts = ts

    return {
        "video_name":    data.get("video_name", "unknown"),
        "total_frames":  data.get("total_frames", len(frames)),
        "duration":      frames[-1].get("timestamp", 0) if frames else 0,
        "top_concepts":  concept_counter.most_common(25),
        "top_text":      [(t, n) for t, n in text_counter.most_common(50) if n >= 3],
        "timeline":      timeline,
        "tech_details":  tech_details[:35],
    }


def format_digest(d: dict) -> str:
    concepts  = "\n".join(f"  {c} ({n}x)" for c, n in d["top_concepts"])
    texts     = "\n".join(f"  {t} ({n}x)" for t, n in d["top_text"])
    tl_lines  = []
    for e in d["timeline"]:
        tl_lines.append(
            f"  [{e['t']}s | {e['scene']}] {e['description']}\n"
            f"    Said: {e['translation']}\n"
            f"    Concepts: {', '.join(e['concepts'])}"
        )
    tech = "\n\n".join(f"[{i+1}] {t}" for i, t in enumerate(d["tech_details"]))
    return USER_TEMPLATE.format(
        video_name=d["video_name"],
        total_frames=d["total_frames"],
        duration=d["duration"],
        top_concepts=concepts,
        top_text=texts,
        timeline="\n".join(tl_lines),
        tech_details=tech,
    )


# ── Section parser ─────────────────────────────────────────────────────────────
def parse_sections(text: str) -> dict:
    headers = [
        ("### SECTION 1: DOMAIN & WORKFLOW", "domain"),
        ("### SECTION 2: TOOL DESIGN",       "design"),
        ("### SECTION 3: PYTHON IMPLEMENTATION", "code"),
    ]
    sections = {}
    for i, (hdr, key) in enumerate(headers):
        start = text.find(hdr)
        if start == -1:
            sections[key] = ""
            continue
        start += len(hdr)
        end = len(text)
        if i + 1 < len(headers):
            nxt = text.find(headers[i + 1][0])
            if nxt != -1:
                end = nxt
        sections[key] = text[start:end].strip()
    return sections


def extract_code(raw_code: str) -> str:
    """Extract Python code from a block that may contain markdown fences and trailing prose."""
    text = raw_code.strip()
    # Find the first ```python or ``` opening fence
    start = text.find("```python")
    if start == -1:
        start = text.find("```")
    if start != -1:
        # Move past the opening fence line
        start = text.index("\n", start) + 1
        # Find the matching closing fence
        end = text.find("\n```", start)
        if end != -1:
            return text[start:end].strip()
        # No closing fence found — take everything after the opening
        return text[start:].strip()
    # No fences at all — return as-is
    return text


# ── Report writer ──────────────────────────────────────────────────────────────
def write_report(out_dir: Path, sections: dict, digest: dict, usage: dict, raw: str):
    # Full markdown report
    report = "\n\n---\n\n".join([
        f"# Video-to-Tool Report\n"
        f"**Video:** {digest['video_name']}  \n"
        f"**Frames:** {digest['total_frames']}  \n"
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
        f"**Tokens used:** {usage.get('total_tokens', '?')}",

        f"## Domain & Workflow\n\n{sections.get('domain', '—')}",
        f"## Tool Design\n\n{sections.get('design', '—')}",
        f"## Python Implementation\n\n```python\n{sections.get('code', '')}\n```",
    ])
    (out_dir / "tool_report.md").write_text(report, encoding="utf-8")

    # Standalone calculator
    code = extract_code(sections.get("code", ""))
    if code:
        (out_dir / "calculator.py").write_text(code, encoding="utf-8")

    # Raw LLM response
    (out_dir / "raw_response.txt").write_text(raw, encoding="utf-8")

    return code


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set"); sys.exit(1)

    # Args
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        # Default to the HVAC all_results.json if it exists
        default = Path(__file__).parent.parent / "test" / "gemini_all_frames" / "all_results.json"
        if default.exists():
            in_path = default
        else:
            print("Usage: python video_to_tool.py <all_results.json> [output_dir]")
            sys.exit(1)
    else:
        in_path = Path(args[0])

    out_dir = Path(args[1]) if len(args) > 1 else in_path.parent.parent / "auto_tool" / in_path.parent.name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Input : {in_path}")
    print(f"Output: {out_dir}")

    # Load & compact
    print("Loading results ...")
    with open(in_path, encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {data.get('total_frames', '?')} frames")

    digest = build_digest(data)
    with open(out_dir / "digest.json", "w", encoding="utf-8") as f:
        json.dump(digest, f, ensure_ascii=False, indent=2)

    user_msg = format_digest(digest)
    print(f"  Prompt: ~{len(user_msg):,} chars")

    # Call GPT
    model = os.getenv("OPENAI_MODEL", "gpt-4o")
    print(f"\nCalling {model} ...")
    client = OpenAI(api_key=api_key, timeout=120.0)
    response = client.chat.completions.create(
        model=model,
        max_tokens=8000,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
    )
    raw    = response.choices[0].message.content
    usage  = response.usage.model_dump() if response.usage else {}
    print(f"  Tokens: {usage.get('total_tokens', '?')}")

    # Parse & save
    sections = parse_sections(raw)
    code = write_report(out_dir, sections, digest, usage, raw)

    # Domain summary
    domain_line = (sections.get("domain") or "").splitlines()
    domain_summary = next((l for l in domain_line if l.strip()), "")
    print(f"\nDomain detected: {domain_summary[:120]}")

    print(f"\nSaved to {out_dir}/")
    print(f"  tool_report.md")
    if code:
        print(f"  calculator.py   ({len(code.splitlines())} lines)")

    # Quick-run the generated calculator to verify it works
    if code and (out_dir / "calculator.py").exists():
        print("\nVerifying calculator runs with demo data ...")
        import subprocess, sys as _sys
        result = subprocess.run(
            [_sys.executable, str(out_dir / "calculator.py")],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print("  OK — calculator runs cleanly")
            # Show first 10 lines of output
            for line in result.stdout.splitlines()[:10]:
                print(f"    {line}")
        else:
            print(f"  WARNING — calculator exited with error:")
            for line in (result.stderr or result.stdout).splitlines()[:8]:
                print(f"    {line}")


if __name__ == "__main__":
    main()
