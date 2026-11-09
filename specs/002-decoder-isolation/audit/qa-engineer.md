# T040 audit: qa-engineer (7-layer: module boundaries, integration, E2E)

- Scope: 002 "decoder isolation", `master` (be72910, P1 deployed) ... HEAD `5b8220f` on `002-decoder-isolation`, plus the P1 commits
  already on master (`871291b`..`be72910`). I read both sides of every boundary: intake/worker → `reader_client` → reader socket protocol
  and fd passing → supervisor (`reader.serve`) → decode child → `stt.audio_decode`; worker → `audio.wav` → engine → `transcribe_samples` /
  `diarized_blocks_from_wav`; CLI `transcribe_file` → `audio_decode.decode_samples` → model; `nix/module.nix` units/socket → code paths and
  sandbox; `docs/server-security.md` tables → `tests/test_server_security_doc.py` → `uv.lock` / `flake.lock` / `nix/package.nix` (uv2nix).
- Method: read-only. No crafted or malformed media was created or run (FR-028). The NixOS VM test was not run; I reasoned from
  `nix/vm-test.nix` and `nix/vm-reader-double.py`.
- Test run (once, capped): `captest 4G 20m uv run python -m pytest -q -p no:cacheprovider` → **1164 passed in 159.69 s, captest peak
  516 MiB**, on Python 3.12.13 with av 19.0.2 (the production interpreter and wheel). common.md's command (`... -n 2`) does not work here:
  pytest-xdist is not installed, so pytest stopped with "unrecognized arguments: -n" (exit 4) before running any test.
  Python 3.10 (CI) was not run locally.
- L1: ruff/mypy/pyright are not in the environment. Instead I ran an AST scan for unused imports over every changed Python file in `src/`,
  `tests/` and `nix/`. It found none. The only Hangul in changed code is the two operator-approved error strings moved inside
  `src/stt/transcribe.py` (plan, Complexity Tracking).
- L6: the adversary report was not in `specs/002-decoder-isolation/audit/` when I wrote this, so its results are not used here.

## Discovery

### Critical (blocks deployment)

- None.

### High (must fix)

- None.

### Medium (should fix)

- **M1 [L4/L7] Decoding inside the real reader sandbox has been checked only for PCM WAV under FFmpeg 9. If the syscall filter blocked
  a codec, the job would just show `unreadable_audio`, and the operator guide would point to the wrong place.**
  - **Evidence, VM.** The VM feeds the real reader only `two-seconds.wav` (decoded), `over-three-hours.ogg` (probed and refused,
    never decoded) and the text file `corrupt.mp3` (`nix/vm-test.nix:20-27`, `:194`, `:472`, `:690`). Every other reader in the VM is
    the double.
  - **Evidence, R5.** The only sandboxed decode of a compressed format is R5: AAC on av 18, under `systemd-run --user`, before
    `~@keyring`, `PrivatePIDs`, `ProcSubset=pid` and `PrivateDevices` were added (`research.md:128-135`).
  - **Evidence, host plan.** C-P2-3 measures the 3-hour Opus/AAC decode with `captest`, outside the unit (`quickstart.md:139-140`). So
    MP3, Ogg Vorbis/Opus, FLAC and WMA (asf) never run through the deployed `stt-reader@` filter, MDWE and namespaces before go-live.
    T042's baseline recordings (m4a) will cover AAC only.
  - **Scenario.** A demuxer or codec path in FFmpeg 9.0.2 calls something outside `@system-service ~@privileged ~@keyring ~process_vm_*
    ~io_uring_* ~userfaultfd`. The child dies of SIGSYS, `_verdict` turns any non-zero code into `failed` (`src/stt/server/reader.py:203-209`),
    the supervisor still exits 0 (`reader.py:244`), and the job ends `unreadable_audio` with the fixed sentence
    (`src/stt/server/worker.py:46-52`).
  - **Why the operator misses it.** Every benign upload in that format is refused. `systemctl --failed` stays empty, because the reader
    unit ended normally. `docs/server-security.md:72-79` tells the operator that a filter kill shows up as a failed unit (SIGSYS 31), so
    the operator would blame the recordings.
  - **Requirements touched.** FR-023 (tell a unit fault from a job failure), edge case 7 (rare paths under the filter), SC-005/SC-007
    for those formats.
  - **Status.** UNVERIFIED that any format actually breaks. The risk is that the misclassification would be silent, not that it is likely.

### Low (information)

- **L1 [L2/L3] `src/stt/server/worker.py:297-301` vs `src/stt/server/reader_client.py:326-329`.**
  - `decode()` documents `ValueError` when the recording is not a regular file (`reader_client.py:120-122`). `_run_job` catches only
    `OSError`.
  - If it happened, the exception would leave `run()`, the unit would exit 1 with a traceback, and the job would stay `running` until the
    next worker start or sweep marks it `interrupted`.
  - It cannot happen under the trust model: `audio/<id>/audio.<ext>` is created only by `_copy_attachment` with
    `O_CREAT|O_NOFOLLOW` (`src/stt/server/intake.py:191-198`). The documented exception is simply not handled at the boundary.
- **L2 [L2/L5] `src/stt/server/worker.py:299-301`.**
  - Any `OSError` from the read stage becomes `unavailable`, which maps to `engine_error` with "The server could not start the audio
    reader." That includes errors after the reader said `started`, such as `_is_running` hitting an I/O error inside the `job_running`
    callback (`reader_client.py:226-229`).
  - The classification (server fault, not `unreadable_audio`) is reasonable. But the case is not in the failure map
    (`data-model.md` §5), and the sentence is false for it.
- **L3 [L7] `src/stt/server/worker.py:85-93`.**
  - `_write_audio` creates `audio.wav` 0600 with `O_EXCL`, closes it, then reopens it by path with `wave.open(str(path), "wb")`.
  - In the delete race, `lifecycle.finish` unlinks `audio.wav` while `rmtree` runs (`lifecycle.py:125-141`, ENOTEMPTY tolerated). The
    reopen then re-creates it with mode `0666 & ~UMask 0027` = **0640**, holding up to 363 MB of decoded audio for the seconds until
    `_run_engine` sees the job gone and the `finally` removes the folder (`worker.py:315, 322-323`).
  - Group `stt` has no other members, so nothing is exposed today. It still breaks the 0600 invariant in `data-model.md` §3.
- **L4 [L7/ops] `nix/module.nix:213-217`.**
  - The `InaccessiblePaths` entries have no `-` prefix. If `/var/lib/stt-models` (for example, removed by the operator to force a
    re-fetch) or `/run/stt` is missing, every `stt-reader@` instance should fail namespace setup (226/NAMESPACE).
  - The effect is that every read becomes `unavailable`: intake drops every attachment request with no response (001 FR-032a), and
    every job fails `engine_error`.
  - UNVERIFIED: this is reasoned from systemd's `InaccessiblePaths=` semantics, not run. Both folders are recreated by tmpfiles
    (`module.nix:143-144`), so this needs an operator action to happen.
- **L5 [L4] `src/stt/server/reader.py:36` (`MAX_DECODED_BYTES` = 3 h × 1.05).**
  - Intake accepts a recording by the container duration from the probe (`intake.py:217-230`). The worker rejects decoded output
    above the cap (`reader_client.py:251-252`).
  - A real recording longer than 3 h whose container duration under-reports it (for example, a VBR MP3 without a Xing header, where
    FFmpeg estimates the length from the bit rate) is accepted as ≤ 3 h. It then fails as `unreadable_audio` ("could not be read as
    audio") instead of `audio_too_long`. In 001 the same file was transcribed.
  - UNVERIFIED (reasoned). An edge case.
- **L6 [L3/VM test] `nix/module.nix:162-192`.**
  - The production worker unit has `huggingFaceTokenFile` set, so `fetchesModels=true` and its `wants`/`after` are overridden by
    `lib.optionalAttrs fetchesModels {...}`. It gets `stt-reader.socket` only because `needsReader` wraps outermost.
  - The VM asserts `Wants`/`After stt-reader.socket` and the `--reader-socket` `ExecStart` only on `server`, which has no token
    (`nix/vm-test.nix:387-396`). The production-shaped `fetcher` node asserts `stt-models.service` only (`nix/vm-test.nix:580-581`).
  - Correct today, but nothing guards against a reordering.
- **L7 [L4] End-to-end gap.**
  - No automated test has the real engine read an `audio.wav` that the worker wrote from a real reader decode. Worker runs use
    `fake_engine.py`.
  - The real-engine tests use `make_wav` files (`tests/server/test_decoder_us2.py:288-340`, `tests/server/test_engine.py:558-596`).
  - In the VM, the engine stops at the GPU check before reading the file.
  - The boundary is covered by matching header assertions on both sides (`fake_engine.py --record` and `engine._read_samples`).
    A full end-to-end run happens only on the host (T042).
- **L8 [L5] Constants duplicated outside `reader.py`.**
  - `contracts/reader.md` §4 says the protocol constants live once, in `src/stt/server/reader.py`. Copies exist elsewhere:
    `worker._SAMPLE_RATE_HZ`/`_SAMPLE_WIDTH_BYTES`/`_AUDIO_FILE` (`worker.py:38-40`), `engine.AUDIO_NAME` and the literal
    `(1, 2, 16000)` (`engine.py:31, 144`), and `audio_decode.SAMPLE_RATE` (`audio_decode.py:24`).
  - Only `audio_decode` ↔ `reader` is tied by a test (`tests/server/test_audio_decode.py:16`). Drift would be caught only indirectly,
    by the US2-5 header assertions.
- **L9 [docs] `docs/server-security.md:117`.**
  - It says the `uv.lock` entry for av 19.0.2 has the marker `== '3.12.*'`. The entry actually serves 3.12, 3.13 and 3.14
    (`uv.lock:317-326`), and the plan says "Python 3.12 이상".
  - The test matches by substring, so it passes (`tests/test_server_security_doc.py:283-289`). Only the wording is wrong.
- **L10 [traceability] `specs/002-decoder-isolation/tasks.md:211-212` (T021).**
  - T021 cites US4-7 evidence as "the existing CLI tests pass **unchanged**". T044 changed them: the empty `.m4a` fixtures became real
    silent WAVs (`tests/test_cli_integration.py:56-58`, `tests/test_cli_gpu_lock.py:72`, `tests/test_transcribe.py:323-324, 721`), and
    `FakeModel.transcribe` now takes `audio: object`.
  - The assertions were not weakened, and a new test checks that the model receives float32 samples
    (`tests/test_cli_integration.py:74-84`). Only the claim is stale.
  - FR-022 "CLI output unchanged" on Python 3.12 (av 19) rests on the same operator acceptance of the 7.75 ms AAC tail
    (2026-11-10, `docs/server-security.md:134-139`), which covers the server decode. The P2 reply should say it also applies to the CLI.
- **L11 [info] `tests/server/test_reader.py:862-877`.**
  - Byte equality of the decoded output with `faster_whisper.decode_audio` is asserted only where av ≤ 18 (Python 3.10 in CI).
  - On the production interpreter (3.12, av 19), lossy formats are checked only for length within 0.15 s and level.
  - Documented in `contracts/reader.md` §6. The evidence for production is the operator's s16 `cmp` of two baseline recordings.
- **L12 [info, accepted] Intake probes run serially.**
  - Each bad attachment holds intake for up to about 35 s, or about 65 s if the instance start stalls (`research.md:229-232`, decided
    2026-11-09). So FR-014 / 001 SC-011 can be exceeded for a request queued behind two such attachments.
  - At 17 or more hanging probes in one batch, intake reaches `TimeoutStartSec=10min` (`nix/module.nix:158`) and is killed. The rest of
    the batch is retried on the next trigger (the inbox entries stay, the copy is overwritten, and orphan folders go after an hour,
    `lifecycle.py:301-329`). It recovers by itself; reported only so the operator knows the bound.
- **L13 [info, by design R16]** `tests/test_server_security_doc.py:380-386` cannot detect a missing **current** month (no "today"), so
  the "no missing month" part of US5-4 / SC-009 is the operator's job, not CI's.

## Plan

1. M1: close the filter coverage gap by test, then document the symptom.
   - Fix: add sine fixtures to the `audio` derivation in `nix/vm-test.nix` (m4a/AAC, mp3, ogg/Vorbis, ogg/Opus, flac, wma/asf, all made
     with nixpkgs `ffmpeg -f lavfi sine`; FR-028-compliant).
   - Fix: add a B-P2 subtest that probes and decodes each one through the base-configuration reader. Assert `{"started": true}`,
     `ok`, an even non-zero output, and an empty `journalctl -k -g type=1326`. Running the real `reader_client` as `stt` against
     `/run/stt-reader.sock` is enough; no job is needed.
   - Fix: in `docs/server-security.md` §3, add that a filter kill of the reader's decode child shows as `unreadable_audio` on the job,
     not as a failed unit, and that `journalctl -k -g 'type=1326'` is where to look when benign recordings are refused.
   - Fix: in T042, run one benign file per allowed format through the deployed unit.
   - Affected tests: `nix/vm-test.nix` (new subtest), `checks.x86_64-linux.server-vm`.
   - RED → GREEN: the subtest fails first if the base reader is replaced by a double that refuses decode.
2. L4: choose fail-closed or fail-open.
   - Option (a): prefix `-` on the two optional paths (`-/var/lib/stt-models`, `-/run/stt`). A missing path has nothing to hide.
   - Option (b): keep fail-closed and add a line to §3: "if every read fails `engine_error` and intake drops requests, check that
     `/var/lib/stt-models` and `/run/stt` exist."
   - Affected test: `nix/vm-test.nix:375` (the exact `InaccessiblePaths` set), plus `contracts/module.md` §2.2 / §4.
3. L3: write the WAV through the descriptor opened with `O_EXCL`, e.g. `os.fdopen(fd, "wb")` passed to `wave.open`, and never reopen by
   path. Affected tests: `tests/server/test_decoder_us3.py::test_decoder_edge_5_...` (it patches `wave.open`, so keep that seam) and
   `tests/server/test_us1.py::test_running_job_has_spec_in_private_work_folder_and_no_marker` (mode 0600).
4. L1 + L2: these two can be fixed together.
   - Fix: in `_run_job`, treat `ValueError` from `reader_client.decode` like the `OSError` path (a server fault; the "stopped" check is
     already there).
   - Fix: either add a row to `data-model.md` §5 for "the caller failed after start", or use a sentence that does not claim the reader
     failed to start. A new detail sentence is visible in the response, so it needs operator sign-off (FR-020); updating only the doc
     is enough.
   - Affected test: `tests/server/test_decoder_us3.py::test_decoder_us3_8_an_os_error_before_anything_is_sent_...`. Extend it with a
     `ValueError` case and an OSError raised from `job_running` after `started`.
5. L6: on the `fetcher` node, assert that `stt-worker.service` has `stt-reader.socket` in `Wants` and `After` and `--reader-socket
   /run/stt-reader.sock` in `ExecStart`. Affected test: `nix/vm-test.nix:580-581`.
6. L9 + L10: documentation only.
   - Fix: `docs/server-security.md:117` → "19.0.2 serves Python 3.12 and later (`3.12.*`, `3.13.*`, `>= 3.14`)".
   - Fix: `tasks.md` T021 → US4-7 evidence = the CLI tests (fixtures adapted in T044) plus
     `test_main_gives_the_model_decoded_samples_not_a_path`.
   - Fix: `handoff/p2-reply.md` (T043) → say the 2026-11-10 acceptance of the AAC tail covers the command line too.
   - No test change.
7. L8 (optional): import `SAMPLE_RATE` from `stt.server.reader` in the worker and the engine, or add a test that ties
   `worker._SAMPLE_RATE_HZ`, the engine's header tuple and `reader.SAMPLE_RATE` together. Behaviour does not change.

Order and dependencies: M1 (test first) → L4 (touches the same VM subtest file and the §3 doc section as M1; do them together) → L3 →
L1+L2 → L6 → L9/L10/L8. None of them depends on another fix in code. M1 and L4 both edit `nix/vm-test.nix` and §3 of
`docs/server-security.md`, so one implementer should do both.

### 미수정 (사유)

- L5: a behaviour edge outside the formats the caller sends today. Fixing it would mean re-deciding the 3-hour check (decoded length
  versus container length), which is a spec question, not a fix in this change. Recommend recording it in the P2 reply as a known edge.
- L7: an end-to-end run with the real engine needs a GPU. It is closed by host check T042 (C-P2-5), not by CI.
- L11: equality with faster-whisper cannot be tested on av 19, because faster-whisper 1.2.1's decoder does not run on it. Documented
  in `contracts/reader.md` §6. The operator's `cmp` is the evidence.
- L12: accepted by the operator on 2026-11-09 (`research.md:229-232`). It recovers by itself.
- L13: by design (R16: CI never reads today's date). It is part of the operator's monthly routine.

## Verdict

PASS — Critical 0, High 0, boundary mismatches 0, missing integration calls 0
(FAIL tags: none. BOUNDARY_MISMATCH 0, INTEGRATION_MISSING 0, RULE4_VIOLATION 0, SILENT_SKIP_BOUNDARY / SILENT_SKIP_NEW 0.)
Medium 1 (M1), Low 13 (L1–L13, of which L11–L13 are information or accepted). I recommend closing M1 before T041, or at the latest as
an explicit step of T042 with the operator before bhu-llm opens to real users.

How the zero counts were established:
- **Stage 1, signatures.**
  - `intake.run_once(layout, now, reader)` and `worker.run(..., reader=)` are required, with no default. `stt.server.cli` passes
    `args.reader` (`src/stt/server/cli.py:121-139`), and its default goes through argparse `type` and becomes
    `ReaderConfig("/run/stt-reader.sock")` (`tests/server/test_server_cli.py:462-470`).
  - `transcribe_samples` and `diarized_blocks_from_wav` match their engine call sites (`engine.py:178-193`).
  - `open_container` and `write_s16` match both callers (`reader.py:311-328`, `audio_decode.py:106-107`).
- **Stage 2, feature-enabling arguments.**
  - The only argument that could silently turn off isolation is `open_container(format_whitelist=None)`. The reader child always
    passes `AUDIO_FORMATS` (`reader.py:314`), and this is enforced by
    `test_reader.py::test_a_format_outside_the_list_fails[_even_with_a_listed_extension]`.
  - `reader.serve`'s default `child_command` is the real child.
  - Nothing that enables a feature is left as None or empty.
- **Orchestrators.**
  - `stt/server/cli.py` wires `read`, `--reader-socket`, `intake` and `work`.
  - `nix/module.nix` adds `stt-reader.socket`/`@.service` and adds `--reader-socket` plus `wants`/`after` to intake and the worker.
  - No server path still decodes or reads recording bytes: intake and the worker never load `av` (`test_decoder_us2.py:257-286`), and the
    engine reads only `audio.wav` without `ffmpeg` (`test_decoder_us2.py:288-340`).

## Appendix: acceptance scenarios and FRs claimed closed in tasks.md, and the test that exercises each

| Claim | Test that exercises it | Status |
|---|---|---|
| US1-1, US1-2 | VM "isolation settings" (`vm-test.nix:335-339`, four units on two nodes); host T009 (`handoff/p1-reply.md`) | covered |
| US1-3, US1-4 | `tests/test_server_security_doc.py:155-185` (module ↔ `stt:syscall-table`, `added`, `narrowed`, `mdwe`) | covered |
| US1-5, US1-6, edge 7 | host T009 (operator, P1 reply); VM B-P1-3 with positive control (`vm-test.nix:590-596`) | covered (host) |
| US2-1–4, US2-6 (VM), edge 2 | VM B-P2-4 (`vm-test.nix:705-880`): fake NVIDIA nodes, decoys, A / B / A′, overlapping probe, UID ≠ stt, `visible_pids == 1` | covered (VM) |
| US2-5, SC-003 | `test_decoder_us2.py:113-128, 257-340`, `test_engine.py:558-596`, VM B-P2-1/2 (`vm-test.nix:663-682`) | covered |
| US2-6 | `test_decoder_us2.py:131-147` (two connections, one recording fd each) | covered |
| US2-7 | `test_reader.py:390-512` (hang-up, limit, group kill), the conftest teardown leftover assertion, VM `no_reader_instance()` after every subtest | covered |
| US2-8 | `test_decoder_us2.py:150-169` (< 10 s with the real supervisor); host T042 pending | pytest only, host pending |
| US2-9, edge 3 | host T042 (C-P2-3/6) | pending (by plan) |
| US3-1, SC-004 | `test_reader.py:774-830`, `test_decoder_us3.py:125-139`, VM B-P2-3 (`vm-test.nix:684-703`) | covered |
| US3-2 | `test_decoder_us3.py:149-171` (error, SIGKILL, hang, wrong result); VM B-P2-5 | covered |
| US3-3–6, SC-005 | `test_decoder_us3.py:213-273` (6 failure modes + next job done); VM B-P2-5 (no-verdict, hang, real cgroup OOM, not-started) | covered (memory cap: SIGKILL child in pytest, real OOM in VM) |
| US3-7 | `test_decoder_us3.py:276-299` (GPU classifications, `fail=unreadable_audio` → NO_VERDICT) | covered |
| US3-8 | `test_decoder_us3.py:302-343, 500-525`; VM `reader-not-started` | covered |
| US3-9 | `test_decoder_us3.py:346-367` (raw decoder text in no response or log) | covered |
| edge 4, edge 5 | `test_decoder_us3.py:370-469` | covered |
| US4-1, SC-006 (schema bytes) | `tests/server/test_contract.py::test_packaged_schema_matches_contract_bytes`; `git diff master...HEAD` touches no `schemas/*.json` or 001 contract JSON | covered |
| US4-2, US4-3, edge 6 | `test_decoder_us4.py:159-186, 365-385`; host T042 pending | covered (pytest) |
| US4-4, US4-5, US4-6, SC-008 | `test_decoder_us4.py:189-362`, `test_decoder_us3.py:472-497`; VM B-P2-6 (`vm-test.nix:974-1029`) | covered |
| US4-7, FR-022 | CLI tests (fixtures adapted, see L10), `test_transcribe.py:755-790`, `test_diarize.py` `TestDiarizedBlocksFromWav`, `test_audio_decode.py` | covered (claim wording stale) |
| US5-1–5, SC-009 | `tests/test_server_security_doc.py:344-419` (record side; the current month cannot be checked, L13) | covered (record side) |
| FR-006 (incl. probe), FR-010 | `test_decoder_us2.py:257-286` (no `av` in intake/worker, constants only), the engine without ffmpeg test | covered |
| FR-007, FR-008, FR-011 | VM B-P2-4 / B-P2-5 / B-P2-6 | covered (VM) |
| FR-009 | pytest limit tests, VM memory and hang doubles; host C-P2-3 pending | covered + pending |
| FR-012, FR-013 | SC-008 tests above; VM settings `MemorySwapMax=0`, `LimitCORE=0`, `LimitFSIZE=0` (`vm-test.nix:367`) | covered |
| FR-019 | `test_decoder_us3.py:346-367`; VM B-P2-7 (`vm-test.nix:1031+`) | covered |
| FR-023 | `docs/server-security.md` §3 | covered, except the reader-child SIGSYS case (M1) |
| FR-027 / SC-010 | `handoff/p1-reply.md` (P1); P2 reply = T043 | P1 done, P2 pending (by plan) |
| FR-028 | No crafted media in the repo or tests. Fixtures are sine/silence WAV, `encode_tone` sines, and plain text with a recording extension (`test_us5.py` swapped `os.urandom` bytes for text) | holds |
