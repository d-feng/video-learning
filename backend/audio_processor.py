"""
audio_processor.py
1. Extracts audio track from video using ffmpeg (subprocess).
2. Transcribes with OpenAI Whisper API → timestamped segments.
3. Aligns transcript segments to extracted frames by timestamp.
"""
import os
import asyncio
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from openai import AsyncOpenAI

from models.schemas import FrameData, TranscriptSegment, AlignedSegment

_executor = ThreadPoolExecutor(max_workers=2)
_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# How many seconds of transcript to attach around a frame timestamp
TRANSCRIPT_WINDOW_SEC = float(os.getenv("TRANSCRIPT_WINDOW_SEC", "4.0"))


class AudioProcessor:
    def __init__(self, audio_dir: Path):
        self.audio_dir = audio_dir

    # ── public ────────────────────────────────────────────────────────────────

    async def extract_audio(self, video_path: str) -> str:
        """Extract audio to 16 kHz mono WAV (optimal for Whisper)."""
        stem = Path(video_path).stem
        out_path = str(self.audio_dir / f"{stem}.wav")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            _executor,
            self._run_ffmpeg,
            video_path, out_path
        )
        return out_path

    async def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        """Call Whisper API with verbose_json to get word-level timestamps."""
        # Files > 25 MB must be chunked; for simplicity we compress to mp3 first
        mp3_path = audio_path.replace(".wav", ".mp3")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, self._compress_audio, audio_path, mp3_path)

        use_path = mp3_path if Path(mp3_path).exists() else audio_path

        with open(use_path, "rb") as f:
            response = await _client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )

        segments: list[TranscriptSegment] = []
        if hasattr(response, "segments") and response.segments:
            for s in response.segments:
                segments.append(TranscriptSegment(
                    start=s.start,
                    end=s.end,
                    text=s.text.strip(),
                ))
        else:
            # Fallback: no timestamps, treat whole transcript as one segment
            segments.append(TranscriptSegment(start=0.0, end=9999.0, text=response.text))

        return segments

    def align_transcript_to_frames(
        self,
        segments: list[TranscriptSegment],
        frames: list[FrameData],
    ) -> list[AlignedSegment]:
        """
        For each frame, collect all transcript segments whose time window
        overlaps [frame.timestamp - half_window, frame.timestamp + half_window].
        """
        half = TRANSCRIPT_WINDOW_SEC / 2.0
        aligned: list[AlignedSegment] = []

        for frame in frames:
            t = frame.timestamp
            window_start = max(0.0, t - half)
            window_end = t + half

            matching_text = " ".join(
                s.text for s in segments
                if s.start < window_end and s.end > window_start
            ).strip()

            aligned.append(AlignedSegment(
                frame=frame,
                transcript=matching_text or "[no speech]",
            ))

        return aligned

    # ── private ───────────────────────────────────────────────────────────────

    @staticmethod
    def _run_ffmpeg(video_path: str, out_path: str):
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",                  # no video
            "-acodec", "pcm_s16le", # WAV
            "-ar", "16000",         # 16 kHz
            "-ac", "1",             # mono
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # If no audio track, create silent WAV so pipeline doesn't break
            cmd_silent = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-t", "1",
                out_path,
            ]
            subprocess.run(cmd_silent, capture_output=True)

    @staticmethod
    def _compress_audio(wav_path: str, mp3_path: str):
        """Compress WAV → MP3 to stay under Whisper's 25 MB limit."""
        if not Path(wav_path).exists():
            return
        cmd = [
            "ffmpeg", "-y",
            "-i", wav_path,
            "-codec:a", "libmp3lame",
            "-qscale:a", "5",       # ~130 kbps, good for speech
            mp3_path,
        ]
        subprocess.run(cmd, capture_output=True)
