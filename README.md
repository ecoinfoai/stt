# stt — 강연 전사 도구

강연·강의 영상(.mp4 .mkv …)이나 녹음(.m4a .mp3 .wav …)을
한국어 전사문(.txt)으로 바꾸는 CLI 도구. faster-whisper 기반으로
로컬 GPU에서 동작한다.

## 구성

```
transcribe.py        전사 CLI (faster-whisper 기반)
전사.bat             끌어다 놓기 실행기 (Windows)
requirements.txt     파이썬 의존성
terms_example.txt    용어 파일 예시 (hotwords)
tests/               단위·통합 테스트 (pytest, 36개)
설치_사용_안내.md    Windows 11 설치·사용 안내
```

## 특징

- GPU 자동 감지, VRAM 부족 시 모델 자동 폴백
  (large-v3 → large-v3-turbo → small)
- VAD 내장(무음·음악 구간 환각 방지), 한국어 기본
- 용어 파일(hotwords)로 전문용어·인명 표기 유도
- 세그먼트를 문장·문단으로 복원, `[MM:SS]` 문단 타임스탬프
- 폴더 일괄 처리, 기존 결과 자동 건너뛰기, `--srt` 자막 출력 옵션

## 빠른 시작

설치는 [설치_사용_안내.md](설치_사용_안내.md) 참조 (가상환경,
CUDA 12용 cuBLAS/cuDNN 9 배치 포함). 이후:

```powershell
python transcribe.py "강연.mp4" --terms terms_example.txt
```

## 테스트

```powershell
python -m pip install pytest
python -m pytest tests/
```

## 참고

- 미디어 파일·전사 결과물·지식베이스 문서·모델 라이브러리
  압축본은 커밋하지 않는다(.gitignore 참조). 전사 결과에는 강연
  저작물이 포함되므로 공개 저장소에 올리지 않는 방침.
- 요구사항: Python 3.11+, faster-whisper 1.2.1, NVIDIA GPU(선택,
  CPU도 동작).
