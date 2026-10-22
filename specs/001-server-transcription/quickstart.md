# Quickstart — 교내 서버 전사가 동작함을 확인하는 순서

이 기능은 세 층에서 확인한다. 앞의 층이 통과해야 다음 층으로 간다.

| 층 | 어디서 | GPU | 무엇을 증명하나 |
|---|---|---|---|
| A. 개발 시험 | 개발 셸, CI | 없음 | 계약·받기·줄·삭제·워커·엔진의 판단(가짜 엔진·가짜 모델), 명령줄 회귀 |
| B. VM 시험 | `nix build` (NixOS 시험 VM) | 없음 | 단위·권한·잠금·격리 설정·예정된 삭제가 실제 systemd 위에서 맞는가 |
| C. 호스트 시험 | miniecoinfo (배포 뒤) | 있음 | 실제 전사·화자 구분·2패스·처리 시간·외부 접속 없음 |

수용 시나리오 ↔ 시험 이름 대응은 `tasks.md` 가 과제마다 적는다(헌법 VII). 시험 이름은 `test_us<N>_<n>_...` 꼴이다.

## A. 개발 시험

```sh
nix develop            # 또는 direnv
uv sync --locked --extra server
uv run python -m pytest -q
```

- 기대: 기존 243개를 포함해 모두 통과(SC-013). GPU·망·모델 없이 돈다 — 무거운 라이브러리는 엔진 안에서만 늦게 불러온다.
- 계약: `tests/server/test_contract.py` 가 `src/stt/server/schemas/*.json` 과 `specs/001-server-transcription/contracts/*.json` 의 바이트 일치,
  README 예시의 통과, 틀린 예시의 거절을 확인한다(SC-012).
- 시각: 자정·24시간 계산은 시각을 주입해 시험한다(실제 시계를 기다리지 않는다).
- 워커: 가짜 엔진 명령(판정만 쓰는 작은 스크립트)으로 완료·실패·시간 초과·중간 취소·엔진 사망을 시험한다.
- 엔진: 가짜 모델·가짜 화자 파이프라인으로 무음(문단 0개), GPU 메모리 부족, GPU 없음, 힌트 반영 수를 시험한다.
- CI(`.github/workflows/ci.yml`)도 `--extra server` 로 설치하도록 바꾼다. CI 는 Python 3.10·3.12 둘 다 돈다 — 이 호스트(3.12)에서 통과해도
  3.10 에서 깨질 수 있다(3.11 에 새로 생긴 것을 쓰지 않는다).

## B. VM 시험

```sh
nix build .#checks.x86_64-linux.server-vm -L
```

VM 에는 GPU 도 모델도 없다. 그래서 모든 전사 job 은 엔진의 GPU 확인에서 `failed(gpu_unavailable)` 로 끝난다 — 그것으로 충분하다.
받기·줄·워커·삭제·잠금의 실제 동작은 전사 결과와 상관없기 때문이다. 시험 VM 이 확인하는 것:

1. 호출하는 쪽 역할의 계정(`stt-requests` 구성원)이 ffmpeg 로 만든 2초 녹음과 요청을 쓰면 수 초 안에 `queued` 응답이 생기고,
   inbox 가 빈다(US1-1 앞부분, SC-011).
2. 그 job 이 `failed(gpu_unavailable)` 로 끝나고, `after_job` 녹음이 바로 지워진다(US2-2, US5-5).
3. `at_midnight` job: 운영자 역할 계정이 `flock /run/stt/gpu.lock sleep 120` 으로 GPU 잠금을 쥔 채 VM 시계를 23:59:30(KST)로 옮기고 job 을
   맡긴다(잠금 때문에 `queued` 로 머문다). 자정을 넘기면 1분 안에 녹음이 없고 job 이 `failed(audio_deleted)`·`cause: midnight` 다(US2-3, US2-5).
4. 시계를 23시간 58분 + 1분 넘기면 그 job 의 응답·기록이 모두 없다(SC-004 의 응답 부분 — VM 에서는 결과가 생기지 않으므로 결과 만료는 A 층
   `test_us2_7` 이 본다).
5. 「지금 지우기」·「결과 지우기」·남의 job 지우기·이미 지운 job·결과 없는 job 의 답(US2-8, US2-10~12).
6. 중복 `request_id`, 잘못된 이름, 스키마 위반, 깨진 JSON, 0600 으로 만든 요청(stt 가 못 읽음 → `refusal`), 심볼릭 링크 첨부, 3시간 넘는
   녹음(무음 저비트), 손상 파일, 없는 `from_request` 의 답(US5-1·2·6, US3-3·4·6). 어느 경우에도 inbox 가 빈다.
7. 잠금 줄: 운영자 역할 계정이 `flock /run/stt/gpu.lock sleep 20` 으로 잠금을 쥔 동안 job 이 `queued`(순서 1)로 머물고, 놓은 뒤 처리된다(US4-2).
8. 격리 설정: `systemctl show` 로 모든 단위의 `MemorySwapMax=0`, `LimitCORE=0`, 망 없는 단위의 `PrivateNetwork=yes`, 워커의 `DeviceAllow`·
   `RemoveIPC=yes`·`TimeoutStartUSec=infinity`, 청소 타이머의 `AccuracyUSec=1s`, `stat -c %a /var/lib/stt` 가 751 인지 확인한다(FR-015·FR-035).
9. 로그: 시험에 쓴 힌트 낱말과 요청자 표식이 `journalctl` 에 0번 나온다(FR-037).
10. 죽은 워커의 흔적: `running` job 기록과 작업 폴더(임시 WAV 흉내 파일 포함)를 stt 계정으로 만들어 두고 청소 단위를 돌리면, 워커 생존
    잠금이 비어 있으므로 폴더가 지워지고 job 이 `failed(interrupted)` 가 된다(US2-4). VM 에서는 엔진이 GPU 확인에서 바로 끝나 job 을 처리 중에
    붙잡아 둘 수 없으므로, 처리 도중 강제 종료 자체는 A 층(가짜 엔진이 잠든 채 죽임)에서 시험한다.
11. 워커가 되풀이해 깨어나지 않는다: job 이 끝난 뒤 `queue/` 가 비고, 10초 동안 `stt-worker.service` 가 다시 시작되지 않는다
    (`systemctl show stt-worker.service -p ExecMainStartTimestampMonotonic` 값이 그대로).

## C. 호스트 시험 (운영자, 배포 뒤)

준비: nixos-config 에 모듈을 붙여 배포하고([contracts/module.md](contracts/module.md) §4), 모델 받기가 끝났는지 본다.

```sh
systemctl status stt-models.service            # 성공으로 끝났는가
test -f /var/lib/stt-models/.complete && echo models ok
systemctl show stt-worker.service -p PrivateNetwork -p MemorySwapMax -p LimitCORE -p DeviceAllow
findmnt -T /var/lib/stt                        # LUKS 위 루트인가
```

시험 요청은 stt 계정으로 inbox 에 쓴다(`sudo -u stt`). 녹음은 운영자의 시험 녹음을 쓰고, 끝나면 남은 것이 없는지 본다.

1. **60초, 1차**: 힌트 없음·화자 구분 없음·`at_midnight` → `done`. `result.json` 이 스키마를 통과하고 문단마다 시작·끝 시각이 있다(US1-1·3).
   녹음은 자정까지 남는다(`audio.delete_at`).
2. **60초, 2차**: `from_request` = 1차, 힌트 몇 개, 화자 구분, `after_job` → `done`. 문단에 `화자N` 과 불확실 표시가 있고(US1-2),
   실행 요약에 반영 수가 있으며(US1-4·5), 완료 직후 녹음이 없다(US3-1·2).
3. **결과 지우기**: 1·2의 결과를 지우면 `outbox/<id>/` 가 없다(US2-6).
4. **지금 지우기**: 처리 중인 job 에 보내면 1분 안에 그 job 의 모든 파일이 없다(US2-9, SC-005).
5. **명령줄 줄 서기**: 서버 job 이 도는 동안 `stt transcribe <파일> --device cuda` → 대기 안내 뒤 서버 job 이 끝나고 시작한다(US4-1, SC-010).
6. **처리 시간(SC-001)**: 1시간 녹음으로 1차·2차를 각각 재고 기록한다. 목표 1차 10분, 2차 25분(제안값 — 이 실측으로 확정).
7. **외부 접속 없음(SC-002)**: 워커 단위의 망이 없음(`PrivateNetwork=yes`)과 job 이 `done` 인 것을 기록하고, 시도 수를 센다. 망이 열린 환경에서
   엔진을 직접 돌려야 시도가 드러나므로 단위 밖에서 한다:
   1. `sudo -u stt install -d -m 0700 /var/lib/stt/work/manual-connect` 에 시험 녹음을 복사하고, `spec.json` 을 쓴다 — 칸은 contracts/cli.md §1.1
      (`request_id` 는 새 UUID, `audio_path` 는 복사한 녹음, `diarize: true`, 힌트 몇 개).
   2. `sudo -u stt env HF_HOME=/var/lib/stt-models HF_HUB_OFFLINE=1 flock /run/stt/gpu.lock strace -f -e trace=connect -o /tmp/stt-connect.log
      <서버 패키지>/bin/python -m stt.server.engine /var/lib/stt/work/manual-connect` — GPU 잠금을 쥐어 서버 job 과 겹치지 않게 한다.
   3. `grep -cE 'AF_INET6?' /tmp/stt-connect.log` 이 0 이고 `outcome.json` 이 `done`. 끝나면 `/var/lib/stt/work/manual-connect` 와
      `/tmp/stt-connect.log` 를 지운다.
8. **로그(FR-037)**: `journalctl -u 'stt-*' --since today | grep -c <시험 힌트 낱말>` 이 0.
9. **화자 번호 일관성(SC-007)**: 2인 이상 시범 녹음의 전사록을 사람이 읽고, 한 사람의 번호가 바뀐 곳이 없는지 기록한다.
10. **재부팅 뒤 첫 job(research R2)**: 호스트를 다시 켜고 다른 GPU 사용 없이 1차 job 하나를 맡겨 `done` 인지 본다(`lsmod | grep nvidia_uvm`,
    `ls /dev/nvidia-uvm` 도 기록). `gpu_unavailable` 이면 `boot.kernelModules` 에 `nvidia_uvm` 이 빠진 것이다.
11. **모델 판을 바꾼 배포(research R9 `[VERIFY]`)**: 고정 모델 목록을 처음 바꾸는 배포 때 `systemctl show stt-models.service -p ActiveEnterTimestamp`
    가 배포 시각으로 바뀌는지(다시 돌았는지) 기록한다. 안 바뀌면 module.md §4-6 의 손 절차를 따른다.

결과는 `specs/001-server-transcription/handoff/` 에 날짜와 함께 남기고, 핸드오프 문서 §7 의 S1~S11 칸을 채운다.
