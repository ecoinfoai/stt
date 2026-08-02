"""강연 미디어를 내려받아 한국어 전사문으로 바꾸는 로컬 파이프라인.

서브커맨드 하나씩이 한 단계를 맡는다.

- ``stt fetch``: yt-dlp로 음원과 info.json을 내려받는다.
- ``stt transcribe``: faster-whisper로 전사문(.txt)과 .meta.yaml을 만든다.
- ``stt batch``: 목록 파일에 적은 여러 미디어를 순서대로 전사한다.
"""
