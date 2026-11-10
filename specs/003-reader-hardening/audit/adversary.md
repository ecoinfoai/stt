# T032 T-Verify-Final — adversary report (003 reader hardening)

- Audit range: `master` (0cfae45) ... `HEAD` (5636047), branch `003-reader-hardening`.
- Method: code, config, tests and documents read only (`git diff master...HEAD`, full files at HEAD). No media file was created,
  downloaded or run; no test suite and no VM test were run (shared-context hard rules). Every persona below was simulated against the
  code. Where a claim depends on FFmpeg internals that this host has no source for, it is marked **UNVERIFIED**.
- Inputs read: spec.md, research.md (R1–R14), contracts/{reader,operator-log,module}.md, quickstart.md, tasks.md (T005, T034),
  `src/stt/server/{reader,reader_client,worker,intake,lifecycle,records,layout}.py`, `src/stt/audio_decode.py`, `src/stt/server/engine.py`
  (audio load only), `nix/{module,module-eval,vm-test}.nix`, `flake.nix`, `docs/server-security.md`, the new and edited tests,
  `.superpowers/sdd/003-reader-hardening/{final-review,progress}.md` (to avoid re-reporting closed items as new).

## Discovery

### Critical (배포 차단)

None.

### High (수정 필수)

- **[persona A13 + hostile recording / doc truthfulness] `docs/server-security.md:355` (§4.2 new rule), with `:133-134` (§2.1 남는 범위)
  and `:341` (§4.1)** — The new advisory rule lets the operator record 「서버 경로에서 열리지 않음(§2)」 for any advisory that concerns a
  video, image or subtitle *decoder*. That is only safe when the vulnerable code is used by the decoder alone. The H.264 **parser** runs on
  the server path — research R3 measured it 🟢: a `.m4a` with an H.264 track calls `av_parser_parse2` twice during the probe's
  `find_stream_info` and once per video packet during decode (21 calls for 2 s at 10 fps), with the allowlist in place — and FFmpeg builds
  that parser from the decoder's own header code: `h264_parser.c` calls `ff_h264_decode_seq_parameter_set` /
  `ff_h264_decode_picture_parameter_set` (`h264_ps.c`), `ff_h264_sei_decode` (`h264_sei.c` / `h2645_sei.c`), `ff_h264_parse_ref_count` and
  `ff_h264_pred_weight_table` (`h264_parse.c`) on every SPS/PPS/SEI/slice NAL it sees. The same pattern holds for other parsers the
  allowed demuxers start (`mpeg4video_parser` → the MPEG-4 Part 2 decoder's picture-header code; `vc1_parser` → `vc1.c`; `av1_parser` →
  CBS AV1; `hevc` parser → HEVC parameter sets/SEI). (The code sharing is from FFmpeg's source layout; it was not re-read for FFmpeg 9.0.2 in
  this audit — **UNVERIFIED** for that exact release, measured parser activity is 🟢.)
  — Scenario: an FFmpeg advisory reads "h264 decoder: out-of-bounds read in SEI / SPS parsing". The operator, triaging in good faith under
  the 7-day rule (A13), classifies it as "video decoder only" exactly as §4.2 invites and writes 「서버 경로에서 열리지 않음(§2)」. A hostile
  caller then uploads a `.m4a` whose H.264 track carries the crafted NAL; the H.264 parser reaches the vulnerable function inside the probe
  child (`reader._probe`, `src/stt/server/reader.py:381-385` → `open_container` → `avformat_find_stream_info`) and again in the decode child,
  although no video decoder is ever opened. §2.1's escape clause ("공지가 분석기·형식에 관한 것이면 지금처럼 판정한다") does not catch it,
  because such advisories name the decoder, not the parser. research R3's premise "분석기는 해독기보다 훨씬 작은 코드" understates this.
  — Impact bounded by the 002 sandbox (seccomp, MDWE, DynamicUser, no network, PID namespace), hence High, not Critical: the rule
  lowers the urgency of a reachable bug class below what 002 required (002 treated every FFmpeg advisory as reachable).
  — Fix (doc only, no code or VM rerun beyond `tests/test_server_security_doc.py`): in §4.2, allow 「서버 경로에서 열리지 않음」 only when the
  vulnerable code is used by the decoder alone; treat advisories in shared parameter-set / SEI / header / CBS code of a codec whose parser
  runs on the server path as parser advisories, and name those codecs (at least H.264, measured; candidates to confirm: MPEG-4 Part 2, VC-1,
  AV1, HEVC, VP8/VP9, MPEG-1/2 via `mov`/`asf` stream parsing). Optional code follow-up already on file: `discard` non-audio streams at decode
  (R3 alternative, operator-deferred) — removes the decode-time calls, not the probe-time ones.

### Medium (수정 권장)

- **M1 [hostile recording / doc completeness] `docs/server-security.md:133-134`** — §2.1 names two uncovered families (parser, demuxer). Two
  more non-decoder code paths parse attacker bytes on the server path and are in neither family as an operator would read them:
  (a) `avformat_find_stream_info` runs the `extract_extradata` bitstream filter on packets of H.264/HEVC/VVC/AV1/MPEG-1/2/4/VC-1 streams that
  have no extradata (e.g. H.264 in an ASF `.wma`, which carries Annex B without `avcC`); it splits NALs/OBUs with `h2645_parse.c` /
  `av1_parse.c`. (b) An allowed entry demuxer calls code of demuxers that are not in `AUDIO_FORMATS`: `wav` calls the S/PDIF demuxer's
  `ff_spdif_probe` / `ff_spdif_read_packet`; `mp3`/`aac`/`wav` call ID3v2/APE tag parsers; `ogg` calls per-codec Ogg header parsers
  (Theora, VP8, Kate…). An advisory for "the extract_extradata BSF" or "the spdif demuxer" would look out of scope to an operator who reads
  `AUDIO_FORMATS` and §2.1. — Both are **UNVERIFIED** for FFmpeg 9.0.2 (research's watch tool hooked decoder opens and parser calls only).
  Fix: one sentence in §2.1 naming bitstream filters used by `find_stream_info` and code shared between demuxers; measure (a) with the R1
  watch tool on an operator-made solid-colour H.264-in-ASF file if the operator wants it confirmed.

- **M2 [A15 auditor / A1 operator] `docs/server-security.md:130-131`** — The "first recording" check that is meant to close the
  `[VERIFY]` for `wmapro`, `wmavoice`, `wmalossless` and `amrwb` cannot be executed and, when it can, passes on a failed read.
  (1) Nothing tells the operator that such a recording arrived: no log line names a decoder, the response does not name a codec, and the
  same document forbids opening server recordings (`:273`). (2) The pass criterion is "not `unreadable_audio` and no failure line", and the
  text adds that `reader child decode stopped early at invalid data` is *not* a failure line — so a decoder that stops at its first frame
  (0 s decoded, empty transcript, `done`, plus the worker's truncation line) counts as 「읽힌 것이다」.
  — Scenario: the first real WMA Voice lecture hits a mode FFmpeg's `wmavoice` rejects with `InvalidDataError`; the job ends `done` with an
  empty transcript; the operator's documented check reports success. Fix: make the pass criterion "a non-empty transcript and neither the
  early-stop line nor the truncation line at that time", and say how the operator learns of such a recording (e.g. only from a caller report
  for a `.wma`/`.m4a` job), or record the four names as unverifiable-until-reported instead of "checked on first arrival".

- **M3 [B1 + doc/contract truthfulness] `src/stt/server/worker.py:352-361`; `docs/server-security.md:159,163`;
  `contracts/operator-log.md:37`; `contracts/reader.md:80`** — The documents state that no read-stage failure stops the worker and list
  "작업 기록 읽기" under `unexpected`. In code, a `ValueError`/`OSError` raised by the `job_running` callback (`_is_running` →
  `load_job`, `worker.py:158-163`) is caught at `:358-360`, but the very next statement `:361` calls `_is_running` again *outside* any
  `try`; a persistent record problem (undecodable `job.json`, a state-lock open failure that persists) raises there, the worker exits with a
  traceback, and the job becomes `failed interrupted` at the next start — the `unexpected` line is never printed. A malformed-but-valid JSON
  record raises `KeyError`/`TypeError` from `_job_from_dict`, which is not caught at all. `test_hardening_edge_6_an_error_that_is_neither_os_nor_value_error_rises_out_of_the_worker`
  (`tests/server/test_hardening_us3.py:334`) pins that other exceptions rise, contradicting 「어느 것도 워커를 멈추지 않는다」.
  The "볼 것" column for `unexpected` sends the operator to the worker journal for descriptor/memory hints that the worker never writes
  (the exception is dropped without its class).
  — Scenario: a disk error corrupts one `job.json` while its job is reading; the operator expects `failed engine_error unexpected` per
  §3 and instead finds a worker crash. (A corrupt record also breaks `load_jobs` for intake and sweep — legacy, out of 003 scope.)
  Fix: narrow the documents to what the code does ("transient I/O errors of the read stage"; record-read failures and other exceptions stop
  the worker and surface as `interrupted`), or catch at `:361` too. No behaviour change is required by the spec (FR-009 is about recording
  errors, which are handled).

- **M4 [A13 well-meaning operator] `docs/server-security.md:160`** — The new `engine_failed` row tells the operator to check 「같은 녹음을
  재전사해 되풀이되는지」. The operator has no sanctioned re-run path: re-transcribing means writing an inbox request in the owner's name
  (`requested_by` must equal the source job's, `intake.py:_usable_source`), which produces a fresh full transcript and paragraph list of a
  professor's recording in `outbox/` — content the diagnosis does not need, and a new "user" of the recording that the owner never created.
  — Fix: reword to "ask the caller to resubmit (bhu-llm), then compare the outcome", or "check GPU/model state; re-run only an
  operator-made known-good file through the safe recheck (§3)".

- **M5 [A15 auditor] `specs/003-reader-hardening/quickstart.md:123-125` (C-003-3), tasks.md T034** — SC-001 claims host evidence that
  video/image decoders are not opened. C-003-3 reads an operator-made tone + solid-colour video `.m4a` and a tone MP3 with a PNG cover
  through the safe snippet and expects `ok … ok` with no `reader child` line. 002 code (no allowlist) gives exactly the same result, so the
  check cannot tell whether the list is active on the deployed host (C-003-1 only proves the worker restarted). There is no decoder watch in
  production. — Fix: add one discriminating host step: an operator-made Speex tone (`ffmpeg … -c:a libspeex -f ogg`, as the VM uses) through
  the same snippet must give `probe failed` (002 gives `ok`), and its time must show `reader child probe exited with status 1`.

### Low (정보 / 레거시 WARN)

- **L1 [B2, legacy 002] `src/stt/server/worker.py:53,148-152`** — `_write_audio` failing (ENOSPC, EIO) maps to `storage_error` →
  `unreadable_audio`, line `<id> failed unreadable_audio`, no cause. A full disk is indistinguishable from a bad recording for both the
  caller and the operator — the gap US3 closes for `engine_error` stays open here. WARN only (pre-003 mapping).
- **L2 [B9] `src/stt/server/worker.py:153-154`** — `print(TRUNCATION_LINE)` sits inside the `try` whose `except (OSError, ValueError)`
  maps to `unexpected`; a stdout write error after `audio.wav` was written ends the job as `engine_error unexpected`. Already deferred
  (progress.md U05 #31); safe outcome, wrong cause.
- **L3 [A4 + B5] `src/stt/server/reader.py:324-327`, `docs/server-security.md:229`** — If the socket unit were ever misconfigured
  (`Accept=no` → listening socket; `ListenStream=` → stream socket), every instance would print `MANUAL_RUN_LINE` ("only serves a connection
  that systemd passes from stt-reader.socket") although systemd did pass it; the document says the line never appears in operation. The
  socket unit is module-owned, so unlikely; a doc clause ("…or the socket unit is not a sequential-packet `Accept=yes` socket") would make the
  line diagnostic in that case too.
- **L4 [test strength, VM-only] `nix/vm-test.nix:915-925` (B-003-1(0)), `:105-112` (watch)** — The GREEN guard proves the watch library is
  mapped in the supervisor, not that it intercepts `avcodec_open2` in the decode child. If a future PyAV wheel bound `avcodec_open2`
  internally (static FFmpeg, or a call path that bypasses the PLT), B-003-1(2)/(3) would pass vacuously. The RED run at `6ac747f` proves
  interception for today's wheel only. Suggest a positive control in one specialisation (e.g. an env switch that makes the watch also abort
  on an audio open, expected `killed by signal 6`).
- **L5 [test strength] `tests/server/test_hardening_us4.py:115-124`** — US4-5 asserts only `returncode != 99`; an import failure under `-I`
  (e.g. a dev shell that relies on `PYTHONPATH=src`) also passes. Deferred (U06 #32); the VM's real reads and `gpu_unavailable` engine
  verdicts cover the Nix closure. In such a dev shell every recording would read as `unreadable_audio` with the "most common, benign" line.
- **L6 [coverage] `tests/server/conftest.py` `_require_encoder` (+ `make_amr_nb_3gp`)** — AMR-NB is skipped on Python 3.12 / av 19.0.2, the
  production stack; it is read only on 3.10/3.11 (av 17/18) and in research R2. Explicit `pytest.skip` with reason — not a silent skip.
- **L7 [A3] `docs/server-security.md:158`** — The `recording_unusable` "볼 것" names `/var/lib/stt/audio/<audio_id>/`, but the log line
  carries `<request_id>`; for a re-transcription the recording lives under the source job's id. An operator who looks under the request id
  will find nothing and conclude "missing". Add "(`audio_id` from `jobs/<request_id>/job.json`)".
- **L8 [B9] `src/stt/server/reader_client.py:126-129`** — EMFILE/ENFILE/ENOMEM at the recording open become `recording_unusable`, while the
  same EMFILE at `os.pipe2`/`socket()` becomes `unexpected`. Documented (R6), so WARN only.
- **L9 [A10] `src/stt/server/reader.py:323`** — SIGINT is ignored from the start, so an operator who hand-starts the supervisor with a real
  socket on stdin cannot Ctrl-C it during the 30 s request wait or a 240 s decode (SIGQUIT/SIGTERM still work). The decode child inherits
  `SIG_IGN` (noted in contracts/reader.md §4). Harmless in the unit.
- **L10 [latent] `src/stt/server/worker.py:261,288`** — `_settle(cause=None)` default; a future `engine_error` call site without a cause
  would print `failed engine_error None`. All four current call sites pass a cause. Deferred (U04 #21).
- **L11 [B9] `src/stt/server/reader_client.py:130-132`** — an `os.fstat` failure after a successful open leaks the descriptor and surfaces
  as a plain `OSError` (`unexpected`). Deferred (U04 #23).
- **L12 [doc] `docs/server-security.md:341`** — §4.1 still says `pending` for the deploy commit/date; no test guards it. Tracked by T033.

## Plan

### Persona Attack 결과

- New entry points / external-input handlers identified: **12**
  1. `reader.serve()` — `stt server read` (SIGINT disposition, manual-run line) `reader.py:307-329`
  2. decode child `python -I -m stt.server.reader child probe|decode` — `_open_container` + `first_audio_decoder_allowed` `reader.py:359-395`
  3. `audio_decode.open_container(codec_whitelist=…)` `audio_decode.py:31-66`
  4. `reader_client._open_recording` (`O_NONBLOCK`, `RecordingError`) — used by intake `_measure` and worker `_read` `reader_client.py:112-133`
  5. `worker._run_job` read-stage exception → cause mapping `worker.py:348-384`
  6. `worker._read` + `is_truncated` (truncation line) `worker.py:68-79,120-155`
  7. `worker._write_audio` (write through the creating descriptor) `worker.py:105-117`
  8. `worker._prepare` (recording path derived from `original_name`) `worker.py:82-102`
  9. `worker.DEFAULT_ENGINE_CMD` / `reader.DEFAULT_CHILD_COMMAND` with `-I`
  10. `nix/module.nix` systemd ≥ 257 assertion + `nix/module-eval.nix`
  11. VM-only decoder watch (`nix/vm-test.nix`)
  12. operator procedures added to `docs/server-security.md` §2.1/§3/§4.2 (operator-facing entry point)

- **Group A — 18 scenarios, 8 expose a defect** (H1, M2, M3, M4, M5, plus Lows L3/L5/L7/L9):

  | ID | Scenario | Result |
  |---|---|---|
  | A1 | operator runs `stt server read` in a terminal | stderr line, exit 2, nothing read — OK (`reader.py:324-327`, VM B-003-4) |
  | A2 | hurried deploy while `.ogg` Speex / `.mp3` Layer I jobs are queued | they fail `unreadable_audio` at processing — expected, reply T035 informs; OK |
  | A2b | operator sees `failed engine_error unexpected`, opens worker journal as §3 says | finds nothing — **M3** |
  | A3 | operator checks `/var/lib/stt/audio/<request_id>/` for a re-transcription's `recording_unusable` | wrong folder — **L7** |
  | A4 | `systemd.package` overridden to 256.x | evaluation refuses with the English sentence; module-eval pins it — OK |
  | A4b | socket unit edited to `Accept=no` / stream | manual-run line in production, misleading — **L3** |
  | A5 | dev shell relying on `PYTHONPATH=src` | `-I` children cannot import `stt`; tests still pass (`!= 99`) — **L5** |
  | A6 | "다 지우기" lands while `audio.wav` is written | file stays 0600 on the unlinked inode, path does not reappear, job `stopped` — OK (`worker.py:111-117`, US4-3, US2-8) |
  | A7 | Korean / emoji original file names | never reach paths or lines (`audio.<ext>` from a schema-fixed extension set) — OK |
  | A8 | copy-paste of the `journalctl -g` pattern from the doc | plain ASCII inside a code block — OK |
  | A9 | re-transcription of a truncated recording | one truncation line per job (US2-5) — OK |
  | A10 | Ctrl-C on a hand-started supervisor with a socket stdin | ignored — **L9** |
  | A11 | jobs queued by 002 after the 003 deploy | `_prepare` derives the same `audio.<ext>` intake wrote since 001; out-of-list codecs fail at processing (`place_accepted_job` test) — OK |
  | A12 | recording mode 000 | `RecordingError(EACCES)` → `recording_unusable`, next job runs — OK |
  | A13 | operator triages a "video decoder" advisory with §4.2 | reachable via the H.264 parser — **H1** |
  | A13b | operator follows `engine_failed` "볼 것" and re-transcribes | full transcript of someone's lecture for a diagnosis — **M4** |
  | A14 | own-data vs admin path | no such shared path in this feature — N/A |
  | A15 | auditor asks for host evidence of SC-001 / closure of the four unverified decoders | C-003-3 not discriminating (**M5**); first-recording check unexecutable and accepts empty reads (**M2**) |

  → Fix direction: document-only changes for H1, M2, M3 (or one extra `try` at `worker.py:361`), M4, M5 (one extra host step).

- **Group B — 13 scenarios, 5 expose a defect** (H1 and M1 via B1c, M3, L1, L2, L8, L11; all others graceful):

  | ID | Scenario | Result |
  |---|---|---|
  | B1 | upload cut at the end (M4A index-first, MP3 with Xing, FLAC) | `done` + one truncation line — OK; WAV/Ogg/ADTS cannot show it — documented |
  | B1b | `job.json` undecodable during the read | crash at `worker.py:361`, not `unexpected` — **M3** |
  | B1c | hostile `.m4a` with an H.264 track / hostile ASF with Annex-B H.264 | decoders refused; parser (H1) and `extract_extradata` BSF (**M1**, UNVERIFIED) still parse it |
  | B1d | recording replaced by a FIFO / directory | `O_NONBLOCK` open + `S_ISREG` → `recording_unusable`, no hang (VM B-003-3(2)) — OK |
  | B2 | disk full while writing `audio.wav` | `unreadable_audio`, no cause — **L1** (legacy) |
  | B3 | recording unreadable (EACCES) / symlink (ELOOP) | `recording_unusable` — OK |
  | B4 | reader socket missing / refused | `reader_unavailable` — OK (VM B-003-3(1)) |
  | B5 | compromised decode child: garbage verdict, early hang-up, `SIGINT`/other signals to the supervisor | verdict `failed`; SIGINT ignored; other default-action signals are dropped for PID 1 of the namespace (R8) — OK; a recording cannot steer the cause name (`unavailable` is only reachable before `started`) — OK |
  | B6 | encoding tricks in names | no user text reaches the new lines — OK |
  | B7 | delete / 23:58 purge during the read | `RecordingError(ENOENT)` or reader `stopped`; the `_is_running` re-check turns it into `stopped`, no cause line — OK |
  | B8 | clock jumps | monotonic deadlines; no new wall-clock use — OK |
  | B9 | EMFILE at open / stdout broken | `recording_unusable` misattribution (**L8**, documented); `unexpected` after a written audio (**L2**) |
  | B9b | `fstat` failure after open | descriptor leak (**L11**, deferred) |

  → Fix direction: M1 doc sentence; M3 doc narrowing or one `try`; Lows optional.

- **Cross-persona — 5 scenarios, 3 expose a defect**:

  | ID | Scenario | Result |
  |---|---|---|
  | C1 | A2 + B7: operator deletes while the worker writes `audio.wav` | 0600 throughout, `stopped`, next job runs — OK |
  | C2 | A4 + B5: misconfigured socket type + reader refusing | `reader_unavailable` plus a manual-run line that points the wrong way — **L3** |
  | C3 | A10 + B1: worker killed mid-write | `clear_dead_worker` removes `work/*`, job `interrupted` — OK |
  | C4 | A13 + hostile recording | dismissed "decoder" advisory reached through the parser — **H1** |
  | C5 | A15 + A1: auditor asks the operator to prove the four untested decoders | no way to find the recording without opening it; check accepts an empty read — **M2** |

### Silent-Skip 결과

- New silent-skips: **0** (high 0, low 0).
- Reviewed and excluded:
  - `src/stt/audio_decode.py:63-65` `if stream.codec_context is not None` — a stream without a codec context has no decoder PyAV could open;
    FFmpeg's own opens are covered by the container-level list. Not a skip of a security step.
  - `src/stt/server/reader.py:383-384`, `worker.py:356-360` — every branch raises or ends in a logged `_settle` / `stopped` line.
  - `src/stt/server/worker.py:280` existing `# intentional-skip` (legacy, unchanged).
  - Tests: `tests/server/conftest.py` `_require_encoder` (`pytest.skip`, AMR-NB on av 19), `tests/server/test_hardening_us3.py:151-153` and
    `tests/server/test_decoder_us3.py` (root cannot fail a mode-000 open), `tests/server/test_hardening_us4.py:131-132` (console script
    absent) — explicit `pytest.skip` with a reason (rule (b): raises `Skipped`, visible in the summary); `tests/server/test_hardening_us3.py:108-111`
    asserts the errno; VM `b003()` collects failures and asserts once at the end (`assert not B003_FAILURES`) — none silent.
- Mitigation to add: none required.

### 미수정 (사유)

- Not fixed by this audit (adversary is discovery-only). Every item above is a proposal for the controller.
- 002 residuals re-accepted on 2026-11-10 (shared reader UID, world-readable host files, hour.m4a tail) and the operator-deferred
  "discard video packets at decode" were not re-raised; H1 and M1 are about the *documented triage built on* the parser residual, not
  about the residual itself.
- L2, L5, L10, L11 were already deferred in progress.md (U04/U05/U06); listed again only for completeness.

### qa-engineer 협력 필요

- **H1 / M1**: boundary of the allowlist is decoders only — qa-engineer may want a doc-sync check that §4.2 names the codecs whose parsers
  run on the server path (once the list is agreed), so the rule cannot drift from research R3.
- **M3**: boundary between "read-stage failure handled" and "worker stops" at `worker.py:361` — qa-engineer re-check of FR-009 wording vs
  behaviour (and whether a test should pin the crash path or the narrowed document).
- **M5**: SC-001 host evidence — qa-engineer to judge whether the Speex discriminating step belongs in T034.

## Verdict

FAIL — 신규 silent-skip high 0건, persona-driven critical 0건 (High 1, Medium 5, Low 12)
사유 태그 (FAIL 시): PERSONA_ATTACK_SUCCESS (H1: A13 operator triage + hostile recording reaches decoder-shared code through the H.264
parser while §4.2 lets the advisory be recorded as 「서버 경로에서 열리지 않음」). The fix is a document change only; no code, VM or schema
change is needed to clear the FAIL.

### Catalog 보강 필요

- **Advisory-triage operator** (domain-specific A13 variant): the person who applies the 7-day advisory rule. Defects here are invisible to
  A1–A12 because nothing "breaks"; the failure is a correct-looking `reason` entry for a reachable bug. Recommended for every feature that
  changes `docs/server-security.md` §4.
