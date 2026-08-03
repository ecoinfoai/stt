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
