# Clipper trusted Hypit render farm

Private-media rendering for persisted Clipper cloud jobs. Heavy processing runs in GitHub Actions; the Mac performs inventory, authoring and lightweight checks only. Source files remain in the private R2 library. The runner never receives Buffer credentials or publication approval tokens.

The canonical dispatcher is the Clipper Worker. It saves the exact GitHub run ID returned by API version `2026-03-10` before authorizing runner requests. Run identity must match repository ID `1423099026`, configured trusted workflow/ref and OIDC audience `clipper-render`.

## Workflow

1. Worker validates rights/source evidence, bounded revision and free-tier quota; persists the job and dispatches `.github/workflows/render.yml` with only `job_id`.
2. Runner authenticates the exact job using fresh GitHub OIDC, then installs frozen dependencies from Hypit commit `1efedb925d375686ab107b6e67f914dced7b102b`.
3. Runner streams authorized source objects, verifies their SHA-256 and size, and executes the requested phase.
4. Runner uploads private artifact receipts, then submits the result for that exact revision. Failed, stale or cancelled jobs cannot become publishable through runner claims.

| Phase | Output | Evidence |
|---|---|---|
| analysis | `analysis.json` | Actual source stream metadata, scene changes and silence; semantic analysis and transcription explicitly not performed |
| preview | 540×960 H.264/AAC MP4 + QA JSON | Full-artifact decode, duration/FPS/codecs, measured loudness/true peak, SHA-256 |
| final | 1080×1920 H.264/AAC MP4 + QA JSON | Same actual-artifact QA; human creative/rights review remains required |

`vertical-v1` is trusted Python-generated native Hypit SVML, using only `STORYTELLING`, `PODCAST`, `JJ`. Accepted edit parameters are `clips[{asset_id,start_s,end_s}]`, `title`, timed `subtitles[{start_s,end_s,text}]`, and optional `music_asset_id`. Text is escaped. No user JS, SVML, filesystem paths, URLs, projects or packages are executed. FFmpeg prepares bounded trims, center crops, source audio and optional looped music; Hypit composes clips/title/subtitles/music; FFmpeg masters audio and mechanically validates the final physical file.

Limits: one active workflow globally, 90-minute runner timeout, at most 16 assets / 2 GiB per asset / 4 GiB total, 24 clips, 180-second output, 300 non-overlapping subtitle cues, artifact strictly below 500 MiB. Source clip audio is required. Loudness acceptance is −18..−12 LUFS and true peak ≤−1 dBTP after two-pass mastering. Center cropping and timed plain subtitles require human review; there is no claim of automatic face tracking, word alignment or semantic editorial certification.

## Configuration and activation

Set repository variable `CLIPPER_API_ORIGIN` to the trusted HTTPS Worker origin. Worker bindings, approved workflow/ref, verified free-tier quota, schema and private R2 must exist first. This patch does not provision, deploy, push or dispatch anything. Workflow content is read-only and its checkout cannot push. Dependencies and actions are pinned; no public releases or GitHub artifact storage are used.

The runner consumes shared protocol v1:

- `GET /runner/jobs/:id` → `{job,assets:[{asset_id,sha256,size,mime}]}`
- `GET /runner/assets/:sha?job_id=:id` → authorized private source stream
- `PUT /runner/jobs/:id/artifacts/:kind` with `Content-Type`, `Content-Length`, `X-Content-SHA256` → `{object_key,sha256,size,kind,mime}`
- `POST /runner/jobs/:id/result` → `{status,build_id,artifacts,qa?,error?}`

Every request mints fresh OIDC. Redirects are forbidden; FFmpeg source protocols/containers are allowlisted. No secret appears in URLs, process arguments, callbacks or logs. `build_id` is the exact completed Hypit CLI submission ID, and `null` for analysis or failure before submission. No latest-run/build lookup exists.

## Local diagnostics

Use the Clipper virtualenv when this farm has no `.venv`:

```sh
/Users/ozy/Projects/Clipper/.venv/bin/python scripts/render_cloud.py \
  --job-id job_example --repo owner/repo --ref refs/heads/approved --dry-run

/Users/ozy/Projects/Clipper/.venv/bin/python scripts/render_cloud.py \
  --project clip_03_jei_cincin_nikah --dry-run

/Users/ozy/Projects/Clipper/.venv/bin/pytest -q tests/test_secure_runner.py tests/test_render_cloud.py tests/test_ingest.py
```

Legacy project preflight and asset lookup helpers remain available; legacy project dispatch, auto Git add/commit/push and public release upload are disabled. Live `--job-id` dispatch is a low-level integration diagnostic: the authoritative Worker must reconcile and persist its returned run ID. It dispatches once and fails `DISPATCH_UNKNOWN` on ambiguous responses; never redispatch blindly. Production uses the Worker dispatcher so exact run binding is durable.

Full cloud rendering, OIDC interoperability, private R2 transfer and human approval pilots remain activation acceptance checks. Passing local fixtures or the native author compiler does not certify rendered production quality.
