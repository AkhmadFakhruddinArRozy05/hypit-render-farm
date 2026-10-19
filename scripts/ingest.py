#!/usr/bin/env python3
"""
Universal Asset Ingestion Script for Hypit Render Farm
Supports:
- Social Video URLs (TikTok, YouTube Shorts, IG Reels) via yt-dlp with platform-aware selectors:
  * TikTok: best[format_id!^=music]/download_addr-0/play_addr-0/best (rejects isolated music stream)
  * Instagram: best
  * YouTube: bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]
  * TikTok Dual-Audio: container video (vocal) + official_music.mp3
- Audio integrity verification via ffprobe (stream check) and ffmpeg volumedetect (mean_volume > -60 dB)
- Local Assets uploaded via temporary GitHub Release via gh CLI
- Direct Cloud URLs via curl / gdown
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Tuple, Union

FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
MIN_MEAN_VOLUME_DB = -60.0


def run_cmd(cmd, check=True, cwd=None):
    print(f"[RUN] {' '.join(str(c) for c in cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0 and check:
        print(f"[ERROR] Command failed with code {res.returncode}")
        print(f"[STDOUT] {res.stdout}")
        print(f"[STDERR] {res.stderr}")
        sys.exit(res.returncode)
    return res


def get_format_selector_for_url(url: str) -> str:
    """Returns platform-aware yt-dlp format selector."""
    url_lower = url.lower()
    if "tiktok.com" in url_lower:
        return "best[format_id!^=music]/download_addr-0/play_addr-0/best"
    elif "instagram.com" in url_lower or "instagr.am" in url_lower:
        return "best"
    elif "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]"
    return "best[format_id!^=music]/bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best"


def verify_audio_integrity(filepath: Union[str, Path]) -> Tuple[bool, Dict[str, Any]]:
    """
    Verifies that the media file at filepath has a valid, non-silent audio stream:
    1. ffprobe checks for audio stream codec and channels >= 1.
    2. ffmpeg volumedetect verifies mean_volume > -60.0 dB.
    """
    path = Path(filepath)
    if not path.is_file() or path.stat().st_size == 0:
        return False, {"error": "FILE_NOT_FOUND_OR_EMPTY", "path": str(path)}

    cmd_probe = [
        FFPROBE,
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_name,channels,sample_rate",
        "-of", "json",
        str(path),
    ]
    try:
        res_probe = subprocess.run(cmd_probe, capture_output=True, text=True, check=True, timeout=30)
        probe_data = json.loads(res_probe.stdout)
        streams = probe_data.get("streams", [])
        if not streams:
            return False, {"error": "NO_AUDIO_STREAM", "path": str(path)}
        audio_stream = streams[0]
        codec = audio_stream.get("codec_name")
        channels = int(audio_stream.get("channels") or 0)
        if not codec or channels < 1:
            return False, {"error": "INVALID_AUDIO_STREAM", "codec": codec, "channels": channels, "path": str(path)}
    except Exception as exc:
        return False, {"error": f"FFPROBE_FAILED: {exc}", "path": str(path)}

    cmd_vol = [
        FFMPEG,
        "-i", str(path),
        "-af", "volumedetect",
        "-vn", "-sn", "-dn",
        "-f", "null",
        "/dev/null",
    ]
    try:
        res_vol = subprocess.run(cmd_vol, capture_output=True, text=True, timeout=60)
        mean_match = re.search(r"mean_volume:\s*(-?[\d\.]+|-?inf)\s*dB", res_vol.stderr)
        max_match = re.search(r"max_volume:\s*(-?[\d\.]+|-?inf)\s*dB", res_vol.stderr)
        if not mean_match:
            return False, {"error": "VOLUMEDETECT_NO_OUTPUT", "path": str(path)}

        mean_str = mean_match.group(1).lower()
        if mean_str in {"-inf", "inf"}:
            return False, {
                "error": "SILENT_AUDIO_STREAM",
                "mean_volume_db": -999.0,
                "is_audible": False,
                "path": str(path),
            }

        mean_vol = float(mean_str)
        max_vol = float(max_match.group(1)) if max_match and max_match.group(1) not in {"-inf", "inf"} else -999.0

        is_audible = mean_vol > MIN_MEAN_VOLUME_DB
        result_info = {
            "codec": codec,
            "channels": channels,
            "mean_volume_db": mean_vol,
            "max_volume_db": max_vol,
            "is_audible": is_audible,
            "threshold_db": MIN_MEAN_VOLUME_DB,
            "path": str(path),
        }
        if not is_audible:
            result_info["error"] = f"AUDIO_TOO_QUIET_OR_MUTED: mean_volume {mean_vol} dB <= {MIN_MEAN_VOLUME_DB} dB"
            return False, result_info

        return True, result_info
    except Exception as exc:
        return False, {"error": f"VOLUMEDETECT_FAILED: {exc}", "path": str(path)}


def main():
    parser = argparse.ArgumentParser(description="Universal Asset Ingestion for Hypit Render Farm")
    parser.add_argument("--project", required=True, help="Project directory name inside projects/")
    parser.add_argument("--release-tag", default="", help="GitHub Release tag for temporary local assets")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    project_dir = repo_root / "projects" / args.project
    manifest_file = project_dir / "source_manifest.json"

    if not manifest_file.exists():
        print(f"[WARN] No source_manifest.json found at {manifest_file}. Skipping ingestion.")
        return

    with open(manifest_file, "r") as f:
        manifest = json.load(f)

    sources = manifest.get("sources", [])
    print(f"=== Starting Ingestion for Project: {args.project} ({len(sources)} sources) ===")

    for src in sources:
        src_id = src.get("id", "unnamed")
        src_type = src.get("type")
        dest_rel = src.get("destination")
        dest_path = project_dir / dest_rel
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"\n--> Ingesting '{src_id}' (type: {src_type}) -> {dest_rel}")

        if src_type == "social_url":
            url = src.get("url", "")
            print(f"    Downloading from social URL: {url}")
            format_sel = get_format_selector_for_url(url)
            print(f"    Platform format selector: {format_sel}")

            # 1. Download primary video container with creator vocal audio
            cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "-f", format_sel,
                "--merge-output-format", "mp4",
                "-o", str(dest_path),
                url,
            ]
            run_cmd(cmd)

            # 2. Dual-Audio for TikTok: download official commercial music if available
            if "tiktok.com" in url.lower():
                raw_dir = dest_path.parent
                music_dest = raw_dir / "official_music.mp3"
                print(f"    🎵 Attempting Dual-Audio: official music track -> {music_dest.name}")
                cmd_music = [
                    "yt-dlp",
                    "--no-check-certificates",
                    "-f", "music",
                    "-x",
                    "--audio-format", "mp3",
                    "-o", str(music_dest),
                    url,
                ]
                res_music = run_cmd(cmd_music, check=False)
                if res_music.returncode == 0 and music_dest.exists() and music_dest.stat().st_size > 0:
                    sz_kb = round(music_dest.stat().st_size / 1024, 1)
                    print(f"    ✓ Dual-Audio official music extracted: {music_dest.name} ({sz_kb} KB)")
                else:
                    print("    ℹ️ No separate 'music' format track found; creator container audio will serve as master.")

        elif src_type == "local_asset":
            file_name = src.get("file_name")
            if args.release_tag:
                print(f"    Downloading '{file_name}' from temporary GitHub Release '{args.release_tag}'...")
                cmd = [
                    "gh", "release", "download", args.release_tag,
                    "-p", file_name,
                    "-D", str(dest_path.parent),
                ]
                run_cmd(cmd)
            else:
                if dest_path.exists() and dest_path.stat().st_size > 0:
                    print(f"    [OK] Local asset already present at {dest_path}")
                else:
                    print(f"    [ERROR] Missing local asset '{file_name}' and no --release-tag provided!")
                    sys.exit(1)

        elif src_type == "cloud_url":
            url = src.get("url")
            print(f"    Downloading from Cloud URL: {url}")
            cmd = ["curl", "-L", "-o", str(dest_path), url]
            run_cmd(cmd)

        else:
            print(f"    [WARN] Unknown source type '{src_type}', skipping.")

        # Verification of destination file
        if dest_path.exists() and dest_path.stat().st_size > 0:
            sz_mb = round(dest_path.stat().st_size / (1024 * 1024), 2)
            print(f"    ✓ Successfully resolved: {dest_path.name} ({sz_mb} MB)")
            # Audio integrity verification on video/audio files
            if dest_path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav"}:
                valid_audio, audio_info = verify_audio_integrity(dest_path)
                if not valid_audio:
                    print(f"    ❌ [AUDIO INTEGRITY ERROR] {audio_info.get('error')}")
                    print(f"       Details: {audio_info}")
                    sys.exit(1)
                else:
                    print(f"    ✓ Audio integrity verified: codec={audio_info.get('codec')}, channels={audio_info.get('channels')}, mean_volume={audio_info.get('mean_volume_db')} dB")
        else:
            print(f"    ✗ Failed to resolve destination file: {dest_path}")
            sys.exit(1)

    print(f"\n=== Universal Ingestion Completed Successfully for {args.project} ===")


if __name__ == "__main__":
    main()
