# Secure Hypit farm implementation — 2026-10-26

## Status and scope

Local implementation is reviewable; external cloud acceptance remains pending. Baseline `c74d6fd22f7a7bddba1820a34d5af3c20b27242e` was clean. Changes belong only to `/Users/ozy/Projects/hypit-render-farm` (Clipper exposes it through `Tools/Hypit-Render-Farm`). No commit, push, deployment, infrastructure mutation, actual workflow dispatch, local render, transcription, model download or publication occurred.

Progress: [100%] inspect/protocol/tests; [100%] trusted runner/workflow; [100%] targeted verification; [0%] external activation/pilot (requires the parent activation gates).

## Files changed

| File | Reason |
|---|---|
| `.github/workflows/render.yml` | Replace project/release/unpinned execution with job-ID-only trusted pipeline; read-only repository access; GitHub OIDC; global concurrency 1 |
| `scripts/cloud_runner.py` | Standard-library authenticated streaming client, bounded edit validation, trusted native Hypit template, technical prep, measured analysis, actual MP4/audio QA and private receipts |
| `scripts/render_cloud.py` | Remove automatic Git add/commit/push, release writes and latest-run lookup; retain local project inventory/preflight; add exact HTTP200 dispatch diagnostic |
| `tests/test_secure_runner.py` | Regression fixtures for execution boundary, OIDC/run binding, transfer integrity, exact dispatch/build IDs, real-QA command structure and native author compilation |
| `README.md` | Document secure protocol, canonical Worker dispatch, actual capabilities, constraints and activation residual |
| `IMPLEMENTATION_REPORT.md` | This execution/evidence record |

## Trusted execution boundary

- The sole workflow input is a bounded job ID. Worker/API origin comes from trusted repository variable `CLIPPER_API_ORIGIN`, never user data.
- Pinned Hypit SHA `1efedb925d375686ab107b6e67f914dced7b102b`, upstream `pnpm-lock.yaml`, `pnpm@10.33.0`, Node `22.16.0`, immutable action SHAs, frozen dependency installation. No project package installation, arbitrary JS/SVML, template file paths, user URLs or user package names.
- Runner verifies pinned Hypit checkout again before rendering. `vertical-v1` is fixed native SVML generated from bounded parameters. Template code SHA is recorded in QA.
- Families are exactly `STORYTELLING`, `PODCAST`, `JJ`; v1 varies the fixed accent style while using the same compositing contract.
- Per job: 16 unique SHA assets maximum; 2 GiB each / 4 GiB combined; 24 clips / 180 seconds output; 300 non-overlapping subtitle cues; title 160 and cue 240 characters. Finite numbers only; no Boolean/NaN/Infinity numeric coercion.
- Private source descriptors must match job asset IDs/SHA/size. Streams hash incrementally; mismatches remove partial files. Source MIME and container/protocol allowlists exclude arbitrary HLS/concat/network source fetching.
- Every API request mints fresh GitHub OIDC with `aud=clipper-render`. Exact `GITHUB_RUN_ID` must equal persisted job run ID. Backend enforces repository ID 1423099026, trusted ref/workflow, stale/cancelled revision and authorization. Authenticated redirects are forbidden.
- No Buffer token, approval nonce, publication/push/release credential or user executable input enters the render job. Repository permission is `contents: read`; checkout does not persist credentials.
- Preflight authorizes the exact job before provisioning expensive engine dependencies. All rendering runs on one Ubuntu runner at a time. No runner waits for human approval.

## Actual media behavior

`analysis` probes stream metadata and duration, detects scene changes and silence from actual source files, and explicitly records `semantic_analysis=NOT_PERFORMED` and `transcription=NOT_PERFORMED`. It returns `build_id:null`; no synthetic Hypit or semantic evidence.

`preview` and `final` use FFmpeg only for bounded technical trims, constant-rate center crops and source-audio preparation. Hypit then composes native visual/audio clips, title, timed plain-text subtitles and optional prepared looped music at fixed conservative gain. Hypit output is exported using the exact completed `hypit.cli-build@1` submission ID. Even a failed submission records an actual returned build ID when available. There is no latest build/run fallback.

A two-pass FFmpeg loudness master targets −15 LUFS / −1.5 dBTP. The delivered physical MP4 is then validated through:

- ffprobe H.264/AAC stream identity, mandatory audio, exact 540×960 preview or 1080×1920 final, 30 fps and duration/audio-duration tolerance;
- full-duration FFmpeg decode of both streams with `-xerror`, without sample-duration truncation;
- actual `ebur128=peak=true` integrated loudness and true-peak evidence; finite −18..−12 LUFS and peak≤−1dBTP required;
- black-interval detection, rejecting an almost entirely black artifact;
- SHA-256 tied to the exact delivered object receipt, plus bytes/content type checked against Worker response.

Mechanical QA cannot certify editorial taste, face framing, rights, subtitle correctness or platform acceptance. `human_review=REQUIRED`, `framing=CENTER_CROP_REQUIRES_REVIEW`, `editorial_quality=NOT_AUTOMATICALLY_CERTIFIED` remain explicit.

## Protocol integration

The farm and backend agreed the exact shared shapes in the Clipper implementation plan:

- GET job envelope `{job,assets}`; job includes exact `run_id`.
- Private asset route `/runner/assets/:sha?job_id=:id`.
- PUT preview/final as `video/mp4`; qa/analysis as `application/json`; headers include SHA and content length; receipts are server-derived `{object_key,sha256,size,kind,mime}`.
- Result callback `{status,build_id,artifacts,qa?,error?}`. Analysis and pre-submission failure use null build ID. Structured failure is `{code:'RUNNER_FAILED',stage,message}` with deterministic safe stage, no raw secret-bearing logs.
- QA keys include `artifact_sha256,actual_mp4,full_decode,phase,width,height,codec,duration_s,fps,audio_present,integrated_lufs,true_peak_dbtp,passed`, plus metadata/human-review fields.

The local dispatch diagnostic calls GitHub API 2026-03-10 once and accepts only HTTP200 with exact `workflow_run_id`, matching API/run URLs for the requested repository. Ambiguous or 204 responses become `DISPATCH_UNKNOWN`, never latest-run lookup or silent retry. Canonical production dispatch remains the Worker, which persists run binding transactionally in its controller flow. Direct CLI diagnostics alone cannot finalize that durable binding.

Primary API source verified: [GitHub workflow dispatch API 2026-03-10](https://docs.github.com/en/rest/actions/workflows?apiVersion=2026-03-10) and [official run-ID changelog](https://github.blog/changelog/2026-02-19-workflow-dispatch-api-now-returns-run-ids/). Version 2026 returns details by default; obsolete `return_run_details` is not sent.

## Verification evidence

RED: before `cloud_runner.py` existed, new regression suite failed collection with `ModuleNotFoundError: cloud_runner` (exit 2). GREEN: new and existing targeted suites passed.

Final command:

```sh
HYPIT_TEMPLATE_CHECK_ROOT=/Users/ozy/Projects/hypit \
  /Users/ozy/Projects/Clipper/.venv/bin/pytest -q \
  tests/test_secure_runner.py tests/test_render_cloud.py tests/test_ingest.py
```

Result: **30 passed in 50.04 s**. After adding transport-timeout classification, the final lightweight rerun passed **30 tests, 1 opt-in compiler test skipped**; the unchanged native template compiler had already passed in the preceding full opt-in run. This includes a native pinned-Hypit author compiler check with two clips, source audio, music, title/subtitles and hostile markup/braces text. No render/build command was executed by this check. Earlier native compiler revealed missing required `hypit.project` marker; fixed by generating a trusted project package.json before the successful check. Native single-clip check also returned `hypit.cli-check@1`, `ok:true`,17 modules,32 outputs.

Additional checks passed: Python AST/byte compile of touched scripts/tests; workflow YAML parse and permission assertion; `git diff --check`; new job dispatch dry-run; existing real-project asset preflight (5 local assets, 50.10 MiB) without network/mutation.

## Complete farm residual register

### Review correction evidence (2026-10-27)

Independent review found a conflicting FAILED callback after a lost SUCCEEDED acknowledgement and an upload limit exceeding Free Worker ingress. Seven new regression cases failed before correction and then passed. The runner now emits `RESULT_DELIVERY_UNKNOWN` without a contradictory callback; exact-run reconciliation remains authoritative. It checks final ≤95,000,000 bytes, preview ≤20,000,000 bytes, and JSON ≤1,000,000 bytes before hashing or network upload. Final mastering now encodes bounded H.264 (3,800k final / 600k preview) plus AAC without truncating duration; actual artifact QA still follows encoding. The workflow's `run-name` is `clipper:<job_id>` for exact reconciliation.

Final lightweight suite: **37 passed, 1 skipped** (`test_secure_runner.py`, `test_render_cloud.py`, `test_ingest.py`). The optional compiler check was already executed successfully before this fix; encoding/callback changes do not alter its template. No local render was run. This is correction evidence, not a live acceptance or independent clean-review claim.

1. Push/release/deploy exact reviewed farm and backend refs only through separately authorized activation; no remote state was changed here.
2. Provision and validate private R2/D1/configuration plus account-wide free-tier usage evidence. Worker must HOLD unknown or insufficient quota before dispatch.
3. Live GitHub OIDC pilot: exact repository/ref/workflow/run-ID checks, auth failure, stale revision, duplicate result, cancelled job and upload receipt readback.
4. Actual cloud analysis/preview/final pilot: cold Ubuntu dependency installation, pinned browser preparation, full physical MP4 and audible/mobile review. Local compiler/fixtures do not prove these outcomes.
5. Worker must reconcile exact GitHub run on external cancellation/timeout; runner callback can be interrupted. Preserve partial receipts and do not redispatch automatically.
6. GitHub concurrency allows one running/one pending and may cancel an older pending run. Authoritative backend should serialize dispatch/reserve one active render, reconcile cancelled pending runs and maintain residual history.
7. Current v1 supports video clips with source audio and optional audio music. Images, generated narration, semantic AI moment selection, WhisperX alignment/karaoke, automatic face tracking and creative certification are not implemented here and must remain explicit unsupported/HOLD capabilities if requested.
8. Human rights/source/creative review remains necessary. Publication approval and Buffer scheduling belong exclusively to the backend’s separate authenticated approval flow; reward claims stay manual. This runner cannot publish.
