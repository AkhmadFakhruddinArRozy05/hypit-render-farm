import json
import shutil
import subprocess
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from ingest import get_format_selector_for_url, verify_audio_integrity


def test_format_selectors():
    # TikTok selector rejects music stream
    tt_sel = get_format_selector_for_url("https://www.tiktok.com/@creator/video/123456789")
    assert tt_sel == "best[format_id!^=music]/download_addr-0/play_addr-0/best"

    # Instagram selector
    ig_sel = get_format_selector_for_url("https://www.instagram.com/reel/C8xyz123/")
    assert ig_sel == "best"

    # YouTube selector
    yt_sel = get_format_selector_for_url("https://www.youtube.com/watch?v=abcdef12345")
    assert yt_sel == "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]"


def test_verify_audio_integrity_synthetic(tmp_path):
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    if not Path(ffmpeg).exists():
        pytest.skip("ffmpeg binary not found")

    # Audible audio
    audible = tmp_path / "audible.mp4"
    cmd_audible = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.5",
        "-f", "lavfi", "-i", "color=c=red:s=160x120:d=0.5",
        "-c:v", "libx264", "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(audible)
    ]
    subprocess.run(cmd_audible, capture_output=True, check=True)
    ok, meta = verify_audio_integrity(audible)
    assert ok is True
    assert meta["mean_volume_db"] > -60.0

    # Silent audio
    silent = tmp_path / "silent.mp4"
    cmd_silent = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.5",
        "-t", "0.5",
        "-c:v", "libx264", "-c:a", "aac",
        "-pix_fmt", "yuv420p",
        str(silent)
    ]
    subprocess.run(cmd_silent, capture_output=True, check=True)
    ok_silent, meta_silent = verify_audio_integrity(silent)
    assert ok_silent is False
