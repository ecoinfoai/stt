# T051 scoped re-audit: qa-engineer

- Scope: fix range `6357f25..372fd73` (T045–T050) only. The question is whether each finding the fixes claim to close is closed,
  and whether the fixes add defects or regressions in what they touch. Parked items (ledger rulings 28 and 30) are not re-reported.
- Read on both sides of each boundary: `src/stt/audio_decode.py` ↔ `src/stt/server/reader.py` (child) ↔ the supervisor
  (`_verdict`/`_journal_line`/`_supervise`) ↔ `src/stt/server/reader_client.py` ↔ `src/stt/server/intake.py:209` and
  `src/stt/server/worker.py:110` ↔ `src/stt/server/engine.py`. Also `src/stt/transcribe.py:780-791` (CLI),
  `tests/server/gated_decode_child.py` (passes the child's exit code through), `nix/module.nix:202-261` ↔ `nix/vm-test.nix` /
  `nix/vm-reader-double.py`, and the spec, contracts, research, plan, quickstart and `docs/server-security.md` against the code.
- Runs:
  - `captest 4G 20m uv run python -m pytest -q`: **1190 passed**, 164 s, peak 802 MiB.
  - `ruff check --select F,E9,B` on every touched Python file: nothing inside the fix range. The hits it gives are older
    code: `reader.py:124` B905, `transcribe.py:1185` F821/B023, a false positive caused by `del model` in `finally`.
  - mypy is not installed and cannot be fetched offline, so the type check is UNVERIFIED.
  - No VM run (per the rules). The VM result at 372fd73 comes from the dispatch context.

## Closure check of the findings the fixes claim

| Finding | Claimed fix | Verdict | Evidence |
|---|---|---|---|
| adversary H1: host lock files reachable | `/run:ro` and `/var:ro` tmpfs, `-/nix/var` inaccessible | **Closed** for `/run`, `/var` and `/nix/var` (one residual check, L7) | `nix/module.nix:215-231`. The VM double now tries `flock -n` (`nix/vm-reader-double.py:94-107`, `:137`) on planted 0644 root files under all three trees (`nix/vm-test.nix:665-671`, `:783-786`, `:810`), and `assert_confined` requires 0 reaches. The files exist on the host while the double runs (`common`, `:887`). The positive control (`control == 2` on `/etc/vm-double/targets.json`) still proves the double can reach a readable file. Both fcntl and flock are covered, because the files cannot be opened at all. |
| adversary H2: silent truncation at the first invalid frame | `write_s16 -> bool`, child exit 3, supervisor `ok` plus a fixed line, CLI warning | **Closed** (per the operator decision the verdict stays `ok`) | `audio_decode.py:73-94` breaks on `InvalidDataError` with the `# intentional-skip:` marker and the true reason, then returns `stopped_early`. `reader.py:356-360` and `:380-383` map it to `EARLY_STOP_EXIT`. `reader.py:208-236` sends `ok` for decode only. `reader_client.py:172-173` accepts exactly `{"status": "ok"}`. I checked the generator claim against `.venv/.../av/container/input.py:246-260` (`yield from packet.decode()`) and `faster_whisper/audio.py:79-88`: the old `continue` did reach `StopIteration`, so clean recordings still decode to the same bytes (the existing equality tests are green). |
| adversary M1: no record of an abnormal reader end | `_journal_line`, five fixed lines | **Closed** | `reader.py:212-227` and `:263-268`. `op` comes only from `_parse_request` (`probe`/`decode`), and `returncode` is an int, so nothing from the caller reaches the line. The line is written after the verdict (`test_the_verdict_is_sent_even_when_writing_the_journal_line_fails`). Parametrised tests cover exit 1, SIGSYS 31, SIGKILL 9, early stop, a probe ending with code 3, the timeout, six invalid probe results, a valid probe and a hang-up. The VM sees the line through the real unit (`vm-test.nix:756-759`). The table in contracts/reader.md §5.7 matches the code line for line. |
| adversary M2/M3: docs | §2 residual risk reworded; §3 rule not to open server recordings | **Closed** (wording issues L1–L3) | `docs/server-security.md:49-55`, `:142-145` |
| qa M1 (T040): sandboxed decode only checked for PCM WAV | VM per-format sine test | **Closed** | `vm-test.nix:28-36`, `:435-450`. All seven demuxers of `AUDIO_FORMATS` are covered (mov/m4a, mp3, wav, flac, ogg Vorbis, ogg Opus, ADTS aac, asf/wma) by real probe and decode through `ReaderConfig()` as user `stt`, with deterministic checks on duration, length and peak. One small timing issue is L4. |

No regression found in what the fixes touch:
- `write_s16` has two callers (`reader._decode`, `decode_samples`) and both changed together.
- `decode_samples` gained only a keyword-only argument that defaults to None.
- `gated_decode_child.py` returns `reader.main()`, so exit code 3 passes through.
- The supervisor still imports only the standard library (that test is green).
- intake only probes, and a probe exiting with code 3 is `failed`, as the contract says.
- The VM shows Python and PyAV start with `/run` and `/var` covered, and `StandardError=journal` still delivers lines, because the stream fd is set up before the namespace.

## Discovery

### Critical (blocks deployment)
- None.

### High (must fix)
- None.

### Medium (should fix)
- **M1 [L4/L5] Edge case 10 has no test of its observable outcome: the job ends `done` with the audio before the damage, and with
  an empty transcript when the first frame is damaged.** T047 says it "closes 경계 사례 10", but it tests the chain one hop at a time:
  `write_s16` (`tests/server/test_audio_decode.py:90-113`), `reader.main` in process (`tests/server/test_reader.py:1065-1080`), and
  the supervisor with a fake child (`test_reader.py:615`). Nothing runs worker → reader → engine for an early stop.
  - `grep early-stop tests/` finds only `test_reader.py` and `fake_decode_child.py`.
  - `tests/server/test_decoder_us3.py` has `test_decoder_edge_4_*` and `edge_5_*` but no `edge_10`. That breaks plan VII's naming
    rule and the "every scenario maps to ≥1 test" rule.
  - The empty-transcript claim, repeated in `spec.md` edge case 10 and `docs/server-security.md:133`, depends on the engine
    accepting a 0-frame `audio.wav` (`engine.py:142-153`, `allow_empty=True`). It is UNVERIFIED for real zero-sample input. It is
    plausible, because a silent recording reaches the same empty post-VAD array (`faster_whisper/vad.py:193-199`).
  - Scenario: a later change in the worker or reader_client treats the code-3 path differently, for example a size check against
    the probe duration, and no test fails.
- **M2 [L3 docs↔code / L4 host verification] `quickstart.md` and T042 were not updated for the fixes, so the host check after
  deployment never confirms the FR-023 addendum or US2-10 on the real host.**
  - B-P2-1 still expects only `TemporaryFileSystem=/dev/shm:ro` (`specs/002-decoder-isolation/quickstart.md:69`). The VM asserts
    `{/dev/shm:ro, /run:ro, /var:ro}` (`vm-test.nix:415`).
  - B-P2-4's target list (`quickstart.md:77-83`) has no host lock files, `/nix/var/nix/db`, `/var` host file or `flock`.
  - B-P2-3 (`:71-72`) does not mention the journal line the VM now checks.
  - There is no B-P2 step for the per-format read (T049). The new subtest is named `(US2-8, SC-007)`, not after a quickstart step,
    although the convention in `tasks.md:43` asks for that.
  - C-P2 (`quickstart.md:136-158`) and T042 (`tasks.md:357-361`) have no step that does any of the following on the host:
    - looks for `reader child probe exited with status 1` after the C-P2-2 text attachment;
    - confirms `journalctl -k -g 'type=1326'` is still empty after the reads;
    - checks that a reader instance cannot see `/run/nixos` or `/nix/var`.
  - The VM is not the host: the host may hold different things under `/run`/`/var`/`/etc`, which is why T042 exists. C-P2-2 would
    still catch a reader broken by the `/run` cover, because the normal recording must complete. The new log and isolation
    behaviour, though, is never checked on the host.

### Low (information)
- **L1 [L5 docs] The "two layers" wording is now inaccurate.** `docs/server-security.md:39-41` describes `/var/lib/stt`
  (permissions plus `InaccessiblePaths`) and `/run/stt/gpu.lock` (`InaccessiblePaths` plus the `/run` tmpfs) as covered by two
  layers. At the default paths, the entries `-/var/lib/stt`, `-/var/lib/stt-models` and `-/run/stt` now do nothing. Under the
  empty tmpfs those paths do not exist and the `-` makes systemd skip them; research R12 (`research.md:286-287`) and the
  `module.nix:213-214` comment say exactly that. The real cover is the tmpfs alone. The `InaccessiblePaths` entries act only for a
  non-default `stateDir`/`modelsDir`, or if the tmpfs entry is removed later. `contracts/module.md:39` ("권한 위에 한 겹 더")
  has the same drift. This reasoning follows systemd's mount order (tmpfs on `/run` before `/run/stt`, then `lstat` → ENOENT → ignored);
  the effective mount table was not inspected.
- **L2 [L5 docs] The OOM case is described as producing `killed by signal 9`, and the line is called the only trace.**
  `docs/server-security.md:128-129` and `contracts/reader.md:80` say so. The reader unit leaves `OOMPolicy` at its default, which is
  `stop` (`grep OOMPolicy nix/` finds nothing). After the kernel kills the child, systemd stops the instance with `KillSignal=SIGKILL`.
  That may kill the supervisor before it writes the line, so whether the line appears depends on a race (UNVERIFIED). Meanwhile systemd
  writes its own `oom-kill` lines under the unit, which the VM proves for the memory double (`vm-test.nix:1002`). "이 줄이 단위 수준의
  유일한 흔적이다" is therefore wrong for OOM and `RuntimeMaxSec`.
- **L3 [L5 docs] `docs/server-security.md:121-140` explains every line except the most common one, `reader child probe exited with
  status 1`.** That line appears for every unreadable upload (a text file, a broken header, a format outside the list), as
  `vm-test.nix:758` shows. Since the section frames these lines as abnormal ends, an operator may take routine bad uploads for incidents.
- **L4 [L4 VM test] The negative journal check at `vm-test.nix:449-450` can miss a line.** The supervisor writes its line after it
  sends the verdict (`reader.py:264-268`), and journald reads stream lines asynchronously. A line from the last one or two formats
  can therefore land after `journalctl` has run. Failures are still caught deterministically by the per-format outcome checks. The only
  thing that could slip through is an early stop near the end of a tone, within the 0.35 s tolerance.
- **L5 [L2 test double] `tests/server/fake_decode_child.py:159` writes `bytes(args.count or ONE_SECOND_OF_SILENCE_BYTES)`.** With
  `--count 0` it writes one second rather than nothing, which contradicts the docstring ("write `--count` zero bytes"). So the
  first-frame-damage case (empty output plus exit 3) cannot be set up at supervisor or worker level. The same idiom already exists at `:92`.
- **L6 [L4 handoff] The consumer is not told that a `done` result may be partial.** FR-023 requires the way to check this to go into
  the docs **and the reply** (FR-027). Edge case 10 reaches bhu-llm only as a normal `done`; the only hint is that
  `summary.audio_duration_s` holds the decoded length. The response carries no probe duration
  (`grep duration src/stt/server/schemas/response.schema.json` is empty). T043's content list (`tasks.md:362-363`) mentions neither edge
  case 10 nor the reader lines.
- **L7 [L7 UNVERIFIED] H1 is closed for the three trees named.** World-readable lock targets elsewhere on the real host (for
  example under `/boot` or `/etc`, or directories some service locks) have not been listed, and the VM's tree is not the host's.
- **L8 [L5 CLI] The CLI warning does not name the file** (`src/stt/transcribe.py:782-787`). In a batch, `[n/total] name` goes to
  stdout (`:1194`) and the warning to stderr. If the two streams are split (`2>err.log`), a warning cannot be tied to its recording.

## Plan

1. M1 → add `test_decoder_edge_10_a_decode_that_stopped_early_ends_the_job_done_with_the_audio_before_it` to
   `tests/server/test_decoder_us3.py`.
   - Set it up with `reader_socket.use_supervisor(child_command("--mode", "early-stop", "--count", "64000"))` and `run_worker_once`.
   - Assert the response state is `done`, that `outbox/<id>/result.json` exists, that the worker's stdout lines are unchanged, and
     that the GPU stage got 2 s of audio, read from the fake engine's input or `summary.audio_duration_s`.
   - For the empty variant, fix L5 first.
   - Regression guards: `tests/server/test_reader.py::test_the_supervisor_writes_one_fixed_line_when_the_child_ends_abnormally_and_the_verdict_stays[decode-early-stop]`
     and `tests/server/test_audio_decode.py::test_a_recording_damaged_from_its_first_frame_gives_empty_output_and_reports_the_early_stop`.
2. L5 (before the empty variant of M1) → in `fake_decode_child.py`, default `--count` to `None` and test `is None`. Check that no
   other test depends on `--count 0`. Affected: `tests/server/test_reader.py` cases using `write-bytes`/`early-stop`.
3. M2 → update `quickstart.md`:
   - B-P2-1: the three tmpfs entries and the four `-` hidden paths.
   - B-P2-3: the fixed probe line.
   - B-P2-4: the `host-files` group and `flock`.
   - A new B-P2 step, or a renamed subtest, for the per-format read.
   - C-P2-2: on the host, grep `journalctl -u 'stt-reader@*'` for the probe line, confirm `journalctl -k -g type=1326` is empty after
     the reads, and optionally use `sudo nsenter -t <PID> -m ls /run /nix/var` from C-P2-7.
   - Then name these in T042. Documentation only, no tests. Rename the VM subtest title if a B-P2 step number is added
     (`nix/vm-test.nix:435`).
4. L4 → before `vm-test.nix:449`, wait until `systemctl list-units --all --plain --no-legend 'stt-reader@*'` is empty, then run
   `journalctl --sync`, then do both negative checks. Affected: the VM subtest "every format of the reader's list…".
5. L1, L2, L3 → wording in `docs/server-security.md` §2/§3 and `contracts/module.md:39`, `contracts/reader.md:80`:
   - the tmpfs is the cover, and `InaccessiblePaths` is the fallback for non-default paths;
   - OOM may leave no supervisor line, so look for systemd's `oom-kill` lines under the unit;
   - add a bullet for `exited with status 1`, the usual result of an unreadable upload.
   Documentation only.
6. L6 → add edge case 10 and the reader lines to T043's scope (`handoff/p2-reply.md`).
7. L7 → at T042, list world-readable lock files on the host outside the covered trees, for example
   `sudo find / -xdev \( -path /proc -o -path /sys -o -path /nix/store -o -path /run -o -path /var -o -path /nix/var \) -prune -o \( -name '*.lock' -o -name '*.lck' \) -perm -o=r -print`.
   Record the result in R12. If something is found, add a task.
8. L8 → optional: add `media.name` to the warning text and update
   `tests/server/test_audio_decode.py::test_the_command_line_warns_once_on_standard_error_when_the_decode_stopped_early`.

### Not fixed (reasons)
- mypy over the touched files: the tool is not available offline. Suggest running it in CI or the devShell before T041.
- `transcribe.py:1185` F821/B023 and `reader.py:124` B905: older code outside the fix range, and the F821 is a false positive.
  Mentioned only.
- The parked items (adversary L1–L12, L14, test-side silent skips, qa Lows, EOF-truncated or zero-packet stream ending `done` with no
  log line) were not re-reported.

## Verdict

PASS — Critical 0, High 0, boundary mismatches 0, missing integration calls 0
(Medium 2: M1 edge case 10 has no worker-level test; M2 the quickstart and T042 host checks predate the fixes. Low 8.)
