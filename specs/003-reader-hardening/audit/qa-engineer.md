# T032 project-audit — qa-engineer (003-reader-hardening)

- Range: `master` (0cfae45) ... `HEAD` (5636047), branch `003-reader-hardening`. Date: 2026-11-11.
- Mode: read-only. Nothing was edited except this file. No media was created or run; reasoning is from code, config and
  the existing test suite only (FR-017).
- Inputs read in full: `src/stt/server/{reader,reader_client,worker}.py`, `src/stt/audio_decode.py`, the touched parts of
  `src/stt/server/{intake,cli,layout,records,engine}.py`, `nix/{module,module-eval,package}.nix`, the `nix/vm-test.nix`
  diff, `flake.nix`, the `docs/server-security.md` diff, `tests/test_server_security_doc.py`, the new and edited tests,
  spec.md, plan.md, data-model.md, contracts/{reader,operator-log,module}.md, research R6, the 002 audit Low items that 003
  claims to close, the final-review report and the deferred-minor ledger. PyAV 19.0.2 sources in `.venv`
  (`av/codec/context.py:414-440`, `av/container/input.py:26-87,170-240`) to confirm both whitelist layers.

## Evidence run

- Full suite at HEAD (5636047, which changed `docs/server-security.md` after the last recorded run at ccbccba):
  `captest 4G 20m uv run --locked python -m pytest -q -p no:cacheprovider --basetemp=/tmp/audqa1` →
  **1338 passed, 2 skipped in 200 s, peak 541 MiB** (Python 3.12, av 19.0.2). basetemp removed afterwards.
- No linter is configured in the repo or CI. An AST unused-import scan over the four changed source files and the new
  test files found nothing. No circular import added (`reader_client` → `reader`; `reader` imports only the standard
  library at module level, new: `signal`).
- VM test not run (hard rule); reasoned from `nix/vm-test.nix` and the recorded GREEN run at ccbccba. HEAD differs from
  ccbccba only in docs/contracts; the strings the VM hardcodes (`TRUNCATION_LINE`, `MANUAL_RUN_LINE`, `TRUNCATION_CHECK`)
  are unchanged.

## T032 focus items — results

| Focus | Result | Evidence |
|---|---|---|
| Two-layer list on every server open path | Holds | `reader._open_container` (`src/stt/server/reader.py:359-362`) is the only open in `_probe` (`:381-388`) and `_decode` (`:391-395`); it always passes `codec_whitelist=AUDIO_DECODERS`. Layer 1: `container_options["codec_whitelist"]` reaches `avformat_find_stream_info` through `AVFormatContext.codec_whitelist` (`src/stt/audio_decode.py:54-61`). Layer 2: every stream's `CodecContext.options` (`audio_decode.py:62-65`), which PyAV 19 copies into the dictionary it hands to `avcodec_open2` (`av/codec/context.py:420-438`); codec contexts are allocated but not opened by `av.open` (`av/container/input.py:66-87`), so the option is in place before any decode. Streams that appear after `find_stream_info` are never decoded by PyAV (`input.py:212-218`). Re-transcription has no probe (`intake._accept_second_pass`, `intake.py:296-318`) and decodes through the same `_decode`. Probe adds the name check `first_audio_decoder_allowed` (`reader.py:365-378,383-384`). RED-capable tests for each layer: `video_streams_unopened` (`tests/server/test_hardening_us1.py:134-148`, `format is None` + `ArgumentError`) and `test_hardening_us1_5_the_decode_child_ends_with_status_1…`, `…refused_when_received`. |
| Nowhere on the CLI | Holds | `decode_samples` → `open_container(str(path))` without a whitelist (`audio_decode.py:132`); the only other `av.open` in `src/` is that same function. `transcribe.py:780` is the only caller. Pinned by `test_hardening_fr_5_the_command_line_decodes_a_codec_outside_the_list`. |
| Intake/worker never import PyAV | Holds | Top-level imports of `intake`, `worker`, `reader_client`, `reader`, `layout`, `records`, `lifecycle`, `deadlines`, `gpu_lock` are standard library or `stt.server.*`; `reader` loads `stt.audio_decode`/`av` only inside child functions. Guarded by `tests/server/test_decoder_us2.py:236-280` (runtime `sys.modules` + AST), green. |
| Cause-name set closed | Holds | Suffix is written only for `failure.reason == "engine_error"` (`worker.py:288`). Every `engine_error` settle carries a cause from the five literals: read stage `worker.py:354-360` (each branch that yields `unavailable` → `_READER_NOT_STARTED` sets `cause`), engine stage `worker.py:376-378`. The other two `_settle` calls (`:372`, `:380`) pass `_TIMEOUT` (`processing_timeout`). Set pinned by `test_hardening_us3_4_the_cause_names_are_the_five_of_the_contract` and the doc test. |
| No new line leaks a request id, path or number | Holds | New lines: `TRUNCATION_LINE` (constant, `worker.py:56,154`), `MANUAL_RUN_LINE` (constant, `reader.py:57,326`), the cause word (closed set), intake `intake error RecordingError` / errno name via `describe_error` (`layout.py:119-123`). `RecordingError` drops the `filename` of the original `OSError` (`reader_client.py:129`). PyAV's error text for a refused decoder (`avcodec_open2("h264", {…})`) and FFmpeg's log stay inside the decode child, whose stdout/stderr are `/dev/null` (`reader.py:159-166`) and which swallows the exception (`reader.py:412-417`). |
| VM decoder watch cannot leak into production | Holds | The only reference is `nodes.server.systemd.services."stt-reader@".environment.LD_PRELOAD` in `nix/vm-test.nix`. `nixosModules.default` imports only `./nix/module.nix` (`flake.nix:70-73`), which has no `LD_PRELOAD`/watch reference (grep). `audio`/`decoderWatch` are extra attributes on the `checks.…server-vm` derivation only. |

## L3 — both sides of each boundary

| Producer → consumer | Stage 1 (signature) | Stage 2 (enabling argument not left at None/default) |
|---|---|---|
| `audio_decode.open_container(…, codec_whitelist=None)` → `reader._open_container` | match | `AUDIO_DECODERS` passed explicitly; the `None` default is reached only by the CLI, as FR-005 requires |
| `reader.first_audio_decoder_allowed(container)` → `reader._probe` | match | called before `container_duration_s`; false → `ValueError` → exit 1 → `failed` |
| `reader_client.RecordingError(OSError)` → `worker._run_job` | match | caught before the `OSError` base (`worker.py:356-360`) |
| `reader_client.RecordingError` → `intake.run_once` | match | falls to the per-request `except Exception` (`intake.py:413-418`, `except Exception` at `:415`); errno name or `RecordingError`, request removed (001 FR-032a unchanged) |
| `worker._read(…, probed_s)` ← `_run_job` | match (new required positional) | `job.duration_s` (always set by intake: `intake.py:258`, `:311`) |
| `worker._settle(…, cause=None)` ← 4 call sites | match | every `engine_error` site passes a cause (above) |
| `worker.DEFAULT_ENGINE_CMD` (`-I`) → `cli._work` | match | `cli.py:123-130` passes the constant, not a local literal |
| `reader.DEFAULT_CHILD_COMMAND` (`-I`) → `reader.serve` ← `cli._read` | match | `cli.py:112-115` calls `serve()` with the default |
| `_prepare` recording path ↔ `intake._copy_attachment` | match | both `audio.<attachment_name.rsplit(".",1)[1]>`; `original_name = attachment_name` (`intake.py:240,266`); schema allows lowercase extensions only (`layout.py:20`, `request.schema.json:55`) |
| Code constants ↔ `docs/server-security.md` markers | match | doc tests (`tests/test_server_security_doc.py:422-468`) green at HEAD |
| Code constants ↔ `nix/vm-test.nix` copies | match | literal copies of `TRUNCATION_LINE`/`MANUAL_RUN_LINE`; a drift fails the VM loudly, not silently |

## Discovery

### Critical (blocks deployment)

None.

### High (must fix)

None.

### Medium (should fix)

None.

### Low (information)

- **[L2/L5] `docs/server-security.md:163`, `specs/003-reader-hardening/contracts/operator-log.md:37` vs
  `specs/003-reader-hardening/data-model.md` §3 and `src/stt/server/worker.py:361` — the operator doc and the contract say
  "any read-stage failure/exception never stops the worker"; the design and the code do not hold that, and one listed
  cause row is unreachable.**
  - The data model (§3) and research R6 decide that only `OSError`/`ValueError` become `unexpected` and anything else
    rises as in 002; `test_hardening_edge_6_an_error_that_is_neither_os_nor_value_error_rises_out_of_the_worker`
    (`tests/server/test_hardening_us3.py:334-354`) pins that the worker stops. The doc sentence
    「읽기 단계의 어떤 실패도 워커를 멈추지 않는다」 and contract §2 「읽기 단계의 어떤 예외도 워커를 멈추지 않는다」 are therefore broader than the
    behaviour.
  - Scenario (fd exhaustion, listed as `recording_unusable` 「기술자 부족」 in `docs/server-security.md:158`,
    `contracts/operator-log.md:31`, `data-model.md:76`): when the worker's descriptor table is full, `os.open` of the
    recording fails `EMFILE` → `RecordingError` → `recording_unusable` is assigned (`worker.py:356-357`), but the
    follow-up `_is_running` at `worker.py:361` sits outside the `try`, and `hold()` opens a new descriptor
    (`layout.py:162-174`), so it raises `EMFILE` again and the exception leaves `_run_job`; the job stays `running` and
    becomes `failed interrupted` at the next start. No `failed engine_error recording_unusable` line is written. The same
    holds for a persistently unreadable `job.json` listed under `unexpected` (「작업 기록 읽기」, `docs/server-security.md:159`):
    the re-check and `_settle` both need `load_job`. The `pipe2` EMFILE test passes only because the recording fd was
    closed before the re-check.
  - Reasoned from code; not executed (would need a lowered `RLIMIT_NOFILE` harness). Behaviour equals 002 for these
    cases and is not reachable without an fd leak or a corrupt record, hence Low. Fix is wording, not code.
- **[L5] `docs/server-security.md:158` — the `recording_unusable` row tells the operator to look at
  `/var/lib/stt/audio/<audio_id>/`, but the log line carries only `<request_id>`.** For a first-pass job the two are equal
  (`intake.py:254`); for a second pass the job uses the source's `audio_id` (`intake.py:307`), so following the line
  literally leads to a folder that does not exist. One clause (「2차 작업은 `jobs/<request_id>/job.json` 의 `audio_id`」)
  closes it.
- **[L4] UNVERIFIED pre-deploy: the engine under `-I` in the full `server` closure (with the diarization stack).** The VM
  exercises `python -I -m stt.server.engine` only in `server-light` (B-003-2 jobs reach `gpu_unavailable`, so the module
  imports and runs), and `checks.server-smoke` (`flake.nix:75-84`) imports without `-I`. Both closures are uv2nix
  virtualenvs (`nix/package.nix:96-102`), whose site-packages `-I` keeps (it drops only the current folder, user site and
  `PYTHON*` variables, none of which the units set — `nix/module.nix` has no `PYTHON*` environment), so the risk is
  negligible. The first real run is C-003-2 at the host; optionally add `-I` to the server-smoke `python -c` line.

Already tracked elsewhere, not counted here: `docs/server-security.md:361` 「형식별 시험이 이를 잡는다」 is not true for
`wmapro`/`wmavoice`/`wmalossless`/`amrwb` (final review, carried to the T033 doc commit); `_settle(cause: str | None)` not a
required `Literal` (U04 deferred #21); truncation `print` inside the read `try` (U05 deferred); `_open_recording` fd leak
if `fstat` fails (U04 deferred #23).

### Layer notes

- **L1** clean (see Evidence run). **L5** new constants, docstrings (Google style), log strings and test names follow the
  existing patterns; no Hangul in code/tests/nix.
- **L2** contracts match the code: `open_container` signature and semantics (contracts/reader.md §2), `AUDIO_DECODERS`
  (32 names, identical to data-model §1 and the doc table), `MANUAL_RUN_LINE`, `DEFAULT_CHILD_COMMAND`/`DEFAULT_ENGINE_CMD`
  with `-I`, `RecordingError` errno semantics (`ELOOP`/`ENOENT`/None, pinned in `tests/server/test_reader_client.py`),
  `is_truncated` boundary (`>=`), `TRUNCATION_THRESHOLD_S = 1.0`, `ENGINE_ERROR_CAUSES`, the module assertion text and
  `module-eval` checks (contracts/module.md §1-§2). Only inconsistency: the Low above.
- **L7** `O_NONBLOCK|O_NOFOLLOW|O_CLOEXEC` open then `fstat` on the same descriptor (no TOCTOU); access mode stays
  `O_RDONLY` for the supervisor's `_descriptors_fit` (`reader.py:139`); `O_NONBLOCK` has no effect on regular-file reads
  in the child. `audio.wav` is 0600 from `O_EXCL` creation to removal and never reopened by path (`worker.py:111-117`);
  `wave` does not close a passed file object, so the header patch precedes the outer close. SIGINT is ignored before the
  child exists (`reader.py:323`); the inherited `SIG_IGN` in the child is harmless (all kills are SIGKILL). No shell, no
  secrets, no new network or subprocess without a bound.
- **L6** adversary results were not available when this report was written (parallel, one-way); L6 integration skipped.
  The 002 adversary items 003 claims to close were checked against the code: L1 (SIGINT), L5 (systemd ≥ 257 assertion +
  `module-eval`), L6 (`-I`), L8 (manual-run line), L9 (cause word), L11 (codec allowlist) — all closed; 002 qa L1 (worker
  stopped on `ValueError`/`StopIteration`/FIFO hang) and L3 (0640 re-creation) closed; qa L2 (the reader sentence is false
  for post-`started` I/O errors) is kept by design (contract unchanged) and now distinguishable by `unexpected`.
- **L4** End to end: intake probe (list + name check) → `job.duration_s` → worker `_prepare` (deterministic path) →
  decode (list) → `audio.wav` (0600, same fd) → truncation check (before the GPU stage, at most one line) → engine (`-I`) →
  verdict → `_settle` (cause) → `finally` removes the work folder on every path. Delete/purge races on any non-ok read
  outcome become `stopped` (`worker.py:361-366`). Deploy: reader instances pick up new code per connection, the old
  worker keeps old lines until it exits (documented MainPID check). A job accepted by 002 with an out-of-list codec fails
  `unreadable_audio` at decode (documented, tested by `test_hardening_us1_5_a_job_accepted_before_the_list_fails_when_processed`).

## Plan

1. L-doc-1 (absolute "never stops the worker" claim and the unreachable fd-shortage row) → reword
   `docs/server-security.md:158,159,163` and `contracts/operator-log.md:31,37` (and `data-model.md:76`) to say: only
   `OSError`/`ValueError` in the read stage are classified; other exceptions, and an error that also breaks the job-record
   re-check or settle (descriptor exhaustion, an unreadable `job.json`), stop the worker as in 002 and the job ends
   `interrupted`. No code change. → affected test: `tests/test_server_security_doc.py` (markers unchanged; re-run to
   confirm green).
2. L-doc-2 (`<audio_id>` vs `<request_id>`) → one clause in the same table row; can ride with item 1 in the T033 doc commit.
   → affected test: `tests/test_server_security_doc.py`.
3. L-verify-1 (`-I` engine in the full closure) → no change needed; confirm at C-003-2. Optional: add `-I` to the
   `server-smoke` import line. → affected check: `checks.x86_64-linux.server-smoke`.

Order: 1 and 2 together (same table), 3 independent. None blocks T033.

### Not fixed (reason)

- The underlying behaviour behind L-doc-1 (re-check outside the `try` at `worker.py:361`): unchanged from 002, reachable
  only with an fd leak or a corrupt record, and the fail-fast choice is a recorded design decision (data-model §3,
  research R6); changing it would be a separate spec item.
- Tracked minors listed above: already owned by the final review / deferred ledger.

## Verdict

PASS — Critical 0, High 0, 경계면 불일치 0건, 통합 호출 누락 0건
(Medium 0, Low 3: two documentation-accuracy items and one UNVERIFIED-but-negligible pre-deploy check.)
