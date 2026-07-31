"""Tests for transcribe.py pure post-processing functions.

Covers: timestamp formatting, media discovery, terms loading,
paragraph assembly, and TXT/SRT rendering. Model inference is
covered separately by an end-to-end smoke run.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe import (
    build_header,
    build_paragraphs,
    discover_media,
    format_timestamp,
    load_terms,
    prepare_dll_search_path,
    render_srt,
    render_txt,
    srt_timestamp,
)


class TestPrepareDllSearchPath:
    """Tests for prepare_dll_search_path."""

    def test_prepare_dll_search_path_safe_on_any_os(self):
        """어느 OS에서 불러도 예외 없이 동작해야 한다."""
        prepare_dll_search_path()


def seg(start: float, end: float, text: str) -> SimpleNamespace:
    """Build a minimal segment stand-in with start/end/text."""
    return SimpleNamespace(start=start, end=end, text=text)


class TestFormatTimestamp:
    """Tests for format_timestamp."""

    def test_format_timestamp_zero(self):
        assert format_timestamp(0.0) == "00:00"

    def test_format_timestamp_minutes(self):
        assert format_timestamp(75.4) == "01:15"

    def test_format_timestamp_hours(self):
        assert format_timestamp(3725.0) == "1:02:05"

    def test_format_timestamp_negative_raises(self):
        with pytest.raises(ValueError):
            format_timestamp(-1.0)


class TestSrtTimestamp:
    """Tests for srt_timestamp."""

    def test_srt_timestamp_basic(self):
        assert srt_timestamp(75.5) == "00:01:15,500"

    def test_srt_timestamp_hours(self):
        assert srt_timestamp(3725.042) == "01:02:05,042"


class TestDiscoverMedia:
    """Tests for discover_media."""

    def test_discover_media_folder_filters_and_sorts(self, tmp_path):
        (tmp_path / "b.m4a").touch()
        (tmp_path / "a.mp4").touch()
        (tmp_path / "notes.txt").touch()
        found = discover_media([tmp_path])
        assert [p.name for p in found] == ["a.mp4", "b.m4a"]

    def test_discover_media_single_file(self, tmp_path):
        f = tmp_path / "talk.mp3"
        f.touch()
        assert discover_media([f]) == [f]

    def test_discover_media_missing_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            discover_media([tmp_path / "nope.mp4"])

    def test_discover_media_unsupported_file_raises(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.touch()
        with pytest.raises(ValueError):
            discover_media([f])

    def test_discover_media_empty_folder_raises(self, tmp_path):
        with pytest.raises(ValueError):
            discover_media([tmp_path])


class TestLoadTerms:
    """Tests for load_terms."""

    def test_load_terms_skips_comments_and_blanks(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text(
            "기업가정신\n# 주석입니다\n\n비즈니스 모델 캔버스\n",
            encoding="utf-8",
        )
        assert load_terms(f) == "기업가정신, 비즈니스 모델 캔버스"

    def test_load_terms_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_terms(tmp_path / "none.txt")

    def test_load_terms_no_effective_terms_raises(self, tmp_path):
        f = tmp_path / "terms.txt"
        f.write_text("# 전부 주석\n\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_terms(f)


class TestBuildParagraphs:
    """Tests for build_paragraphs."""

    def test_build_paragraphs_splits_on_gap(self):
        segments = [
            seg(0.0, 2.0, " 안녕하세요."),
            seg(5.0, 7.0, "반갑습니다. "),
        ]
        paras = build_paragraphs(segments, gap_s=2.0, max_chars=800)
        assert len(paras) == 2
        assert paras[0] == (0.0, "안녕하세요.")
        assert paras[1] == (5.0, "반갑습니다.")

    def test_build_paragraphs_merges_short_gap(self):
        segments = [
            seg(0.0, 2.0, "안녕하세요."),
            seg(2.5, 4.0, "반갑습니다."),
        ]
        paras = build_paragraphs(segments, gap_s=2.0, max_chars=800)
        assert paras == [(0.0, "안녕하세요. 반갑습니다.")]

    def test_build_paragraphs_splits_on_length_at_sentence_end(self):
        long_text = "가" * 799 + "."
        segments = [
            seg(0.0, 30.0, long_text),
            seg(30.5, 40.0, "다음 문장입니다."),
        ]
        paras = build_paragraphs(segments, gap_s=2.0, max_chars=800)
        assert len(paras) == 2
        assert paras[1] == (30.5, "다음 문장입니다.")

    def test_build_paragraphs_keeps_long_run_without_sentence_end(self):
        segments = [
            seg(0.0, 30.0, "가" * 900),
            seg(30.5, 40.0, "이어지는 말"),
        ]
        paras = build_paragraphs(segments, gap_s=2.0, max_chars=800)
        assert len(paras) == 1

    def test_build_paragraphs_skips_empty_text(self):
        segments = [seg(0.0, 1.0, "  "), seg(1.2, 2.0, "본문.")]
        paras = build_paragraphs(segments, gap_s=2.0, max_chars=800)
        assert paras == [(1.2, "본문.")]

    def test_build_paragraphs_empty_input(self):
        assert build_paragraphs([], gap_s=2.0, max_chars=800) == []

    def test_build_paragraphs_invalid_params_raise(self):
        with pytest.raises(ValueError):
            build_paragraphs([], gap_s=0.0, max_chars=800)
        with pytest.raises(ValueError):
            build_paragraphs([], gap_s=2.0, max_chars=0)


class TestRenderTxt:
    """Tests for render_txt."""

    def test_render_txt_with_timestamps(self):
        paras = [(0.0, "첫 문단."), (75.0, "둘째 문단.")]
        out = render_txt(paras, timestamps=True)
        assert out == "[00:00] 첫 문단.\n\n[01:15] 둘째 문단.\n"

    def test_render_txt_plain(self):
        paras = [(0.0, "첫 문단."), (75.0, "둘째 문단.")]
        out = render_txt(paras, timestamps=False)
        assert out == "첫 문단.\n\n둘째 문단.\n"

    def test_render_txt_empty_raises(self):
        with pytest.raises(ValueError):
            render_txt([], timestamps=True)


class TestBuildHeader:
    """Tests for build_header."""

    def test_build_header_contains_metadata(self):
        head = build_header(
            source_name="강연.m4a",
            model_name="large-v3",
            language="ko",
            duration_s=930.0,
        )
        assert "강연.m4a" in head
        assert "large-v3" in head
        assert "ko" in head
        assert "15:30" in head
        for line in head.strip().splitlines():
            assert line.startswith("#")


class TestRenderSrt:
    """Tests for render_srt."""

    def test_render_srt_blocks(self):
        segments = [seg(0.0, 2.5, " 안녕하세요."), seg(3.0, 4.0, "네.")]
        out = render_srt(segments)
        expected = (
            "1\n00:00:00,000 --> 00:00:02,500\n안녕하세요.\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\n네.\n"
        )
        assert out == expected

    def test_render_srt_empty_raises(self):
        with pytest.raises(ValueError):
            render_srt([])
