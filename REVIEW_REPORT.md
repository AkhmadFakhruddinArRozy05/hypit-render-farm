# Independent secure-farm review

Reviewed 2026-10-27 WIB against baseline `c74d6fd22f7a7bddba1820a34d5af3c20b27242e` and Clipper's approved `docs/superpowers/plans/2026-10-26-clipper-chat-cloud-implementation.md`. Scope: workflow, runner, dispatch helper, regression tests, implementation report, and pinned local Hypit source. No implementation edits, dispatch, render, transcription, dependency installation, deployment or publication performed. This report is the sole intended mutation.

**Verdict: REQUEST CHANGES — two important correctness issues; no confirmed critical execution/SSRF bypass in the inspected bounded-input path.** The local implementation substantially follows the specified security and evidence model. Production/cloud acceptance remains pending independently of these findings.

## Important findings

### F1 — A lost success acknowledgement sends a contradictory FAILED result

**Location:** `scripts/cloud_runner.py:441-448`, also analysis success at line 407 and response parsing at lines 183-187.

`client.result(SUCCEEDED)` is inside the same broad exception handler as media execution. If the Worker commits SUCCEEDED but the response is lost, times out, or cannot be parsed, the runner posts FAILED for that same job/run and exits with `RUNNER_FAILED`. Even if the backend correctly rejects the terminal-state overwrite, GitHub now reports failure for an actually accepted artifact; a reconciler must distinguish this from genuine processing failure. A backend that accepts the second callback can incorrectly erase success. This is an uncertain write outcome, not evidence that rendering failed.

**Reproduction performed:** invoked `main()` with a fake client and temporary directory, no media processing/network. The fake success callback recorded acceptance then raised `TimeoutError`. Observed callback statuses were exactly `['SUCCEEDED', 'FAILED']`. Analysis exercises the same exception path used by preview/final.

**Required change:** separate execution failure from final-result delivery. Once attempting a terminal success callback, preserve its exact payload/receipts and classify a missing acknowledgement as delivery unknown; reconcile the exact job/run, or replay only the identical payload under an explicitly idempotent backend contract. Never convert an ambiguous success write into FAILED. Add a regression for both successful server commit with lost response and rejected callback; ensure immutable backend terminal status. Do not redispatch rendering automatically.

### F2 — The single-request upload allowance exceeds the planned free transport limit

**Location:** `scripts/cloud_runner.py:170-177`; output preparation at lines 226-228 and runtime template at lines 306-308 impose no final byte budget.

The runner accepts an artifact up to just under 500 MiB and streams it as one PUT through the Worker origin. The prescribed no-new-paid-services deployment cannot assume that allowance: Cloudflare's documented Free/Pro request-body maximum is 100 MB, and oversized requests receive HTTP 413 before application handling. Streaming does not bypass the ingress limit. A valid high-entropy 1080p/180-second CRF-encoded artifact can exceed this threshold, pass all QA, consume the render budget, and then fail delivery despite being inside the runner's stated limit.

**Evidence/reproduction condition:** any physical artifact above the verified origin request limit but below 500 MiB passes this local size check and is sent as one request. No large dummy file/upload was generated. The platform limit was verified live in [Cloudflare Workers request and response limits](https://developers.cloudflare.com/workers/platform/limits/#request-and-response-limits) (100 MB Free/Pro; request limit follows Cloudflare account plan, not Workers plan).

**Required change:** align farm/backend admission and output budgeting to a conservative proven single-request byte cap, with explicit HOLD/error before an unsupported transfer and bounded encoding when the supported output duration requires it. Alternatively implement an authorized private multipart/chunked transfer contract with independently verified final SHA/size. Do not solve by assuming paid capacity. Test exact boundary rejection and a successful representative artifact below the chosen bound.

## Reviewed contract and quality

- Input/execution boundary: sole workflow input is a bounded job ID; API origin is trusted configuration; fixed native template uses bounded numbers/plain escaped text and verified local SHA asset paths. User code/packages/templates/URLs are not executed. Subprocess calls use argv lists. FFmpeg protocol/container allowlists block ordinary HTTP/HLS/concat fetching from disguised uploaded media. This review does not certify the absence of native decoder vulnerabilities.
- Private identity: fresh OIDC token for each request, fixed audience, no authenticated redirects, explicit job/run equality, integrity-checked downloads and receipt-bound uploads are present. Repository/workflow/ref/stale/cancelled checks intentionally belong to the backend; they need integration review and live negative tests before acceptance.
- Exact IDs: direct dispatch requires explicit API 200 and exact response run URLs, no latest fallback; actual Hypit build response ID is used for export. Pinned Hypit `packages/cli/src/main.ts:318-342,421-424` confirms preflight, automatic runtime execution startup and the `hypit.cli-build@1` response structure. `programs prepare` is appropriate for the fixed local HTML browser, as documented in pinned `packages/provider-html-local/README.md`. No cold-run bootstrap defect was established by source inspection.
- QA evidence: actual produced MP4 is probed, both streams decoded, audio measured and physical SHA recorded. Semantic analysis/transcription/editorial certification are explicitly absent rather than fabricated. Existing tests predominantly exercise fixtures and command structure; compiler-only success does not demonstrate rendered pixels/audio.
- Failure/retry: exact dispatch ambiguity is preserved without retry. Preflight/setup failures, workflow timeout/cancellation and interrupted callbacks still require durable backend reconciliation. Workflow global concurrency can replace an older pending job; backend admission/serialization must account for this. These are integration acceptance requirements, not a claim that the unfinished backend already violates them.

## Verification and residual acceptance gates

Independently executed only the lightweight mocked callback reproduction above. Read the implementation's recorded 30-test/optional compiler evidence; did not rerun or represent that record as an independent full render verification. Local Hypit HEAD verified equal to `1efedb925d375686ab107b6e67f914dced7b102b`. Native plan tool was not exposed to this reviewer; progress: [100%] inspect, [100%] source/reproduction review, [100%] report, [0%] external acceptance.

Still required after findings are resolved:

1. Backend/farm integration tests for exact repository/ref/workflow/run and revision authorization, cross-job assets, duplicate result, terminal-status immutability, upload readback, cancelled/stale jobs and result-delivery uncertainty.
2. Account-wide free capacity evidence, private R2/D1 provisioning/configuration, approved immutable refs and separately authorized activation.
3. A genuinely cold Ubuntu Actions pilot through dependency installation, browser preparation, actual analysis/preview/final, private object readback SHA/size, and full MP4/audio/mobile/editorial review. The absence of this live pilot is a retained acceptance gate, not itself a local implementation defect.
4. Exact-run reconciliation for dependency/provisioning failures, timeout/cancellation, failed callbacks and pending-run replacement, preserving partial receipts without automatic redispatch.
5. Explicit unsupported-capability reporting for images, generated narration, semantic moment selection, WhisperX alignment/karaoke and automatic face tracking.
6. Human rights/source/creative gates; isolated in-ChatGPT publishing approval pilot and downstream platform tests remain outside this runner. Publishing stays disabled until those gates are satisfied; reward claims remain manual.
