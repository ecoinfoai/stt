"""Tests for stt.enroll pure input handling.

Covers: enrollment map parsing and validation. The pyannote pass that
turns audio into embeddings is covered by an end-to-end smoke run.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from stt.enroll import Entry, parse_args, parse_enroll_map


class TestParseEnrollMap:
    """Tests for parse_enroll_map."""

    def test_parse_enroll_map_reads_entries(self):
        data = [
            {"media": "a.m4a", "speakers": {"화자1": "한석준", "화자2": "차인표"}},
        ]
        entries = parse_enroll_map(data)
        assert entries == [
            Entry(Path("a.m4a"), {"SPEAKER_00": "한석준", "SPEAKER_01": "차인표"})
        ]

    def test_parse_enroll_map_accepts_raw_labels(self):
        data = [{"media": "a.m4a", "speakers": {"SPEAKER_00": "한석준"}}]
        assert parse_enroll_map(data)[0].speakers == {"SPEAKER_00": "한석준"}

    def test_parse_enroll_map_not_a_list_raises(self):
        with pytest.raises(ValueError):
            parse_enroll_map({"media": "a.m4a"})

    def test_parse_enroll_map_missing_media_raises(self):
        with pytest.raises(ValueError):
            parse_enroll_map([{"speakers": {"화자1": "한석준"}}])

    def test_parse_enroll_map_missing_speakers_raises(self):
        with pytest.raises(ValueError):
            parse_enroll_map([{"media": "a.m4a"}])

    def test_parse_enroll_map_empty_speakers_raises(self):
        with pytest.raises(ValueError):
            parse_enroll_map([{"media": "a.m4a", "speakers": {}}])

    def test_parse_enroll_map_blank_name_raises(self):
        with pytest.raises(ValueError):
            parse_enroll_map([{"media": "a.m4a", "speakers": {"화자1": "  "}}])

    def test_parse_enroll_map_empty_list_raises(self):
        with pytest.raises(ValueError):
            parse_enroll_map([])


class TestParseArgs:
    """Tests for parse_args."""

    def test_parse_args_requires_map(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_parse_args_defaults(self):
        args = parse_args(["--map", "enroll.yaml"])
        assert args.db == Path("voices.json")
        assert args.device == "auto"

    def test_parse_args_rejects_cpu_device(self, capsys):
        with pytest.raises(SystemExit) as stopped:
            parse_args(["--map", "enroll.yaml", "--device", "cpu"])
        assert stopped.value.code == 2
        assert "invalid choice" in capsys.readouterr().err


class TestGpuOnlyMain:
    """enroll stops with a reason when no GPU is usable (US4-6, SC-014)."""

    NO_GPU = "No usable GPU was found; stt runs on the GPU only."
    NO_CUDA = (
        "Speaker diarization needs a GPU, but torch cannot use CUDA; "
        "stt runs on the GPU only."
    )

    @staticmethod
    def _arguments(tmp_path: Path) -> list[str]:
        media = tmp_path / "talk.m4a"
        media.touch()
        enroll_map = tmp_path / "enroll.yaml"
        enroll_map.write_text(
            f"- media: {media}\n  speakers:\n    SPEAKER_00: Alice\n",
            encoding="utf-8",
        )
        return ["--map", str(enroll_map), "--db", str(tmp_path / "voices.json")]

    @staticmethod
    def _ctranslate2_gpus(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
        import ctranslate2

        monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: count)

    @pytest.mark.parametrize("device", ["auto", "cuda"])
    def test_us4_5_main_without_cuda_device_stops(
        self, tmp_path, monkeypatch, capsys, device
    ):
        from stt import diarize, enroll

        self._ctranslate2_gpus(monkeypatch, 0)
        monkeypatch.setattr(
            diarize, "load_pipeline", lambda dev: pytest.fail("pipeline loaded")
        )
        with pytest.raises(SystemExit) as stopped:
            enroll.main([*self._arguments(tmp_path), "--device", device])
        assert stopped.value.code == 1
        assert capsys.readouterr().err.strip() == self.NO_GPU
        assert not (tmp_path / "voices.json").exists()

    @pytest.mark.parametrize("device", ["auto", "cuda"])
    def test_us4_6_main_with_torch_without_cuda_stops(
        self, tmp_path, monkeypatch, capsys, device
    ):
        import sys
        import types
        from types import SimpleNamespace

        from stt import diarize, enroll

        self._ctranslate2_gpus(monkeypatch, 1)
        monkeypatch.delenv("STT_GPU_LOCK", raising=False)
        monkeypatch.setenv("HF_HUB_OFFLINE", "1")
        torch = types.ModuleType("torch")
        torch.cuda = SimpleNamespace(is_available=lambda: False)
        audio = types.ModuleType("pyannote.audio")
        audio.Pipeline = SimpleNamespace(
            from_pretrained=lambda *a, **k: pytest.fail("pipeline built")
        )
        monkeypatch.setitem(sys.modules, "torch", torch)
        monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
        monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
        monkeypatch.setattr(
            diarize, "diarize_embeddings",
            lambda *a, **k: pytest.fail("diarized"),
        )
        with pytest.raises(SystemExit) as stopped:
            enroll.main([*self._arguments(tmp_path), "--device", device])
        assert stopped.value.code == 1
        assert capsys.readouterr().err.strip() == self.NO_CUDA
        assert not (tmp_path / "voices.json").exists()
