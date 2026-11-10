# T051 adversary re-audit — fixes T045–T050 (002 decoder isolation)

- Auditor role: project-audit adversary (persona attack + silent-skip). One stateless run, scoped re-audit.
- Range: `6357f25..372fd73` only (10 commits, 17 files). Findings parked by the operator (ledger rulings 28 and 30:
  adversary L1–L12, L14, test-side silent-skips, qa Lows, EOF-truncated or zero-packet stream ending `done` with no line)
  are not re-reported.
- Method: read the diff package, the files at `372fd73`, the Nix module and the VM test. No crafted or malformed input was
  created or run (FR-028). No test was run. Host facts below are read-only metadata checks on miniecoinfo (`stat`, `ls`,
  `find -name`, `grep` on store scripts, `systemctl show -p` of manager defaults); no file content under `/mnt` was opened.
- Threat personas carried over from T040: T1 hostile uploader, T2 compromised reader child, T3 operator under incident
  pressure.

Host facts checked for this report (2026-11-10, miniecoinfo, systemd 262):

| Fact | Value | How |
|---|---|---|
| `/etc/.pwd.lock` | `0600 root` — not openable by the dynamic UID | `stat` |
| NixOS user activation lock | `update-users-groups.pl` takes no lock ("FIXME: acquire lock", lines 193, 281) | `grep` on the store script named by `/run/current-system/activate` |
| `switch-to-configuration` locks | only `/run/nixos/switch-to-configuration.lock` (now under the `/run` tmpfs) | `strings` |
| `/nix/store/*.lock` | only `0444` `Cargo.lock`/`Gemfile.lock` data files; Nix's build locks are created `0600` | `find -maxdepth 1 -name '*.lock'` |
| `/boot`, `/mnt/hdd-1_8t`, `/root` | `0700` | `stat`, `ls -ld` |
| `/etc/nixos` | symlink into `/home/kjeong/localgit/nixos-config` (hidden by `ProtectHome`) | `ls -ld` |
| `/mnt/hdd-3_6t` | `0755 kjeong`, mounted rw; `gdrive/` holds 251 `takeout-*.zip` files, all `0644`, 2.4 TB apparent | `ls -la`, `du --apparent-size` |
| `/opt` | `0711 root` — traversable, not listable by me | `ls -ld` |
| `/srv`, `/out` | `0755`, empty | `ls -la` |
| Manager OOM default | `DefaultOOMPolicy=stop`; the reader unit sets no `OOMPolicy` (`nix/module.nix:204-265`) | `systemctl show -p DefaultOOMPolicy` |
| PyAV 19.0.2 `InputContainer.decode` | a generator: `for packet in self.demux(...): yield from packet.decode()` (`.venv/.../av/container/input.py:246-260`) | source read |

## Discovery

### Critical (배포 차단)

- None.

### High (수정 필수)

- None. H1 and H2 are closed (see "Closure of the T040 findings" below), and no new High was found in the fix range.

### Medium (수정 권장)

- **RM1 [persona A3/A4, C2 / B9 memory cap; doc ≠ behaviour, FR-023] `docs/server-security.md:125-129`,
  `nix/module.nix:204-265`, `src/stt/server/reader.py:259-268` — the doc maps OOM to `killed by signal 9` and calls the
  supervisor line "the only unit-level trace", but with the host's `OOMPolicy=stop` the same OOM event makes systemd
  SIGKILL the supervisor, so the line is a race, and the documented `-g 'reader child'` filter hides the trace that does
  reliably appear.**
  Scenario: "A host override sets the reader's `MemoryMax` too low (or a new FFmpeg build needs more memory for one
  codec). Every decode child is OOM-killed. The kernel kills the largest task (the child); `memory.events` `oom_kill`
  increments; systemd, with `OOMPolicy=stop` inherited from `DefaultOOMPolicy=stop`, stops the unit and sends
  `KillSignal=SIGKILL` to the remaining process, the supervisor. The supervisor is at the same moment returning from
  `poll` (`reader.py:176`), reaping the child (`:262`), sending the verdict (`:265`) and printing the line (`:268`).
  Whichever wins, the job ends `unreadable_audio`; but when systemd wins there is no `reader child … killed by signal 9`
  line. The operator follows §3 (`journalctl -u 'stt-reader@*' -g 'reader child'`, and for one format always failing,
  the 'type=1326' / `killed by signal 31` checks) and sees nothing, because systemd's own lines for the instance
  ('A process of this unit has been killed by the OOM killer', 'Failed with result 'oom-kill'') do not contain
  'reader child'."
  Evidence:
  - `DefaultOOMPolicy=stop` on the host (table above); the reader unit sets no `OOMPolicy`.
  - The doc sentence "이 줄이 단위 수준의 유일한 흔적이다" (`server-security.md:129`) is also untrue for `RuntimeMaxSec`:
    systemd logs its own runtime-limit line for the instance. The doc correctly says no 'reader child' line is left in
    that case (`:128`).
  - Neither test exercises the real supervisor under an OOM kill: the pytest case `probe-sigkill`
    (`tests/server/test_reader.py:614`) uses a child that SIGKILLs itself, and the VM `memory` behaviour
    (`nix/vm-reader-double.py`) replaces the supervisor itself (it is the unit's `ExecStart`), so systemd, not the
    supervisor, records that end.
  - UNVERIFIED (not executed): which side wins the race on this host. Both orders are possible from the code and the
    systemd OOM policy; the outcome of the job is the same either way.
  Mitigation: `OOMPolicy=continue` on `stt-reader@` (the supervisor survives the child's OOM kill, sends an explicit
  `failed` instead of EOF and always writes `killed by signal 9`) — `[VERIFY]` in the VM with a benign memory-hog child
  injected through the supervisor's child command (no media, FR-028). Independently, fix §3: drop "유일한 흔적", and add
  systemd's own instance lines to the check (for example `journalctl -u 'stt-reader@*' -g 'reader child|oom-kill'` —
  `[VERIFY]` the exact systemd 262 wording).

- **RM2 [persona A15 auditor, T1 hostile uploader] `docs/server-security.md:50-55`, `nix/module.nix:215-231` — the
  re-accepted residual names `/etc`, `/nix/store`, `/usr` as what a compromised reader can read and return through its
  transcript, and adds a host-specific reassurance about `/var/lib/private`; on this host the largest readable exposure is
  elsewhere: `/mnt/hdd-3_6t`, including a 2.4 TB Google Takeout archive set (`0644`).**
  Scenario: "An uploader's recording exploits FFmpeg in the decode child. The child walks `/mnt/hdd-3_6t/gdrive`, opens
  a `takeout-*.zip` with the standard-library `zipfile` module (Python is in the sandbox), extracts chosen mail or Drive
  items, and spells them out as synthesized speech in its own decoded output (up to `MAX_DECODED_BYTES`, about three
  hours per job). Whisper transcribes it; the transcript returns to the uploader. Repeated uploads continue the
  extraction."
  Evidence:
  - Nothing in the reader unit hides `/mnt`, `/srv` or `/opt`: `InaccessiblePaths` lists only the stt paths and
    `/nix/var` (`module.nix:215-220`); `TemporaryFileSystem` covers `/dev/shm`, `/run`, `/var` (`:227-231`);
    `ProtectHome` covers `/home`, `/root`, `/run/user` only.
  - Host metadata in the table above. I listed names and modes only; I did not open any file under `/mnt`.
  - This is the accepted residual class ("world-readable host files", 2026-11-09, re-accepted 2026-11-10 with the
    exfiltration wording), so it is not a regression of the fix. It is reported because the record the operator
    re-accepted describes the class by system paths and gives a host-specific "this host is fine" note that does not
    mention the host's data mount. The archive is the operator's own account export. If it is the university account,
    it may hold students' and staff members' personal data. That is a different decision from "system files may leak".
  - Not raised to High: exploitation needs a working FFmpeg RCE plus a speech-synthesis channel, and the operator has
    accepted the class twice.
  Mitigation (operator decision): either add `"-/mnt" "-/srv" "-/opt"` to the reader's `InaccessiblePaths` (all with
  `-`, so a missing path does not fail the unit; the reader needs nothing there) and a `/mnt` target to the VM permission
  double, or make the host data not world-readable (`chmod o-rx /mnt/hdd-3_6t`, a nixos-config/host task). In either
  case, name the host's data mounts in §2 and re-confirm the acceptance with that wording.

- **RM3 [silent-skip candidate `if-not-config-skip`, latent] `src/stt/audio_decode.py:97`, `:120-121` — the early-stop
  notice of `decode_samples` reaches a caller only through an optional hook whose default `None` drops it without a
  log, an exception or an `# intentional-skip:` marker.**
  Scenario: "A later change adds a second CLI entry point (for example a batch or diarize helper) that calls
  `decode_samples(path)` as the existing tests do. A recording with a damaged frame at minute 20 of 60 now yields a
  20-minute transcript with no warning, which is the H2 defect again, on that path."
  Evidence and grading: the only production caller, `src/stt/transcribe.py:790`, passes `on_early_stop`
  (`grep decode_samples src/` finds no other), and the server path reports through `write_s16`'s return value and exit
  status 3. So no production path is silent today. Graded Medium (latent API hazard) and not counted as
  `SILENT_SKIP_NEW`. If the controller applies the location rule mechanically (production file → high), this becomes the
  only High of this run. The fix is one line.
  Mitigation: make the hook keyword-required (no default), or return `(samples, stopped_early)`; tests that do not care
  pass `on_early_stop=lambda _: None` explicitly. RED: a test that the call without the hook is a `TypeError`.

### Low (정보 / 레거시 WARN)

- **RL1 [T2 / B7] `nix/module.nix:215-231` — mount pinning.** A compromised child can keep a file or directory under
  `/mnt/hdd-3_6t` open for the life of the instance (≤ 300 s). Its namespace holds a slave copy of that mount, and a busy
  propagated copy makes the host's `umount /mnt/hdd-3_6t` fail with `EBUSY`. Availability only, rare operation,
  `umount -l` works. It existed before the fix and applies to every service with a private namespace. UNVERIFIED (not
  executed). Closed by the RM2 `InaccessiblePaths` option.
- **RL2 [T2 / B7] `/opt` (`0711 root`)** — I cannot list it, so I cannot rule out a world-readable lock file there that host
  tooling `flock`s. The reader can open files there by name. UNVERIFIED; the RM2 option (`-/opt`) closes it.
- **RL3 [A4, doc precision] `docs/server-security.md:39-41` (line 40), `contracts/module.md:39`** — under the empty `/run` and
  `/var` tmpfs, the `InaccessiblePaths` entries `-/run/stt`, `-/var/lib/stt` and `-/var/lib/stt-models` find no path and
  are skipped at runtime. The tmpfs is the one layer in effect. The second layer only re-engages if the tmpfs entry is
  later removed, for example by a host `lib.mkForce` override of `TemporaryFileSystem`, which no host-side test would
  catch. "두 겹이 막는다" is true only as configuration redundancy. Suggest "configured twice; at runtime the tmpfs hides
  them".
- **RL4 [A3/A10 batch user] `src/stt/transcribe.py:782-790`, `:1194`** — the CLI warning does not name the file. In a
  batch with stdout redirected, the `[n/N] name` progress line is block-buffered on stdout while the warning goes to
  stderr unbuffered. In a combined log the warning can appear before the name it belongs to, and in a split log it
  appears with no name. The transcript header and `.meta.json` do not record that the transcript is partial (only the
  shorter duration hints at it). Suggest putting `media.name` in the warning.
- **RL5 [A1] `src/stt/server/reader.py:268`** — the line is written at the journal's default priority (info). An operator
  who scans with `journalctl -p warning` misses every reader line. Suggest a `<4>` prefix (journald's level prefix is on by
  default) or `SyslogLevel=warning` on the reader unit.
- **RL6 [A13, T3, C6] `docs/server-security.md:142-144`** — the new rule (never open a server recording with the CLI,
  `ffprobe` or `ffmpeg`) closes T040 M3 as a rule. Its safe alternative, "읽기 단위를 거친다(요청을 다시 맡기는 것 등)",
  gives no concrete procedure. Resubmitting needs a caller identity and a schema-valid request. Under pressure, the
  operator also has no way to send a known-good file through the reader to tell a broken format path from hostile input
  (§3 "한 형식이 늘 `unreadable_audio` 일 때" assumes known-good recordings). The VM's `readFormats` script
  (`nix/vm-test.nix:41-61`) already does exactly this as `stt` through `reader_client`. Document that snippet (run as
  `stt`; it sends only a descriptor to the sandbox) as the operator's re-check.
- **RL7 [T2 / B9, pre-existing, info]** — a compromised child can fork up to `TasksMax=32` grandchildren that each make a
  denied call. Each death logs a kernel `type=1326` line, and without auditd these go through the kernel's printk rate
  limit. A burst can therefore drop the `type=1326` line of another, honest instance. That instance's supervisor line
  (`killed by signal 31`) is a separate journal stream and survives, so the §3 signal remains. Grandchildren do not
  produce supervisor lines (only the direct child's end is observed, `reader.py:262`). No change needed. Worth one
  clause in §3: trust the supervisor line over the presence or absence of `type=1326`.
- Test side (not counted): `nix/vm-reader-double.py:94-107` `locked()` returns 0 on `OSError`. That is the measurement
  ("not reachable"), not a skip. `tests/server/conftest.py:839-840` `__exit__` returns `None`, which is not a skip.

### Closure of the T040 findings in scope

| T040 finding | Fix | Status at 372fd73 |
|---|---|---|
| H1 host lock files reachable | `TemporaryFileSystem=/dev/shm:ro /run:ro /var:ro`, `InaccessiblePaths=-…/nix/var` (`module.nix:215-231`); VM double adds `locked()` and host lock targets; R12 records RED before GREEN | **Closed.** `/run/nixos/switch-to-configuration.lock`, `/run/lock`, `/var/lock`, the Nix DB, `gc.lock`, `big-lock`, profile locks and temproots are gone from the namespace. Remaining visible trees hold no lock that host administration or Nix takes on a world-readable file: `/etc/.pwd.lock` is `0600`, `update-users-groups.pl` takes no lock, the store's `*.lock` files are data, `/boot` is `0700`. The tmpfs mounts are per-instance, so locking them affects nothing outside. `/opt` stays UNVERIFIED (RL2). |
| H2 silent truncation at the first invalid frame | `write_s16` returns the flag with an `# intentional-skip:` reason (`audio_decode.py:82-89`); child exit `EARLY_STOP_EXIT=3` (`reader.py:45`, `:383`); supervisor `ok` plus one fixed line (`:208-227`, `:263-268`); CLI warning (`transcribe.py:782-790`); docstrings, `contracts/reader.md:99`, R7 and plan V corrected | **Closed.** Parity with faster-whisper holds: PyAV 19.0.2 `decode` is a generator, so stopping there is the same as faster-whisper's `continue` (table above). No stale "skip frames" wording remains (grep over `src docs specs/002 tests nix`). Residual latent path: RM3. |
| M1 no operator signal for reader faults | five fixed lines, verdict sent before the line (3f8af77), VM proves the line reaches `journalctl -u 'stt-reader@*'` | **Closed, with RM1** (OOM case racy, doc overclaims). |
| M2 risk record says no way out | §2 rewritten, re-accepted 2026-11-10 | **Closed as wording, with RM2** (host data mount not in the record). |
| M3 no safe procedure | §3 rule added | **Closed as a rule, with RL6** (no concrete safe re-check). |
| L13 stale `[VERIFY] B-P2-1` | markers removed in `contracts/module.md` | **Closed** (`grep VERIFY` empty). |

Exit status 3 and the journal lines under attack (the controller's focus questions):
- **Abuse of exit 3** (T2): a compromised decode child that exits 3 gets the same verdict as exit 0 (`reader.py:230-236`)
  with the same output bound (`MAX_DECODED_BYTES` on the caller side, unchanged), so it gains nothing. A probe exiting 3
  is `failed` plus `exited with status 3` (`:209`, test `probe-early-stop-code`, `test_reader.py:616`). No new capability.
- **Log injection**: impossible. The line is built from `op`, which comes from the request and is checked against
  `_LIMITS_S` (`:110`), and from an integer exit status or signal. No child-supplied text reaches it. The child cannot write
  to the journal itself: its stderr is `/dev/null` (`:149-150`), `close_fds` drops the supervisor's journal stream, sockets
  cannot be reopened through `/proc`, `pidfd_getfd` and `ptrace` are filtered, `socket()` is denied
  (`RestrictAddressFamilies=none`), and the journald sockets under `/run` are now hidden too.
- **Flooding**: at most one line per instance (two per job: probe and decode). Instances are bounded by the
  serial intake and worker and by `MaxConnections=4`. A single small write on a fresh per-instance stream cannot block,
  so the line cannot hold an instance open. `sys.stderr` being `None` would send `print` to stdout, which is `/dev/null`
  (`StandardOutput=null`), never the connection.
- **Misleading the operator**: the child picks which line appears (any exit status, any signal by self-kill, a
  timeout by hanging, or no line by exiting 0). The doc says so: "보안 통제가 아니다" (`server-security.md:114`,
  `contracts/reader.md:86`). Attribution stays sound: the line carries the mode word, journald adds the
  per-connection instance unit name, and the worker brackets each read with `<id> running` … `<id> done|failed`
  (`worker.py:293`, `:232-235`).
- **Changing another job's verdict**: no path. Each instance has its own supervisor, PID namespace and descriptors. The
  verdict is computed before and independently of the line (`reader.py:263-268`). A failing journal write raises only
  after the verdict was sent (test at `tests/server/test_reader.py:461`).

## Plan

### Persona Attack 결과

- New or changed entry points in the fix range (7):
  - F1 supervisor journal line and ordering (`reader.py:208-275`)
  - F2 decode child exit status 3 (`reader.py:348-383`)
  - F3 `audio_decode.write_s16` return value, `decode_samples(on_early_stop=…)` (`audio_decode.py:57-122`)
  - F4 CLI warning (`transcribe.py:782-790`)
  - F5 reader unit mount namespace: `/run`, `/var` tmpfs, `-/nix/var`, `-` prefixes (`module.nix:215-231`)
  - F6 operator procedures: `server-security.md` §2 residual, §3 lines, filters, safe-check rule
  - F7 VM test: targets file moved to `/etc/vm-double`, host lock targets, per-format real-reader decode (`vm-test.nix`)
- Group A: 15 personas, 17 scenarios. 6 expose a defect (A1 → RL5, A3 → RM1, A4 → RM1/RL3, A10 → RL4, A13 → RL6,
  A15 → RM2). Fix direction: deterministic OOM line plus accurate §3 filter, residual record that names the host's data
  mount, small operator-visibility fixes.
- Group B: 9 personas, 11 scenarios. 3 personas, 4 scenarios expose a defect (B7 → RL1, RL2; B9 → RM1, RL7). Fix direction:
  `OOMPolicy=continue`, hide `/mnt`, `/srv`, `/opt`.
- Cross-persona: 5 scenarios, 2 expose a defect (C2 → RM1, C6 → RL6). C1 (deploy under a hostile backlog) is now closed.
- T1–T3: 8 scenarios, 4 expose a defect (T1 → RM2, T2 → RL1/RL7, T3 → RL6 and RM1).

| ID | Scenario (fix range) | Behaviour (code) | Result |
|---|---|---|---|
| A1 | New operator scans `journalctl -p warning` for reader trouble | lines are priority info | RL5 |
| A1 | New operator sees `stopped early at invalid data` | §3 says it is not an error | pass |
| A2 | Hurried admin runs `nixos-rebuild switch` with hostile reads in flight (T040 C1) | `/run/nixos` absent in the namespace; switch lock free | pass (H1 closed) |
| A3 | Operator looks for memory-cap failures with the documented `-g 'reader child'` | OOM line racy; systemd's `oom-kill` lines filtered out | RM1 |
| A4 | Host override shrinks `MemoryMax`, or `mkForce`s `TemporaryFileSystem` | OOM: RM1. Override: `-/run/stt` re-engages, host locks exposed again, no host test | RM1, RL3 |
| A5 | VM or host without `/run/nixos` or `/nix/var/nix/db` | `-` prefixes; VM creates the targets | pass |
| A6 | Probe and decode instances end abnormally at the same time | separate streams, mode word, instance unit name | pass |
| A7 | Korean or emoji recording name | never in the line; CLI warning carries no name at all | pass (see RL4) |
| A8 | Operator pastes the journalctl command | glob quoted in the doc | pass |
| A9 | Same damaged recording re-transcribed | same `stopped early` line and `ok` each time | pass |
| A10 | CLI batch with stdout redirected, one file damaged | warning separated from or ahead of its `[n/N] name`; transcript not marked partial | RL4 |
| A11 | Backup during reads | no new state | pass |
| A12 | Non-`stt` user on the reader socket | unchanged `0600` | pass |
| A13 | Admin wants to re-check an `unreadable_audio` recording safely | rule exists, no concrete reader-path procedure | RL6 |
| A14 | Professor's damaged lecture ends `done`, partial | operator decision 2026-11-10 (edge case 10) | accepted, not re-reported |
| A15 | Auditor checks the re-accepted residual against the host | `/mnt/hdd-3_6t` Takeout archives readable, not in the record | RM2 |
| B1 | Damaged frame mid-file, or at the first frame | exit 3 → `ok` + line; empty → `done` with an empty transcript (engine `allow_empty=True`, `engine.py:180`) | pass (H2 closed) |
| B2 | Journal disk full | journald drops; the stream write succeeds; verdict already sent | pass |
| B3 | Journal stream broken | `BrokenPipeError` after the verdict; test covers it | pass |
| B4 | Child tries to reach journald or the network | no sockets; `/run` hidden | pass |
| B5 | Compromised child lies through exit status | verdict as for exit 0; the line is forgeable and documented as such | pass (documented) |
| B6 | Encoding traps in the line | only a checked `op` and integers | pass |
| B7 | Child `flock`s or `fcntl`-locks every readable host file | no admin lock left on the known paths; mount pin on `/mnt`; `/opt` unknown | RL1, RL2 |
| B7 | Two instances lock their own `/run` tmpfs | per-instance tmpfs | pass |
| B8 | Clock jump while correlating lines by time | monotonic deadlines; `ProtectClock`; correlation by journal order | pass |
| B9 | Child OOM under `OOMPolicy=stop` | supervisor line racy | RM1 |
| B9 | Child forks grandchildren into SIGSYS | `type=1326` printk rate limit can drop another instance's line | RL7 |
| C1 | A2 + B7: ship the FFmpeg fix during a hostile backlog | switch not blocked | pass (closed) |
| C2 | A4 + B9: memory cap too small plus hostile files | operator cannot see OOM with the documented filter | RM1 |
| C3 | A10 + B1: worker killed after an early-stop decode | unchanged cleanup | pass |
| C6 | A13 + hostile file: operator re-checks | no tool, temptation to use `ffprobe` | RL6 |
| C7 | T3 + B5: exploit succeeds, child exits 3 to look benign | documented as not a control | pass (documented) |
| T1 | Hostile uploader floods damaged-frame uploads | one line per read, bounded | pass |
| T1 | Hostile uploader exfiltrates `/mnt` archives through transcripts | readable, not in the record | RM2 |
| T2 | Exit 3 for advantage / text injection / another job's verdict | no gain / impossible / impossible | pass |
| T2 | Hold a `/mnt` file open | host `umount` `EBUSY` ≤ 300 s | RL1 |
| T2 | Suppress its own line (exit 0, SIGINT the supervisor, raise `/proc/1/oom_score_adj`) | possible; documented as forgeable; no gain beyond exit 0 | pass (documented) |
| T3 | Operator under pressure sees `killed by signal 31` for one recording | §3: do not open, correlate by time, tell the caller | pass |
| T3 | Same operator checks whether reads die of memory | RM1 | RM1 |
| T3 | Same operator wants a safe re-check | RL6 | RL6 |

### Silent-Skip 결과

- New silent-skip in production code counted as FAIL: 0 (high 0).
- New silent-skip candidate, latent, graded Medium: 1, `src/stt/audio_decode.py:120-121` (RM3).
- Reviewed and excluded:
  - `audio_decode.py:82-89` (`except InvalidDataError` → `break`): has an `# intentional-skip:` marker with the reason,
    and the condition is returned.
  - `reader.py:219-220` (`_journal_line` → `None` for a hang-up) and `:221-222` (normal `ok`): the contracted "no line"
    cases (`contracts/reader.md` §5.7, docstring). A pure return value, nothing skipped.
  - `reader.py:381-382` (`except Exception: return 1`): legacy, now reported by the supervisor line.
  - Test side: `vm-reader-double.py:94-107`, `conftest.py:839-840` (low, not counted).
- Mitigation needed: `src/stt/audio_decode.py:97` (RM3).

### 미수정 (사유)

- All findings are unfixed: the adversary only discovers and plans. Proposed tasks (RED → GREEN):
  - RM1: RED — a VM specialisation runs the real supervisor with a benign child that allocates past `MemoryMax`
    (injected through the child command; no media). Assert `reader child decode killed by signal 9` in the journal and a
    `failed` verdict. GREEN — `OOMPolicy = "continue"` on `stt-reader@` (`[VERIFY]` that the unit then ends on its own
    after the verdict), plus §3 wording and filter.
  - RM2: operator decision. If hiding: RED — a `0644` file under `/mnt` in the VM double's `host-files` targets; GREEN —
    `InaccessiblePaths` gains `-/mnt -/srv -/opt`. Otherwise a host-side permission change plus §2 wording.
  - RM3: RED — calling `decode_samples` without the hook is a `TypeError`; GREEN — required keyword (tests pass a no-op).
  - RL3–RL7: doc lines (RL3, RL6, RL7), CLI warning text (RL4, RED by `capsys`), log level (RL5).
- Not re-reported: L1–L12, L14, the test-side silent-skips of T040, qa Lows, and the EOF/zero-packet `done` case
  (rulings 28 and 30); the shared dynamic UID; the `hour.m4a` tail.

### qa-engineer 협력 필요

- RM1: FR-023 contract coverage. §3 promises `killed by signal 9` for OOM, but no test exercises it through the real
  supervisor (pytest uses self-SIGKILL; the VM `memory` double replaces the supervisor).
- RM3: boundary of `decode_samples`. The optional hook is a public-API default that can silently drop the early-stop
  condition.

### Catalog 보강 필요

- T1 hostile uploader, T2 compromised sandbox child, T3 operator under incident pressure — as proposed in T040; used
  again here (RM2, RL1, RL6, RL7 came from them).
- T4 (new) host-data neighbour: personal or institutional data that shares the host with the service (data mounts,
  sync folders, archives). A15 found RM2 only by looking at the real host rather than the module, which a
  module-only review would miss.

## Verdict

PASS — 신규 silent-skip high 0건, persona-driven critical 0건
사유 태그: 없음 (FAIL 아님). H1·H2 closed; no new High.

Counts: Critical 0 · High 0 · Medium 3 (RM1, RM2, RM3) · Low 7 (RL1–RL7), plus 2 test-side items not counted.
