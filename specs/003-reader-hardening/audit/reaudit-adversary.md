# T041 scoped re-audit — adversary report (003 reader hardening)

- Scope: only the fix diff `5636047..ec6df78` (branch `003-reader-hardening`): commits `c29cfa0`, `63b0053`, `74f43aa`, `ec6df78`.
  `git diff --stat` shows 8 files, all documents; `git diff 5636047..HEAD -- src tests nix flake.nix` is empty, so there is no code to re-audit.
- Method: read-only. Fix diff, the changed documents at HEAD, and the code each changed sentence describes:
  `src/stt/server/{worker,reader,reader_client,records,lifecycle,sweep,layout}.py`, `tests/server/{test_hardening_us1,conftest}.py`,
  `tests/test_server_security_doc.py`, `nix/{vm-test,package,module}.nix`, `flake.nix`, spec.md, data-model.md, research.md R2/R3/R10/R14.
  No media was created or run, no test suite or VM test was run. Claims about FFmpeg internals that cannot be checked here are marked
  **UNVERIFIED**.
- The original report is `specs/003-reader-hardening/audit/adversary.md` (T032 at `5636047`).
- Two of the defects below (NEW-2, R-M2) come from fix wording that **the T032 adversary report itself proposed**: the M4 text "re-run only
  an operator-made known-good file through the safe recheck (§3)" and the M2 text "e.g. only from a caller report for a `.wma`/`.m4a` job".
  Both proposals were wrong. The fix applied them faithfully.

## Closure of the targeted findings

| Finding | Status | Evidence |
|---|---|---|
| **H1** (§4.2 lets an H.264 SEI/SPS advisory be recorded as 「서버 경로에서 열리지 않음」) | **CLOSED** | `docs/server-security.md:363-366`: the exemption now needs the vulnerable code to be used by the decoder **alone**. Advisories in parameter-set, SEI, header or CBS code of a codec whose parser runs on the server path count as parser advisories. H.264 is named as measured (research R3). If the operator cannot tell, the default is "parser advisory". The H1 scenario ("h264 decoder: OOB read in SEI / SPS parsing") now falls under the 7-day rule. `§2.1` (`:133-137`), `§4.1` (`:349`), research R3 (`research.md:154`) and `contracts/operator-log.md:136-138` say the same. Residual wording gap: R-H1 (Low). |
| **M1** (BSF and code shared between demuxers not named) | **CLOSED** | `docs/server-security.md:139-142` names `extract_extradata` in `find_stream_info` and the other-demuxer and tag code (S/PDIF in WAV; ID3v2/APE in MP3, AAC and WAV; Ogg per-codec headers). It says neither was measured on 9.0.2 and that advisories naming them are not skipped. §4.2 grants the exemption to decoders only, so these advisories keep the 7-day rule. |
| **M2** (first-recording check cannot run and passes on an empty read) | **NOT CLOSED, now Low (R-M2)** | Part (2) is closed. `:131` now needs a non-empty transcript, no failure line, no early-stop line (`reader.py:236-237`) and no truncation line (`worker.py:153-154`). Those are the lines an empty read from the first frame leaves (`_journal_line`, `reader.py:225-240`), so the empty read no longer passes. Part (1) is answered with a trigger that does not identify the codec. See R-M2. |
| **M3** (documents say no read-stage failure stops the worker) | **NOT CLOSED, now NEW-1 (Medium)** | The absolute claim is gone. The new text matches `worker.py:349-367` for recording-open errors, transient `OSError`/`ValueError`, other exceptions (not caught) and a re-check or `_settle` that raises (`:361`, `:257-281`, outside any `try`). The new limit sentence is wrong for one of the two triggers it names (NEW-1). The M3 sub-point about the `unexpected` row's 「볼 것」 was not addressed (R-M3). |
| **M4** (engine_failed: "re-transcribe the same recording") | **CLOSED** | `:168` now says 「이용자의 녹음을 재전사하지 않는다」. The other branch it offers cannot see an engine failure. See NEW-2. |
| **M5** (C-003-3 does not tell 002 and 003 apart) | **CLOSED** | `quickstart.md:125-128`, `tasks.md:365,388`. For an Ogg Speex tone, the 003 probe fails: `first_audio_decoder_allowed` is false → `ValueError` → `main` returns 1 (`reader.py:365-385,411-417`). The supervisor writes `reader child probe exited with status 1` (`reader.py:240`). 002 had neither the name check nor the list, and accepted Speex (data-model §5 row "목록 밖 음성 코덱"). The snippet's `DecodeResult.audio` defaults to `b""` (`reader_client.py:103`), so the print line does not crash on `failed`. The command is the same shape as the VM's `tone-speex.ogg` (`nix/vm-test.nix:29,45`). Optional strengthening: R-M5. |
| **L7** (`<audio_id>` vs `<request_id>`) | **CLOSED** | `:166` and `contracts/operator-log.md:99` add 「2차 작업은 `jobs/<request_id>/job.json` 의 `audio_id`」. The record is at `layout.jobs/<id>/job.json` (`layout.py:67`, `records.py:157-161`) and carries `audio_id` (`records.py:115`). A second pass uses the source's `audio_id` (`intake.py:307`). |
| **qa Low 1** (the "never stops" claim and the fd-shortage row) | **NOT CLOSED, now NEW-1** | The fd-shortage part is accurate now (`:166` caveat; the `EMFILE` from `hold()` at `worker.py:361` propagates). The "persistently unreadable `job.json`" part is now described with a recovery that does not happen (NEW-1). |
| **qa Low 2** (= L7) | **CLOSED** | As L7. |
| **qa Low 4** (`-I` engine in the full `server` closure) | **CLOSED** (on recorded evidence; not re-run here) | `research.md:330`. Config cross-check: `services.stt.package` defaults to `self.packages.<system>.server` (`flake.nix:70-73`). The `-wrapped` join only wraps `bin/stt` with a `PATH` prefix (`nix/package.nix:85-92`), so `bin/python` is the venv interpreter the units' children use (`sys.executable`). `-I` drops no `PYTHON*` variable that this closure relies on. |

## Discovery

### Critical (배포 차단)

None.

### High (수정 필수)

None.

### Medium (수정 권장)

- **NEW-1 [B1 + A1 / doc truthfulness, introduced by `63b0053`] `docs/server-security.md:171`, `contracts/operator-log.md:37-39`,
  `contracts/reader.md:80`.** The new sentence reads: when the re-check or settle step fails again (「작업 기록을 읽을 수 없거나 기술자가 계속
  모자라는 등」), the worker stops and 「그 작업은 다음 시작 때 `failed interrupted` 가 된다」. This holds for descriptor exhaustion, which ends
  with the process. It is false for the first trigger the sentence names, a job record that stays unreadable:
  - The next worker start calls `lifecycle.clear_dead_worker` before anything else (`worker.py:482-484`, no guard). That calls `load_jobs`
    (`lifecycle.py:235`), which parses every `jobs/*/job.json` with no error handling (`records.py:173-178`). It raises on the same record
    (`ValueError` for bad JSON; `KeyError`/`TypeError` for a well-formed JSON with the wrong shape; `OSError` for a persistent EIO), so every
    start ends in the same traceback. No queued job runs, for any user, until someone removes or repairs that record.
  - The sweep's dead-worker step is guarded (`sweep.py:76-77`), so it logs a fault, moves on and leaves the job `running`. The job never
    reaches `failed interrupted` by itself.
  - Even in the descriptor case, 「다음 시작 때」 leaves out the sweep. When no worker holds `worker.lock`, the sweep marks the job
    `interrupted` too (`sweep.py:45-53`); data-model §5 says 「다음 시작·청소에서」.
  - `contracts/operator-log.md:39` cites `data-model.md` §3 for this limit. §3 (`data-model.md:56-57`) says only that other exceptions rise;
    it says nothing about a failing re-check or settle.
  - Scenario: a disk error leaves one `job.json` truncated while its job reads. The worker stops with a traceback, as documented. The
    operator reads §3 and waits for `<id> failed interrupted` at the next start. Instead every path-triggered start of `stt-worker` crashes
    in the cleanup, and the whole queue stalls.
  - Behaviour is 002 legacy and loud (tracebacks), hence Medium, not High. The defect is the new sentence: it promises a one-job outcome
    where the real blast radius is the whole queue.
  - Fix (doc only): say that after descriptor exhaustion the job becomes `failed interrupted` at the next worker start or sweep. A job
    record that stays unreadable stops every worker start in the dead-worker cleanup (`load_jobs`), so no job runs until the operator moves
    that record aside (002 behaviour). Apply it in the three places and point `operator-log.md:39` at `worker.py`/`records.py`, not
    data-model §3. Then re-run `tests/test_server_security_doc.py`. A code change (guarding `load_jobs`) would be a separate spec item and
    is not proposed.

### Low (정보 / 레거시 WARN)

- **NEW-2 [A13b / A1, introduced by `63b0053`; wording proposed by the T032 adversary] `docs/server-security.md:168`,
  `contracts/operator-log.md:101`.** The `engine_failed` row says recurrence can be checked by running 「운영자가 만든 알려진 정상 파일만 §3
  「안전한 재확인」으로」. The safe recheck (`:284-303`) calls only `reader_client.probe` and `reader_client.decode`. It never starts the
  engine, so it cannot show whether the engine fails again.
  - Scenario: after `failed engine_error engine_failed`, the operator runs the snippet and gets `ok 2.0 ok 2.0 s`. The operator concludes
    the engine is healthy and the recording was at fault, while the GPU or model fault persists.
  - Fix: replace that branch with 「운영자가 만든 알려진 정상 파일을 1차 작업으로 맡겨 본다(quickstart C-003-2·C-003-4 와 같은 길 — 엔진까지 돈다). §3
    안전한 재확인은 읽기 단위까지만 본다」. The first branch (ask the caller to resubmit) is fine.
- **NEW-3 [A15 / doc precision, introduced by `63b0053` (T038(e))] `docs/server-security.md:371`.** The new exception says the four names
  without test files are 「예외 … 이름은 §2.1 의 첫 실제 녹음 확인으로만 본다」. That is not what the tests do.
  `test_hardening_fr_1_every_name_on_the_list_is_an_installed_audio_decoder` (`tests/server/test_hardening_us1.py:455-463`) asserts that all
  32 names, these four included, are installed audio decoders (`av.codec.Codec(name, "r").type == "audio"`). So a removal or rename of
  `wmapro` fails step (2). T034's deploy check is the same test. What the four lack is an end-to-end read, which would catch FFmpeg
  switching the default decoder for those codecs.
  - The list is also incomplete for the production stack. The AMR-NB read test skips on Python 3.12 / av 19.0.2 (no
    `libopencore_amrnb` encoder; `tests/server/conftest.py:425-436`), and the VM's `RECORDER_CODECS` (`nix/vm-test.nix:875`) has no AMR-NB.
    So `amrnb` has no end-to-end read on the deployed stack either.
  - Fix: 「32 이름이 설치된 음성 해독기인지는 `test_hardening_fr_1_…` 이 잡는다(이름이 없어지거나 바뀜). 시험 파일이 없어 끝까지 읽기를 못 보는
    것은 넷과, av 19 에서는 `amrnb` 다(시험이 건너뜀). 그 코덱의 기본 해독기가 바뀌는 것은 첫 실제 녹음에서만 보인다」.
- **R-M2 [A1 + A15, residual of M2; trigger wording proposed by the T032 adversary] `docs/server-security.md:131`, `tasks.md:390-392`.** The
  trigger is 「호출하는 쪽의 알림(그 녹음이 `.wma`·`.m4a` 작업이었다는 보고)」. An extension does not identify the codec. Most `.wma` files are
  WMA 2 (`wmav2`, tested) and most `.m4a` files are AAC. No line or response names a decoder (`reader.py:225-240`, by design).
  - Scenario: an auditor asks for proof that WMA Pro reads. The operator applies the pass criterion to a `.wma` job the caller reported,
    which was actually WMA 2, and records `wmapro` as verified.
  - The safe default (「알려질 때까지 확인 안 됨」) is stated, and the impact is on the evidence record, not on security. Hence Low.
  - Fix: the report must name the codec (for example the recorder's setting). A `.wma`/`.m4a` extension alone verifies none of the four.
    Optionally say that the caller, who holds the transcript, is the one who confirms it is non-empty.
- **R-M5 [A2 / A5 / A12, optional strengthening of M5] `quickstart.md:125-128`, `tasks.md:365,388`.** The Speex step tells 003 from 002, but
  only through the Python name check (`first_audio_decoder_allowed`, `reader.py:365-385`). That check fails the probe even if the deployed
  FFmpeg ignored `codec_whitelist`. The snippet already runs `decode` (`docs/server-security.md:297`), and with the list active the decode
  child exits 1 too. The VM asserts both: probe and decode `failed`, plus one `reader child probe exited with status 1` and one
  `reader child decode exited with status 1` per file (`nix/vm-test.nix:951-960`). Requiring the printed `failed None failed 0.0 s` and the
  decode line would also exercise the FFmpeg-level per-stream list on the host at no cost.
  - A `failed` with status 1 can also come from a broken Ogg demuxer. An Ogg Vorbis or Opus tone that must read `ok … ok` beside it would
    rule that out.
  - Two smaller gaps: the step omits the snippet's `chmod 0644`, and under a root umask of 077 `probe()` raises `RecordingError(EACCES)` as
    a traceback. Whether the host's `ffmpeg` has `libspeex` is **UNVERIFIED** (the VM's `pkgs.ffmpeg` has it, `nix/vm-test.nix:20,45`). If
    it lacks it the command fails loudly.
- **R-M3 [B9, residual M3 sub-point, unchanged] `docs/server-security.md:167`, `contracts/operator-log.md:100`.** The `unexpected` row still
  sends the operator to 「같은 시각의 워커 저널(기술자·메모리)」. For a job that ends `unexpected` the worker journal holds only `<id> running`
  and `<id> failed engine_error unexpected`, because the exception is dropped without its class (`worker.py:358-360`). Fix: say that the
  worker journal has no detail for this cause, and point to `journalctl -k` / `systemctl status stt-worker` for memory, or drop
  「(기술자·메모리)」.
- **R-H1 [A13 variant, residual wording of the H1 fix] `docs/server-security.md:136,364-365`.** The rule is sound in principle: the exemption
  needs decoder-only code, and the default when the operator cannot tell is "parser". But the codec list (「잰 것: H.264. 후보: MPEG-4 Part 2,
  VC-1, AV1, HEVC, VP8/VP9, MPEG-1/2」) and the code categories (「매개변수 집합·SEI·머리·CBS」) read as closed. An operator can then
  reason "this codec is not listed, so no parser runs for it, so the code is decoder-only".
  - **UNVERIFIED** examples outside the list: VVC, whose native decoder and parser both use CBS H.266 in FFmpeg ≥ 7.0; Dirac, whose
    sequence-header code in `dirac.c` the Ogg Dirac header parser may share; and NAL/RBSP splitting (`h2645_parse.c`), which the H.264
    parser and the `extract_extradata` BSF call but which is not a "parameter set / SEI / header" word.
  - Fix: make both lists explicitly open, e.g. 「목록에 없는 영상 코덱도, 또 NAL 나누기 코드도, 해독기만 쓴다는 것을 보이지 못하면 분석기 공지로
    본다」.
- **DOC-SYNC [A15, nits] — earlier statements the narrowing did not reach.**
  - `spec.md:31` (US1 "Why this priority") still says future video-decoder advisories reduce to 「서버 경로에서 열리지 않음」.
  - `data-model.md:76` still maps 「기술자 부족」 to `recording_unusable` without the limit.
  - `contracts/operator-log.md:133-135` (5.3, the planned §4.1 text) still reads 「분석기·형식은 남는다(§2)」.
  - `docs/server-security.md:349` (§4.1) names parsers and shared code but not the BSF and tag paths of `:139-142`.
  - research R14 (`research.md:379-380`) keeps the broad rule as the historical decision. That is acceptable as history, but a one-line
    「T041: 좁힘 → §4.2」 pointer would stop a reader from taking it as current.

## Plan

### Persona Attack 결과

- Operator-facing entry points changed by the fix diff, simulated: **6**
  1. §4.2 advisory triage rule, with §2.1 「남는 범위」 and the §4.1 line (H1, M1)
  2. §2.1 first-real-recording check for `wmapro`·`wmavoice`·`wmalossless`·`amrwb` (M2), and T034's wording of it
  3. §3 cause table rows `recording_unusable`·`unexpected`·`engine_failed` and the "worker stops" paragraph (M3, M4, L7), with the same
     text in `contracts/operator-log.md` §2 and `contracts/reader.md` §5
  4. quickstart C-003-3 Speex step and T034 (M5)
  5. §4.2 update step (2), the exception for names without test files (final-review item, T038(e))
  6. research R10 pre-deploy `-I` import check (qa L4, T040)

- **Group A — 14 scenarios, 5 distinct defects** (R-M2, R-M5, R-H1, NEW-2, NEW-3):

  | ID | Scenario (against the fix diff) | Result |
  |---|---|---|
  | A1 | new operator applies the §2.1 check after the caller reports "it was a `.wma` job" | criterion runs, but on a WMA 2 file; `wmapro` falsely "verified" — **R-M2** |
  | A2 | hurried operator runs only the Speex step after deploy | `failed` + probe line tells 003 from 002, but cannot rule out a broken Ogg demuxer and does not exercise the FFmpeg-level list — **R-M5** (optional) |
  | A3 | operator looks for a second pass's recording under `/var/lib/stt/audio/<request_id>/` | row now says use `job.json`'s `audio_id` — OK (L7 closed) |
  | A4 | operator mistypes the `-g` patterns | the patterns in `:131` and `:183` are plain ASCII substrings of the fixed lines (`reader.py:231-240`, `worker.py:56`) — OK |
  | A5 | host `ffmpeg` built without `libspeex` | `Unknown encoder` — loud; step cannot be done; **UNVERIFIED** for the host — in R-M5 |
  | A6 | intake probes a real upload while the operator runs the Speex step | an unrelated `probe exited with status 1` may appear, but the printed per-file outcome is unambiguous; in the M2 check an unrelated failure line only blocks a pass (conservative) — OK |
  | A7 | non-ASCII names | no user text in any changed line — N/A, OK |
  | A8 | copy-pastes the Speex command from rendered Markdown | command is in inline code; straight quotes kept — OK |
  | A9 | re-runs the Speex step | existing `/tmp/known-speex.ogg` + `-nostdin` without `-y` → ffmpeg exits with a message; step says delete afterwards — OK |
  | A12 | root shell with umask 077 creates the Speex file | `stt` cannot open it → `RecordingError(EACCES)` traceback, not `failed`; the step omits the snippet's `chmod` — in **R-M5** |
  | A13 | operator triages "h264 decoder: OOB in SEI/SPS parsing" (the H1 scenario) | H.264 measured + SEI/SPS named → parser advisory → 7-day rule — **blocked, H1 closed** |
  | A13' | operator triages an advisory for a codec or code class missing from the lists (VVC CBS, NAL splitting) | list reads as closed — **R-H1** (UNVERIFIED examples) |
  | A13b | operator follows `engine_failed` 「볼 것」 | no longer re-transcribes a user's recording (M4 closed); the safe-recheck branch cannot see the engine — **NEW-2** |
  | A15 | auditor asks (a) host evidence for SC-001, (b) proof for the four decoders, (c) whether an upgrade catches a decoder rename | (a) Speex step discriminates — M5 closed; (b) extension-only trigger — **R-M2**; (c) doc says only a real recording shows it, while `test_hardening_fr_1` pins all 32 names and AMR-NB is also unread on av 19 — **NEW-3** |

  → Fix direction: document-only edits (R-M2, R-M5, R-H1, NEW-2, NEW-3); no code, VM or schema change.

- **Group B — 10 scenarios, 2 distinct defects** (NEW-1, R-M3):

  | ID | Scenario | Result |
  |---|---|---|
  | B1 | `job.json` left truncated or undecodable while its job reads | worker stops (doc correct), then every later start crashes in `clear_dead_worker` → `load_jobs`; the job never becomes `interrupted` and the queue stalls — **NEW-1** |
  | B1c | hostile `.m4a` with a crafted H.264 SEI after an H.264 SEI advisory | triaged as a parser advisory under the new rule → updated within 7 days — OK |
  | B3 | recording mode 000 / symlink / FIFO | `RecordingError` → `recording_unusable`; row text matches `reader_client.py:120-133` — OK (unchanged code) |
  | B4 | reader socket missing | `reader_unavailable` — unchanged, OK |
  | B5 | reader returns a garbage verdict | `failed`/`unreadable_audio` — unchanged, OK |
  | B6 | encoding tricks | no user text in any changed line — N/A, OK |
  | B7 | delete or purge during the read | re-check → `stopped`, no cause line; doc unchanged and correct — OK |
  | B8 | clock or timezone jumps vs the 「그 시각」 correlation of journals | all journals share the same clock; no new wall-clock use — OK |
  | B9 | fd exhaustion at the recording open, then at the re-check | worker stops; next start (or sweep, omitted by the doc) marks it `interrupted` — OK apart from the NEW-1 sub-point |
  | B9b | memory or descriptor trouble behind an `unexpected` line | 「볼 것」 sends the operator to a worker journal with no detail — **R-M3** |

  → Fix direction: NEW-1 doc correction in three places; R-M3 one clause.

- **Cross-persona — 5 scenarios, 3 expose a defect**:

  | ID | Scenario | Result |
  |---|---|---|
  | C1 | A13 + hostile recording (H1 replay) | blocked — OK |
  | C2 | A1 + B1: operator reads §3 after a crash caused by a corrupt record | waits for `failed interrupted` that never comes; queue stalled — **NEW-1** |
  | C3 | A15 + A1: auditor asks for closure of the four names | extension-only report → false closure — **R-M2** |
  | C4 | A2 + B9: hurried operator restarts after an fd-exhaustion crash | job `interrupted`, queue continues — OK |
  | C5 | A13b + engine fault: `engine_failed` diagnosis via the safe recheck | reader-only `ok … ok` → misdiagnosis — **NEW-2** |

### Silent-Skip 결과

- New silent-skips: **0** (high 0, low 0). The fix diff has no code (`git diff 5636047..HEAD -- src tests nix flake.nix` is empty).
- Mitigation to add: none.

### 미수정 (사유)

- The adversary is discovery-only; every item above is a proposal for the controller.
- NEW-1 describes 002 behaviour (an unguarded `load_jobs` in the worker's start-up cleanup). Guarding it would be a code and spec change
  outside 003 (operator ruling: docs only). Only the sentence is proposed for change.
- The T032 Lows not in T041's scope (L1–L6, L8–L12) were not re-audited.

### qa-engineer 협력 필요

- **NEW-1**: boundary between "the job ends `interrupted`" and "the worker cannot start" (`worker.py:482-484` ↔ `records.py:173-178`).
  qa-engineer may want to confirm the corrupt-record path, and say whether a test should pin it or only the corrected sentence.
- **NEW-3**: test coverage statement vs `test_hardening_fr_1_…` and the AMR-NB skip on av 19. qa-engineer to confirm which names lack an
  end-to-end read on the production stack.

## Verdict

PASS — 신규 silent-skip high 0건, persona-driven critical 0건 (Critical 0, High 0, Medium 1, Low 7)

H1 is closed: the narrowed §4.2 rule blocks the H1 scenario. M1, M4, M5, L7, qa Low 2 and qa Low 4 are closed. M2, M3 and qa Low 1 are
narrowed but not closed:

- M2 → R-M2 (Low).
- M3 and qa Low 1 → NEW-1 (Medium): the new limit sentence promises `failed interrupted` at the next start, but a job record that stays
  unreadable stops every worker start instead.

The fix also introduced NEW-2 and NEW-3 (Low). All remaining items are document-only. None is High or above, so T041's gate to T033 is met
from the adversary side. NEW-1 is recommended before T033 because it is in the operator's incident text.
