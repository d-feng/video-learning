# Video Learning

Analyze instructional/tutorial videos: extract frames + audio, transcribe speech, analyze content with GPT-4o Vision, and search by natural language.

## Features
- Upload video file, YouTube URL, or local file path
- Audio transcription via OpenAI Whisper with timestamp alignment to frames
- GPT-4o Vision analysis of each frame + transcript segment
- Hybrid natural-language search (semantic + keyword) over all segments
- Auto-generated step-by-step instruction guide with frame images
- Fully local storage (ChromaDB + TinyDB — no cloud database needed)

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- ffmpeg installed and on PATH
- OpenAI API key

### Backend

```bash
cd backend
cp ../.env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm start
# Opens at http://localhost:3000
```

### Docker (both services)

```bash
cp .env.example .env
# Set OPENAI_API_KEY in .env
docker-compose up --build
```

## Usage

1. **Upload** — drop a video file, paste a YouTube URL, or enter a local file path
2. **Wait** — progress bar shows: audio extraction → transcription → frame analysis → instruction extraction → indexing
3. **Search** — type any natural language query, e.g. *"how to tighten the bolt"*
4. **Instructions** — click "Step-by-Step Instructions" panel to see the auto-extracted guide with images

## Configuration (.env)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | required | Your OpenAI key |
| `OPENAI_MODEL` | `gpt-4o` | Vision model |
| `EMBED_MODEL` | `text-embedding-3-small` | Embedding model |
| `FRAME_INTERVAL` | `2.0` | Seconds between frames |
| `TRANSCRIPT_WINDOW_SEC` | `4.0` | Transcript context per frame |
| `STORAGE_DIR` | `./storage` | Local storage directory |

## Architecture

```
backend/
  main.py              FastAPI routes + WebSocket progress
  video_processor.py   OpenCV frame extraction + thumbnails
  audio_processor.py   ffmpeg audio + OpenAI Whisper transcription
  ai_service.py        GPT-4o Vision analysis + instruction extraction
  search_service.py    Hybrid search (ChromaDB vector + TinyDB text)
  db_service.py        ChromaDB + TinyDB storage layer
  models/schemas.py    Pydantic data models

frontend/src/
  App.jsx              Main layout
  components/
    VideoUploader.jsx  File/URL/path upload with progress
    VideoLibrary.jsx   List of processed videos
    SearchBar.jsx      Query input with mode selector
    SearchResults.jsx  Result grid with thumbnails
    InstructionViewer.jsx  Step-by-step guide with images
```
