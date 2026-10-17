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
# Render Nutriflakes x CR7 clip (106s)
python3 scripts/render_cloud.py --project nutriflakes-cr7-hypit

# Render Zara Leola TikTok clone
python3 scripts/render_cloud.py --project zaraleolaa-tiktok-clone
```
