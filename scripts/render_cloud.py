#!/usr/bin/env python3
"""
Antigravity Cloud Render Orchestrator for Hypit Render Farm
Triggers GitHub Actions 16 GB RAM cloud runner, coordinates temporary asset uploads,
watches execution, and downloads the rendered MP4 back to Mac.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
from pathlib import Path

def run_cmd(cmd, check=True, cwd=None):
    print(f"[RUN] {' '.join(cmd)}")
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if res.returncode != 0 and check:
        print(f"[ERROR] Command failed with code {res.returncode}")
        print(f"[STDOUT] {res.stdout.strip()}")
        print(f"[STDERR] {res.stderr.strip()}")
        sys.exit(res.returncode)
    return res

def main():
    parser = argparse.ArgumentParser(description="Trigger Cloud Render on GitHub Actions (16 GB RAM)")
    parser.add_argument("--project", required=True, help="Project name inside projects/ (e.g. nutriflakes-cr7-hypit)")
    parser.add_argument("--skip-push", action="store_true", help="Skip git add/commit/push before triggering")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    project_dir = repo_root / "projects" / args.project
    manifest_file = project_dir / "source_manifest.json"

    if not manifest_file.exists():
        print(f"[ERROR] Project manifest not found at: {manifest_file}")
        sys.exit(1)

    with open(manifest_file, "r") as f:
        manifest = json.load(f)

    print(f"\n=======================================================")
    print(f"🚀 INITIATING CLOUD RENDER: {args.project.upper()}")
    print(f"=======================================================")

    # 1. Check for local assets requiring temporary release upload
    local_files_to_upload = []
    sources = manifest.get("sources", [])
    for src in sources:
        if src.get("type") == "local_asset":
            local_path = src.get("local_path")
            file_name = src.get("file_name")
            p = Path(local_path) if local_path else (project_dir / src.get("destination"))
            if not p.exists():
                # Fallback to standard project location
                fallback = Path(f"/Users/ozy/Projects/{args.project}/assets/{file_name}")
                if fallback.exists():
                    p = fallback
            if p.exists() and p.stat().st_size > 0:
                local_files_to_upload.append(str(p))
            else:
                print(f"[ERROR] Required local asset not found: {file_name}")
                sys.exit(1)

    release_tag = ""
    if local_files_to_upload:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        release_tag = f"temp-assets-{args.project[:12]}-{ts}"
        print(f"\n📦 Found {len(local_files_to_upload)} local asset(s) to upload to temporary release: '{release_tag}'")
        for f in local_files_to_upload:
            sz_mb = round(os.path.getsize(f) / (1024 * 1024), 2)
            print(f"   • {os.path.basename(f)} ({sz_mb} MB)")

        create_rel_cmd = [
            "gh", "release", "create", release_tag,
            *local_files_to_upload,
            "--title", f"Temporary Render Assets - {args.project}",
            "--notes", f"Ephemeral media handoff for job {release_tag}. Auto-deleted upon completion."
        ]
        run_cmd(create_rel_cmd, cwd=str(repo_root))
        print("✓ Temporary asset release created successfully.")

    # 2. Sync Git changes
    if not args.skip_push:
        print("\n🔄 Syncing project timeline, scripts, and workflow to GitHub...")
        run_cmd(["git", "add", "-A"], cwd=str(repo_root))
        # Check if there are changes to commit
        status = run_cmd(["git", "status", "--porcelain"], cwd=str(repo_root)).stdout.strip()
        if status:
            run_cmd(["git", "commit", "-m", f"chore: sync timeline and workflow for {args.project}"], cwd=str(repo_root))
            run_cmd(["git", "push", "origin", "main"], cwd=str(repo_root))
            print("✓ Pushed latest timeline and workflow to GitHub origin/main.")
        else:
            print("✓ Working tree clean, no pending changes.")

    # 3. Trigger GitHub Actions Workflow
    print(f"\n⚡ Dispatching GitHub Actions Workflow on 16 GB RAM Runner...")
    dispatch_cmd = ["gh", "workflow", "run", "render.yml", "-f", f"project={args.project}"]
    if release_tag:
        dispatch_cmd.extend(["-f", f"release_tag={release_tag}"])
    run_cmd(dispatch_cmd, cwd=str(repo_root))

    # Wait 5 seconds for GitHub to create the workflow run
    time.sleep(5)
    runs_json = run_cmd(["gh", "run", "list", "--workflow=render.yml", "--limit", "1", "--json", "databaseId,status,url"], cwd=str(repo_root)).stdout
    runs_data = json.loads(runs_json)
    if not runs_data:
        print("[ERROR] Could not find triggered workflow run!")
        sys.exit(1)

    run_id = str(runs_data[0]["databaseId"])
    run_url = runs_data[0]["url"]
    print(f"✓ Workflow Run Started: ID {run_id}")
    print(f"🔗 Live Logs: {run_url}")

    # 4. Watch Execution
    print("\n⏳ Monitoring cloud render progress (Mac M1 remains 100% idle & cool)...")
    subprocess.run(["gh", "run", "watch", run_id], cwd=str(repo_root))

    # Authoritative check of workflow conclusion
    view_res = run_cmd(["gh", "run", "view", run_id, "--json", "conclusion"], cwd=str(repo_root))
    conclusion = json.loads(view_res.stdout).get("conclusion")
    if conclusion != "success":
        print(f"\n[ERROR] Workflow completed with failure: '{conclusion}'.")
        print(f"🔗 Check failed logs with: gh run view --log-failed {run_id}")
        sys.exit(1)

    # 5. Download Output Artifact
    dest_dist = project_dir / "dist"
    dest_dist.mkdir(parents=True, exist_ok=True)
    print(f"\n📥 Downloading rendered video MP4 artifact to {dest_dist}...")
    run_cmd(["gh", "run", "download", run_id, "-n", f"final-video-{args.project}", "-D", str(dest_dist)], cwd=str(repo_root))

    # Also mirror to original standalone project directory if it exists
    original_project_dist = Path(f"/Users/ozy/Projects/{args.project}/dist")
    if original_project_dist.parent.exists():
        original_project_dist.mkdir(parents=True, exist_ok=True)
        for mp4 in dest_dist.glob("*.mp4"):
            import shutil
            shutil.copy2(mp4, original_project_dist / mp4.name)
            print(f"✓ Mirrored MP4 to standalone project: {original_project_dist / mp4.name}")

    print("\n=======================================================")
    print("🎉 CLOUD RENDER COMPLETED SUCCESSFULLY!")
    print("=======================================================")
    for mp4 in dest_dist.glob("*.mp4"):
        print(f"🎬 Output File: {mp4}")
        # Run ffprobe check
        probe_res = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,r_frame_rate",
            "-of", "default=noprint_wrappers=1", str(mp4)
        ], capture_output=True, text=True)
        if probe_res.returncode == 0:
            print("📊 Stream Probe:")
            for l in probe_res.stdout.strip().split("\n"):
                print(f"   {l}")

if __name__ == "__main__":
    main()
