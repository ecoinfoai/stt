"""End-to-end CLI tests with a stubbed Whisper model.

Hugging Face 모델 다운로드 없이 CLI 전체 경로(파일 수집 → 전사 →
문단 복원 → 저장, 폴백 포함)를 검증한다. 실제 모델 추론은 사용자의
GPU 환경에서 첫 실행으로 확인한다.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stt import transcribe


class FakeModel:
    """WhisperModel.transcribe와 같은 계약을 가진 스텁."""

    def __init__(self, fail_oom: bool = False):
        self.fail_oom = fail_oom
        self.calls: list[dict] = []

    def transcribe(self, audio: str, **kwargs):
        self.calls.append({"audio": audio, **kwargs})
        if self.fail_oom:
            raise RuntimeError("CUDA failed with error out of memory")
        segments = iter([
            SimpleNamespace(start=0.0, end=3.0, text=" 첫 문장입니다."),
            SimpleNamespace(start=3.2, end=6.0, text="이어지는 문장."),
            SimpleNamespace(start=10.0, end=12.0, text="새 문단입니다."),
        ])
        info = SimpleNamespace(duration=12.0, language="ko")
        return segments, info


@pytest.fixture()
def media(tmp_path) -> Path:
    """빈 내용의 가짜 미디어 파일(확장자만 유효)."""
    f = tmp_path / "강연.m4a"
    f.touch()
    return f


def test_main_writes_txt_with_header(tmp_path, media, monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: fake
    )
    code = transcribe.main([str(media), "--device", "cpu"])
    assert code == 0
    out = (tmp_path / "강연.txt").read_text(encoding="utf-8")
    assert out.startswith("# 원본: 강연.m4a")
    assert "[00:00] 첫 문장입니다. 이어지는 문장." in out
    assert "[00:10] 새 문단입니다." in out


def test_main_passes_terms_as_hotwords(tmp_path, media, monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: fake
    )
    terms = tmp_path / "terms.txt"
    terms.write_text("기업가정신\n헬스케어\n", encoding="utf-8")
    transcribe.main(
        [str(media), "--device", "cpu", "--terms", str(terms)]
    )
    assert fake.calls[0]["hotwords"] == "기업가정신, 헬스케어"
    assert fake.calls[0]["language"] == "ko"
    assert fake.calls[0]["vad_filter"] is True


def test_main_writes_srt_when_requested(tmp_path, media, monkeypatch):
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: FakeModel()
    )
    transcribe.main([str(media), "--device", "cpu", "--srt"])
    srt = (tmp_path / "강연.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:03,000" in srt


def test_main_skips_existing_output(tmp_path, media, monkeypatch, capsys):
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: FakeModel()
    )
    transcribe.main([str(media), "--device", "cpu"])
    code = transcribe.main([str(media), "--device", "cpu"])
    assert code == 0
    assert "건너뜀" in capsys.readouterr().out


def test_main_writes_meta_yaml(tmp_path, media, monkeypatch):
    """전사문 옆에 노트용 메타데이터 파일이 함께 생긴다."""
    import yaml

    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: FakeModel()
    )
    transcribe.main([str(media), "--device", "cpu", "--model", "small"])
    meta_file = tmp_path / "강연.meta.yaml"
    meta = yaml.safe_load(meta_file.read_text(encoding="utf-8"))
    assert meta["model"] == "faster-whisper small"
    assert meta["language"] == "ko"
    assert meta["duration"] == "00:12"
    assert meta["speaker"] == ""


def test_main_meta_uses_info_json(tmp_path, media, monkeypatch):
    """미디어 옆 info.json이 있으면 URL과 후보값이 채워진다."""
    import json

    import yaml

    (tmp_path / "강연.info.json").write_text(
        json.dumps({
            "id": "abc",
            "title": "홍길동 교수 특강",
            "webpage_url": "https://youtu.be/abc",
            "channel": "테스트채널",
            "upload_date": "20200103",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: FakeModel()
    )
    transcribe.main([str(media), "--device", "cpu"])
    meta = yaml.safe_load(
        (tmp_path / "강연.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["source_url"] == "https://youtu.be/abc"
    assert meta["recorded"] == ""
    assert meta["_meta"]["published"] == "2020-01-03"
    assert "홍길동" in meta["_meta"]["candidates"]["speaker"]


def test_main_no_meta_option(tmp_path, media, monkeypatch):
    """--no-meta를 주면 메타데이터 파일을 만들지 않는다."""
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: FakeModel()
    )
    transcribe.main([str(media), "--device", "cpu", "--no-meta"])
    assert not (tmp_path / "강연.meta.yaml").exists()


def test_fallback_moves_to_smaller_model(tmp_path, media, monkeypatch):
    models = {"big": FakeModel(fail_oom=True), "small": FakeModel()}
    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: models[name]
    )
    args = transcribe.parse_args([str(media), "--device", "cpu"])
    transcribe.run_with_fallback(
        [media], ("big", "small"), "cpu", args, None
    )
    assert (tmp_path / "강연.txt").exists()
    assert len(models["big"].calls) == 1
    assert len(models["small"].calls) == 1


def test_fallback_exhausted_raises(tmp_path, media, monkeypatch):
    monkeypatch.setattr(
        transcribe,
        "load_model",
        lambda name, device: FakeModel(fail_oom=True),
    )
    args = transcribe.parse_args([str(media), "--device", "cpu"])
    with pytest.raises(RuntimeError, match="모델 체인"):
        transcribe.run_with_fallback(
            [media], ("big",), "cpu", args, None
        )


def test_dll_error_gets_guidance(tmp_path, media, monkeypatch):
    class DllModel:
        def transcribe(self, audio: str, **kwargs):
            raise RuntimeError(
                "Library cublas64_12.dll is not found or cannot be loaded"
            )

    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: DllModel()
    )
    args = transcribe.parse_args([str(media), "--device", "cpu"])
    with pytest.raises(RuntimeError, match="INSTALL.md"):
        transcribe.run_with_fallback(
            [media], ("big",), "cpu", args, None
        )


def test_non_oom_runtime_error_propagates(tmp_path, media, monkeypatch):
    class BrokenModel:
        def transcribe(self, audio: str, **kwargs):
            raise RuntimeError("invalid model configuration")

    monkeypatch.setattr(
        transcribe, "load_model", lambda name, device: BrokenModel()
    )
    args = transcribe.parse_args([str(media), "--device", "cpu"])
    with pytest.raises(RuntimeError, match="invalid model"):
        transcribe.run_with_fallback(
            [media], ("big", "small"), "cpu", args, None
        )
