import sys
import pytest
from pathlib import Path

# Add scripts directory to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from render_cloud import (
    calculate_adaptive_concurrency,
    check_artifact_sizes,
    MAX_CONCURRENCY_HARD_CEILING,
    MIN_CONCURRENCY,
    TIMEOUT_MINUTES,
    TIMEOUT_SECONDS,
    ARTIFACT_MAX_MB,
    ARTIFACT_WARN_THRESHOLD_MB,
)


def test_constants():
    assert MAX_CONCURRENCY_HARD_CEILING == 10
    assert MIN_CONCURRENCY == 1
    assert TIMEOUT_MINUTES == 120
    assert TIMEOUT_SECONDS == 7200
    assert ARTIFACT_MAX_MB == 500.0
    assert ARTIFACT_WARN_THRESHOLD_MB == 400.0


def test_adaptive_concurrency():
    # Asset size < 20 MB -> 10 concurrent jobs
    assert calculate_adaptive_concurrency(0.0) == 10
    assert calculate_adaptive_concurrency(10.5) == 10
    assert calculate_adaptive_concurrency(19.99) == 10

    # Asset size 20 MB <= size < 50 MB -> 8 concurrent jobs
    assert calculate_adaptive_concurrency(20.0) == 8
    assert calculate_adaptive_concurrency(35.0) == 8
    assert calculate_adaptive_concurrency(49.99) == 8

    # Asset size >= 50 MB -> 5 concurrent jobs
    assert calculate_adaptive_concurrency(50.0) == 5
    assert calculate_adaptive_concurrency(100.0) == 5
    assert calculate_adaptive_concurrency(500.0) == 5


def test_artifact_sizes_check(tmp_path, capsys):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    # 1. Normal size (10 MB)
    small_mp4 = dist_dir / "small.mp4"
    small_mp4.write_bytes(b"0" * (10 * 1024 * 1024))
    check_artifact_sizes(dist_dir)
    captured = capsys.readouterr()
    assert "[ARTIFACT OK]" in captured.out
    assert "10.00 MB" in captured.out
    small_mp4.unlink()

    # 2. Approaching threshold (450 MB)
    mid_mp4 = dist_dir / "mid.mp4"
    # Create sparse file for 450 MB without allocating full disk blocks
    with open(mid_mp4, "wb") as f:
        f.seek(450 * 1024 * 1024 - 1)
        f.write(b"\0")
    check_artifact_sizes(dist_dir)
    captured = capsys.readouterr()
    assert "is approaching 500 MB limit" in captured.out
    mid_mp4.unlink()

    # 3. Exceeding threshold (550 MB)
    large_mp4 = dist_dir / "large.mp4"
    with open(large_mp4, "wb") as f:
        f.seek(550 * 1024 * 1024 - 1)
        f.write(b"\0")
    check_artifact_sizes(dist_dir)
    captured = capsys.readouterr()
    assert "exceeds GitHub Actions 500 MB recommended limit" in captured.out
    large_mp4.unlink()
