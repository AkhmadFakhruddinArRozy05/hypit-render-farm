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
import re
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


def legacy_preflight():
    parser = argparse.ArgumentParser(description="Trigger Cloud Render on GitHub Actions (16 GB RAM)")
    parser.add_argument("--project", required=True, help="Project name inside projects/ (e.g. nutriflakes-cr7-hypit)")
    parser.add_argument("--project-dir", default=None, help="Explicit path to source project directory containing source_manifest.json")
    parser.add_argument("--output-dir", default=None, help="Explicit destination directory for downloaded MP4 artifact")
    parser.add_argument("--concurrency", type=int, default=None, help="Maximum concurrent render jobs (1 to 10). If omitted, adaptively defaults based on raw asset size (<20MB: 10, >=50MB: 5, 20-50MB: 8).")
    parser.add_argument("--headline-preset", default=None, help="Visual headline preset ID (overrides source_manifest.json)")
    parser.add_argument("--subtitle-preset", default=None, help="Kinetic subtitle preset ID (overrides source_manifest.json)")
    parser.add_argument("--skip-push", action="store_true", help="Deprecated compatibility flag; script never mutates Git")
    parser.add_argument("--dry-run", action="store_true", help="Perform full preflight validation without git push, release creation, or workflow dispatch")
    args = parser.parse_args()

    if not args.dry_run:
        parser.error("Legacy project dispatch is disabled. Use --job-id with a persisted cloud job; local --project --dry-run remains available.")

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

    # Resolve Presets with fallback to source_manifest.json
    headline_preset = (args.headline_preset or manifest.get("headline_preset") or "HEADLINE_BOXED_YELLOW").strip()
    subtitle_preset = (args.subtitle_preset or manifest.get("subtitle_preset") or "SUB_MODERN_PILL").strip()
    headline_source = "CLI override" if args.headline_preset else ("manifest" if manifest.get("headline_preset") else "default")
    subtitle_source = "CLI override" if args.subtitle_preset else ("manifest" if manifest.get("subtitle_preset") else "default")

    print(f"\n=======================================================")
    print(f"🚀 INITIATING CLOUD RENDER: {args.project.upper()}")
    print(f"📁 Source Directory: {source_project_dir}")
    print(f"🚜 Farm Directory:   {farm_project_dir}")
    print(f"🎨 Headline Preset:  {headline_preset} [{headline_source}]")
    print(f"📝 Subtitle Preset:  {subtitle_preset} [{subtitle_source}]")
    print(f"=======================================================")

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
        print(f"✓ Headline Preset:  {headline_preset} [{headline_source}]")
        print(f"✓ Subtitle Preset:  {subtitle_preset} [{subtitle_source}]")
        print(f"✓ Concurrency:       {concurrency} concurrent job(s) [{concurrency_source}]")
        print(f"✓ Timeout Guard:     {TIMEOUT_MINUTES} minutes ({TIMEOUT_SECONDS}s fail-closed ceiling)")
        print(f"✓ Artifact Ceiling:  {ARTIFACT_MAX_MB} MB max (warning at {ARTIFACT_WARN_THRESHOLD_MB} MB)")
        print("✓ Legacy asset preflight complete. Migrate verified assets to the private library and create a persisted cloud job.")
        print("✓ Legacy project/concurrency options do not dispatch the secure workflow; cloud concurrency is 1.")
        return



def dispatch_job(repo, ref, job_id):
    """Dispatch exactly once. Ambiguous responses must be reconciled, never retried."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid GitHub repository")
    if not ref or not ref.startswith("refs/") or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        raise ValueError("Explicit approved full ref and bounded job ID required")
    body = json.dumps({"ref": ref, "inputs": {"job_id": job_id}})
    try:
        result = subprocess.run([
            "gh", "api", "--include", "--method", "POST",
            f"repos/{repo}/actions/workflows/render.yml/dispatches",
            "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2026-03-10",
            "--input", "-"], input=body, capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError):
        raise RuntimeError("DISPATCH_UNKNOWN: transport interrupted; reconcile exact run before any redispatch") from None
    # HTTP 200 is required under API 2026-03-10. 204 cannot identify this run.
    match = re.fullmatch(r"HTTP/[^ ]+ 200[^\n]*\n(?:[^\n]+\n)*?\n(.*)", result.stdout.replace("\r\n", "\n"), re.S)
    if result.returncode or match is None:
        raise RuntimeError("DISPATCH_UNKNOWN: preserve job and reconcile exact run; do not redispatch")
    try:
        details = json.loads(match.group(1))
        run_id = details["workflow_run_id"]
        if (type(run_id) is not int or run_id < 1
            or details["run_url"] != f"https://api.github.com/repos/{repo}/actions/runs/{run_id}"
            or details["html_url"] != f"https://github.com/{repo}/actions/runs/{run_id}"):
            raise ValueError("Missing exact run details")
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError("DISPATCH_UNKNOWN: response has no trustworthy exact run ID") from error
    return details


def main():
    if any(argument == "--project" or argument.startswith("--project=") for argument in sys.argv[1:]):
        legacy_preflight()
        return
    parser = argparse.ArgumentParser(description="Secure persisted-job dispatch; no auto Git mutations or public release handoff")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ref", required=True, help="Exact approved full Git ref")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({"mode": "DRY_RUN", "repository": args.repo, "ref": args.ref,
            "workflow": "render.yml", "inputs": {"job_id": args.job_id}, "api_version": "2026-03-10"}))
        return
    print(json.dumps(dispatch_job(args.repo, args.ref, args.job_id)))


if __name__ == "__main__":
    main()
