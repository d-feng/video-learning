"""
video_processor.py
Extracts frames from a video file at a configurable interval.
Each frame is saved as full-res JPEG + a 320×240 thumbnail.
"""
import os
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import cv2
from PIL import Image

from models.schemas import FrameData

FRAME_INTERVAL_SEC = float(os.getenv("FRAME_INTERVAL", "2.0"))
THUMBNAIL_SIZE = (320, 240)
JPEG_QUALITY = 85

_executor = ThreadPoolExecutor(max_workers=4)


class VideoProcessor:
    def __init__(self, frames_dir: Path, thumbnails_dir: Path):
        self.frames_dir = frames_dir
        self.thumbnails_dir = thumbnails_dir

    # ── public ────────────────────────────────────────────────────────────────

    async def extract_frames(self, video_path: str) -> list[FrameData]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self._extract_sync, video_path)

    # ── private ───────────────────────────────────────────────────────────────

    def _extract_sync(self, video_path: str) -> list[FrameData]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        interval = max(1, int(fps * FRAME_INTERVAL_SEC))

        video_stem = Path(video_path).stem
        frames_out: list[FrameData] = []

        frame_num = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_num % interval == 0:
                timestamp = frame_num / fps

                # Full-res JPEG
                fname = f"{video_stem}_f{frame_num:06d}.jpg"
                full_path = self.frames_dir / fname
                cv2.imwrite(str(full_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])

                # Thumbnail
                thumb_name = f"{video_stem}_t{frame_num:06d}.jpg"
                thumb_path = self.thumbnails_dir / thumb_name
                self._make_thumbnail(frame, str(thumb_path))

                frames_out.append(FrameData(
                    frame_number=frame_num,
                    timestamp=round(timestamp, 3),
                    file_path=f"/video-files/{self.frames_dir.parent.name}/frames/{fname}",
                    thumbnail_path=f"/video-files/{self.frames_dir.parent.name}/thumbnails/{thumb_name}",
                ))

            frame_num += 1

        cap.release()
        return frames_out

    @staticmethod
    def _make_thumbnail(bgr_frame, out_path: str):
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img = img.resize(THUMBNAIL_SIZE, Image.LANCZOS)
        img.save(out_path, "JPEG", quality=JPEG_QUALITY)
