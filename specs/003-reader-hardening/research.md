# Research: 녹음 읽기 추가 좁히기와 알아차리기 (Phase 0)

- 작성: 2026-11-10 · 입력: [spec.md](spec.md), `idea/reader-hardening/idea.md`, 002 산출물(`specs/002-decoder-isolation/`), 002 감사
  (`audit/adversary.md` L1·L5·L6·L8·L9·L11, `audit/qa-engineer.md` L1·L2·L3), 이 저장소 `c5c128a`(코드는 배포된 `f41e921` 와 같다), 이 호스트
  (miniecoinfo) 실측.
- 실측은 배포된 서버 해석기(`/nix/store/n1i484x74clbyn9apzr314mqrycv8sq4-stt-server-env/bin/python -I`, Python 3.12.13, av 19.0.2, FFmpeg 9.0.2,
  libavcodec 63.1.102)로 했다. 무거운 실행은 모두 `captest` 로 돌렸다. 버리는 코드와 시험 녹음은 저장소 밖(scratchpad)에 두었고 끝난 뒤 지웠다.
- 시험 녹음은 nixpkgs `ffmpeg` 8.1.2 의 `lavfi` 사인파·단색 영상·단색 그림과, 그것을 PyAV 로 만든 것, 그리고 그 끝을 잘라 낸 사본뿐이다. 기준 녹음
  두 개(`~/stt-baseline-002/2026-11-09/clip60.m4a`·`hour.m4a`)는 사본으로만 읽고 지웠다. 해독기를 노리는 파일은 만들지 않았다(FR-017).
- 형식: 결정 · 근거 · 대안. 🟢 = 이 세션에서 잰 사실, `[VERIFY]` = 구현·VM·호스트에서 확인할 것(괄호 안은 [quickstart.md](quickstart.md)의 단계).
- `path:line` 은 `c5c128a` 기준이다.

## 관측 도구 — 어떤 해독기가 실제로 열리는가

해독기가 「열렸는가」는 PyAV 의 공개 API 로 보이지 않는다. 그래서 작은 `LD_PRELOAD` 공유 라이브러리(C, scratchpad)를 만들어 해석기에 미리
올렸다. `avcodec_open2`(해독기 열기)·`av_parser_init`(분석기 만들기)·`avcodec_send_packet`(해독기에 묶음을 넣음)·`av_parser_parse2`(분석기 호출)·
`avformat_find_stream_info` 를 가로채, 해독기 이름·`codec_whitelist` 옵션·돌려준 값·처음 받은 묶음을 표준 오류에 적고 진짜 함수를 부른다.
PyAV 휠은 자기 FFmpeg 라이브러리를 `av.libs/` 에 넣고 Python 은 확장 모듈을 `RTLD_LOCAL` 로 읽으므로, 진짜 함수는 `RTLD_NEXT` 가 아니라
`dlopen(<그 경로>, RTLD_NOLOAD)` 로 찾았다. 읽기 자식과 같은 코드(`stt.audio_decode.open_container`·`write_s16`, `reader.container_duration_s`)로
probe 와 decode 를 따로 열어(실제처럼 자식 둘) 각각 기록했다.

## R1. 해독기 허용목록 — 두 겹으로 건다: 형식 단계의 `codec_whitelist` 와 해독할 흐름의 `codec_whitelist`, 길이 재기에서 이름 확인

- **결정**:
  - 해독기 이름 목록 `AUDIO_DECODERS` 를 `src/stt/server/reader.py` 에 `AUDIO_FORMATS` 옆에 둔다(값은 R2, FR-004).
  - 공유 해독 모듈의 `open_container` 에 키워드 `codec_whitelist` 를 더한다. 값이 있으면
    1. `av.open(..., container_options={"format_whitelist": …, "codec_whitelist": …})` — 형식(AVFormatContext) 단계의 허용목록. `avformat_find_stream_info`
       가 여는 모든 해독기에 같은 목록이 걸린다.
    2. 열린 컨테이너의 모든 흐름에서 PyAV 코덱 문맥의 `options["codec_whitelist"]` 를 같은 목록으로 둔다. PyAV 는 해독할 때(`container.decode(audio=0)`)
       이 문맥을 `avcodec_open2(…, options)` 로 여므로 FFmpeg 가 목록 밖 해독기를 `EINVAL` 로 거절한다(`av.error.ArgumentError`).
  - 읽기 자식(`reader._open_container`)만 두 목록을 넘긴다. 명령줄의 `decode_samples` 는 지금처럼 목록 없이 연다(FR-005).
  - 길이 재기 자식은 해독하지 않으므로 FFmpeg 가 첫 음성 흐름의 해독기를 열 일이 없다. 그래서 「첫 음성 흐름에 해독기가 있고 그 이름이 목록 안」을 이름으로
    확인하고, 아니면 끝 코드 1(→ `failed` → 받기의 `unreadable_audio`)로 끝낸다. 목록 밖 코덱은 받는 순간 거절되고 줄을 서지 않는다(FR-003).
- **근거** 🟢 (2026-11-10, 위의 관측 도구, 46개 파일 × probe·decode):
  - **지금(형식 허용목록만)**: 길이 재기와 해독 둘 다 `avformat_find_stream_info` 가 영상·그림 해독기를 열고 묶음까지 넣는다.

    | 파일(사인파 + …) | 열린 비음성 해독기(probe·decode 둘 다) |
    |---|---|
    | 단색 영상 H.264 · MPEG-4 · MJPEG · PNG (mov/mp4, `.m4a`) | `h264` · `mpeg4` · `mjpeg` · `png` — 열림(0) + 첫 묶음 받음 |
    | 단색 영상 WMV2 · MJPEG (asf, `.wma`) | `wmv2` · `mjpeg` — 열림 |
    | 표지 그림 PNG · JPEG (MP3 의 ID3 APIC), PNG (M4A) | `png` · `mjpeg` · `png` — 열림 + 그림 묶음 받음 |
    | 영상만(H.264, 음성 없음) | `h264` — 열림. 길이 재기는 그 뒤 「음성 흐름 없음」으로 실패 |

    002 감사 L11 과 보안 문서 4.3 의 판정(「허용한 컨테이너의 분석 중 불릴 수 있다」)이 실제였다.
  - **형식 단계 목록만 더함**(`container_options` 의 `codec_whitelist`): `find_stream_info` 안의 영상·그림 해독기는 모두 `-22`(EINVAL)로 거절되고 묶음도
    받지 않는다. 길이 재기는 그대로 성공한다(경계 사례 1). 그러나 해독 단계에서 PyAV 가 여는 해독기에는 목록이 걸리지 않았다 — 그때(22개) 목록 밖이던 `ac3`(MP4 안 AC-3)·
    `adpcm_ima_qt`·`mp2`(MP2 를 담은 `.mp3`)가 `whitelist=-` 로 열려 끝까지 해독됐다. `av.open(options=…)` 도 같다: 형식과 `find_stream_info` 에는
    가지만 PyAV 의 코덱 문맥은 `options == {}` 로 남아 `ac3` 가 해독됐다.
  - **결정한 두 겹 + 이름 확인**: 46개 파일 모두에서 비음성 해독기가 한 번도 열리지 않았다(`h264`·`mpeg4`·`mjpeg`·`png`·`wmv2` 는 모두 거절).
    목록 안 코덱의 파일은 길이 재기 값이 같고, 해독 출력이 SHA-256 까지 같았다(형식 8가지의 2초·60초, ALAC, PCM 변형 8, WMA 1, Ogg 안 FLAC,
    48 kHz 스테레오 AAC, 영상·표지 그림이 든 파일 10, 음성 흐름 둘). 기준 녹음도 같다: `clip60.m4a` 1,920,342 B, `hour.m4a` 115,201,800 B 로 바이트까지
    같았다. 이때의 목록(22개)에서 목록 밖 코덱은 길이 재기의 이름 확인(`ac3`·`adpcm_ima_qt`, WAV 안 `mp2`)이나 해독의 `ArgumentError`(`.mp3` 안 `mp2` —
    길이 재기에서는 `mp3float` 로 보인다, R2 표)로 거절됐다(32개 목록에서는 `ac3`·`mp2` 가 읽히고 `adpcm_ima_qt` 는 그대로 거절된다 — R2). 영상만 든 파일은 지금처럼
    「음성 흐름 없음」으로 거절된다(US1-4). 녹음 확장자를 붙인 텍스트는 지금처럼 `InvalidDataError` 다.
  - **PyAV 17.1.0·18.1.0**(Python 3.10·3.11 의 명령줄 판, uv 캐시의 cp311-abi3 휠을 Python 3.11.15 로): 같은 코드가 같은 해독기를 열고 같게
    거절했다(`aac`·`mp3float`·`wmav2` 열림, `h264`·`png` 거절, 22개 목록에서 `ac3`·`mp2` 거절 — 32개 목록의 결과도 같다, R2). 두 판 모두 `CodecContext.options` 가 `avcodec_open2` 로 넘어간다(소스
    `av/codec/context.py`). 그래서 3.10·3.11 의 시험도 같은 결과를 본다.
  - **시간**: 10분 사인파 + 320×240 단색 H.264 `m4a` 의 길이 재기 0.09초, 해독 0.56초로 목록이 있으나 없으나 같았다. 영상 해독기가 막힌 흐름은
    `find_stream_info` 가 매개변수를 끝내 못 채우지만, 읽는 양은 FFmpeg 의 탐색 한도 안이다.
  - **시험이 볼 수 있는 것**: 목록을 건 컨테이너에서 영상 흐름의 `codec_context.pix_fmt` 는 `None`(`find_stream_info` 가 그 해독기로 한 장도 풀지
    못함, 목록 없이 열면 `yuv420p`·`rgb24`), `container.decode(video=0)` 은 `ArgumentError` 다. FFmpeg 는 `Codec (h264) not on whitelist '…'` 을
    오류 수준으로 남긴다(PyAV 기록을 켰을 때). 그래서 pytest 가 GPU·C 없이 「열리지 않음」을 RED → GREEN 으로 본다(R12).
- **대안**:
  - **영상·그림 트랙이 있으면 거절**: 가장 좁지만 운영자가 기각했다(2026-11-10, FR-002). 휴대폰으로 녹화한 강의 영상이 지금은 된다.
  - **형식 단계 목록만**: 위 측정대로 해독 단계가 열려 있어 FR-001·FR-003 을 못 지킨다.
  - **해독 단계의 이름 확인만**(FFmpeg 옵션 없이): `find_stream_info` 의 영상 해독기를 막지 못한다 — 공격면의 대부분이 거기다.
  - **해독 자식이 FFmpeg 거절 대신 이름 확인도 함께**: 같은 결과를 두 번 확인하는 셈이라 두지 않는다. 길이 재기에는 FFmpeg 가 해독기를 열 기회가 없어서
    이름 확인만 쓴다.
  - **명령줄도 목록으로**: 범위 밖(FR-005). 명령줄은 `stt fetch` 가 받은 영상 파일도 연다.

## R2. 목록의 내용 — 음성 해독기 32개: 형식 8가지·ALAC 의 22개와 녹음기 코덱 10개

- **결정**: `AUDIO_DECODERS` 는 FFmpeg 해독기 이름(`AVCodec.name`)의 쉼표 목록이다. 처음 측정한 22개에 운영자가 정한 녹음기 코덱 10개를 더했다
  (운영자 결정 2026-11-10: ADPCM(WAV 의 IMA·Microsoft), WMA Pro·Voice·Lossless, MP2, AC-3·E-AC-3, AMR-NB·AMR-WB 를 넣는다. Speex·GSM 과 영상·이미지·자막
  해독기는 넣지 않는다).

  | 해독기 이름 | 코덱 | 쓰는 형식(확장자) | 끝까지 읽어 봄 |
  |---|---|---|---|
  | `aac` | AAC(LC·HE) | m4a, aac | 🟢 |
  | `alac` | Apple Lossless | m4a | 🟢 |
  | `mp3float` | MP3 | mp3 | 🟢 |
  | `mp2` | MPEG Layer II (더함) | mp3, wav | 🟢 |
  | `flac` | FLAC | flac, ogg | 🟢 |
  | `vorbis` | Vorbis | ogg | 🟢 |
  | `opus` | Opus | opus, ogg | 🟢 |
  | `wmav1`, `wmav2` | WMA 1·2 | wma | 🟢 |
  | `wmapro`, `wmavoice`, `wmalossless` | WMA Pro·Voice·Lossless (더함) | wma | `[VERIFY]` — 인코더 없음 |
  | `ac3`, `eac3` | AC-3·E-AC-3 (더함) | m4a(mp4) | 🟢 |
  | `amrnb` | AMR-NB (더함) | m4a(3GP·MP4, `mov` 형식) | 🟢 |
  | `amrwb` | AMR-WB (더함) | m4a(3GP·MP4) | `[VERIFY]` — 인코더 없음 |
  | `adpcm_ima_wav`, `adpcm_ms` | IMA ADPCM·Microsoft ADPCM (더함) | wav | 🟢 |
  | `pcm_u8`, `pcm_s8`, `pcm_s16le`, `pcm_s16be`, `pcm_s24le`, `pcm_s24be`, `pcm_s32le`, `pcm_s32be`, `pcm_f32le`, `pcm_f32be`, `pcm_f64le`, `pcm_f64be` | 선형 PCM | wav, m4a(mov) | 🟢(8개 변형) |
  | `pcm_alaw`, `pcm_mulaw` | G.711 | wav, m4a(mov) | 🟢 |

- **근거** 🟢:
  - 목록에 넣는 이름은 「그 코덱 번호로 FFmpeg 가 고르는 해독기」의 이름이어야 한다(`codec_whitelist` 는 `av_match_list` 로 해독기 이름을 정확히
    맞춘다). PyAV 는 흐름마다 `avcodec_find_decoder(codec_id)` 로 해독기를 고른다. 실제 파일로 잰 이름:
    - 형식 8가지: `pcm_s16le`(wav)·`aac`(m4a·aac)·`mp3float`(mp3 — 고정소수점 `mp3` 가 아니다)·`flac`·`vorbis`·`opus`(휠에 든 `libopus` 가 아니라
      내장 해독기)·`wmav2`. ALAC 은 `alac`. WAV·MOV 의 PCM 변형은 `pcm_s24le`·`pcm_f32le`·`pcm_u8`·`pcm_s32le`·`pcm_mulaw`·`pcm_alaw`·`pcm_s16be`·`pcm_s16le`,
      WMA 1 은 `wmav1`, Ogg 안 FLAC 은 `flac`.
    - 더한 코덱(FFmpeg 8.1.1·8.1.2·9.0.2 — PyAV 17·18·19 — 모두 같음): MP2 → `mp2`(`mp2float` 가 아니다. 고정소수점 인코더 `mp2fixed` 로 만든 파일도
      `mp2`), AC-3 → `ac3`(`ac3_fixed` 가 아니다. `ac3_fixed` 인코더로 만든 파일도 `ac3`), E-AC-3 → `eac3`, AMR-NB → `amrnb`(PyAV 17·18 에는
      `libopencore_amrnb` 해독기도 있지만 내장이 먼저 골라진다), IMA ADPCM WAV → `adpcm_ima_wav`, Microsoft ADPCM → `adpcm_ms`. 운영자가 말한 「FFmpeg 가
      고르면 넣을」 부동소수 MP2·고정소수점 AC-3 변형은 어느 판에서도 골라지지 않아 넣지 않았다.
    - 인코더가 없는 넷은 코덱 번호마다 해독기가 하나뿐이라 이름이 하나로 정해진다: `wmapro`·`wmavoice`·`wmalossless`·`amrwb`(세 PyAV 휠의
      `av.codecs_available` 과 nixpkgs `ffmpeg -decoders`). 세 휠 모두 이 이름의 해독기가 있다.
  - 끝까지 읽기(관측 도구, 32개 목록·두 겹·이름 확인, 사인파 2초, 2026-11-10): 인코더가 있는 더한 코덱 모두 길이 재기와 해독이 `ok` 였고 열린 해독기는
    그 코덱의 것 하나뿐이었다(거절된 해독기 없음).

    | 파일(만든 것) | 길이 재기 | 해독(초 · 최대 진폭) | 열린 해독기 |
    |---|---|---|---|
    | WAV 안 IMA ADPCM(nixpkgs `ffmpeg`) | 2.0 | 2.041 · 0.128 | `adpcm_ima_wav` |
    | WAV 안 Microsoft ADPCM | 2.0 | 2.036 · 0.128 | `adpcm_ms` |
    | MP2 를 담은 `.mp3`(`mp2`·`mp2fixed` 인코더) | 2.011 | 2.011 · 0.125 | 길이 재기 `mp3float`(형식이 처음에 MP3 로 적는다), 해독 `mp2` |
    | WAV 안 MP2(형식 태그 0x0050) | 2.011 | 2.011 · 0.125 | `mp2` |
    | MP4 안 AC-3(`ac3`·`ac3_fixed` 인코더) | 1.994 | 1.994 · 0.125 | `ac3` |
    | MP4 안 E-AC-3 | 1.994 | 1.994 · 0.125 | `eac3` |
    | 3GP 안 AMR-NB 12.2 kbit/s(`.m4a`, PyAV 18.1.0 의 `libopencore_amrnb` 인코더 — 저장소 잠금의 Python 3.11 판, uv 캐시) | 2.0 | 2.014 · 0.135 | `amrnb` |

    PyAV 17·18 로도 같은 해독기가 열렸다(AC-3·E-AC-3 의 해독 길이만 FFmpeg 8 이 끝을 자르지 않아 2.011초). 22개 목록으로 같은 파일을 읽으면 모두 이름
    확인과 `ArgumentError` 로 거절됐다 — 목록을 넓힌 것이 이 결과를 바꾼다.
  - PCM 은 WAV(`riff` 의 PCM·IEEE float·G.711 태그)와 MOV(`twos`·`sowt`·`in24`·`in32`·`fl32`·`fl64`·`raw `·`alaw`·`ulaw`)가 만드는 이름을 모두 넣었다.
    FFmpeg 의 PCM 해독기는 한 소스(`pcm.c`)의 표 변환이라 이름을 늘려도 공격면이 거의 늘지 않고, 24비트·부동소수 WAV 를 쓰는 녹음기에서 회귀를 막는다.
  - 휠의 해독기는 535개(음성 211개)다. 목록은 그중 32개, 모두 음성이다. 영상·이미지·자막 해독기는 하나도 없다(FR-001).
- `[VERIFY]` (운영, 실제 녹음이 처음 올 때): WMA Pro·Voice·Lossless 와 AMR-WB 를 끝까지 읽는 것. nixpkgs `ffmpeg` 8.1.2 와 PyAV 17·18·19 어디에도 이 넷의
  인코더가 없고(`ffmpeg -encoders`, `av.codecs_available`), 시험 파일을 인터넷에서 받지 않는다(운영자 규칙). 이름은 위대로 확인했고 걸리는 방식(`av_match_list`
  의 이름 맞춤)은 끝까지 읽어 본 코덱들과 같다. 그런 녹음이 받기에서 `unreadable_audio` 와 `reader child probe exited with status 1` 로 끝나면 이 항목부터 본다.
- **여전히 거절되는 것**(목록 밖 — 받을 때 `unreadable_audio`, 읽기 단위에 `reader child probe exited with status 1`):
  - Speex(`speex`), GSM(`gsm`·`gsm_ms`) — 운영자 결정. Ogg 안 Speex 는 🟢 거절을 확인했다(`speex` 열리지 않음). GSM 은 nixpkgs `ffmpeg` 에 인코더
    (`libgsm`)가 없어 파일을 만들지 않았다.
  - IMA·Microsoft 밖의 ADPCM. 운영자의 조건(「실제 녹음기 형식이 허용 상자에서 필요할 때만」)에 맞는 것을 찾지 못해 넣지 않았다 — 판단이다:
    QuickTime IMA4(`adpcm_ima_qt`)는 AIFF-C·CAF·MOV 의 코덱이고 AIFF·CAF 는 허용 형식이 아니다. G.726(`g726`·`g726le`)·G.722(`g722`)는 전화·VoIP 녹음의
    코덱이고, Yamaha(`adpcm_yamaha`)는 옛 휴대폰의 형식이다. 음성 녹음기가 쓰는 「ADPCM WAV」는 대개 IMA(형식 태그 0x0011)나 Microsoft(0x0002)로 본다.
    🟢 WAV 안 Yamaha·G.726, MOV(`.m4a`) 안 QuickTime IMA4 는 거절됐다(그 해독기 열리지 않음). 실제 녹음이 오면 이름 하나를 더하는 별도 변경으로 한다.
  - 그 밖의 음성 코덱: MPEG Layer I(`mp1`), TrueSpeech(`truespeech`) 등 목록에 없는 모든 것.
- **판을 올릴 때**: FFmpeg 이 어떤 코덱의 기본 해독기 이름을 바꾸면(예: `mp3float` → 다른 이름) 그 형식이 모두 거절된다. 매달 갱신 순서의
  「빌드·시험」(4.2)에서 A 층의 형식별 시험과 VM 의 B-P2-8 이 잡는다(인코더가 없는 넷은 잡지 못한다 — 위 `[VERIFY]`).
- **대안**: 형식 8가지에 실제로 쓰인 7개만 — 24비트 WAV·ALAC 같은 정상 녹음이 회귀한다. 목록을 운영자 설정으로 — 판단 경로가 늘고(헌법 I),
  코드 한 곳·문서 한 곳의 규칙(FR-004)과 맞지 않는다. 처음 낸 22개(녹음기 코덱 없이) — 002 까지 전사되던 녹음기 녹음(ADPCM WAV, MP2, AC-3, AMR, WMA
  Pro·Voice)이 거절된다. 운영자가 더하기로 정했다(2026-11-10).

## R3. 남는 것 — 분석기(parser)와 묶음 읽기

- **결정**: 이번 범위는 해독기다. 분석기는 그대로 두고, 보안 문서 §2 에 남는 범위로 적는다. 공지 판정 때 「해독기」와 「분석기·형식(demuxer)」을
  나눠 본다. 해독 단계에서 영상 묶음을 버리는 안(아래 대안)은 운영자가 미뤘다(2026-11-10).
- **남는 위험**(보안 문서 §2 에 함께 적는다):
  - 길이 재기: `find_stream_info` 가 영상 트랙의 첫 묶음 몇 개를 영상 분석기로 읽는다(H.264 에서 2번). PyAV 로는 끌 수 없다.
  - 해독: 영상 트랙의 묶음이 모두 형식(demuxer)을 지나고, 분석이 필요한 코덱이면 묶음마다 영상 분석기가 불린다(2초·10 fps H.264 에서 21번). 해독기는 열리지
    않는다. 비음성 흐름을 버리면 없앨 수 있으나 미뤘다(운영자 2026-11-10).
- **근거** 🟢 (`av_parser_parse2` 호출 수):
  - H.264 영상이 든 `m4a`: 목록을 걸어도 길이 재기의 `find_stream_info` 가 H.264 분석기를 2번 부르고, 해독(음성만) 중에도 영상 묶음마다 불러
    21번이었다. 해독 전에 비음성 흐름을 `discard = all` 로 두면 해독 중 호출이 없어졌다(합 3번, 모두 `find_stream_info`).
  - MP3 의 PNG 표지 그림과 ASF 의 MJPEG 영상은 분석기 호출이 0번이었다(MP3 분석기만 불렸다).
  - 분석기는 해독기보다 훨씬 작은 코드지만 맡긴 바이트를 해석한다. 길이 재기 단계(`find_stream_info`)의 분석기는 PyAV 로 끌 수 없다(흐름이
    그 안에서 만들어진다). 그래서 `discard` 는 해독 단계만 줄일 뿐 「서버 경로에서 닿지 않음」을 만들지 못한다.
- **대안**: 해독 단계에서 비음성 흐름을 버리기(`stream.discard = all`) — 두 줄이면 해독 중의 영상 분석기 호출이 없어지지만(21번 → 0번) 길이 재기의
  분석기는 남는다. 운영자가 미뤘다(2026-11-10) — 후속 후보로 남기고 위의 남는 위험으로 적는다. `fflags=+noparse` — 음성 분석기도 끈다(2초 MP3 하나에
  MP3 분석기를 63~93번 부른다). 기각.

## R4. 잘림 기준 — 1초. 처음 잰 길이보다 해독된 길이가 1초 이상 짧으면 한 줄

- **결정**: `TRUNCATION_THRESHOLD_S = 1.0`(`src/stt/server/worker.py`). 워커는 읽기 단계가 `ok` 로 끝나고 `audio.wav` 를 쓴 뒤,
  `job.duration_s − (해독된 바이트 ÷ 2 ÷ 16 000) ≥ 1.0` 이면 운영자 기록에 고정된 한 줄을 쓴다(문구는 R5). 결과·응답은 바꾸지 않는다(FR-007).
- **근거** 🟢 (FFmpeg 9.0.2, 읽기 자식과 같은 코드. 「차이」 = 처음 잰 길이 − 해독된 길이, 초):

  | 형식 | 정상 2초 / 60초 / 600초 | 끝을 잘라 낸 60초(99 % / 90 % / 50 % 바이트) | 잘림을 알아보나 |
  |---|---|---|---|
  | WAV | 0 / 0 / 0 | 6e-5 / 0 / 6e-5 — 처음 잰 길이가 남은 파일 크기를 따른다 | 아니다 |
  | M4A, 색인이 뒤(ffmpeg 기본, 기준 녹음 둘) | 0 / 0 / 0 (clip60 −0.00002, hour −0.056) | 길이 재기 실패(`InvalidDataError`) → 받기에서 `unreadable_audio` | 해당 없음(거절) |
  | M4A, 색인이 앞(`+faststart`) | 0 / 0 / 0 | 0.63 / 6.13 / 30.65 | 그렇다 |
  | MP3(CBR, LAME Info 머리) | 0 / 0 / 0 | 0.60 / 5.98 / 30.01 | 그렇다 |
  | MP3(VBR, Xing 머리 있음) | — / 0 / — | 0.54 / 5.95 / 30.09 | 그렇다 |
  | MP3(VBR, Xing 머리 없음) | — / −5.44 / −55.87 | −5.44 / −4.95 / −2.67 | 아니다(비트율 추정) |
  | FLAC | 0 / 0 / 0 | 0.65 / 6.08 / 30.43 | 그렇다 |
  | Ogg Vorbis | 0 / 0 / 0 | −2e-5 / −3e-5 / 1e-5 — 마지막 페이지로 잰다 | 아니다 |
  | Ogg Opus | 0.0065 / 0.0065 / 0.0065 | 0.0065 / 0.0065 / 0.0065 | 아니다 |
  | AAC(ADTS) | −0.028 / −0.63 / −5.96 | −0.62 / −0.55 / −0.33 | 아니다(비트율 추정) |
  | WMA(ASF) | 0.002 / 0.045 / 0.043 | 0.70 / 4.27 / 2.42 | 부분적으로 |

  - 정상 녹음에서 처음 잰 길이가 더 긴 경우의 최대는 WMA 의 0.045초(PyAV 로 만든 10초 WMA 0.046초)와 Opus 의 0.0065초(앞 건너뛰기 312표본)다.
    1초는 그 21배 넘는 여유다. 002 의 AAC 끝 7.75 ms 는 FFmpeg 8 과 9 사이의 차이고, 같은 판 안에서 기준 녹음은 해독된 길이가 오히려 길다(차이 ≤ 0).
    PyAV 17·18(FFmpeg 8.1)의 시험에서도 M4A 는 해독된 길이가 더 길었다(2초 파일에서 2.020초) — 줄이 생기지 않는 쪽이다.
  - 정상 녹음의 차이는 녹음 길이와 함께 커지지 않는다(인코더 앞뒤 채움·머리의 길이 단위). 그래서 비율이 아니라 고정 초로 둔다. 3시간 녹음의 1 %(108초)
    같은 비율 기준은 실제 잘림을 놓친다.
  - 잘림을 알아보는 형식은 머리가 전체 길이를 적어 두는 것(색인이 앞인 M4A, Xing·Info 머리의 MP3, FLAC 의 STREAMINFO, 대부분의 WMA)이다. 길이를 파일
    자체에서 재는 형식(WAV, Ogg, ADTS AAC)은 잘려도 처음 잰 길이가 같이 줄어 줄이 생기지 않는다. 색인이 뒤인 M4A(휴대폰 녹음과 기준 녹음 둘)는 끝이
    잘리면 처음부터 읽히지 않는다(지금도 `unreadable_audio`).
  - 음성 묶음이 하나도 없는 녹음(US2-4): 색인이 앞인 M4A 를 음성 자료가 시작하는 곳에서 자르면 길이 재기는 머리의 길이(2·60·600초, PyAV 로 만든
    10초는 10.064초)를 내고 해독은 0바이트, 일찍 멈춤 없이 `ok` 다 → 줄이 남고, 엔진은 빈 전사록으로 `done` 이다. 머리만 남은 WAV 는 길이 재기에서
    `InvalidDataError` 로 거절된다.
  - pytest 의 사인파(PyAV 로 만든 10초)를 반으로 자르면 차이가 MP3 5.03, FLAC 5.61, 색인이 앞인 M4A 5.14초로 기준보다 넉넉히 크다. VM 도 10초 녹음을
    쓴다(2초 MP3 의 반은 1.006초라 기준에 너무 가깝다).
- **처음 잰 길이가 있는 곳**: 받기의 길이 재기 값이 작업 기록 `job.json` 의 `duration_s`(`records.Job.duration_s: float`, 필수)에 있다. 재전사는 앞
  작업의 값을 그대로 쓴다(`intake.py:311`). 워커는 `_run_job` 에서 이미 `job.duration_s` 를 처리 상한 계산에 쓴다(`worker.py:295`).
- **스펙의 경계 사례와 맞춰 본 것**:
  - 경계 사례 3(추정값): 머리 없는 VBR MP3 와 ADTS AAC 의 처음 잰 길이는 첫 묶음의 비트율로 추정한 값이다. 사인파에서는 짧게 추정됐지만, 앞부분이
    조용한 강의(적은 비트)는 길게 추정돼 줄이 남을 수 있다. 영상 트랙이 음성보다 긴 파일도 같다 — 처음 잰 길이는 컨테이너 전체 길이다. 줄은 「해독된
    길이가 처음 잰 길이보다 짧다」는 사실만 말하고, 보안 문서 §3 에 이 경우들을 적는다.
  - 경계 사례 4(일찍 멈춘 해독): 읽기 단위의 `stopped early at invalid data` 줄과 이 줄이 함께 남을 수 있다.
  - 경계 사례 5(「처음 잰 길이가 없는 작업」): 지금의 자료 모양에서는 생기지 않는다. 길이 재기는 컨테이너 길이가 없으면 첫 음성 흐름의 묶음 길이 합을
    내고(`reader.py:334-339`), 그 값이 늘 `duration_s` 에 들어간다. 그런 녹음의 끝이 잘리면 묶음 합도 같이 줄어 줄이 생기지 않으므로 경계 사례 5 가
    바라는 결과(줄 없음)는 그대로다. 다만 「묶음 합으로 잰 녹음이 가운데 깨진 조각에서 멈춘」 경우는 줄이 남는다(경계 사례 4 가 허용하는 두 줄).
    길이를 어디서 쟀는지 읽기 프로토콜과 작업 기록에 더하는 안은 계약 밖 내부 형식 둘을 바꾸므로 택하지 않았다. spec 에 이 측정을 `**Revised**:` 줄로
    적었다(2026-11-10).
- **대안**: 0.5초 — 여유는 11배로 충분하지만 2초 시험 녹음에서 기준과 측정값이 가까워져 시험이 흔들린다. 길이 비율 — 위 이유로 기각. 일찍 멈춤
  (`EARLY_STOP_EXIT`)만으로 알아보기 — MP3 를 잘라도 대부분 일찍 멈춤이 없다(표의 `early=False`).

## R5. 잘림 줄의 자리와 문구

- **결정**:
  - 문구(고정, 영어): `decoded audio is shorter than the probed length`. 작업 ID·경로·파일 이름·길이 값이 없다(FR-006).
  - 워커가 표준 출력(작업 줄 `<request_id> <state>` 와 같은 흐름)에 쓴다. 읽기 단계가 `ok` 이고 `audio.wav` 를 쓴 직후, GPU 단계 전이다. 그래서 GPU
    단계가 어떻게 끝나든(VM 의 `gpu_unavailable` 포함) 줄은 남는다.
  - 어느 작업인지는 바로 앞의 `<request_id> running` 줄로 안다 — 워커는 한 번에 한 작업만 하므로 그 줄과 같은 작업의 끝 줄 사이에 놓인다.
  - 비교는 순수 함수(처음 잰 초, 해독된 바이트 → 참·거짓)로 두어 경계값(정확히 1초 → 줄, 그보다 조금 작음 → 없음)을 프로세스 안에서 시험한다.
- **근거**: 스펙 FR-006(줄에 작업 ID 없음)과 002 의 감독 줄 규칙이 같다. 줄을 표준 오류가 아니라 표준 출력에 두는 까닭은 작업 줄과 순서가 섞이지
  않게 하려는 것이다(워커 단위는 두 흐름을 같은 저널 흐름으로 보낸다). 헌법 I: 호출하는 쪽은 처음 잰 길이를 받지 않는다(응답 스키마에 길이가 없다).
  bhu-llm 은 자기 원본 파일로 같은 비교를 할 수 있으므로 stt 는 응답에 아무것도 더하지 않는다. 운영자는 서버 녹음을 열지 않는 규칙(002, 보안 문서 §3)
  때문에 다른 확인 길이 없어 기록 한 줄만 더한다.
- **대안**: 작업이 끝날 때(`_settle`) 쓰기 — GPU 단계가 실패하면 줄이 빠지고, VM(GPU 없음)에서 볼 수 없다. 줄에 작업 ID 붙이기 — 스펙이 막는다.

## R6. 서버 오류의 원인 종류 — 다섯 이름, 줄 끝에 붙인다

- **지금** 🟢(코드): `<request_id> failed engine_error` 는 `worker._settle` 의 `_log(job.request_id, f"failed {failure.reason}")`(`worker.py:235`)
  하나에서 나온다. `engine_error` 가 되는 길은 다섯이다.
  1. 읽기 클라이언트가 `unavailable` — 소켓 없음·접속 거부·「시작」 없음·`started: false`(`worker.py:49`).
  2. 읽기 클라이언트가 `OSError` — 녹음 열기 실패(없음·심볼릭 링크·권한), 파이프·소켓 만들기 실패, 「시작」 뒤의 `job_running` 확인 중 입출력 오류.
     모두 `outcome = "unavailable"` 로 바뀐다(`worker.py:297-301`).
  3. 읽기 클라이언트가 `ValueError` — 녹음이 일반 파일이 아님(`reader_client.py:120-122`), `job_running` 의 깨진 `job.json`. **잡지 않는다** — 워커가
     트레이스백과 함께 끝 코드 1 로 멈추고 작업은 다음 워커 시작이나 청소 때 `interrupted` 가 된다(qa L1).
  4. 엔진이 `failed engine_error` 판정을 씀(엔진 안의 예상 못 한 예외, `worker.py:200`).
  5. 엔진이 쓸 만한 판정 없이 끝남(`_NO_VERDICT`, `worker.py:186-192`·`:201`).

  그리고 녹음 파일이 아예 없으면 2 까지 가지 못한다: `_prepare` 가 `next((layout.audio / job.audio_id).glob("audio.*"))`(`worker.py:69`)에서
  `StopIteration` 을 내고, 상태 잠금 안에서 작업을 `running` 으로 둔 채 워커가 멈춘다. 녹음 자리에 FIFO 가 있으면 `os.open(…, O_RDONLY)` 가 쓰는 쪽을
  기다리며 영원히 막힌다 🟢(2초 뒤에도 돌아오지 않음). 둘 다 FR-009 의 「처리 단위를 멈추지 않고」에 어긋난다.
- **결정**:
  - 줄 모양: `<request_id> failed engine_error <cause>`. 이름 다섯(고정, 영어, `worker.ENGINE_ERROR_CAUSES`):

    | `<cause>` | 언제 | 응답의 `failure.detail`(바뀌지 않음) |
    |---|---|---|
    | `reader_unavailable` | 읽기 클라이언트가 `unavailable`(위 1) | The server could not start the audio reader. |
    | `recording_unusable` | 녹음을 일반 파일로 열지 못함: 없음, 심볼릭 링크, 디렉터리·FIFO 등 일반 파일 아님, 권한, 기술자 부족. 읽기 단위에 아무것도 보내지 않았다 | The server could not start the audio reader. |
    | `unexpected` | 읽기 단계의 그 밖의 `OSError`·`ValueError`(파이프·소켓 만들기, `job_running` 확인 등) — 경계 사례 6 의 일반 이름 | The server could not start the audio reader. |
    | `engine_failed` | 엔진이 `engine_error` 판정을 씀(위 4) | The engine stopped because of an unexpected error. |
    | `engine_no_verdict` | 엔진이 판정 없이 끝남(위 5) | The engine stopped without a verdict. |

    FR-008 은 「처리 단위가 작업을 `engine_error` 로 끝낼 때」라 GPU 단계의 둘도 이름을 받는다. 그래서 규칙이 「`failed engine_error` 줄에는 늘 이름이
    하나 붙는다」로 단순해진다.
  - 녹음 열기를 한 예외로: `reader_client` 에 `RecordingError(OSError)` 를 두고, `_open_recording` 이 여는 데 실패하면(`OSError` 의 errno 를
    그대로 실어) 또 일반 파일이 아니면(errno 없음) 이것을 낸다. 받기의 기록(`intake error <이름>`)은 errno 가 있으면 지금처럼 errno 이름이다.
  - 녹음 열기에 `O_NONBLOCK` 을 더한다. FIFO 도 바로 열리고 `fstat` 이 「일반 파일 아님」을 알려 `recording_unusable` 이 된다. 일반 파일에는 효과가
    없다 🟢(60초 M4A 를 두 방식으로 연 기술자에서 해독 출력이 SHA-256 까지 같았다).
  - `_prepare` 는 녹음 경로를 `glob` 하지 않고 녹음 기록의 원래 이름에서 정한다: `audio/<audio_id>/audio.<원래 이름의 확장자>` — 받기가 복사할 때
    쓰는 규칙(`intake.py:193`, 확장자 = `원래 이름.rsplit(".", 1)[1]`)과 같다. 파일이 없으면 열기에서 `RecordingError`(ENOENT)가 되어 위 표대로 끝난다.
  - 워커의 읽기 단계는 `RecordingError` → `recording_unusable`, 그 밖의 `OSError`·`ValueError` → `unexpected`, 클라이언트의 `unavailable` →
    `reader_unavailable` 로 나눈다. 셋 다 응답은 지금과 같다(SC-004).
- **근거**: 원인 이름은 응답에 없고 운영자 기록에만 있다(계약 그대로, FR-015). 「읽기 단위 고장」과 「녹음 파일 문제」는 대응이 다르다 — 앞은
  `systemctl status stt-reader.socket`, 뒤는 그 녹음 폴더. 이름은 「무엇이 실패했나」(단계)를 말하고 「왜」(errno)는 말하지 않는다 — 스펙 US3-4 가
  분류 이름만 허락한다. 기술자 부족(EMFILE)으로 녹음을 못 연 것도 `recording_unusable` 로 보이는 것은 받아들인다(같은 녹음을 다시 맡겨 보면 가린다).
- **바꿔야 하는 기존 시험**(줄 끝이 바뀌어서): `tests/server/test_decoder_us3.py:321`·`:571`(줄 전체 비교), `:548-571`(`_open_recording` 을 맨
  `OSError` 로 바꿔 넣는 시험 — 녹음 쪽은 실제 없는 파일·권한으로, 그 밖은 `os.pipe2` 의 EMFILE 로 넣는다), `tests/server/test_reader_client.py:155-197`
  (열기 오류의 예외 형: `RecordingError`, errno 유지). `tests/server/test_logging.py:174` 는 부분 문자열 비교라 그대로 통과한다. 그리고 US2 의 잘림 줄 때문에
  `tests/server/test_decoder_us3.py:514`(`test_decoder_edge_10_a_recording_damaged_from_its_first_frame_…`, 2초로 잰 녹음이 0바이트로 해독됨)의 표준
  출력 기대를 `[running, TRUNCATION_LINE, done]` 으로 고친다(경계 사례 4). `:492` 시험은 길이가 같아 그대로다.
- **대안**: 작업 줄에 errno 이름(`describe_error`)을 붙이기 — 이름 집합이 열려 있고(스펙 Key Entities 의 「작은 집합」과 다름) 같은 errno 가 여러
  단계에서 나와 원인을 가리지 못한다. 워커가 읽기 전에 녹음을 `lstat` 해 보기 — 클라이언트의 확인과 겹치는 방어 분기다. 열기를 호출하는 쪽으로
  옮기고 `probe`·`decode` 가 기술자를 받기 — 받기·VM 스크립트·문서의 스니펫·시험 수십 곳이 바뀐다.

## R7. systemd 판 — 257 이상. 모듈의 `assertions`

- **결정**: `nix/module.nix` 의 `config` 에 단언 하나:
  `lib.versionAtLeast config.systemd.package.version "257"`, 어기면 영어 문장(문구는 [contracts/module.md](contracts/module.md))으로 빌드를 거부한다.
  시험은 flake 의 새 검사 `checks.x86_64-linux.module-eval` 이 평가 시점의 `assert` 로 한다(빌드 없이 `nix flake check --no-build` 도 잡는다).
- **근거** 🟢:
  - 이 호스트의 systemd 262 설명서(`systemd.exec(5)` 등)의 「Added in version」: 서버 단위들이 쓰는 설정 가운데 가장 새것이 `PrivatePIDs=`(257)다.
    그다음이 `PrivateIPC=`(248), `ProtectProc=`·`ProcSubset=`(247), `ProtectClock=`(245), `ProtectKernelLogs=`(244), `OOMPolicy=`(243),
    `ProtectHostname=`(242)이고 나머지는 그 아래다. `PrivatePIDs` 는 읽기 단위만이 아니라 001 의 공통 묶음 `hardening`(`module.nix:52`)에도 있다.
  - 모르는 설정은 systemd 가 경고만 남기고 무시한다(002 감사 L5). 257 아래에서는 읽기 인스턴스들(같은 동적 UID)이 서로를 보고 신호를 보낼 수 있다.
  - `config.systemd.package.version` 이 판 문자열이다: 이 flake 의 nixpkgs 에서 `"261.1"`(VM 과 같음). 시험 모듈로 `systemd.package` 를
    `pkgs.systemd.overrideAttrs (_: { version = "256.10"; })` 로 바꾸자 단언이 그 문장으로 실패했고, 그대로(261.1)는 통과했다. `pkgs.systemd // { version = …; }`
    로는 바뀌지 않았다 — 시험은 `overrideAttrs`(+ `__intentionallyOverridingVersion = true`, nixpkgs 경고를 끈다)를 쓴다. NixOS 평가 한 번의 최대 메모리가 약 4.2 GB 였다
    (`captest` 의 최대값). `boot.isContainer = true` 로 부트로더·파일시스템 단언을 피했다.
- `[VERIFY]` (B-003-6): CI 의 `nix flake check --no-build` 가 이 검사를 평가하는 시간과 메모리(VM 시험 노드 평가와 같은 급으로 본다).
- **대안**: 단위 파일에 `ConditionVersion=systemd >= 257` — 이 호스트의 설명서는 이 설정을 258 에서 들어온 것으로 적어, 바로 그 낮은 판들에서는 설정
  자체가 경고만 남기고 무시된다. 조건이 걸리는 판에서도 단위가 조용히 건너뛰어져 서버가 말없이 멈춘다(헌법 V). 실행 중 확인 — 배포 뒤에야 안다. 기각.

## R8. 끊기 신호 — 감독은 SIGINT 를 무시한다

- **결정**: `reader.serve()` 가 맨 처음 `signal.signal(signal.SIGINT, signal.SIG_IGN)` 을 한다. 해독 자식은 이 처분을 물려받는다(자식에게는 상관없다).
- **근거** 🟢 (`unshare --user --map-root-user --pid --fork` 로 Python 을 새 PID 이름공간의 PID 1 로 띄우고, 자식이 `os.kill(os.getppid(), SIGINT)`):

  | 감독의 SIGINT 처분 | 보통 프로세스(pytest 의 감독) | PID 1(실제 읽기 단위) |
  |---|---|---|
  | 지금(Python 기본 처리기) | `KeyboardInterrupt` | `KeyboardInterrupt` — 002 감사 L1 그대로 |
  | `SIG_DFL` | 신호로 죽음(끝 코드 130) | 살아남음(커널이 처리기 없는 신호를 PID 1 에 넘기지 않음) |
  | `SIG_IGN` | 살아남음 | 살아남음 |

  감사가 권한 `SIG_DFL` 은 PID 1 일 때만 맞고, pytest(PID 1 아님)에서는 감독이 죽어 RED → GREEN 시험을 못 한다. `SIG_IGN` 은 두 곳에서 같다.
  Python 이 처리기를 거는 신호는 SIGINT 뿐이라 다른 신호는 PID 1 에서 이미 버려진다.
- **대안**: `SIG_DFL`(위 이유), `pthread_sigmask` 로 막기(대기 신호가 쌓이고 자식이 막힌 마스크를 물려받는다 — 같은 효과에 더 복잡). 기각.
- 시험 자리: `_supervise` 를 프로세스 안에서 부르는 시험은 pytest 자신의 SIGINT 처분을 바꾸지 않는다 — 처분은 `serve()` 에서만 바꾸고 시험은 감독을
  하위 프로세스(`tests/server/reader_harness.py`)로 띄운다.

## R9. 손으로 띄운 `stt server read` — 표준 오류에 한 줄, 끝 코드 2 그대로

- **결정**: `serve()` 가 표준 입력을 연결된 `AF_UNIX` `SOCK_SEQPACKET` 소켓으로 받지 못하면(`_adopt_stdin()` 이 `None`) 표준 오류에 고정 문장
  `stt server read only serves a connection that systemd passes from stt-reader.socket on standard input` 한 줄을 쓰고 지금처럼 2 를 돌려준다.
- **근거**: 터미널에서 띄우면 표준 입력이 TTY 라 `socket.socket(fileno=0)` 이 실패하고 지금은 아무 말 없이 2 로 끝난다(002 감사 L8,
  `tests/server/test_server_cli.py:511-516` 가 「출력 없음」을 단언한다). 읽기 단위 안에서는 표준 입력이 늘 연결이라 이 줄은 운영 중에 나오지 않는다.
  나오더라도 단위 로그로 가고, 작업 ID·경로가 없다.
- **대안**: 끝 코드를 바꾸기 — 스펙이 막는다(「끝 코드는 지금과 같다」). 감독의 다섯 줄과 같은 `reader child …` 꼴 — 자식과 무관한 줄이라 쓰지 않는다.

## R10. 현재 폴더를 모듈 찾는 길에서 뺀다 — `-m` 으로 띄우는 두 자식에 `-I`

- **지금** 🟢:
  - 세 단위는 makeWrapper 셸 스크립트 `…-stt-server-env-wrapped/bin/stt` 를 띄우고, 그것이 `.stt-wrapped`(→ `…-stt-server-env/bin/stt`, 셔뱅
    `…/bin/python3.12` 인 콘솔 스크립트)를 실행한다. 스크립트로 뜬 Python 의 `sys.path[0]` 은 스크립트가 있는 폴더(심볼릭 링크를 푼 뒤)다 — 읽기
    전용 Nix 저장소다. 단위의 현재 폴더는 `/`(`WorkingDirectory=` 비어 있음)다.
  - 감독은 해독 자식을 `[sys.executable, "-m", "stt.server.reader", "child", …]`(`reader.py:47`)로, 워커는 엔진을
    `(sys.executable, "-m", "stt.server.engine")`(`worker.py:31`)로 띄운다. `-m` 은 현재 폴더를 `sys.path[0]` 에 넣는다.
  - 다른 폴더에서 `-m` 으로 띄우자 `sys.path[0]` 이 그 폴더였고, 그 폴더에 둔 `json.py` 가 읽기 자식에 먼저 읽혀 끝 코드 99 로 끝났다. `-I` 를 붙이자
    읽히지 않았다(끝 코드 1 — 인자가 없어서). `-P`·`PYTHONSAFEPATH=1` 도 막았다. `-I` 로 배포된 해석기에서 `stt.server.reader`·`stt.server.engine` 이
    읽히고, 개발 환경(`.venv`, `__editable__.stt-0.3.0.pth`)에서도 `-I -c "import stt, av"` 가 된다.
- **결정**: `DEFAULT_CHILD_COMMAND = (sys.executable, "-I", "-m", "stt.server.reader", "child")`,
  `DEFAULT_ENGINE_CMD = (sys.executable, "-I", "-m", "stt.server.engine")`. 단위 설정은 바꾸지 않는다.
- **근거**: 현재 폴더를 넣는 것은 이 둘뿐이다(세 단위의 주 프로세스는 스크립트). `-I` 는 Python 3.4 부터 있어 3.10 시험에서도 돈다. 두 자식은
  `PYTHON*` 환경 변수를 쓰지 않는다(단위 환경은 `LOCALE_ARCHIVE`·`PATH`·`TZDIR`, 워커가 엔진에 더하는 것은 `TMPDIR`·`HF_HOME`·`HF_HUB_OFFLINE`).
  pytest 가 「현재 폴더에 심은 모듈」로 두 명령을 RED → GREEN 으로 본다.
- **대안**: `-P` — Python 3.11+ 라 3.10 시험에서 `Unknown option` 으로 깨진다. 단위에 `Environment=PYTHONSAFEPATH=1` — 자식이 환경을 물려받을 때만
  성립하는 암묵 규칙이고 pytest 로 볼 수 없으며 3.10 은 무시한다. 기각.

## R11. 해독된 임시 음성 — 만든 기술자로 끝까지 쓴다

- **결정**: `worker._write_audio` 는 `os.open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0o600)` 의 기술자를 `os.fdopen(…, "wb")` 로
  감싸 `wave.open(<그 파일 객체>, "wb")` 에 넘긴다. 경로로 다시 열지 않는다.
- **근거** 🟢 (단위와 같은 `umask 0027`): 지금 코드는 만든 뒤 닫고 경로로 다시 연다. 그 사이에 「다 지우기」가 파일을 지우면 다시 연 쪽이 `0640` 으로
  새로 만든다(qa L3 확인). 기술자로 쓰면 지워진 아이노드에 쓰고 경로에는 아무것도 다시 생기지 않았다. 정상 경우 두 방식 모두 `0600`, 같은 WAV 머리
  (1채널·2바이트·16 kHz·1600프레임)였다. `wave` 는 받은 파일 객체를 닫지 않으므로 `with os.fdopen(...)` 이 닫는다. `wave.open` 을 모듈로 부르는 자리는
  그대로라 저장 오류 시험(`test_decoder_us3.py:452`)의 바꿔치기가 여전히 닿는다.
- **대안**: 다시 연 뒤 `fchmod` — 잠깐이라도 넓은 권한으로 생기는 순간이 남는다. 기각.

## R12. 시험 전략 — 공격 파일 없이, 세 층

- **A. pytest**(GPU·망·systemd·C 컴파일러 없이):
  - 시험 녹음은 PyAV 로 만든다 🟢: MP4 안 AAC + 단색 MPEG-4·H.264 영상(`libx264` 는 휠에 있다), MP3(`libmp3lame`) + 단색 PNG 표지
    (`Disposition.attached_pic` 흐름에 PNG 묶음 하나), 색인이 앞인 M4A(`options={"movflags": "+faststart"}`), 더한 녹음기 코덱(WAV 안 IMA·Microsoft
    ADPCM, MP2, MP4 안 AC-3·E-AC-3 — 휠 19 에 인코더가 있다), 목록 밖 코덱(WAV 안 Yamaha ADPCM, MOV 안 QuickTime IMA4). 모두 위 R1·R2 의 관측으로 같은
    해독기가 열리고 거절됨을 확인했다. AMR-NB 는 PyAV 17·18 에만 인코더(`libopencore_amrnb`)가 있어 Python 3.10·3.11 에서만 만들고, 3.12 에서는 이유를
    적고 건너뛴다. 끝 자르기는 파일을 반으로 `truncate` 한다.
  - 「영상·그림 해독기가 열리지 않음」은 프로세스 안에서 `reader._open_container` 로 연 컨테이너의 비음성 흐름이 `pix_fmt is None` 이고
    `decode(video=0)` 이 `ArgumentError` 인 것으로 본다(R1). 진짜 감독·자식(흉내 소켓)으로 길이 재기·해독 결과가 음성만 든 녹음과 같은지 본다.
  - 감독의 SIGINT 는 대역 자식에 새 동작 `sigint-parent`(부모에게 SIGINT 를 보내고 정상 결과를 씀)를 더해 본다.
  - 이름: 이 기능의 시험은 `tests/server/test_hardening_us<N>.py` 의 `test_hardening_us<N>_<n>_…` 이다(001 `test_us…`, 002 `test_decoder_us…` 와 겹치지 않게).
- **B. Nix 검사**:
  - `checks.x86_64-linux.module-eval`(새것): systemd 판 단언(R7).
  - `checks.x86_64-linux.server-vm`: VM 의 읽기 단위에만 시험용 감시 라이브러리를 미리 올린다(`systemd.services."stt-reader@".environment.LD_PRELOAD`,
    VM 노드 설정이고 모듈이 아니다). 이 라이브러리는 진짜 `avcodec_open2` 가 **성공**했는데 그 문맥이 음성이 아니면 `abort()` 한다. 🟢 시제품을 배포된
    해석기로 돌리자 지금 코드에서는 영상·표지 그림·영상만 든 파일의 길이 재기와 해독이 모두 SIGABRT(끝 코드 134)로 끝났고, 결정한 코드에서는 하나도
    끝나지 않았다. 진짜 감독이 그 끝을 `reader child probe killed by signal 6` 로 적으므로, 「진짜 읽기 단위로 읽어 영상·이미지 해독기가 열리지 않음」을
    VM 의 저널로 본다(SC-001).
  - `[VERIFY]` (B-003-1): 감시 라이브러리가 읽기 단위의 제한(`DynamicUser`·`MemoryDenyWriteExecute`·호출 필터·`TemporaryFileSystem`) 안에서 실려 일한다
    — 시제품은 단위 밖에서만 돌렸다. RED(지금 코드에서 `killed by signal 6`)를 먼저 보면 확인된다.
  - VM 의 녹음은 nixpkgs `ffmpeg` 8.1.2 로 만든다. 끝을 자른 녹음은 `head -c` 로, 음성 묶음이 없는 녹음은 색인이 앞인 M4A 를 `mdat` 내용이 시작하는
    곳에서 자른다. `[VERIFY]` (B-003-2): 빌드 안에서 만든 잘린 파일이 이 세션의 측정과 같게 읽히는지(줄이 그 작업마다 하나).
- **C. 호스트**(운영자와): 배포 뒤 기준 녹음 1차·2차가 2026-11-10 결과와 같고 잘림 줄이 없는지, 영상·표지 그림이 든 사인파가 배포된 읽기 단위로
  읽히는지(§3 의 안전한 재확인 스니펫), 손으로 띄운 `stt server read` 의 안내 줄.
- **근거**: FR-017(공격 파일 없음). 002 R14 와 같은 층. 감시 라이브러리는 해석을 바꾸지 않고 「금지된 일이 일어났다」만 끝으로 알린다.
- **대안**: VM 에서 로그 공유 라이브러리로 열린 해독기를 적기 — 해독 자식의 표준 출력·오류는 `/dev/null` 이고 파일을 쓸 수 없어 알릴 길이 없다. 해독
  자식에 진단 출력을 더하기 — 운영 코드에 시험용 길을 낸다. 기각.

## R13. 문서 — `docs/server-security.md`

- **결정**([contracts/operator-log.md](contracts/operator-log.md) §5):
  - §2 에 「해독기 목록」 절과 표식 `<!-- stt:audio-decoders -->` 표(칸 `decoder`·`codec`·`formats`). 남는 범위(분석기·형식)와 공지 판정 한 줄.
  - §3 에 워커의 두 줄(잘림, 원인 종류)과 그때 볼 것, 표식 `<!-- stt:engine-error-causes -->` 표, `<!-- stt:truncation threshold_s=1.0 -->`, 손으로 띄운
    `stt server read` 의 줄. 「가장 흔한 줄」의 원인에 「목록 밖 코덱」을 더한다.
  - §4 의 「후속 후보: 코덱 허용목록 … 넣지 않았다」(`docs/server-security.md:238`)를 이 기능의 결과로 바꾸고, 4.2 정책에 「영상·이미지·자막 해독기만의
    공지는 『서버 경로에서 열리지 않음』을 이유로 적을 수 있다」를 더한다.
  - `tests/test_server_security_doc.py` 가 표식으로 읽어 `reader.AUDIO_DECODERS`·`worker.ENGINE_ERROR_CAUSES`·`worker.TRUNCATION_THRESHOLD_S`·
    `worker.TRUNCATION_LINE`·`reader.MANUAL_RUN_LINE` 과 같은지 본다(FR-004, US1-6). 문서는 한국어, 시험이 읽는 자리는 영어다(헌법 언어 규칙).
- **근거**: FR-004·FR-016, 002 R16 의 「문서 ↔ 코드 시험」 방식.

## R14. 계약 경계와 고칠 내부 문서

- **바뀌지 않는 것**: 요청·응답·결과 스키마(`src/stt/server/schemas/*.json`), 실패 이유 11개, 응답의 고정 문장, 「다 지우기」, 재전사, 23:58 정리, 읽기
  프로토콜의 메시지·상수(새 상수 `AUDIO_DECODERS` 는 자식 안에서만 쓴다), 단위 설정(모듈은 단언 하나만 더한다).
- **호출하는 쪽에 보이는 바뀜**: 목록 밖 음성 코덱의 녹음이 `unreadable_audio` 가 된다(R2). 실패 이유와 문장은 이미 있는 것이라 계약 위반은 아니다
  (헌법 VI 은 호환되지 않는 변경만 핸드오프를 요구한다). 운영자 결정(2026-11-10): 003 배포 회신에 거절되는 코덱을 적은 한 단락을 싣는다 — Speex, GSM,
  그 밖의 목록 밖 음성 코덱(R2 의 「여전히 거절되는 것」)과 그때의 응답(`unreadable_audio` — 받을 때 바로, MP3 라고 적힌 상자 안 MPEG Layer I 는 처리 때). 회신은
  `specs/003-reader-hardening/handoff/reply.md` 에 쓰고 운영자가 bhu-llm 대장으로 옮긴다(quickstart C-003-6).
- **고칠 내부 문서**(구현 때): `specs/002-decoder-isolation/contracts/reader.md` §4(상수 `AUDIO_DECODERS`), §5(SIGINT 무시, 손으로 띄운 줄),
  §6(자식이 두 목록으로 연다, 길이 재기의 해독기 이름 확인, `-I`), §7(`RecordingError`·`O_NONBLOCK`), §8(끝 코드 2 의 줄). 각 자리에 이 기능의
  [contracts/](contracts/) 를 가리키는 한 줄을 둔다.
- **근거**: 헌법 VI, FR-015.
