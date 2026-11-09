# T040 adversary report — 002 decoder isolation

- Auditor role: project-audit adversary (persona attack + silent-skip). One stateless run.
- Range: `master` (`be72910`, P1 deployed) ... `HEAD` (`5b8220f`), P1 included.
- Method: read code, Nix module, VM test, specs and docs only. No crafted or malformed input was created or run (FR-028).
  No test suite was run. Host facts below were read-only metadata checks on miniecoinfo (`stat`, `systemd-analyze`, sysctl reads).
- Threat model used: a hostile recording compromises FFmpeg/PyAV inside the reader's decode child; what that child (and,
  through it, its supervisor) can reach, delay, forge or leak.

Host facts checked for this report (2026-11-10, read-only):

| Fact | Value | How |
|---|---|---|
| systemd on the host | 262 (VM test runs 261.1) | `systemd-analyze --version` |
| Reader filter, expanded | 368 calls allowed; `ptrace`, `pidfd_getfd`, `process_vm_*`, io_uring, `@keyring`, `userfaultfd`, `bpf`, `open_by_handle_at` denied; `flock`, `fcntl`, `inotify_*`, `kill`, `memfd_create`, `splice` allowed | recursive `systemd-analyze syscall-filter` of `@system-service` minus the module's exclusions |
| Yama | `kernel.yama.ptrace_scope = 1` | `/proc/sys/kernel/yama/ptrace_scope` |
| `/run/nixos` / `switch-to-configuration.lock` | `0755 root` / `0644 root` | `stat` |
| switch-to-configuration-ng | takes a lock on that file ("Acquiring lock on file /run/nixos/switch-to-configuration.lock", "Could not acquire lock") | `strings` on `.switch-to-configuration-wrapped` |
| Nix DB | `/nix/var/nix/db` `0755`; `db.sqlite`, `db.sqlite-shm`, `db.sqlite-wal` `0644 root` | `stat` |
| HF token | `/run/agenix/stt-hf-token` `0400 root` (not reachable) | `stat` |
| cgroup cpu controller | enabled in `system.slice` | `cgroup.subtree_control` |

## Discovery

### Critical (배포 차단)

- None. No path was found by which a compromised reader reads or changes another job's recording, decoded audio, hints,
  results, requests, responses or records, reaches the GPU or the network, or forges anything the caller accepts beyond its
  own job's samples and duration.

### High (수정 필수)

- **H1 [persona T1 hostile uploader / B7 lock contention] `nix/module.nix:204-255` — a compromised reader can take
  locks on world-readable host lock files and stall host administration.**
  Scenario: "An uploader's recording exploits the decoder. The child opens `/run/nixos/switch-to-configuration.lock`
  (0644 root, in `0755` `/run/nixos`) read-only and calls `flock(LOCK_EX)`; it also opens `/nix/var/nix/db/db.sqlite-shm`
  (0644) read-only and takes `fcntl(F_SETLK, F_RDLCK)` on SQLite's WAL write-lock byte. While the instance lives (probe
  ≤ 35 s, decode ≤ 245 s, unit backstop `RuntimeMaxSec=300`), `nixos-rebuild switch` blocks or fails ("Could not acquire
  lock") and nix-daemon writers get `SQLITE_BUSY`. Queuing a few hostile uploads keeps an intake probe and a worker decode
  overlapping, so the operator cannot deploy the FFmpeg fix until they stop `stt-intake.path`/`stt-worker.path`."
  Evidence:
  - Nothing in the reader unit hides `/run` (only `/run/stt`) or `/nix/var` (`nix/module.nix:213-221`). `ProtectSystem=strict`
    makes them read-only, but `flock(2)` needs only a readable fd and works on read-only mounts. `fcntl` read locks need only
    `O_RDONLY`.
  - `flock` and `fcntl` are in the expanded reader filter (host table above).
  - The project already treats this exact capability as a threat for its own lock: research R12
    (`specs/002-decoder-isolation/research.md:279`) and `contracts/module.md:39` add `/run/stt` to `InaccessiblePaths`
    because `gpu.lock` is 0644 and could be `flock`ed. The same reasoning was not applied to the host's other 0644 lock files.
  - The VM permission double (`nix/vm-reader-double.py`, targets `nix/vm-test.nix:729-742`) checks only stt paths, devices,
    `/dev/shm` and sockets; no host lock file is in the target list.
  - Relation to the accepted residual: the 2026-11-09 decision (`docs/server-security.md:45-47`, `handoff/p1-reply.md:106`)
    accepts that the reader can **read** world-readable files. Locking them is an availability effect on the host's
    configuration management, which that decision does not describe. The mitigation as built is weaker than described.
  - UNVERIFIED: whether switch-to-configuration-ng blocks or exits on contention (both are a denial of service), and how
    long Nix's SQLite retry loop waits. The lock reachability itself follows from the permissions and filter above.
  Tag: PERSONA_ATTACK_SUCCESS (threat-model persona outside the catalog, see Catalog 보강) / HOSTILE_ENV_FAIL (B7).

- **H2 [silent-skip except-pass / B1 corrupt input] `src/stt/audio_decode.py:52-61` (used at `:80`) — new production code
  swallows `InvalidDataError` with `continue`, with no log, no exception and no `# intentional-skip:` marker; and the
  "skip" is in fact a silent truncation.**
  Scenario: "A user uploads a recording with one damaged frame at minute 20 of 60. `container.decode(audio=0)` raises
  `InvalidDataError` there. `_valid_frames` catches it and calls `next(frames)` again, but `InputContainer.decode` is a
  generator (`.venv/lib/python3.12/site-packages/av/container/input.py:258-260`, `yield from packet.decode()`), and a
  generator that raised is finished, so `next` raises `StopIteration` and the decode ends. `write_s16` flushes, the child
  exits 0, the reader answers `ok`, the job ends `done` with a 20-minute transcript. Nobody is told; if the first frame is
  bad the job is `done` with an empty transcript instead of `unreadable_audio`."
  Evidence and context:
  - `audio_decode.py` is new in this diff. Mitigation exclusions (a) log, (b) raise, (c) `# intentional-skip: {reason}` —
    none is present; the codebase uses (c) elsewhere (`intake.py:91`, `worker.py:227`, `worker.py:331`, `sweep.py`).
  - The module docstring (`audio_decode.py:5-7`) and `contracts/reader.md:83` describe the behaviour as "skipping frames",
    which is not what happens.
  - It is byte-for-byte parity with faster-whisper's `_ignore_invalid_frames` (same truncation existed in 001 inside the
    engine), and SC-007 baseline parity depends on it. The new part is that stt now owns the code and documents it wrongly.
  - Not executed (FR-028); reasoned from the PyAV source above and Python generator semantics. An in-process unit test with
    a fake generator that raises once can show it without any media file.
  Tag: SILENT_SKIP_NEW.

### Medium (수정 권장)

- **M1 [B9/C2 observability, FR-023] `src/stt/server/reader.py:143-146`, `:243-244`, `:343-349`; `nix/module.nix:203` —
  server-side decoder faults and blocked exploit attempts surface only as per-job `unreadable_audio`.**
  Scenario: "After a dependency bump, `import av` fails in the child under the reader's limits (an `ImportError`, an MDWE
  refusal, or a `MemoryMax` override that is too small). Every job and every intake probe now ends `unreadable_audio`."
  The child's exception is discarded (`except Exception: return 1`), its stderr is `/dev/null`, the supervisor logs nothing
  (contract §5.7) and exits 0 after any verdict, so systemd records success; failed instances are garbage-collected
  (`CollectMode=inactive-or-failed`), so `systemctl --failed` stays empty. The same holds when seccomp kills a child with
  SIGSYS: the only trace is a kernel `type=1326` line not tied to a request. Users are told their recording is unreadable
  (a server fault blamed on the recording, against the intent of FR-016), and the operator has no unit-level signal (FR-023).
  The spec's "any failure after start is `unreadable_audio`" (FR-015) is respected; the gap is operator visibility.
  Mitigation: the supervisor writes one fixed, content-free line to the journal when the child ends abnormally
  (`decode child ended by signal 31`, `decode child exit 1`; no job ID, path or decoder text), and `docs/server-security.md`
  §3 names reader-UID `type=1326` lines as a possible exploit indicator with a correlation step (time → intake/worker log →
  request ID). Needs an operator decision because it changes contract §5.7.

- **M2 [A15 auditor] `specs/002-decoder-isolation/handoff/p1-reply.md:104`, `docs/server-security.md:45-47` — the risk
  record says there is no way to get data out, but the result channel is one.**
  Scenario: "The P1 reply (already in bhu-llm ledger A18) states 'no network, so there is no path to exfiltrate'. Under
  P1 a compromised engine or intake runs as `stt` and can write other users' transcripts into its own job's
  `outbox/<id>/transcript.txt`, which bhu-llm delivers to the uploader. Under P2 a compromised decode child can still
  synthesize speech from a sample bank carried inside the recording, spelling out world-readable host files
  (`/nix/var/nix/db/db.sqlite` store paths, NetworkManager leases, `/var/lib/access-record-health/*.md`, `/etc`); Whisper
  transcribes it and the transcript returns to the uploader."
  The 2026-11-09 acceptance was recorded as "can read world-readable files"; the record does not say the contents can leave
  through the transcript. Mitigation: correct the residual-risk wording in `p2-reply.md` (T043) and `server-security.md` §2
  (read **and** return through the job's own transcript; bandwidth low, scope = own recording + world-readable host
  files), and let the operator re-confirm the acceptance with that wording. The filesystem narrowing proposed for H1 also
  shrinks this.

- **M3 [A13 well-meaning admin, cross C6] `docs/server-security.md` §3 (lines 62-104) — no safe procedure for
  investigating an `unreadable_audio` recording; the natural one runs the exploit unsandboxed.**
  Scenario: "A professor complains that a lecture failed as `unreadable_audio`. The reader gives no reason by design, so the
  operator runs `sudo stt transcribe /var/lib/stt/audio/<id>/audio.m4a` or `ffprobe` on it before 23:58. The CLI is not
  isolated (FR-022), opens every FFmpeg format (no whitelist, `audio_decode.py:31-49` with `format_whitelist=None`), and
  now runs the hostile file as root or as `stt` with every user's recordings, the GPU and the network in reach."
  The system offers no minimal way to look (the A13 question: is what opens the minimum the purpose needs?). Mitigation:
  a §3 rule "never open a server recording with the CLI, ffprobe or ffmpeg; re-check it only through the reader" and,
  if wanted, an operator command that sends one stored recording through `/run/stt-reader.sock` as a probe and prints the
  verdict.

### Low (정보 / 레거시 WARN)

- **L1 `src/stt/server/reader.py:263-281`** — the child can send SIGINT to the supervisor (same UID and PID namespace;
  `kill` is allowed; Python installs a SIGINT handler, so PID 1 does not drop it). The supervisor raises
  `KeyboardInterrupt`, kills the child, prints a traceback (code paths only) to the journal and exits; the caller sees EOF
  after `started` → `failed` → `unreadable_audio`. Same outcome as exit 1, but research R5
  (`research.md:127`, "the child can only write pipe bytes and an exit code") is not quite true. Fix: reset SIGINT to
  `SIG_DFL` at the start of `serve()` (PID 1 then ignores it from inside the namespace).
- **L2 `nix/module.nix:229`, `contracts/module.md:66`** — metadata side channels between jobs. `ProtectControlGroups=yes`
  leaves `/sys/fs/cgroup` readable: an instance can list other `stt-reader@…` instances, their host PIDs and
  `memory.current`, and the worker's memory growth (which tracks another user's decoded length). `/run/systemd/units` and
  `inotify_add_watch` on world-readable directories give timing of other jobs. No content leaks; the claim "cannot see each
  other" is slightly broader than what holds. Options: `ProtectControlGroups=private|strict` (cgroup namespace, systemd
  ≥ 257, `[VERIFY]` on 261/262) and the `/run` narrowing from H1.
- **L3 `src/stt/server/intake.py:410-421`, `nix/module.nix:153-160`** — intake latency under hostile uploads. Accepted for
  `PROBE_LIMIT_S` (research R9, 2026-11-09): two slow probes ahead push a normal request past 001 SC-011. Not covered by R9:
  after about 17 hostile probes intake reaches `TimeoutStartSec=10min`; intake is PID 1 of its PID namespace without
  `KillSignal=SIGKILL`, ignores SIGTERM, keeps working 90 s and is then SIGKILLed mid-request (the orphan copy is cleaned by
  the stale sweep after 1 h, research R13). Recovers by itself; worth one line in R9.
- **L4 `nix/module.nix:300`** — `MaxConnections=4`: a burst in which old instances have not been collected yet makes
  systemd refuse a connection ("Too many incoming connections", logged); intake then drops that request with no answer
  (FR-016 path) and the worker reports `engine_error`. UNVERIFIED whether teardown latency can stack four instances with
  only intake and worker as clients.
- **L5 `nix/module.nix:204-255` [A5]** — no assertion on the systemd version. On a systemd without `PrivatePIDs`
  (< 257), unknown keys are ignored with a warning; instances (same dynamic UID) would then see and `kill` each other and
  could reopen another instance's probe result pipe (created by the supervisor, so owned by the dynamic UID) through
  `/proc/<pid>/fd` to forge its duration. Today's host (262) and VM (261.1, B-P2-4 asserts no other processes) are fine.
  Fix: `assertions` on `lib.versionAtLeast config.systemd.package.version "257"`.
- **L6 `src/stt/server/reader.py:42`** (and legacy `worker.py:31`) — `python -m` puts the working directory on
  `sys.path[0]`. In the unit it is `/` (root-owned), so not exploitable today; `-P` (Python ≥ 3.11) removes the hazard.
- **L7 `src/stt/server/worker.py:375-380`** — the read stage runs while the GPU lock is held, so a hostile recording keeps
  the CLI's GPU queue waiting up to ~245 s per job. Within spec ("GPU queue unchanged"); noted for capacity.
- **L8 `src/stt/server/reader.py:277-279` [A1]** — `stt server read` run by hand in a terminal exits 2 with no message.
- **L9 `src/stt/server/worker.py:299-301` [C5]** — any `OSError` around the read is logged only as `failed engine_error`;
  unlike intake (`describe_error`), the error class is not logged, so "socket missing" and "recording missing" look the same.
- **L10 `nix/module.nix:246`** — MDWE is defence in depth only: a compromised process can still patch its own code through
  `/proc/self/mem`. The `memfd_create` route is closed by `LimitFSIZE=0` (memfd writes and `ftruncate` hit RLIMIT_FSIZE).
  Information only.
- **L11 `docs/server-security.md:146`, research R7** — no codec allowlist. All five recorded advisories are video decoders
  that `avformat_find_stream_info` can open for a `mov`/`asf` track; `container_options={"codec_whitelist": …}` would cut
  that surface. Already listed as a follow-up.
- **L12 `src/stt/audio_decode.py:31-49`, `src/stt/transcribe.py:780-783` (CLI, out of scope)** — the CLI decodes without
  a format whitelist; media from `stt fetch` could make FFmpeg follow references (playlist-style demuxers) in the
  operator's context. Same as before the diff (faster-whisper opened files the same way). Information only.
- **L13 `specs/002-decoder-isolation/contracts/module.md:38,41,42,47,110`** — `[VERIFY] B-P2-1` markers remain although the
  VM test closed them (passed at `7ed79bc`). Document hygiene.
- **L14 `src/stt/server/reader.py:236-237`** — the verdict `_send` result is ignored after the caller hung up. Matches
  contract §5.6 ("nothing is sent after a hang-up"); no marker comment.
- Silent-skip, tests (low, not counted): `nix/vm-reader-double.py:50-52` (comment, no `intentional-skip:` marker),
  `:101-102`; `tests/server/conftest.py:537-538`, `:642-643`, `:649-650`; `tests/server/scripted_reader.py:96-97`.
- Legacy WARN (not in this diff, not blocking): `src/stt/server/intake.py:232` and `src/stt/server/worker.py:215`
  `shutil.rmtree(..., ignore_errors=True)` without a log.

## Plan

### Persona Attack 결과

- New or changed entry points (9):
  - E1 `stt server read` supervisor, stdin connection (`reader.py:212-281`)
  - E2 decode child `python -m stt.server.reader child probe|decode` (`reader.py:284-353`, `audio_decode.py`)
  - E3 caller side `reader_client.probe`/`decode` (`reader_client.py`)
  - E4 intake with `--reader-socket` (`intake.py:201-233`)
  - E5 worker read stage and `audio.wav` (`worker.py:85-119`, `:277-323`)
  - E6 engine reading `audio.wav` only (`engine.py:127-152`, `transcribe.py:788-841`, `:975-1019`)
  - E7 CLI decoding through `audio_decode.decode_samples` (FR-022)
  - E8 Nix units: `stt-reader.socket`, `stt-reader@.service`, P1 `hardening` on four units
  - E9 `--reader-socket` option (`server/cli.py:33-37`, `:79-83`)
- Group A: 15 personas, 20 scenarios, 5 expose a defect (A13 → M3, A15 → M2, A5 → L5, A1 → L8, A4 → part of M1).
  Fix direction: operator procedure for suspicious recordings, accurate residual-risk wording, a systemd version
  assertion, fail-fast messages.
- Group B: 9 personas, 14 scenarios, 5 scenarios in 4 personas expose a defect (B7 → H1, B1 → H2, B9 → L3 and L4,
  B3 → L9); B5 (lying reader) found no defect. Fix direction: hide host lock locations from the reader, make the
  frame-error path explicit, bound intake's run.
- Cross-persona: 6 scenarios, 3 expose a defect (C1 → H1, C2 → M1, C6 → M3).

Persona table (one line per persona: scenario → reasoned behaviour → result):

| ID | Scenario | Behaviour (code) | Result |
|---|---|---|---|
| A1 | New operator runs `stt server read` in a shell, or `stt server intake` as themself | exit 2, no text (`reader.py:277-279`); intake refuses the wrong account (001 FR-038) | L8 |
| A2 | Hurried admin restarts `stt-reader.socket` without stopping the path units, or calls P2 live while the old worker still runs | requests dropped / `engine_error`; old worker keeps decoding in-process until idle — both documented in `server-security.md:93-102` | pass (documented) |
| A3 | Wrong `--reader-socket` path, or `systemctl show stt-reader@.service` | every probe `unavailable` → requests dropped and logged; jobs `engine_error`; show of the template documented as an error | pass (Low, logged) |
| A4 | Host override shrinks reader `MemoryMax` or drops a sandbox key | every read OOM → all `unreadable_audio`, nothing at unit level | M1 |
| A5 | Module on a host with older systemd / without Yama | unknown keys ignored → cross-instance kill and probe-pipe forgery; Yama=0 → child can write `/proc/1/mem` (gains only the verdict) | L5 |
| A6 | Two users' recordings read at the same time (intake probe + worker decode) | separate instances, PID/IPC/tmp/net namespaces; signals, `setpriority(PRIO_USER)`, `kill(-1)` all namespace-bound; pipes owned by `stt` cannot be reopened | pass |
| A7 | Korean or emoji attachment name | schema allows only `<uuid>.audio.(m4a|…)`; reader never sees names | pass |
| A8 | BOM or smart quotes in request JSON | 001 boundary, unchanged | pass |
| A9 | Same recording transcribed five times | five independent reads, five work folders, all removed by `finish` | pass |
| A10 | Operator Ctrl-C during a manual decode | `ExitStack` closes connection and pipe → supervisor kills child; job → `interrupted` at next start | pass |
| A11 | Backup of the state root during a read | decoded audio only in pipe, worker memory (no swap) and `work/` | pass |
| A12 | `stt-requests` member or local user connects to the reader socket | `0600 stt:stt` → `EACCES` | pass |
| A13 | Admin investigates a failed recording | no safe path; CLI/ffprobe as root on the hostile file | M3 |
| A14 | Owner re-transcribes and deletes own recording | from_request reuses only the recording; delete stops the read within `poll_s` | pass |
| A15 | Auditor checks the residual-risk record | "no exfiltration path" is false (P1 and P2) | M2 |
| B1 | Truncated or damaged recording | first invalid frame ends the decode, job `done` with partial or empty transcript | H2 |
| B2 | Disk full while writing `audio.wav` | `storage_error` → `unreadable_audio`, work folder removed (edge case 5) | pass (per spec) |
| B3 | Recording file unreadable by `stt`; socket mode wrong | `unavailable` → intake drop / `engine_error`, error class not logged by worker | L9 |
| B4 | Network down / reader tries network | no network namespace, `socket()` denied (VM-verified) | pass |
| B5 | Compromised reader lies: wrong keys, NaN, 1e400, 4 KiB nesting, extra messages, SCM_RIGHTS, odd or oversize output, claims duration 0 | strict parsing (`reader_client.py:126-177`), no ancillary buffer → fds discarded + `MSG_CTRUNC` → failed; duration lie bounded by `MAX_DECODED_BYTES` and `DECODE_LIMIT_S`; only the attacker's own job is affected | pass |
| B6 | Encoding traps in protocol JSON (BOM, lone surrogates, invalid UTF-8) | `ValueError`/`RecursionError` caught → failed | pass |
| B7 | Reader holds locks on readable host files | `flock`/`fcntl` on `/run/nixos/…lock`, Nix SQLite | H1 |
| B8 | Clock jump during a read | monotonic deadlines on both sides, `ProtectClock` | pass |
| B9 | Memory bomb, fork bomb, CPU spin, hostile-upload backlog | `MemoryMax=768M`, `TasksMax=32`, cpu controller fair share; intake latency and `MaxConnections` edges | L3, L4 |
| C1 | A2 + B7: operator runs `nixos-rebuild switch` to ship the FFmpeg fix while hostile reads hold the lock | switch blocks or fails repeatedly | H1 |
| C2 | A4 + B5: reader misconfigured while hostile files also arrive | operator cannot tell config fault from hostile input | M1 |
| C3 | A10 + B1: worker killed mid-`audio.wav` write | `clear_dead_worker` removes `work/*`; reader sees hang-up | pass |
| C4 | A6 + B4 analogue: socket restarted while both stages read | documented loss (`server-security.md:96-102`) | pass (documented) |
| C5 | A7 + B3: permission error on a recording | only `failed engine_error` in the worker log | L9 |
| C6 | A13 + hostile recording | operator decodes it outside the sandbox | M3 |

Threat-model checks with no defect (for the record):
- Other jobs' files: `InaccessiblePaths` and DAC (`/var/lib/stt` 0751, `jobs`/`audio`/`work`/`queue` 0700, inbox 2770,
  outbox 2750, all `stt`); the recording fd is `O_RDONLY` and reopening through `/proc/self/fd` fails on the 0600 `stt` inode.
- Models, HF token: `/var/lib/stt-models` hidden; token 0400 root.
- GPU: `PrivateDevices`, no `CAP_MKNOD`.
- Supervisor takeover: `ptrace`/`pidfd_getfd`/`process_vm_*` filtered; `/proc/1/mem` blocked by Yama=1 (child is not an
  ancestor); sockets cannot be reopened through `/proc`; even a taken-over supervisor can only send a verdict.
- Persistence: no writable persistent path (`ProtectSystem=strict`, `LimitFSIZE=0`, per-instance tmp).
- Keyring, SysV/POSIX IPC, `/dev/shm`: blocked or private (VM-verified).

### Silent-Skip 결과

- New silent-skip in production code: 1 (high 1) — `src/stt/audio_decode.py:60-61`.
- New silent-skip in tests/test doubles: 6 (low) — listed under Low.
- Candidates reviewed and excluded: every `return None` / `except OSError: return …` in `reader.py` and `reader_client.py`
  maps to a protocol outcome (`started: false`, `failed`, `unavailable`, exit 1/2) that reaches the caller or systemd;
  `worker.py:116-118` and `:299-301` map to logged failures; `audio_decode.py:57-58` (`StopIteration`) is the normal end.
- Mitigation needed: `src/stt/audio_decode.py:60-61` — add `# intentional-skip:` with the true reason ("parity with
  faster-whisper; the decode stops at the first invalid frame"), correct the docstring and `contracts/reader.md:83`, and let
  the operator decide whether the server path should end such a read as `failed` instead.

### 미수정 (사유)

- All findings: not fixed — adversary is discovery and plan only. Proposed RED → GREEN tasks for the end of `tasks.md`:
  - H1: RED — add a host lock target to the VM permission double (a 0644 root file under `/run/nixos` and under
    `/nix/var`, try `open` + `flock -n`, expect failure). GREEN — `InaccessiblePaths` gains `-/run/nixos` and `-/nix/var`, or
    better `TemporaryFileSystem=/run:ro /var:ro` with `/nix/var` hidden (needs `[VERIFY]` in the VM that Python and PyAV
    still start).
  - H2: RED — an in-process test with a fake frame generator that raises `InvalidDataError` once, asserting the documented
    behaviour. GREEN — marker comment, docstring and contract fix; behaviour change only on operator decision.
  - M1: RED — supervisor test with a child that exits 1 / is killed by a signal, asserting one fixed journal line on stderr.
    GREEN — supervisor change plus contract §5.7 and `server-security.md` §3.
  - M2, M3, L13: document changes (no RED).
  - L1: RED — child stand-in that sends SIGINT to its parent; expect the supervisor to keep waiting. GREEN — `SIG_DFL`.
  - L5: Nix assertion; L8: one stderr line; L9: log the error class.
- Accepted residuals not re-reported beyond the points above: shared dynamic UID (only L2/L5 add detail), reading
  world-readable files (H1 and M2 add the lock and the return channel), `hour.m4a` tail, Python 3.10 timing flake.

### qa-engineer 협력 필요

- H2: contract mismatch between `contracts/reader.md:83` ("skip invalid frames") and the code's real behaviour (stop at the
  first one) — a boundary/contract question for qa-engineer.
- L13: stale `[VERIFY]` markers in `contracts/module.md` vs the passed VM test.
- M1: FR-023 coverage — whether "unit abnormal termination vs job failure" is testable for the reader unit at all while the
  supervisor always exits 0.

### Catalog 보강 필요

- T1 hostile uploader: a real end user (through bhu-llm) who controls only recording bytes and can submit many. Covers
  decoder exploits, slow-probe backlogs and result-channel exfiltration. H1, M2 and L3 came from this persona; neither A1–A15
  nor B1–B9 describes it.
- T2 compromised sandbox child: code already running inside the most restricted process, probing every allowed call
  (`flock`, `kill`, `inotify`, cgroupfs) rather than files. L1, L2 and H1 came from this view.
- T3 operator under incident pressure (a sharper A13): investigates a suspected exploit with root tools on the evidence.

## Verdict

FAIL — 신규 silent-skip high 1건, persona-driven critical 0건
사유 태그 (FAIL 시): SILENT_SKIP_NEW, PERSONA_ATTACK_SUCCESS (H1, also HOSTILE_ENV_FAIL)

Counts: Critical 0 · High 2 · Medium 3 · Low 14 (plus 6 low test-side silent-skips and 2 legacy WARN, not counted).
