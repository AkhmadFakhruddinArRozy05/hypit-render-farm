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
import shutil
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


def resolve_project_dir(repo_root: Path, project_name: str, explicit_dir: str | None = None) -> tuple[Path, Path]:
    """
    Resolves the source project directory and the farm project directory.
    Returns: (source_project_dir, farm_project_dir)
    """
    farm_project_dir = (repo_root / "projects" / project_name).resolve()

    if explicit_dir:
        source_dir = Path(explicit_dir).resolve()
        if not source_dir.exists():
            print(f"[ERROR] Specified --project-dir does not exist: {source_dir}")
            sys.exit(1)
        return source_dir, farm_project_dir

    if farm_project_dir.exists():
        return farm_project_dir, farm_project_dir

    # Hierarchical search candidates in Clipper and workspace
    clipper_root = repo_root.parent / "Clipper"
    clean_name = project_name.replace("-hypit", "")
    candidates = [
        clipper_root / "Production" / f"clippo__{clean_name}" / "hypit",
        clipper_root / "Production" / f"clippo__{project_name}" / "hypit",
        clipper_root / "Production" / project_name / "hypit",
        clipper_root / "Production" / f"clippo__{clean_name}",
        clipper_root / "Production" / f"clippo__{project_name}",
        clipper_root / "Production" / project_name,
        clipper_root / "References" / "Reproduction" / project_name,
    ]

    for candidate in candidates:
        if candidate.exists() and (candidate / "source_manifest.json").exists():
            return candidate.resolve(), farm_project_dir

    return farm_project_dir, farm_project_dir


def find_local_asset(
    file_name: str,
    declared_local_path: str | None,
    destination: str | None,
    source_dir: Path,
    farm_dir: Path,
    repo_root: Path,
    project_name: str,
) -> Path | None:
    """Finds a local asset using hierarchical search without hardcoded absolute project paths."""
    candidates = []

    # 1. Declared local_path
    if declared_local_path:
        candidates.append(Path(declared_local_path).resolve())

    # 2. Inside source_dir
    if destination:
        candidates.append(source_dir / destination)
    candidates.append(source_dir / file_name)
    candidates.append(source_dir / "assets" / file_name)
    candidates.append(source_dir / "references" / file_name)

    # 3. If source_dir is a hypit subfolder, check parent raw / assets
    if source_dir.name == "hypit":
        candidates.append(source_dir.parent / "raw" / file_name)
        candidates.append(source_dir.parent / "assets" / file_name)
        candidates.append(source_dir.parent / file_name)

    # 4. Inside farm_dir
    if destination:
        candidates.append(farm_dir / destination)
    candidates.append(farm_dir / file_name)
    candidates.append(farm_dir / "assets" / file_name)
    candidates.append(farm_dir / "references" / file_name)

    # 5. Clipper Production / References candidates
    clipper_root = repo_root.parent / "Clipper"
    clean_name = project_name.replace("-hypit", "")
    candidates.extend([
        clipper_root / "Production" / f"clippo__{clean_name}" / "raw" / file_name,
        clipper_root / "Production" / f"clippo__{project_name}" / "raw" / file_name,
        clipper_root / "Production" / clean_name / "raw" / file_name,
        clipper_root / "Production" / project_name / "raw" / file_name,
        clipper_root / "References" / "Reproduction" / project_name / "references" / file_name,
        clipper_root / "References" / "Reproduction" / project_name / "assets" / file_name,
        clipper_root / "References" / "Reproduction" / project_name / file_name,
    ])

    for c in candidates:
        if c.exists() and c.is_file() and c.stat().st_size > 0:
            return c.resolve()

    return None


def main():
    parser = argparse.ArgumentParser(description="Trigger Cloud Render on GitHub Actions (16 GB RAM)")
    parser.add_argument("--project", required=True, help="Project name inside projects/ (e.g. nutriflakes-cr7-hypit)")
    parser.add_argument("--project-dir", default=None, help="Explicit path to source project directory containing source_manifest.json")
    parser.add_argument("--output-dir", default=None, help="Explicit destination directory for downloaded MP4 artifact")
    parser.add_argument("--skip-push", action="store_true", help="Skip git add/commit/push before triggering")
    parser.add_argument("--dry-run", action="store_true", help="Perform full preflight validation without git push, release creation, or workflow dispatch")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    source_project_dir, farm_project_dir = resolve_project_dir(repo_root, args.project, args.project_dir)

    manifest_file = source_project_dir / "source_manifest.json"
    if not manifest_file.exists():
        manifest_file = farm_project_dir / "source_manifest.json"

    if not manifest_file.exists():
        print(f"[ERROR] Project manifest not found in:")
        print(f"   • {source_project_dir / 'source_manifest.json'}")
        print(f"   • {farm_project_dir / 'source_manifest.json'}")
        sys.exit(1)

    with open(manifest_file, "r") as f:
        manifest = json.load(f)

    print(f"\n=======================================================")
    print(f"🚀 INITIATING CLOUD RENDER: {args.project.upper()}")
    print(f"📁 Source Directory: {source_project_dir}")
    print(f"🚜 Farm Directory:   {farm_project_dir}")
    print(f"=======================================================")

    # Sync definitions to farm_project_dir if working from external source directory
    if source_project_dir != farm_project_dir and not args.dry_run:
        farm_project_dir.mkdir(parents=True, exist_ok=True)
        sync_exts = [".svml", ".svs", ".svrun", ".json"]
        for f in source_project_dir.iterdir():
            if f.is_file() and any(f.name.endswith(ext) for ext in sync_exts):
                shutil.copy2(f, farm_project_dir / f.name)
        media_ignore = shutil.ignore_patterns("*.mp4", "*.m4a", "*.mov", "*.wav", "*.mp3", "*.avi", "*.mkv")
        if (source_project_dir / "assets").exists():
            shutil.copytree(source_project_dir / "assets", farm_project_dir / "assets", dirs_exist_ok=True, ignore=media_ignore)
        if (source_project_dir / "references").exists():
            shutil.copytree(source_project_dir / "references", farm_project_dir / "references", dirs_exist_ok=True, ignore=media_ignore)
        print("✓ Synchronized project definition and assets to farm project directory.")

    # 1. Check for local assets requiring temporary release upload
    local_files_to_upload = []
    sources = manifest.get("sources", [])
    for src in sources:
        if src.get("type") == "local_asset":
            file_name = src.get("file_name")
            destination = src.get("destination")
            declared_path = src.get("local_path")

            p = find_local_asset(
                file_name=file_name,
                declared_local_path=declared_path,
                destination=destination,
                source_dir=source_project_dir,
                farm_dir=farm_project_dir,
                repo_root=repo_root,
                project_name=args.project,
            )

            if p and p.exists() and p.stat().st_size > 0:
                local_files_to_upload.append(str(p))
            else:
                print(f"[ERROR] Required local asset not found: '{file_name}' (id: {src.get('id')})")
                print(f"   Declared path was: {declared_path}")
                sys.exit(1)

    release_tag = ""
    if local_files_to_upload:
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        release_tag = f"temp-assets-{args.project[:12]}-{ts}"
        print(f"\n📦 Found {len(local_files_to_upload)} local asset(s) to upload to temporary release: '{release_tag}'")
        for f in local_files_to_upload:
            sz_mb = round(os.path.getsize(f) / (1024 * 1024), 2)
            print(f"   • {os.path.basename(f)} ({sz_mb} MB)")

    if args.dry_run:
        print("\n=======================================================")
        print("🔍 DRY-RUN PREFLIGHT SUMMARY (Zero Remote Mutation)")
        print("=======================================================")
        print(f"✓ Project Manifest:  {manifest_file}")
        print(f"✓ Target Run File:   {manifest.get('run_file')}")
        print(f"✓ Output Target:     {manifest.get('output_name')}")
        print(f"✓ Local Assets:      {len(local_files_to_upload)} verified")
        for f in local_files_to_upload:
            print(f"   • {f} ({round(os.path.getsize(f) / (1024 * 1024), 2)} MB)")
        if release_tag:
            print(f"✓ Simulated Release: {release_tag}")
        wf_sim = f"gh workflow run render.yml -f project={args.project}" + (f" -f release_tag={release_tag}" if release_tag else "")
        print(f"✓ Simulated Command: {wf_sim}")
        print("✓ Dry run preflight completed successfully with zero mutations.")
        return

    if local_files_to_upload:
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
    dest_dist = farm_project_dir / "dist"
    dest_dist.mkdir(parents=True, exist_ok=True)
    print(f"\n📥 Downloading rendered video MP4 artifact to {dest_dist}...")
    run_cmd(["gh", "run", "download", run_id, "-n", f"final-video-{args.project}", "-D", str(dest_dist)], cwd=str(repo_root))

    # Flexible mirroring to output destinations
    mirror_targets = set()
    if args.output_dir:
        mirror_targets.add(Path(args.output_dir).resolve())
    elif source_project_dir != farm_project_dir:
        mirror_targets.add((source_project_dir / "dist").resolve())
        # If source is a Clipper hypit subfolder, also mirror to final/
        if source_project_dir.name == "hypit" and (source_project_dir.parent / "final").exists():
            mirror_targets.add((source_project_dir.parent / "final").resolve())

    for target_dir in mirror_targets:
        target_dir.mkdir(parents=True, exist_ok=True)
        for mp4 in dest_dist.glob("*.mp4"):
            shutil.copy2(mp4, target_dir / mp4.name)
            print(f"✓ Mirrored MP4 to target: {target_dir / mp4.name}")

    print("\n=======================================================")
    print("🎉 CLOUD RENDER COMPLETED SUCCESSFULLY!")
    print("=======================================================")
    for mp4 in dest_dist.glob("*.mp4"):
        print(f"🎬 Output File: {mp4}")
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
