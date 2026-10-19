import json
import sys
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest

# Add scripts directory to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from render_cloud import (
    calculate_adaptive_concurrency,
    calculate_total_assets_mb,
    check_artifact_sizes,
    check_github_rate_limit,
    MAX_CONCURRENCY_HARD_CEILING,
    MIN_CONCURRENCY,
    TIMEOUT_MINUTES,
    TIMEOUT_SECONDS,
    ARTIFACT_MAX_MB,
    ARTIFACT_WARN_THRESHOLD_MB,
    GITHUB_API_MIN_REMAINING,
)


def test_constants():
    assert MAX_CONCURRENCY_HARD_CEILING == 10
    assert MIN_CONCURRENCY == 1
    assert TIMEOUT_MINUTES == 120
    assert TIMEOUT_SECONDS == 7200
    assert ARTIFACT_MAX_MB == 500.0
    assert ARTIFACT_WARN_THRESHOLD_MB == 400.0
    assert GITHUB_API_MIN_REMAINING == 100


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


def test_calculate_total_assets_mb_deduplication(tmp_path):
    proj_dir = tmp_path / "project"
    assets_dir = proj_dir / "assets"
    assets_dir.mkdir(parents=True)

    # Asset 1 in assets/ (10 MB)
    asset1 = assets_dir / "video1.mp4"
    asset1.write_bytes(b"x" * (10 * 1024 * 1024))

    # Asset 2 in references/ outside assets/ (5 MB)
    refs_dir = proj_dir / "references"
    refs_dir.mkdir(parents=True)
    asset2 = refs_dir / "video2.mp4"
    asset2.write_bytes(b"y" * (5 * 1024 * 1024))

    # Test 1: asset1 is both in local_files_to_upload and in assets_dir
    local_files = [str(asset1), str(asset2)]
    total_mb = calculate_total_assets_mb(local_files, proj_dir)

    # Expected: 10 + 5 = 15 MB (NOT 10 + 10 + 5 = 25 MB)
    assert round(total_mb, 2) == 15.00


def test_artifact_sizes_check(tmp_path, capsys):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()

    # 1. Empty directory
    assert check_artifact_sizes(dist_dir) is False
    captured = capsys.readouterr()
    assert "No MP4 artifact found" in captured.out

    # 2. Corrupt / empty 0-byte MP4
    empty_mp4 = dist_dir / "empty.mp4"
    empty_mp4.write_bytes(b"")
    assert check_artifact_sizes(dist_dir) is False
    captured = capsys.readouterr()
    assert "[ARTIFACT ERROR]" in captured.out
    assert "is empty (0 bytes)" in captured.out
    empty_mp4.unlink()

    # 3. Normal size (10 MB)
    small_mp4 = dist_dir / "small.mp4"
    small_mp4.write_bytes(b"0" * (10 * 1024 * 1024))
    assert check_artifact_sizes(dist_dir) is True
    captured = capsys.readouterr()
    assert "[ARTIFACT OK]" in captured.out
    assert "10.00 MB" in captured.out
    small_mp4.unlink()

    # 4. Approaching threshold (450 MB)
    mid_mp4 = dist_dir / "mid.mp4"
    with open(mid_mp4, "wb") as f:
        f.seek(450 * 1024 * 1024 - 1)
        f.write(b"\0")
    assert check_artifact_sizes(dist_dir) is True
    captured = capsys.readouterr()
    assert "is approaching 500 MB limit" in captured.out
    mid_mp4.unlink()

    # 5. Exceeding threshold (550 MB)
    large_mp4 = dist_dir / "large.mp4"
    with open(large_mp4, "wb") as f:
        f.seek(550 * 1024 * 1024 - 1)
        f.write(b"\0")
    assert check_artifact_sizes(dist_dir) is True
    captured = capsys.readouterr()
    assert "exceeds GitHub Actions 500 MB recommended limit" in captured.out
    large_mp4.unlink()


def test_rate_limit_check_healthy(capsys):
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = json.dumps({
        "resources": {
            "core": {
                "limit": 5000,
                "remaining": 4900,
                "reset": 1700000000
            }
        }
    })
    with patch("subprocess.run", return_value=mock_res):
        core = check_github_rate_limit()
        assert core is not None
        assert core["remaining"] == 4900
        captured = capsys.readouterr()
        assert "4900/5000 requests remaining" in captured.out


def test_rate_limit_check_failed(capsys):
    mock_res = MagicMock()
    mock_res.returncode = 1
    mock_res.stderr = "API rate limit exceeded"
    with patch("subprocess.run", return_value=mock_res):
        core = check_github_rate_limit()
        assert core is None
        captured = capsys.readouterr()
        assert "rate_limit check returned non-zero (1)" in captured.out
