# T041 scoped re-audit — qa-engineer (003-reader-hardening)

- Range: `5636047..ec6df78` (fix commits c29cfa0, 63b0053, 74f43aa, ec6df78), branch `003-reader-hardening`. Date: 2026-11-11.
- Scope: only the fix diff (`docs/server-security.md`, `contracts/{operator-log,reader}.md`, `quickstart.md`, `research.md`, `tasks.md`).
  `git diff --quiet 5636047..HEAD -- src tests nix flake.nix` → no code, test or Nix change. Unchanged code was read only where the new
  text makes a claim about it (both sides of each doc ↔ code boundary).
- Mode: read-only except this file. No media was created, downloaded or run (FR-017). The VM test was not run.

## Evidence run

| Check | Command | Result |
|---|---|---|
| Doc test at HEAD | `captest 4G 10m uv run --locked python -m pytest -q -p no:cacheprovider --basetemp=/tmp/audrq tests/test_server_security_doc.py` | **21 passed in 0.05 s**, peak 43 MiB. basetemp removed. |
| T040 claim, re-run | `captest 1G 3m /nix/store/5h0xq1qilwrsfckjn60af171xj6qz994-stt-server-env-wrapped/bin/python -I -c 'import stt.server.engine, stt.server.reader, …'` (cwd = scratchpad) | exit 0, peak 20 MiB; `stt` loads from the store's site-packages; `av 19.0.2`, Python 3.12.13; the built `DEFAULT_CHILD_COMMAND`/`DEFAULT_ENGINE_CMD` carry `-I` (a 003 build). |
| Beyond T040 (engine imports its heavy stack lazily, `src/stt/server/engine.py:48,55,72,157`) | same interpreter, `-I -c 'import ctranslate2, faster_whisper, torch, pyannote.audio, stt.transcribe, stt.diarize'` | exit 0, peak 575 MiB, torch 2.13.0+cu130. A torchcodec "cannot load libavutil" warning is printed; it is independent of `-I` and benign because `stt.diarize` hands pyannote an in-memory waveform (`src/stt/diarize.py:829-832`). |

## Closure of the targeted findings

| Finding | Status | Evidence |
|---|---|---|
| adversary **H1** (§4.2 lets a parser-reachable advisory be dismissed) | **CLOSED** | `docs/server-security.md:363-366`: the verdict 「서버 경로에서 열리지 않음(§2)」 is allowed only when the vulnerable code is used by the decoder alone; advisories in parameter-set/SEI/header/CBS code of parser-run codecs (measured H.264; candidates MPEG-4 Part 2, VC-1, AV1, HEVC, VP8/VP9, MPEG-1/2) are parser advisories; an undecidable case defaults to "parser" (fail-closed). §2.1 `:133-137` explains the code sharing and marks it unverified for 9.0.2; §4.1 `:349` adds "분석기가 해독기와 나눠 쓰는 코드는 남는다"; research R3 `:154` drops 「훨씬 작은 코드」 as a safety argument; `contracts/operator-log.md:83-85,136-138` record the narrowing. Marker `stt:policy` unchanged; doc test green. |
| adversary **M1** (BSF and cross-demuxer code not named) | **CLOSED** | `docs/server-security.md:139-142` names `extract_extradata` (H.264/HEVC/AV1 …) and the S/PDIF, ID3v2/APE and Ogg per-codec header code, says both are unmeasured on FFmpeg 9.0.2, and forbids skipping such advisories as "outside the list". |
| adversary **M2** (first-recording check passes on an empty read; no way to learn of the recording) | **CLOSED** (residual Low **N3**) | `docs/server-security.md:131`: pass needs a non-empty transcript, no failure line, no early-stop line and no truncation line at that time; states that a first-frame stop ends `done` with an empty transcript plus both lines. Code agrees: early stop → verdict `ok` (`src/stt/server/reader.py:221-247`), 0 bytes → `is_truncated` (`worker.py:68-79,153-154`), engine accepts empty audio (`engine.py:180`, `allow_empty=True`; pinned by `tests/server/test_decoder_us3.py:500`). States the caller's notice as the only signal and the four names as 「알려질 때까지 확인 안 됨」 (also `tasks.md` T034). |
| adversary **M3** / qa **Low 1** (absolute "never stops the worker") | **CLOSED** for the absolute claim (new Low **N1**, residual Low **R1**) | `docs/server-security.md:166-167,171`, `contracts/operator-log.md:37-39`, `contracts/reader.md:80` now say only `RecordingError` and transient `OSError`/`ValueError` are classified, other exceptions rise, and a repeat of the error in the re-check/settle stops the worker without a table line. Matches `worker.py:352-369` (catch at `:356-360`; `_is_running` at `:361` outside the `try`; `_settle` → `load_job` at `:278`) and `layout.hold` opening a new descriptor per call (`layout.py:167-174`). |
| adversary **M4** (operator re-transcribes a user's recording) | **CLOSED** for the privacy point (new Low **N2**) | `docs/server-security.md:168` and `contracts/operator-log.md:101`: ask the caller to resubmit, or use only an operator-made known-good file; 「이용자의 녹음을 재전사하지 않는다」. |
| adversary **M5** (C-003-3 does not discriminate 002 from 003) | **CLOSED** | `quickstart.md:125-128` and `tasks.md` T034 add a host Speex step: same ffmpeg arguments as the VM fixture (`nix/vm-test.nix:29,45`: `-ac 1 -ar 16000 -c:a libspeex -f ogg`, built with `pkgs.ffmpeg`), expected probe `failed` and `reader child probe exited with status 1` — the same lines the VM asserts in B-003-1(3) (`nix/vm-test.nix:950-961`); 002 gives `ok` (T005 RED expectation, `tasks.md:99`). Uses the known-good-file snippet, file removed afterwards (FR-017). Host ffmpeg having `libspeex` is UNVERIFIED; if absent the step fails loudly at file creation, not silently. |
| adversary **L7** / qa **Low 2** (`<audio_id>` vs `<request_id>`) | **CLOSED** | `docs/server-security.md:166`, `contracts/operator-log.md:99`: 「2차 작업은 `jobs/<request_id>/job.json` 의 `audio_id`」; `layout.py:67-68` (`jobs`, `audio` under the state root `/var/lib/stt`, `nix/module.nix:24`). |
| qa **Low 4** (`-I` engine in the full `server` closure) | **CLOSED** | `research.md:330` records the build and the exit-0 import; re-run above reproduces it, and the heavy stack also imports under `-I`. `.#server` is `services.stt.package`'s default (`flake.nix:72`). |

## Spec compliance of T036–T040 (task text in `tasks.md:346-369`)

| Task | Compliant | Note |
|---|---|---|
| T036 | Yes | Every element of the task text is present (decoder-only condition, named codecs, measured vs candidate, §2.1, §4.1, R3). Extra fail-closed default and contract note are consistent. |
| T037 | Yes | Both paths, examples and the "not measured on 9.0.2" sentence present. |
| T038 (a)–(e) | Yes | (a)–(d) as written; `contracts/reader.md:80` also narrowed (consistent extra). (c) reproduces the task's own wording, which carries **N1**; (e) adds a parenthetical that goes beyond the task (**N4**). |
| T039 | Yes | Step added to both C-003-3 and T034. |
| T040 | Yes | Built, imported with exit 0, one line in R10. |
| Doc test GREEN after each doc task | Yes at HEAD | 21 passed (above). Intermediate commits not re-run (docs only, same markers). |

## Discovery

### Critical (blocks deployment)

None.

### High (must fix)

None.

### Medium (should fix)

None.

### Low (information)

- **N1 [L3/L2 doc ↔ code] `docs/server-security.md:171` (and `:167` 「작업 기록을 계속 읽을 수 없으면 … 아래 한계」),
  `contracts/operator-log.md:38-39`, `contracts/reader.md:80` — for a persistently unreadable job record the new text promises
  「그 작업은 다음 시작 때 `failed interrupted` 가 된다」; the code does not get there.**
  - Scenario (the adversary's M3 example): one `jobs/<id>/job.json` becomes undecodable while its job reads. The re-check at
    `worker.py:361` raises, the worker exits (as the doc now says). At the next start `worker.run` calls
    `lifecycle.clear_dead_worker` (`worker.py:482-484`) → `records.load_jobs` (`lifecycle.py:234-235`), which decodes every
    `*/job.json` with no per-record guard (`records.py:173-178`) and raises the same `ValueError`/`KeyError` before any job is
    finished; `cli._work` catches only `MissingGpuLockError` (`cli.py:121-133`). The worker therefore fails at every start (the path
    unit has no start limit, `nix/module.nix:78-81`), the job stays `running`, and the sweep's dead-worker step fails at the same
    call (`sweep.py:52`, logged as a fault). No `failed interrupted` line appears.
  - The promise holds for the other named case, descriptor shortage, because the limit is per process and a restart clears it.
  - Legacy behaviour (001/002), reachable only with a corrupt record; doc-only inaccuracy, hence Low.
- **N2 [L4 operator procedure] `docs/server-security.md:168`, `contracts/operator-log.md:101` — the new `engine_failed` advice
  sends the operator to §3 「안전한 재확인」, which never runs the engine.** The snippet (`docs/server-security.md:287-300`)
  calls only `reader_client.probe`/`decode`; `engine_failed` is a GPU-stage verdict (`worker.py:374-378`). A known-good file
  will print `ok … ok` while the engine fault persists, so the operator may conclude "not reproducible". The sanctioned way to
  exercise the engine with an operator-made file is to submit it as a first-pass job, as quickstart C-003-3 (optional) and
  C-003-4 already do.
- **N3 [L4 / A15 auditor] `docs/server-security.md:131` — the caller's notice is defined as 「그 녹음이 `.wma`·`.m4a` 작업이었다는
  보고」, which does not identify the codec.** Most `.wma` are WMA 1/2 and most `.m4a` are AAC, both already tested; a report
  of the extension alone, followed by the pass criterion, would mark `wmapro`/`wmavoice`/`wmalossless`/`amrwb` as checked
  without any of them having run. The notice has to name the codec (from the caller's or recorder's side). Minor addition: the
  "non-empty transcript" condition can be checked by size (`test -s`) without reading a user's transcript; the text does not say
  so.
- **N4 [L3 doc ↔ test] `docs/server-security.md:371` — 「이름은 §2.1 의 첫 실제 녹음 확인으로만 본다」 understates existing
  coverage.** `tests/server/test_hardening_us1.py:457-463`
  (`test_hardening_fr_1_every_name_on_the_list_is_an_installed_audio_decoder`) fails at step (2) 「빌드·시험」 if any of the 32
  names, the four included, disappears from the installed PyAV, and T034 checks the same four names on the deployed interpreter.
  What only the first real recording can show is that the name is still the decoder FFmpeg picks for that codec and reads it to
  the end. Same over-statement in `research.md:136` (pre-existing).
- **N5 [L5 cross-artifact consistency] `specs/003-reader-hardening/spec.md:31`, `research.md:379-380` (R13 decision) — still state
  the un-narrowed rule** (「앞으로 나올 영상 해독기 공지의 판정 … 을 「서버 경로에서 열리지 않음」으로 줄인다」). The operator acts on
  `docs/server-security.md`, which is correct; a reader of the spec or R13 alone gets the pre-audit rule. T036's text did not
  include these files, so this is not a task-compliance gap.
- **R1 [residual of adversary M3, not addressed by T038] `docs/server-security.md:167` — the `unexpected` "볼 것" still points to
  「같은 시각의 워커 저널(기술자·메모리)」.** The worker drops the exception without its class (`worker.py:358-360`), so the journal
  holds only `<id> failed engine_error unexpected` for this cause. Unchanged text, carried over for completeness.

### Layer notes

- **L1** no code in range; nothing to lint. **L5** markers (`stt:audio-decoders`, `stt:engine-error-causes`, `stt:policy`, …)
  untouched; table rows keep four cells and no stray `|`; doc test green.
- **L2** contracts and the doc agree with each other on every edited sentence (operator-log §2/§5, reader §5); the only
  doc ↔ code mismatches are N1 and N4.
- **L7** no secrets, commands or paths that widen access. The new host step writes an operator-made sine Speex file under `/tmp`
  and reads it through the reader as `stt`, the path §3 already approves; no server recording is opened.
- **L6** the adversary re-audit runs in parallel (one-way); its results were not available. The original adversary report was
  used as the finding list above.
- **L3** both sides read for each new claim: §3 limits ↔ `worker.py:348-369`, `layout.hold`, `lifecycle.clear_dead_worker`,
  `records.load_jobs`, `cli._work`; §2.1 criterion ↔ `reader._journal_line`/`_verdict`, `worker.is_truncated`, `engine.py:180`;
  quickstart Speex ↔ `nix/vm-test.nix` B-003-1(3); §4.2 exception ↔ `test_hardening_us1.py:457`; R10 ↔ the built store path.
- **L4** operator flows: C-003-3 now separates 002 from 003 on the host (M5). The `engine_failed` flow (N2) and the
  first-recording flow (N3) still have a gap each; neither affects jobs or callers.

## Plan

1. N1 → in `docs/server-security.md:171` and `contracts/operator-log.md:38-39` (`contracts/reader.md:80` follows), split the two
   cases: descriptor shortage → the worker stops and the job ends `failed interrupted` at the next start; an unreadable job
   record → the worker cannot start again (the dead-worker clean-up reads every record) until the operator moves or repairs that
   `jobs/<request_id>/job.json`, the same as 001/002. → affected test: `tests/test_server_security_doc.py` (re-run, markers
   unchanged).
2. N2 → replace 「§3 「안전한 재확인」으로 본다」 with 「운영자가 만든 알려진 정상 파일을 1차 작업으로 맡겨(quickstart C-003-3 처럼) 엔진이
   `done` 인지 본다」 in `docs/server-security.md:168` and `contracts/operator-log.md:101`. → `tests/test_server_security_doc.py`.
3. N3 → in `docs/server-security.md:131` say the caller's notice must name the codec (WMA Pro/Voice/Lossless, AMR-WB), not only
   the extension; optionally "전사록은 크기로만 본다". Mirror in `tasks.md` T034 wording if desired. → `tests/test_server_security_doc.py`.
4. N4 → reword `docs/server-security.md:371` to "넷은 이름이 있는지만 `test_hardening_fr_1_…` 가 보고, 끝까지 읽히는지는 §2.1 의 첫 실제
   녹음 확인으로만 본다". → `tests/test_server_security_doc.py`.
5. N5 → one clause in `spec.md:31` and R13 pointing to the narrowed §4.2 rule (spec-first, since the spec is the authority).

Order: 1–4 can ride together in the T033 doc commit (same file, no marker changes); 5 independent. None blocks T033.

### Not fixed (reason)

- All items: auditor is discovery-only; every item is Low and documentation-only.
- The behaviour behind N1 (a corrupt record stops the worker, intake and sweep at `load_jobs`) is legacy 001/002 behaviour and
  out of 003 scope; changing it would be a separate spec item.
- R1: not part of T038's text; carried over, low value.

## Verdict

PASS — Critical 0, High 0, 경계면 불일치 0건, 통합 호출 누락 0건
(Medium 0, Low 6: five new documentation-accuracy items N1–N5 and one carried-over residual R1. Targeted findings H1, M1, M2,
M3, M4, M5, L7 and qa Low 1, 2, 4 are all CLOSED; T036–T040 comply with their task text.)
