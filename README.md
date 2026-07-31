# stt — Lecture Transcription CLI

Transcribe lecture videos (`.mp4 .mkv .mov …`) and audio recordings
(`.m4a .mp3 .wav …`) into readable Korean transcripts (`.txt`),
running fully locally on your GPU with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper).
No audio ever leaves your machine.

## Features

- Automatic GPU detection with VRAM-aware model fallback
  (`large-v3` → `large-v3-turbo` → `small`); CPU also works
- Built-in VAD (skips silence/music, prevents Whisper
  hallucinations), Korean by default (`--language auto` available)
- Terms file (hotwords) to steer spelling of domain jargon and
  proper nouns
- Segments reassembled into sentences/paragraphs with `[MM:SS]`
  paragraph timestamps; optional plain text and `--srt` output
- Batch-process whole folders; already-transcribed files are
  skipped (safe to re-run, cron-friendly)

## Layout

```
transcribe.py       CLI (single file)
transcribe.bat      Windows drag & drop helper
terms_example.txt   example hotwords file
tests/              pytest suite (36 tests)
data/               media and transcripts (git-ignored)
INSTALL.md          setup guide (Windows GPU + Linux)
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and, for GPU use,
NVIDIA libraries (see [INSTALL.md](INSTALL.md)).

```sh
uv sync                      # creates .venv with dependencies
uv run transcribe.py "data/lecture.mp4" --terms terms_example.txt
```

The first run downloads the model from Hugging Face
(large-v3 ≈ 3.1 GB) into the local cache; subsequent runs start
immediately. Output `.txt` is written next to the source file.

Without uv, a plain venv works too:
`pip install -r requirements.txt`.

## Usage

```sh
uv run transcribe.py PATH [PATH ...] [options]
```

| Option | Default | Description |
|---|---|---|
| `--model` | auto | `auto` = large-v3 → large-v3-turbo → small fallback |
| `--device` | auto | `cuda` if available, else `cpu` |
| `--language` | ko | language code, `auto` to detect |
| `--terms FILE` | — | hotwords file (one term per line, `#` comments) |
| `--no-timestamps` | off | plain text without `[MM:SS]` markers |
| `--srt` | off | also write an SRT subtitle file |
| `--overwrite` | off | regenerate existing outputs |
| `--output-dir DIR` | beside source | where to write outputs |
| `--beam` | 5 | beam size (1 = faster, slightly less accurate) |
| `--gap` | 2.0 | silence length (s) that starts a new paragraph |
| `--max-chars` | 800 | max paragraph length |

CPU-only servers: `--model small --beam 1` is the practical
combination for batch automation.

## Tests

```sh
uv run pytest tests/
```

## Notes

- Media files, transcripts, and NVIDIA library archives are never
  committed (`data/` is git-ignored). Transcripts contain
  third-party lecture content and stay local by policy.
- Knowledge-base prompts and templates that consume these
  transcripts live in a separate repository
  ([ecoinfoai/kb](https://github.com/ecoinfoai/kb)).
