"""Tests for the transcription engine, run in-process with fake models."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stt import diarize, transcribe
from stt.server import contract, engine
from stt.server.models import PINNED

REQUEST_ID = "0b0c7a2e-5d63-4c43-9a3e-6c1f0b7d9e11"
ORIGINAL_NAME = "lecture one.m4a"


class FakeTokenizer:
    """A tokenizer where one character is one token."""

    def encode(self, text: str, add_special_tokens: bool = True) -> SimpleNamespace:
        return SimpleNamespace(ids=[ord(ch) for ch in text])


def make_segment(start: float, end: float, text: str) -> SimpleNamespace:
    """Builds a segment whose words share the segment time evenly."""
    pieces = text.split()
    step = (end - start) / len(pieces)
    words = [
        SimpleNamespace(start=start + i * step, end=start + (i + 1) * step, word=f" {piece}")
        for i, piece in enumerate(pieces)
    ]
    return SimpleNamespace(start=start, end=end, text=f" {text}", words=words)


PLAIN_SEGMENTS = [
    make_segment(0.0, 3.0, "First sentence."),
    make_segment(3.2, 6.0, "Continuing sentence."),
    make_segment(10.0, 12.0, "A new paragraph."),
]


class FakeModel:
    """A stand-in for WhisperModel.transcribe with the same contract."""

    def __init__(self, segments: list[SimpleNamespace], duration: float = 12.0) -> None:
        self.segments = segments
        self.duration = duration
        self.calls: list[dict[str, Any]] = []
        self.hf_tokenizer = FakeTokenizer()

    def transcribe(self, audio: str, **kwargs: Any) -> tuple[Any, SimpleNamespace]:
        self.calls.append({"audio": audio, **kwargs})
        return iter(self.segments), SimpleNamespace(duration=self.duration, language="en")


class Harness:
    """Wires fakes into the engine and runs it on a prepared work folder."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.work_dir = tmp_path / "job"
        self.work_dir.mkdir()
        self.audio = tmp_path / "audio.m4a"
        self.audio.touch()
        self.model = FakeModel(PLAIN_SEGMENTS)
        self.load_calls: list[tuple[str, str]] = []
        self.pipeline_devices: list[str] = []
        self.wav_destinations: list[Path] = []
        self.turns: list[diarize.Turn] = []
        monkeypatch.setattr(tempfile, "tempdir", None)
        monkeypatch.setattr(transcribe, "load_model", self._load_model)
        monkeypatch.setattr(diarize, "load_pipeline", self._load_pipeline)
        monkeypatch.setattr(diarize, "to_wav", self._to_wav)
        monkeypatch.setattr(diarize, "diarize_wav", lambda *args, **kwargs: list(self.turns))

    def _load_model(self, name: str, device: str) -> FakeModel:
        self.load_calls.append((name, device))
        return self.model

    def _load_pipeline(self, device: str) -> object:
        self.pipeline_devices.append(device)
        return object()

    def _to_wav(self, src: Path, dst: Path) -> Path:
        self.wav_destinations.append(dst)
        return dst

    def run(
        self,
        *,
        diarize_speakers: bool = False,
        hints: list[str] | None = None,
        language: str = "en",
    ) -> int:
        spec = {
            "request_id": REQUEST_ID,
            "audio_path": str(self.audio),
            "original_name": ORIGINAL_NAME,
            "language": language,
            "diarize": diarize_speakers,
            "hints": hints,
            "duration_s": 12.0,
        }
        (self.work_dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        return engine.main([str(self.work_dir)])

    @property
    def outcome(self) -> dict[str, Any]:
        return json.loads((self.work_dir / "outcome.json").read_text(encoding="utf-8"))

    @property
    def result(self) -> dict[str, Any]:
        doc = json.loads((self.work_dir / "out" / "result.json").read_text(encoding="utf-8"))
        assert contract.validate_result(doc) is None
        return doc

    @property
    def transcript(self) -> str:
        return (self.work_dir / "out" / "transcript.txt").read_text(encoding="utf-8")


@pytest.fixture
def harness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gpu_present: None,
    pinned_models: Path,
) -> Harness:
    return Harness(tmp_path, monkeypatch)


def use_speaker_scenario(harness: Harness) -> None:
    """Three speaker blocks: SPEAKER_00, a short SPEAKER_01 backchannel, SPEAKER_00."""
    harness.model.segments = [
        make_segment(0.0, 4.0, "Welcome everyone to the lecture."),
        make_segment(4.2, 5.0, "Thanks."),
        make_segment(6.2, 9.2, "Let us begin with the cell."),
    ]
    harness.turns = [
        diarize.Turn(0.0, 4.1, "SPEAKER_00"),
        diarize.Turn(4.1, 6.0, "SPEAKER_01"),
        diarize.Turn(6.0, 10.0, "SPEAKER_00"),
    ]


def test_us1_2_diarized_paragraphs_have_speaker_and_times(harness: Harness) -> None:
    use_speaker_scenario(harness)

    assert harness.run(diarize_speakers=True) == 0

    paragraphs = harness.result["paragraphs"]
    first, second = diarize.speaker_label("SPEAKER_00"), diarize.speaker_label("SPEAKER_01")
    assert [p["speaker"] for p in paragraphs] == [first, second, first]
    assert all(p["start"] <= p["end"] for p in paragraphs)
    assert [p["uncertain"] for p in paragraphs] == [False, True, False]
    assert f"[00:04] {second}(?): Thanks." in harness.transcript
    assert f"[00:00] {first}: Welcome everyone to the lecture." in harness.transcript
    assert "SPEAKER_" not in harness.transcript
    assert harness.result["summary"]["diarized"] is True
    assert harness.outcome == {"status": "done"}


def test_us1_3_plain_paragraphs_have_times_no_speaker(harness: Harness) -> None:
    assert harness.run() == 0

    assert harness.result["paragraphs"] == [
        {"start": 0.0, "end": 6.0, "speaker": None,
         "text": "First sentence. Continuing sentence.", "uncertain": False},
        {"start": 10.0, "end": 12.0, "speaker": None,
         "text": "A new paragraph.", "uncertain": False},
    ]
    header = transcribe.build_header(ORIGINAL_NAME, "large-v3", "en", 12.0, diarized=False)
    body = transcribe.render_txt(
        [(0.0, "First sentence. Continuing sentence."), (10.0, "A new paragraph.")], True
    )
    assert harness.transcript == header + body
    assert harness.result["summary"]["diarized"] is False
    assert harness.outcome == {"status": "done"}


def test_result_identity_and_summary_fields(harness: Harness) -> None:
    assert harness.run() == 0

    result = harness.result
    assert result["request_id"] == REQUEST_ID
    assert result["transcript"] == "transcript.txt"
    assert "paragraph_count" not in result["summary"]
    summary = result["summary"]
    assert (summary["model"], summary["device"], summary["language"]) == ("large-v3", "cuda", "en")
    assert summary["audio_duration_s"] == 12.0
    assert summary["processing_s"] >= 0


def test_us1_4_hints_within_budget_all_applied(harness: Harness) -> None:
    assert harness.run(hints=["alpha", "beta"]) == 0

    assert harness.result["summary"]["hints"] == {"applied": 2, "total": 2, "dropped": []}


def test_us1_5_hints_over_budget_report_dropped_in_order(harness: Harness) -> None:
    hints = ["a" * 100, "b" * 100, "c" * 100, "d" * 100]

    assert harness.run(hints=hints) == 0

    assert harness.result["summary"]["hints"] == {
        "applied": 2, "total": 4, "dropped": hints[2:],
    }


def test_us1_6_no_hints_summary_null(harness: Harness) -> None:
    assert harness.run(hints=None) == 0

    assert harness.result["summary"]["hints"] is None
    assert harness.model.calls[0]["hotwords"] is None


def test_edge_4_short_backchannel_marked_uncertain_not_dropped(harness: Harness) -> None:
    harness.model.segments = [
        make_segment(0.0, 5.0, "Is the sample ready."),
        make_segment(6.0, 6.4, "Yes."),
    ]
    harness.turns = [
        diarize.Turn(0.0, 5.5, "SPEAKER_00"),
        diarize.Turn(5.5, 7.0, "SPEAKER_01"),
    ]

    assert harness.run(diarize_speakers=True) == 0

    backchannel = harness.result["paragraphs"][-1]
    assert backchannel["text"] == "Yes."
    assert backchannel["uncertain"] is True
    assert "(?): Yes." in harness.transcript


def test_edge_5_single_overlong_hint_dropped_job_done(harness: Harness) -> None:
    hint = "x" * 300

    assert harness.run(hints=[hint]) == 0

    assert harness.result["summary"]["hints"] == {"applied": 0, "total": 1, "dropped": [hint]}
    assert not harness.model.calls[0]["hotwords"]
    assert harness.outcome == {"status": "done"}


@pytest.mark.parametrize("diarize_speakers", [False, True])
def test_engine_uses_cli_defaults_without_fallback(
    harness: Harness, diarize_speakers: bool
) -> None:
    use_speaker_scenario(harness)

    assert harness.run(diarize_speakers=diarize_speakers) == 0

    assert harness.load_calls == [("large-v3", "cuda")]
    call = harness.model.calls[0]
    assert call["beam_size"] == 5
    assert call["word_timestamps"] is diarize_speakers
    assert call["language"] == "en"
    assert call["audio"] == str(harness.audio)
    assert harness.pipeline_devices == (["cuda"] if diarize_speakers else [])


def test_engine_paragraph_boundaries_are_two_seconds_and_800_chars(harness: Harness) -> None:
    long_a, long_b = "a" * 399 + ".", "b" * 399 + "."
    harness.model.segments = [
        make_segment(0.0, 1.0, "Start."),
        make_segment(2.9, 4.0, "Gap of one point nine stays."),
        make_segment(6.0, 7.0, "Gap of exactly two splits."),
        make_segment(7.1, 8.0, long_a),
        make_segment(8.1, 9.0, long_b),
        make_segment(9.1, 10.0, "After 800 characters."),
    ]

    assert harness.run() == 0

    assert [p["text"] for p in harness.result["paragraphs"]] == [
        "Start. Gap of one point nine stays.",
        f"Gap of exactly two splits. {long_a} {long_b}",
        "After 800 characters.",
    ]


def test_engine_temp_files_stay_in_work_dir(harness: Harness) -> None:
    use_speaker_scenario(harness)

    assert harness.run(diarize_speakers=True) == 0

    assert harness.wav_destinations
    tmp_dir = harness.work_dir / "tmp"
    assert all(tmp_dir in dst.resolve().parents for dst in harness.wav_destinations)
    assert tempfile.tempdir == str(tmp_dir)


def test_engine_passes_only_applied_hints(harness: Harness) -> None:
    hints = ["a" * 100, "b" * 100, "c" * 100]

    assert harness.run(hints=hints) == 0

    assert harness.model.calls[0]["hotwords"] == ", ".join(hints[:2])


def test_engine_prints_no_transcript_text_or_hints(
    harness: Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert harness.run(hints=["secret-term"]) == 0

    captured = capsys.readouterr()
    for text in ("First sentence", "A new paragraph", "secret-term", ORIGINAL_NAME):
        assert text not in captured.out + captured.err


def assert_failed(harness: Harness, reason: str) -> None:
    outcome = harness.outcome
    assert set(outcome) == {"status", "reason", "detail"}
    assert (outcome["status"], outcome["reason"]) == ("failed", reason)
    assert 0 < len(outcome["detail"]) <= 500
    assert outcome["detail"].isascii()
    assert str(harness.work_dir) not in outcome["detail"]
    assert not (harness.work_dir / "out").exists() or not list((harness.work_dir / "out").iterdir())
    assert harness.load_calls == []
    assert harness.pipeline_devices == []


def test_us5_5_no_gpu_fails_before_loading(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "cuda_device_count", lambda: 0)

    assert harness.run() == 0

    assert_failed(harness, "gpu_unavailable")


def test_us5_5_diarize_without_torch_cuda_fails_before_loading(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "torch_cuda_available", lambda: False)

    assert harness.run(diarize_speakers=True) == 0

    assert_failed(harness, "gpu_unavailable")


def test_torch_cuda_is_not_required_without_diarization(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine, "torch_cuda_available", lambda: False)

    assert harness.run(diarize_speakers=False) == 0

    assert harness.outcome == {"status": "done"}


@pytest.mark.parametrize("damage", ["missing_ref", "other_commit"])
def test_models_missing_or_other_revision_is_models_unavailable(
    harness: Harness, pinned_models: Path, damage: str
) -> None:
    org, name = PINNED[1][0].split("/", 1)
    ref = pinned_models / "hub" / f"models--{org}--{name}" / "refs" / "main"
    if damage == "missing_ref":
        ref.unlink()
    else:
        ref.write_text("0" * 40, encoding="utf-8")

    assert harness.run() == 0

    assert_failed(harness, "models_unavailable")


def test_missing_hf_home_is_an_error_not_a_pass(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HF_HOME")

    with pytest.raises(RuntimeError, match="HF_HOME"):
        harness.run()

    assert harness.load_calls == []


def test_wrong_argument_count_is_a_usage_error() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "stt.server.engine"], capture_output=True, text=True, check=False
    )

    assert done.returncode == 2
    assert "usage" in done.stderr.lower()
