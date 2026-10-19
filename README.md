# 🎬 Hypit Cloud Render Farm

Universal Cloud Render Farm for **Hypit** video compositions using **GitHub Actions (Ubuntu 16 GB RAM, 4 vCPU)**.

Offloads heavy video rasterization (Chromium Headless 30 fps), AI voice alignment (WhisperX), and FFmpeg muxing from Apple Silicon Mac M1 (8 GB RAM) into free, high-performance cloud runners.

---

## 🚀 Projects Included

1. **`zaraleolaa-tiktok-clone`**: 100% editable TikTok video clone with custom comment stickers and glitch transitions.
2. **`nutriflakes-cr7-hypit`**: Vertical short-form clip (106s) combining CR7 Supercopa hook, podcast discussion, and nutrition outro.

---

## 🛠️ Architecture & Multi-Source Ingest

- **Social Media Sources (TikTok, IG, YouTube)**: Runner downloads directly via `yt-dlp`.
- **Local Mac Assets**: Ephemeral handoff via temporary GitHub Release (`gh release create`), downloaded by runner and purged immediately after build.
- **Output**: Rendered 1080×1920 MP4 video uploaded to GitHub Actions Artifacts and synced back to Mac.

---

## 💻 How to Trigger Render from Mac (Antigravity CLI)

```bash
# Render Nutriflakes x CR7 clip (106s) - Adaptive default concurrency
python3 scripts/render_cloud.py --project nutriflakes-cr7-hypit

# Render Zara Leola TikTok clone with custom concurrency (1 to 10)
python3 scripts/render_cloud.py --project zaraleolaa-tiktok-clone --concurrency 5

# Preflight validation without mutations (Dry-run)
python3 scripts/render_cloud.py --project nutriflakes-cr7-hypit --dry-run
```

---

## ⚙️ GitHub Actions Technical Limits & Guardrails

| Parameter | Kebijakan / Batasan | Deskripsi & Penegakan Sistem |
|---|---|---|
| **Max Concurrent Jobs** | `5` s.d. `10` (Hard ceiling: `10`, Fleksibel `1..10`) | Default adaptif berbasis ukuran aset: `< 20 MB` ➔ 10 jobs; `>= 50 MB` ➔ 5 jobs; `20–50 MB` ➔ 8 jobs. Pengguna bebas menentukan `1`, `2`, `3` hingga `10` sesuai kebutuhan via `--concurrency <N>`. Nilai `> 10` ditolak keras (*fail-closed*). |
| **Max Job Duration (Timeout)** | `120 menit` (2 jam) | Ditegakkan ganda di workflow (`timeout-minutes: 120`) dan monitoring watcher (`render_cloud.py`). Run dibatalkan otomatis jika melampaui batas. |
| **Max Artifact Size** | `500 MB` per job | Peringatan preventif jika artefak download $\ge 400\text{ MB}$, peringatan keras jika melampaui batas resmi 500 MB. |
| **Artifact Retention** | `1 hari` (`retention-days: 1`) | Efisiensi penyimpanan cloud maksimal untuk mencegah penumpukan storage. |
| **GitHub API Rate Limit** | `5.000 requests / jam` | Pemantauan kuota API core via `gh api rate_limit`. Pacing otomatis jika sisa kuota $< 100$ dan *exponential backoff* pada error HTTP 403/429. |
| **Video Codec & Hardware** | `Apple VideoToolbox` (Mac) vs `libx264` (Ubuntu) | Mac M1 memanfaatkan akselerasi GPU Apple Silicon (`-hwaccel videotoolbox -c:v h264_videotoolbox -b:v 6000k`), sedangkan runner cloud Ubuntu menggunakan CPU software encoding (`-c:v libx264 -preset veryfast -crf 18`). Single-Pass In-Browser compositing menghilangkan overhead WebM VP9 alpha. |

