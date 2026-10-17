#!/usr/bin/env python3
"""
Universal Asset Ingestion Script for Hypit Render Farm
Supports:
- Social Video URLs (TikTok, YouTube Shorts, IG Reels) via yt-dlp
- Local Assets uploaded via temporary GitHub Release via gh CLI
- Direct Cloud URLs via curl / gdown
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

def run_cmd(cmd, check=True):
    print(f"[RUN] {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 and check:
        print(f"[ERROR] Command failed with code {res.returncode}")
        print(f"[STDOUT] {res.stdout}")
        print(f"[STDERR] {res.stderr}")
        sys.exit(res.returncode)
    return res

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
            url = src.get("url")
            print(f"    Downloading from social URL: {url}")
            # Use yt-dlp with merged format if available
            cmd = [
                "yt-dlp",
                "--no-check-certificates",
                "-f", "bv*+ba/b",
                "--merge-output-format", "mp4",
                "-o", str(dest_path),
                url
            ]
            run_cmd(cmd)

        elif src_type == "local_asset":
            file_name = src.get("file_name")
            if args.release_tag:
                print(f"    Downloading '{file_name}' from temporary GitHub Release '{args.release_tag}'...")
                cmd = [
                    "gh", "release", "download", args.release_tag,
                    "-p", file_name,
                    "-D", str(dest_path.parent)
                ]
                run_cmd(cmd)
            else:
                # Check if file already exists locally in dest_path
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

        # Verification
        if dest_path.exists() and dest_path.stat().st_size > 0:
            sz_mb = round(dest_path.stat().st_size / (1024 * 1024), 2)
            print(f"    ✓ Successfully resolved: {dest_path.name} ({sz_mb} MB)")
        else:
            print(f"    ✗ Failed to resolve destination file: {dest_path}")
            sys.exit(1)

    print(f"\n=== Universal Ingestion Completed Successfully for {args.project} ===")

if __name__ == "__main__":
    main()
