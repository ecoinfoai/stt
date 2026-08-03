"""Tests for stt.diarize pure post-processing functions.

Covers: speaker lookup by time overlap, sentence grouping, block
assembly with speaker labels, and diarized TXT rendering. The
pyannote pipeline itself is covered by an end-to-end smoke run.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stt.diarize import (
    Block,
    Turn,
    assign_blocks,
    cosine_similarity,
    ffmpeg_wav_command,
    group_sentences,
    label_for_display,
    merge_voice,
    nearest_speaker,
    rename_turns,
    render_diarized,
    resolve_speaker_names,
    speaker_at,
    speaker_label,
)


def word(start: float, end: float, text: str) -> SimpleNamespace:
    """Build a minimal word stand-in with start/end/word."""
    return SimpleNamespace(start=start, end=end, word=text)


class TestSpeakerAt:
    """Tests for speaker_at."""

    def test_speaker_at_picks_largest_overlap(self):
        turns = [Turn(0.0, 5.0, "SPEAKER_00"), Turn(4.0, 9.0, "SPEAKER_01")]
        assert speaker_at(3.0, 6.0, turns) == "SPEAKER_00"

    def test_speaker_at_prefers_later_turn_when_it_dominates(self):
        turns = [Turn(0.0, 5.0, "SPEAKER_00"), Turn(4.0, 9.0, "SPEAKER_01")]
        assert speaker_at(4.5, 8.0, turns) == "SPEAKER_01"

    def test_speaker_at_returns_none_without_overlap(self):
        turns = [Turn(0.0, 1.0, "SPEAKER_00")]
        assert speaker_at(5.0, 6.0, turns) is None

    def test_speaker_at_empty_turns_returns_none(self):
        assert speaker_at(0.0, 1.0, []) is None


class TestNearestSpeaker:
    """Tests for nearest_speaker."""

    def test_nearest_speaker_picks_closest_turn(self):
        turns = [Turn(0.0, 1.0, "SPEAKER_00"), Turn(10.0, 11.0, "SPEAKER_01")]
        assert nearest_speaker(9.0, turns) == "SPEAKER_01"

    def test_nearest_speaker_empty_turns_raises(self):
        with pytest.raises(ValueError):
            nearest_speaker(0.0, [])


class TestGroupSentences:
    """Tests for group_sentences."""

    def test_group_sentences_splits_on_terminator(self):
        words = [word(0, 1, "안녕. "), word(1, 2, "반가워요. ")]
        assert [len(g) for g in group_sentences(words)] == [1, 1]

    def test_group_sentences_keeps_trailing_fragment(self):
        words = [word(0, 1, "안녕. "), word(1, 2, "그런데 ")]
        assert [len(g) for g in group_sentences(words)] == [1, 1]

    def test_group_sentences_groups_until_terminator(self):
        words = [word(0, 1, "오늘 "), word(1, 2, "날씨가 "), word(2, 3, "좋다. ")]
        assert [len(g) for g in group_sentences(words)] == [3]

    def test_group_sentences_empty(self):
        assert group_sentences([]) == []


class TestAssignBlocks:
    """Tests for assign_blocks."""

    def test_assign_blocks_keeps_sentence_whole_across_boundary(self):
        """문장이 화자 경계에 걸쳐도 다수 화자로 통째 배정돼야 한다."""
        turns = [Turn(0.0, 1.2, "SPEAKER_01"), Turn(1.2, 10.0, "SPEAKER_00")]
        words = [word(0.0, 1.0, "저는 "), word(1.0, 2.0, "그럼 "),
                 word(2.0, 6.0, "여쭤보고 싶은데요. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0)
        assert len(blocks) == 1
        assert blocks[0].speaker == "SPEAKER_00"
        assert blocks[0].text == "저는 그럼 여쭤보고 싶은데요."

    def test_assign_blocks_merges_consecutive_same_speaker(self):
        turns = [Turn(0.0, 20.0, "SPEAKER_00")]
        words = [word(0, 2, "하나. "), word(2, 4, "둘. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0)
        assert len(blocks) == 1
        assert blocks[0].text == "하나. 둘."

    def test_assign_blocks_splits_on_speaker_change(self):
        turns = [Turn(0.0, 3.0, "SPEAKER_00"), Turn(3.0, 8.0, "SPEAKER_01")]
        words = [word(0, 2, "질문입니다. "), word(4, 7, "답변입니다. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0)
        assert [b.speaker for b in blocks] == ["SPEAKER_00", "SPEAKER_01"]

    def test_assign_blocks_marks_short_block_uncertain(self):
        turns = [Turn(0.0, 3.0, "SPEAKER_00"), Turn(3.0, 9.0, "SPEAKER_01")]
        words = [word(0.0, 0.4, "네. "), word(4.0, 8.0, "그러니까 말이죠. ")]
        blocks = assign_blocks(words, turns, min_seconds=1.0)
        assert blocks[0].uncertain is True
        assert blocks[1].uncertain is False

    def test_assign_blocks_falls_back_to_nearest_speaker(self):
        """겹치는 화자 턴이 없는 단어도 미배정으로 남기지 않는다."""
        turns = [Turn(10.0, 12.0, "SPEAKER_00")]
        words = [word(0.0, 1.0, "앞부분입니다. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0)
        assert blocks[0].speaker == "SPEAKER_00"

    def test_assign_blocks_empty_words_returns_empty(self):
        assert assign_blocks([], [Turn(0.0, 1.0, "SPEAKER_00")], 1.0) == []

    def test_assign_blocks_negative_min_seconds_raises(self):
        with pytest.raises(ValueError):
            assign_blocks([], [], -1.0)


class TestSpeakerLabel:
    """Tests for speaker_label."""

    def test_speaker_label_maps_index_to_korean(self):
        assert speaker_label("SPEAKER_00") == "화자1"
        assert speaker_label("SPEAKER_02") == "화자3"

    def test_speaker_label_passes_through_unknown_form(self):
        assert speaker_label("게스트") == "게스트"


class TestRenderDiarized:
    """Tests for render_diarized."""

    def test_render_diarized_writes_timestamp_and_speaker(self):
        blocks = [Block(0.0, 4.0, "SPEAKER_00", "안녕하세요.", False)]
        assert render_diarized(blocks, timestamps=True) == "[00:00] 화자1: 안녕하세요.\n"

    def test_render_diarized_marks_uncertain_block(self):
        blocks = [Block(75.0, 75.4, "SPEAKER_01", "네.", True)]
        out = render_diarized(blocks, timestamps=True)
        assert out == "[01:15] 화자2(?): 네.\n"

    def test_render_diarized_without_timestamps(self):
        blocks = [Block(0.0, 4.0, "SPEAKER_00", "안녕하세요.", False)]
        assert render_diarized(blocks, timestamps=False) == "화자1: 안녕하세요.\n"

    def test_render_diarized_separates_blocks_by_blank_line(self):
        blocks = [
            Block(0.0, 2.0, "SPEAKER_00", "하나.", False),
            Block(2.0, 4.0, "SPEAKER_01", "둘.", False),
        ]
        out = render_diarized(blocks, timestamps=True)
        assert out == "[00:00] 화자1: 하나.\n\n[00:02] 화자2: 둘.\n"

    def test_render_diarized_empty_raises(self):
        with pytest.raises(ValueError):
            render_diarized([], timestamps=True)


class TestFfmpegWavCommand:
    """Tests for ffmpeg_wav_command."""

    def test_ffmpeg_wav_command_is_argument_list(self):
        """셸을 거치지 않도록 인자 목록으로 만들어야 한다."""
        cmd = ffmpeg_wav_command(Path("a b.m4a"), Path("out.wav"))
        assert isinstance(cmd, list)
        assert all(isinstance(part, str) for part in cmd)

    def test_ffmpeg_wav_command_forces_mono_16k(self):
        cmd = ffmpeg_wav_command(Path("a.m4a"), Path("out.wav"))
        assert cmd[cmd.index("-ac") + 1] == "1"
        assert cmd[cmd.index("-ar") + 1] == "16000"

    def test_ffmpeg_wav_command_passes_paths_unquoted(self):
        """공백이 든 경로를 따옴표로 감싸지 않고 그대로 넘겨야 한다."""
        cmd = ffmpeg_wav_command(Path("a b.m4a"), Path("o u.wav"))
        assert "a b.m4a" in cmd
        assert "o u.wav" in cmd

    def test_ffmpeg_wav_command_overwrites_and_is_non_interactive(self):
        cmd = ffmpeg_wav_command(Path("a.m4a"), Path("out.wav"))
        assert "-y" in cmd
        assert "-nostdin" in cmd


class TestAssignBlocksParagraphing:
    """같은 화자 안에서도 문단을 나누는 규칙."""

    def test_assign_blocks_splits_same_speaker_on_long_pause(self):
        turns = [Turn(0.0, 60.0, "SPEAKER_00")]
        words = [word(0.0, 2.0, "앞 문단입니다. "), word(9.0, 11.0, "뒤 문단입니다. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0, gap_s=2.0)
        assert len(blocks) == 2

    def test_assign_blocks_keeps_same_speaker_within_short_pause(self):
        turns = [Turn(0.0, 60.0, "SPEAKER_00")]
        words = [word(0.0, 2.0, "앞 문장입니다. "), word(2.5, 4.0, "뒤 문장입니다. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0, gap_s=2.0)
        assert len(blocks) == 1

    def test_assign_blocks_splits_same_speaker_on_max_chars(self):
        turns = [Turn(0.0, 60.0, "SPEAKER_00")]
        long = "가" * 30 + ". "
        words = [word(float(i), float(i) + 0.5, long) for i in range(4)]
        blocks = assign_blocks(
            words, turns, min_seconds=0.0, gap_s=10.0, max_chars=50
        )
        assert len(blocks) > 1

    def test_assign_blocks_speaker_change_splits_regardless_of_gap(self):
        turns = [Turn(0.0, 3.0, "SPEAKER_00"), Turn(3.0, 8.0, "SPEAKER_01")]
        words = [word(0.0, 2.0, "질문입니다. "), word(2.2, 7.0, "답변입니다. ")]
        blocks = assign_blocks(words, turns, min_seconds=0.0, gap_s=10.0)
        assert [b.speaker for b in blocks] == ["SPEAKER_00", "SPEAKER_01"]

    def test_assign_blocks_invalid_gap_raises(self):
        with pytest.raises(ValueError):
            assign_blocks([], [], 0.0, gap_s=0.0)

    def test_assign_blocks_invalid_max_chars_raises(self):
        with pytest.raises(ValueError):
            assign_blocks([], [], 0.0, max_chars=0)


class TestCosineSimilarity:
    """Tests for cosine_similarity."""

    def test_cosine_similarity_identical_is_one(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal_is_zero(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_cosine_similarity_ignores_magnitude(self):
        assert cosine_similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)

    def test_cosine_similarity_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            cosine_similarity([1.0], [1.0, 2.0])

    def test_cosine_similarity_zero_vector_raises(self):
        with pytest.raises(ValueError):
            cosine_similarity([0.0, 0.0], [1.0, 0.0])


class TestResolveSpeakerNames:
    """Tests for resolve_speaker_names."""

    def test_resolve_speaker_names_maps_above_threshold(self):
        db = {"한석준": [1.0, 0.0], "차인표": [0.0, 1.0]}
        names = resolve_speaker_names(
            ["SPEAKER_00", "SPEAKER_01"], [[0.9, 0.1], [0.1, 0.9]], db, 0.8
        )
        assert names == {"SPEAKER_00": "한석준", "SPEAKER_01": "차인표"}

    def test_resolve_speaker_names_keeps_raw_below_threshold(self):
        db = {"한석준": [1.0, 0.0]}
        names = resolve_speaker_names(["SPEAKER_00"], [[0.0, 1.0]], db, 0.8)
        assert names == {"SPEAKER_00": "SPEAKER_00"}

    def test_resolve_speaker_names_assigns_each_name_once(self):
        """같은 사람에게 두 화자가 붙지 않아야 한다."""
        db = {"한석준": [1.0, 0.0]}
        names = resolve_speaker_names(
            ["SPEAKER_00", "SPEAKER_01"], [[1.0, 0.0], [0.99, 0.01]], db, 0.8
        )
        assert names["SPEAKER_00"] == "한석준"
        assert names["SPEAKER_01"] == "SPEAKER_01"

    def test_resolve_speaker_names_empty_db_keeps_raw(self):
        names = resolve_speaker_names(["SPEAKER_00"], [[1.0, 0.0]], {}, 0.8)
        assert names == {"SPEAKER_00": "SPEAKER_00"}

    def test_resolve_speaker_names_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            resolve_speaker_names(["SPEAKER_00"], [], {"a": [1.0]}, 0.8)


class TestRenameTurns:
    """Tests for rename_turns."""

    def test_rename_turns_applies_mapping(self):
        turns = [Turn(0.0, 1.0, "SPEAKER_00"), Turn(1.0, 2.0, "SPEAKER_01")]
        out = rename_turns(turns, {"SPEAKER_00": "한석준"})
        assert [t.speaker for t in out] == ["한석준", "SPEAKER_01"]

    def test_rename_turns_keeps_times(self):
        turns = [Turn(3.0, 4.5, "SPEAKER_00")]
        out = rename_turns(turns, {"SPEAKER_00": "차인표"})
        assert (out[0].start, out[0].end) == (3.0, 4.5)


class TestLabelForDisplay:
    """Tests for label_for_display."""

    def test_label_for_display_inverts_speaker_label(self):
        assert label_for_display("화자1") == "SPEAKER_00"
        assert label_for_display("화자3") == "SPEAKER_02"

    def test_label_for_display_passes_through_raw_label(self):
        assert label_for_display("SPEAKER_00") == "SPEAKER_00"

    def test_label_for_display_rejects_zero(self):
        with pytest.raises(ValueError):
            label_for_display("화자0")


class TestMergeVoice:
    """Tests for merge_voice."""

    def test_merge_voice_adds_new_name(self):
        db = merge_voice({}, "한석준", [1.0, 0.0])
        assert db == {"한석준": [1.0, 0.0]}

    def test_merge_voice_averages_existing_name(self):
        db = merge_voice({"한석준": [1.0, 0.0]}, "한석준", [0.0, 1.0])
        assert db["한석준"] == pytest.approx([0.5, 0.5])

    def test_merge_voice_does_not_mutate_input(self):
        original = {"한석준": [1.0, 0.0]}
        merge_voice(original, "한석준", [0.0, 1.0])
        assert original == {"한석준": [1.0, 0.0]}

    def test_merge_voice_dimension_mismatch_raises(self):
        with pytest.raises(ValueError):
            merge_voice({"한석준": [1.0, 0.0]}, "한석준", [1.0])
