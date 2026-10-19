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

MAX_CONCURRENCY_HARD_CEILING = 10
MIN_CONCURRENCY = 1
TIMEOUT_MINUTES = 120
TIMEOUT_SECONDS = TIMEOUT_MINUTES * 60  # 7200 seconds (2 hours)
ARTIFACT_MAX_MB = 500.0
ARTIFACT_WARN_THRESHOLD_MB = 400.0
GITHUB_API_MIN_REMAINING = 100


def check_github_rate_limit(min_remaining: int = GITHUB_API_MIN_REMAINING) -> dict | None:
    """
    Checks GitHub API rate limit via gh api rate_limit.
    Applies pacing / exponential backoff if quota remaining is below min_remaining.
    """
    try:
        res = subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            core = data.get("resources", {}).get("core", {})
            remaining = core.get("remaining", 5000)
            limit = core.get("limit", 5000)
            reset_ts = core.get("reset", 0)
            print(f"📡 [RATE-LIMIT] GitHub API Core: {remaining}/{limit} requests remaining.")
            if remaining < min_remaining:
                sleep_secs = max(5, int(reset_ts - time.time()))
                wait_time = min(sleep_secs, 60)
                print(f"⚠️ [RATE-LIMIT] GitHub API core quota critically low ({remaining}/{limit})! Pacing for {wait_time}s...")
                time.sleep(wait_time)
            return core
        else:
            print(f"⚠️ [RATE-LIMIT] GitHub API rate_limit check returned non-zero ({res.returncode}): {res.stderr.strip()}")
    except Exception as e:
        print(f"⚠️ [RATE-LIMIT] Could not check GitHub API rate limit: {e}")
    return None


def calculate_adaptive_concurrency(total_assets_mb: float) -> int:
    """
    Adaptive concurrency logic based on total raw assets size:
    - total_assets_mb < 20 MB: 10 concurrent jobs (maximum throughput)
    - total_assets_mb >= 50 MB: 5 concurrent jobs (bandwidth & runner conservation)
    - 20 MB <= total_assets_mb < 50 MB: 8 concurrent jobs (balanced load)
    Hard ceiling is 10.
    """
    if total_assets_mb < 20.0:
        return 10
    elif total_assets_mb >= 50.0:
        return 5
    else:
        return 8


def calculate_total_assets_mb(local_files_to_upload: list[str], source_project_dir: Path) -> float:
    """
    Calculates total unique raw asset size in MB with strict path deduplication.
    Prevents double-counting files present both in local_files_to_upload and source assets/.
    """
    unique_files = {Path(f).resolve() for f in local_files_to_upload if f}
    assets_dir = source_project_dir / "assets"
    if assets_dir.exists():
        for af in assets_dir.rglob("*"):
            if af.is_file():
                unique_files.add(af.resolve())
    total_bytes = sum(f.stat().st_size for f in unique_files if f.is_file())
    return total_bytes / (1024 * 1024)


def check_artifact_sizes(directory: Path) -> bool:
    """
    Checks size of downloaded artifacts:
    - Fails if 0 bytes (corrupt / empty download).
    - Warns if approaching 500 MB (>= 400 MB).
    - Warns if exceeding 500 MB limit.
    Returns True if valid non-empty artifact found and no hard limit violation, False otherwise.
    """
    mp4_files = list(directory.glob("*.mp4"))
    if not mp4_files:
        print(f"⚠️ [ARTIFACT] No MP4 artifact found in {directory}")
        return False

    all_valid = True
    for mp4 in mp4_files:
        sz_bytes = mp4.stat().st_size
        sz_mb = sz_bytes / (1024 * 1024)
        if sz_bytes == 0:
            print(f"❌ [ARTIFACT ERROR] File '{mp4.name}' is empty (0 bytes)! Corrupt artifact detected.")
            all_valid = False
        elif sz_mb >= ARTIFACT_MAX_MB:
            print(f"⚠️ [ARTIFACT WARNING] File '{mp4.name}' ({sz_mb:.2f} MB) exceeds GitHub Actions 500 MB recommended limit!")
        elif sz_mb >= ARTIFACT_WARN_THRESHOLD_MB:
            print(f"⚠️ [ARTIFACT WARNING] File '{mp4.name}' ({sz_mb:.2f} MB) is approaching 500 MB limit (>= 400 MB).")
        else:
            print(f"✓ [ARTIFACT OK] File '{mp4.name}' ({sz_mb:.2f} MB) is within safe 500 MB threshold.")

    return all_valid


def run_cmd(cmd, check=True, cwd=None, max_retries=3, base_delay=5):
    cmd_str = " ".join(str(c) for c in cmd)
    for attempt in range(max_retries + 1):
        print(f"[RUN] {cmd_str}")
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        if res.returncode == 0:
            return res

        err_msg = (res.stderr + " " + res.stdout).lower()
        if any(term in err_msg for term in ["rate limit", "secondary rate limit", "http 403", "http 429", "too many requests"]):
            if attempt < max_retries:
                backoff_time = base_delay * (2 ** attempt)
                print(f"⚠️ [RATE-LIMIT BACKOFF] Rate limit hit. Backing off for {backoff_time}s (retry {attempt + 1}/{max_retries})...")
                time.sleep(backoff_time)
                continue

        if check:
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
    parser.add_argument("--concurrency", type=int, default=None, help="Maximum concurrent render jobs (1 to 10). If omitted, adaptively defaults based on raw asset size (<20MB: 10, >=50MB: 5, 20-50MB: 8).")
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

    # Evaluate total raw asset size & concurrency policy with deduplication
    total_asset_mb = calculate_total_assets_mb(local_files_to_upload, source_project_dir)
    adaptive_default = calculate_adaptive_concurrency(total_asset_mb)

    if args.concurrency is not None:
        if args.concurrency < MIN_CONCURRENCY:
            print(f"[ERROR] Invalid --concurrency {args.concurrency}: must be between {MIN_CONCURRENCY} and {MAX_CONCURRENCY_HARD_CEILING}.")
            sys.exit(1)
        if args.concurrency > MAX_CONCURRENCY_HARD_CEILING:
            print(f"[ERROR] Concurrency {args.concurrency} exceeds hard ceiling of {MAX_CONCURRENCY_HARD_CEILING} concurrent jobs (GitHub Actions safety limit). Allowed range is {MIN_CONCURRENCY}-{MAX_CONCURRENCY_HARD_CEILING}.")
            sys.exit(1)
        concurrency = args.concurrency
        concurrency_source = f"user-specified (Hard ceiling: {MAX_CONCURRENCY_HARD_CEILING})"
    else:
        concurrency = adaptive_default
        concurrency_source = f"adaptive default based on raw assets size ({total_asset_mb:.2f} MB)"

    print(f"⚡ Concurrency Setting: {concurrency} concurrent job(s) [{concurrency_source}]")

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
        print(f"✓ Local Assets:      {len(local_files_to_upload)} verified ({total_asset_mb:.2f} MB total)")
        for f in local_files_to_upload:
            print(f"   • {f} ({round(os.path.getsize(f) / (1024 * 1024), 2)} MB)")
        print(f"✓ Concurrency:       {concurrency} concurrent job(s) [{concurrency_source}]")
        print(f"✓ Timeout Guard:     {TIMEOUT_MINUTES} minutes ({TIMEOUT_SECONDS}s fail-closed ceiling)")
        print(f"✓ Artifact Ceiling:  {ARTIFACT_MAX_MB} MB max (warning at {ARTIFACT_WARN_THRESHOLD_MB} MB)")
        check_github_rate_limit()
        if release_tag:
            print(f"✓ Simulated Release: {release_tag}")
        wf_sim = f"gh workflow run render.yml -f project={args.project} -f concurrency={concurrency}" + (f" -f release_tag={release_tag}" if release_tag else "")
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

    # Check rate limit before dispatch
    check_github_rate_limit()

    # 3. Trigger GitHub Actions Workflow
    print(f"\n⚡ Dispatching GitHub Actions Workflow on 16 GB RAM Runner (Concurrency: {concurrency})...")
    dispatch_cmd = ["gh", "workflow", "run", "render.yml", "-f", f"project={args.project}", "-f", f"concurrency={concurrency}"]
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

    # 4. Watch Execution with fail-closed timeout guard (120 minutes / 7200 seconds)
    print("\n⏳ Monitoring cloud render progress (Mac M1 remains 100% idle & cool)...")
    print(f"🛡️ Timeout Guard: {TIMEOUT_MINUTES} minutes ({TIMEOUT_SECONDS}s) fail-closed ceiling")
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        remaining_timeout = int(TIMEOUT_SECONDS - elapsed)
        if remaining_timeout <= 0:
            print(f"\n❌ [ERROR] Cloud render timed out after {TIMEOUT_MINUTES} minutes ({TIMEOUT_SECONDS}s)! Fail-closed guard triggered.")
            print(f"🛑 [FAIL-CLOSED] Cancelling active GitHub Actions workflow run {run_id}...")
            subprocess.run(["gh", "run", "cancel", run_id], cwd=str(repo_root))
            sys.exit(1)

        try:
            subprocess.run(["gh", "run", "watch", run_id], cwd=str(repo_root), timeout=remaining_timeout)
        except subprocess.TimeoutExpired:
            print(f"\n❌ [ERROR] Cloud render timed out after {TIMEOUT_MINUTES} minutes ({TIMEOUT_SECONDS}s)! Fail-closed guard triggered.")
            print(f"🛑 [FAIL-CLOSED] Cancelling active GitHub Actions workflow run {run_id}...")
            subprocess.run(["gh", "run", "cancel", run_id], cwd=str(repo_root))
            sys.exit(1)

        # Authoritative check of workflow status and conclusion
        view_res = run_cmd(["gh", "run", "view", run_id, "--json", "status,conclusion"], cwd=str(repo_root), check=False)
        if view_res.returncode == 0:
            run_info = json.loads(view_res.stdout)
            wf_status = run_info.get("status")
            wf_conclusion = run_info.get("conclusion")
            if wf_status == "completed":
                if wf_conclusion != "success":
                    print(f"\n[ERROR] Workflow completed with failure: '{wf_conclusion}'.")
                    print(f"🔗 Check failed logs with: gh run view --log-failed {run_id}")
                    sys.exit(1)
                break
            else:
                print(f"ℹ️ Workflow still {wf_status}... Re-attaching watcher (elapsed: {int(elapsed)}s)...")
                time.sleep(5)
        else:
            time.sleep(5)

    # 5. Download Output Artifact
    dest_dist = farm_project_dir / "dist"
    dest_dist.mkdir(parents=True, exist_ok=True)
    print(f"\n📥 Downloading rendered video MP4 artifact to {dest_dist}...")
    run_cmd(["gh", "run", "download", run_id, "-n", f"final-video-{args.project}", "-D", str(dest_dist)], cwd=str(repo_root))
    if not check_artifact_sizes(dest_dist):
        print("❌ [FAIL-CLOSED] Artifact validation failed! Check corrupted or empty output files.")
        sys.exit(1)

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
