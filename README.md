# stt — Lecture Transcription Pipeline

Fetch lecture videos from YouTube with their metadata, transcribe
them into readable Korean transcripts, and hand the result to a
knowledge-base note generator. Transcription runs fully locally on
your GPU with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) — no
audio ever leaves your machine.

```
urls.txt ──fetch.py──▶ data/title [ID].m4a          (audio)
                       data/title [ID].info.json    (metadata)
                              │
                       transcribe.py
                              ▼
                       data/title [ID].txt          (transcript)
                       data/title [ID].meta.yaml    (note metadata)
```

Every stage skips what is already done: downloads are recorded in
`data/archive.txt`, and a media file with an existing `.txt` is
left alone. Append URLs to the list, re-run, and only the new ones
are processed.

## Features

- **Fetch** — one command turns a URL list into audio plus a
  metadata sidecar; `[videoID]` in the filename keeps audio,
  metadata and transcript reliably paired
- **Transcribe** — automatic GPU detection with VRAM-aware model
  fallback (`large-v3` → `large-v3-turbo` → `small`); CPU works too
- Built-in VAD (skips silence/music, prevents Whisper
  hallucinations), Korean by default (`--language auto` available)
- Terms file (hotwords) to steer spelling of domain jargon and
  proper nouns
- Segments reassembled into sentences/paragraphs with `[MM:SS]`
  paragraph timestamps; optional plain text and `--srt` output
- **Note metadata** — `.meta.yaml` uses the exact field names of
  the `transcript-report` note template, filling only what can be
  established mechanically and recording where each value came from

## Layout

```
fetch.py            download audio + metadata from a URL list
transcribe.py       transcription CLI
metadata.py         info.json + run info → .meta.yaml
batch_stt.py        transcribe a curated list, per-item options

run_all.bat/.sh     fetch + transcribe in one go
run_list.bat/.sh    transcribe from a list file
transcribe.bat      Windows drag & drop helper
_venv.sh            shared interpreter/CUDA setup for the .sh files

urls_example.txt    example URL list
list_example.txt    example media list
list_example.yaml   example media list with per-item options
terms_example.txt   example hotwords file
tests/              pytest suite
data/               media, metadata and transcripts (git-ignored)
INSTALL.md          setup guide (Windows GPU + Linux)
```

## Quick start

Requires [uv](https://docs.astral.sh/uv/), `ffmpeg`, and — for GPU
use — NVIDIA libraries (see [INSTALL.md](INSTALL.md)).

```sh
uv sync                          # creates .venv with dependencies
cp urls_example.txt urls.txt     # edit: one YouTube URL per line
./run_all.sh urls.txt            # Windows: run_all.bat urls.txt
```

The first transcription downloads the model from Hugging Face
(large-v3 ≈ 3.1 GB) into the local cache; later runs start
immediately.

Without uv, a plain venv works too:
`pip install -r requirements.txt`.

## Usage

### fetch.py — download audio and metadata

```sh
uv run fetch.py --urls urls.txt
```

| Option | Default | Description |
|---|---|---|
| `--urls FILE` | required | URL list (one per line, `#` comments) |
| `--out-dir DIR` | data | where downloads land |
| `--video` | off | keep the video instead of extracting audio |
| `--audio-format` | m4a | audio format when extracting |
| `--auto-subs` | off | also fetch YouTube auto-captions (`.srt`) |
| `--sub-langs` | ko | auto-caption language |
| `--cookies-from-browser` | — | e.g. `chrome`, for restricted videos |
| `--dry-run` | off | print the yt-dlp command and stop |

Request pacing (`--sleep-requests 1`, `--sleep-interval 3..8`) is
always applied to stay well under YouTube's rate limits. Runs of
30–50 URLs are routine; a temporary block, if it ever happens,
clears on its own within hours.

### transcribe.py — media → transcript

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
| `--no-meta` | off | skip the `.meta.yaml` sidecar |
| `--overwrite` | off | regenerate existing outputs |
| `--output-dir DIR` | beside source | where to write outputs |
| `--beam` | 5 | beam size (1 = faster, slightly less accurate) |
| `--gap` | 2.0 | silence length (s) that starts a new paragraph |
| `--max-chars` | 800 | max paragraph length |

CPU-only servers: `--model small --beam 1` is the practical
combination for batch automation.

### batch_stt.py — transcribe a curated list

Use this when the media is already downloaded and you want a
subset, or different options for some files.

```sh
uv run batch_stt.py --list list.txt --dry-run
uv run batch_stt.py --list list.yaml --keep-going
```

A list entry may be a bare title without extension — the matching
media file in `data/` is found for you, preferring audio over
video of the same name. Every entry is resolved *before* any
transcription starts, so a typo surfaces in seconds rather than an
hour in. `--dry-run` prints the resolved plan and stops. Items
sharing the same options are grouped so the model loads once per
group; see `list_example.yaml` for per-item overrides.

## Note metadata (`.meta.yaml`)

Field names match the `transcript-report` note template
(`template_version` 1.1), so a note generator can copy them across
without translation:

| Filled automatically | Left for you |
|---|---|
| `source_url`, `language`, `duration`, `model`, `transcribed` | `title`, `speaker`, `institution`, `source_type`, `recorded`, `course`, `topics` |

Values that can only be guessed are never written into the real
fields. They go to `_meta.candidates` (speaker inferred from the
title, institution from the channel, topics from tags) for a human
to pick from. `_meta.provenance` records where each value came
from and maps onto the report's '메타데이터 근거' section.

YouTube's publish date is *not* the same as when a talk was given,
so it is kept as `_meta.published` and `recorded` stays empty.

## Tests

```sh
uv run pytest tests/
```

## Notes

- Media files, metadata sidecars, transcripts and NVIDIA library
  archives are never committed (`data/` is git-ignored).
  Transcripts contain third-party lecture content and stay local
  by policy.
- Personal run lists (`urls.txt`, `list.txt`, `list.yaml`) are
  git-ignored; the `*_example` files are tracked instead.
- Knowledge-base prompts and templates that consume these
  transcripts live in a separate repository
  ([ecoinfoai/kb](https://github.com/ecoinfoai/kb)).
