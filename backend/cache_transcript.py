"""
Run this on YOUR LAPTOP (home Wi-Fi isn't blocked by YouTube) to save a
video's transcript so the deployed app can always load it as a demo.

    cd backend
    pip install "youtube-transcript-api>=1.2,<2" requests
    python cache_transcript.py https://www.youtube.com/watch?v=VIDEO_ID

It writes backend/transcripts/<VIDEO_ID>.json. Commit and push that file.
"""
import json
import re
import sys
from pathlib import Path

import requests
from youtube_transcript_api import YouTubeTranscriptApi

LANGS = ["en", "en-IN", "en-US", "en-GB", "hi"]


def video_id_from(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url) or re.search(r"^([A-Za-z0-9_-]{11})$", url)
    if not m:
        sys.exit(f"Can't find a video ID in: {url}")
    return m.group(1)


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python cache_transcript.py <youtube-url> [<youtube-url> ...]")
    out_dir = Path(__file__).parent / "transcripts"
    out_dir.mkdir(exist_ok=True)
    for url in sys.argv[1:]:
        vid = video_id_from(url)
        segments = YouTubeTranscriptApi().fetch(vid, languages=LANGS).to_raw_data()
        try:
            title = requests.get(
                "https://www.youtube.com/oembed",
                params={"url": f"https://www.youtube.com/watch?v={vid}", "format": "json"},
                timeout=15,
            ).json()["title"]
        except Exception:
            title = vid
        (out_dir / f"{vid}.json").write_text(
            json.dumps({"title": title, "segments": segments}, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Saved {vid}: {title} ({len(segments)} segments)")


if __name__ == "__main__":
    main()
