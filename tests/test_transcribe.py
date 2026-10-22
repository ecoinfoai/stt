"""Tests for stt.transcribe pure post-processing functions.

Covers: timestamp formatting, media discovery, terms loading,
paragraph assembly, and TXT/SRT rendering. Model inference is
covered separately by an end-to-end smoke run.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stt.transcribe import (
    HOTWORD_TOKEN_LIMIT,
    TermsReport,
    build_header,
    build_paragraphs,
    build_terms_report,
    count_applied_terms,
    detect_device,
    discover_media,
    format_timestamp,
    load_term_list,
    load_terms,
    parse_args,
    prepare_dll_search_path,
    render_srt,
    render_txt,
    report_terms_budget,
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


class TestParseArgsDiarize:
    """Tests for the --diarize flag."""

    def test_parse_args_diarize_off_by_default(self):
        assert parse_args(["a.mp4"]).diarize is False

    def test_parse_args_diarize_on_with_flag(self):
        assert parse_args(["a.mp4", "--diarize"]).diarize is True

    def test_parse_args_speakers_none_by_default(self):
        assert parse_args(["a.mp4"]).speakers is None

    def test_parse_args_speakers_accepts_count(self):
        assert parse_args(["a.mp4", "--speakers", "3"]).speakers == 3

    def test_parse_args_min_speaker_seconds_default(self):
        assert parse_args(["a.mp4"]).min_speaker_seconds == pytest.approx(1.5)


class TestBuildHeaderDiarized:
    """Tests for the diarization note in build_header."""

    def test_build_header_omits_note_by_default(self):
        head = build_header("a.m4a", "large-v3", "ko", 60.0)
        assert "화자분리" not in head

    def test_build_header_records_diarization(self):
        head = build_header("a.m4a", "large-v3", "ko", 60.0, diarized=True)
        assert "화자분리" in head
        for line in head.strip().splitlines():
            assert line.startswith("#")


class TestParseArgsVoiceDb:
    """Tests for the --voice-db flag."""

    def test_parse_args_voice_db_none_by_default(self):
        assert parse_args(["a.mp4"]).voice_db is None

    def test_parse_args_voice_db_accepts_path(self):
        from pathlib import Path as _P
        assert parse_args(["a.mp4", "--voice-db", "v.json"]).voice_db == _P("v.json")

    def test_parse_args_voice_threshold_default(self):
        assert parse_args(["a.mp4"]).voice_threshold == pytest.approx(0.75)


NO_GPU_MESSAGE = "No usable GPU was found; stt runs on the GPU only."


def _gpu_count(monkeypatch, count: int) -> None:
    import ctranslate2

    monkeypatch.setattr(ctranslate2, "get_cuda_device_count", lambda: count)


class TestDetectDevice:
    """Tests for detect_device, which only ever returns the GPU."""

    @pytest.mark.parametrize("requested", ["auto", "cuda"])
    def test_detect_device_with_gpu_returns_cuda_silently(
        self, monkeypatch, capsys, requested
    ):
        _gpu_count(monkeypatch, 1)
        assert detect_device(requested) == "cuda"
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("requested", ["auto", "cuda"])
    def test_detect_device_without_gpu_stops_with_reason(
        self, monkeypatch, capsys, requested
    ):
        _gpu_count(monkeypatch, 0)
        with pytest.raises(RuntimeError) as stopped:
            detect_device(requested)
        assert str(stopped.value) == NO_GPU_MESSAGE
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("requested", ["cpu", "tpu"])
    def test_detect_device_rejects_other_values(self, monkeypatch, requested):
        _gpu_count(monkeypatch, 1)
        with pytest.raises(ValueError):
            detect_device(requested)


class TestGpuOnlyCommandLine:
    """The command line never runs on the CPU (US4-5, US4-6, SC-014)."""

    @staticmethod
    def _media(tmp_path):
        media = tmp_path / "talk.m4a"
        media.touch()
        return media

    def test_parse_args_rejects_cpu_device(self, capsys):
        with pytest.raises(SystemExit) as stopped:
            parse_args(["a.mp4", "--device", "cpu"])
        assert stopped.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    @pytest.mark.parametrize("device", ["auto", "cuda"])
    def test_parse_args_accepts_gpu_devices(self, device):
        assert parse_args(["a.mp4", "--device", device]).device == device

    @pytest.mark.parametrize("device", ["auto", "cuda"])
    def test_us4_5_main_without_gpu_stops_before_loading_a_model(
        self, tmp_path, monkeypatch, capsys, device
    ):
        from stt import transcribe

        _gpu_count(monkeypatch, 0)
        loaded: list[str] = []
        monkeypatch.setattr(
            transcribe, "load_model", lambda name, dev: loaded.append(name)
        )
        with pytest.raises(SystemExit) as stopped:
            transcribe.main([str(self._media(tmp_path)), "--device", device])
        assert stopped.value.code == 1
        captured = capsys.readouterr()
        assert captured.err.strip() == NO_GPU_MESSAGE
        assert "장치:" not in captured.out
        assert loaded == []
        assert not (tmp_path / "talk.txt").exists()

    def test_us4_6_main_diarize_with_torch_without_cuda_stops(
        self, tmp_path, monkeypatch, capsys
    ):
        import sys
        import types

        from stt import transcribe

        _gpu_count(monkeypatch, 1)
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
            transcribe, "load_model", lambda name, dev: pytest.fail("model loaded")
        )
        with pytest.raises(SystemExit) as stopped:
            transcribe.main([str(self._media(tmp_path)), "--diarize"])
        assert stopped.value.code == 1
        assert capsys.readouterr().err.strip() == (
            "Speaker diarization needs a GPU, but torch cannot use CUDA; "
            "stt runs on the GPU only."
        )

    def test_us4_5_main_prints_no_cpu_warning_or_cpu_hint(
        self, tmp_path, monkeypatch, capsys
    ):
        from stt import transcribe

        _gpu_count(monkeypatch, 0)
        with pytest.raises(SystemExit):
            transcribe.main([str(self._media(tmp_path))])
        captured = capsys.readouterr()
        assert "CPU" not in captured.err + captured.out
        assert "--device cpu" not in captured.err + captured.out

    def test_auto_model_chain_is_the_gpu_chain(self):
        from stt import transcribe

        assert transcribe.resolve_model_chain("auto") == transcribe.GPU_MODEL_CHAIN
        assert transcribe.resolve_model_chain("medium") == ("medium",)
        assert not hasattr(transcribe, "CPU_DEFAULT_MODEL")

    def test_out_of_memory_hint_does_not_suggest_the_cpu(
        self, tmp_path, monkeypatch
    ):
        from stt import transcribe
        from tests.test_cli_integration import FakeModel

        monkeypatch.setattr(
            transcribe, "load_model",
            lambda name, device: FakeModel(fail_oom=True),
        )
        media = self._media(tmp_path)
        args = transcribe.parse_args([str(media), "--device", "cuda"])
        with pytest.raises(RuntimeError) as stopped:
            transcribe.run_with_fallback(
                [media], ("big", "small"), "cuda", args, None
            )
        assert "--device cpu" not in str(stopped.value)
        assert "--model small" in str(stopped.value)

    def test_dll_guidance_does_not_suggest_the_cpu(self):
        from stt import transcribe

        assert "--device cpu" not in transcribe.dll_guidance(RuntimeError("cublas"))
        assert "INSTALL.md" in transcribe.dll_guidance(RuntimeError("cublas"))


def char_encode(text: str) -> list[int]:
    """Fake encoder: one token per character."""
    return [ord(ch) for ch in text]


class TestCountAppliedTerms:
    """Tests for count_applied_terms (hotword token budget)."""

    def test_limit_matches_faster_whisper_rule(self):
        assert HOTWORD_TOKEN_LIMIT == 448 // 2 - 1 == 223

    def test_count_applied_terms_no_overflow(self):
        terms = ["ab", "cd", "ef"]  # " ab, cd, ef" = 11 tokens
        assert count_applied_terms(terms, char_encode, limit=11) == 3

    def test_count_applied_terms_overflow_at_term_boundary(self):
        terms = ["ab", "cd", "ef"]  # " ab, cd" = 7 tokens exactly
        assert count_applied_terms(terms, char_encode, limit=7) == 2

    def test_count_applied_terms_overflow_inside_term_drops_it(self):
        terms = ["ab", "cd", "ef"]  # limit cuts " ab, cd, e"
        assert count_applied_terms(terms, char_encode, limit=9) == 2

    def test_count_applied_terms_empty_list(self):
        assert count_applied_terms([], char_encode, limit=5) == 0

    def test_count_applied_terms_single_huge_term(self):
        assert count_applied_terms(["a" * 300], char_encode) == 0

    def test_count_applied_terms_encodes_like_faster_whisper(self):
        seen: list[str] = []

        def spy(text: str) -> list[int]:
            seen.append(text)
            return char_encode(text)

        count_applied_terms(["ab", "cd"], spy, limit=99)
        assert seen == [" ab", " ab, cd"]


class TestBuildTermsReport:
    """Tests for build_terms_report."""

    def test_build_terms_report_lists_dropped_tail(self):
        report = build_terms_report(
            "t.txt", ["ab", "cd", "ef", "gh"], char_encode, limit=7
        )
        assert report == TermsReport(
            name="t.txt", applied=2, total=4, dropped=("ef", "gh")
        )

    def test_build_terms_report_nothing_dropped(self):
        report = build_terms_report("t.txt", ["ab"], char_encode, limit=9)
        assert report.applied == report.total == 1
        assert report.dropped == ()


class TestLoadTermList:
    """Tests for load_term_list."""

    def test_load_term_list_keeps_order_and_skips_comments(self, tmp_path):
        f = tmp_path / "t.txt"
        f.write_text("# c\n기업가정신\n\n  헬스케어  \n", encoding="utf-8")
        assert load_term_list(f) == ["기업가정신", "헬스케어"]

    def test_load_term_list_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_term_list(tmp_path / "none.txt")


class TestReportTermsBudget:
    """Tests for report_terms_budget output."""

    def test_report_prints_counts_on_stdout(self, capsys):
        report_terms_budget(TermsReport("t.txt", 3, 3, ()))
        captured = capsys.readouterr()
        assert "t.txt" in captured.out
        assert "반영 3개 / 전체 3개" in captured.out
        assert captured.err == ""

    def test_report_warns_with_dropped_terms_on_stderr(self, capsys):
        report_terms_budget(TermsReport("t.txt", 1, 3, ("cd", "ef")))
        captured = capsys.readouterr()
        assert "반영 1개 / 전체 3개" in captured.out
        assert "[경고]" in captured.err
        assert "cd" in captured.err and "ef" in captured.err


REAL_TOKENIZER_FILES = sorted(
    (Path.home() / ".cache/huggingface/hub"
     / "models--Systran--faster-whisper-large-v3").glob(
        "snapshots/*/tokenizer.json"
    )
)


@pytest.mark.skipif(
    not REAL_TOKENIZER_FILES,
    reason="large-v3 tokenizer.json is not in the Hugging Face cache",
)
class TestRealTokenizerBudget:
    """Acceptance fact measured on 2026-10-15 with the repo's terms.txt."""

    def test_repo_terms_file_budget_with_large_v3_tokenizer(self):
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(str(REAL_TOKENIZER_FILES[-1]))

        def encode(text: str) -> list[int]:
            return tokenizer.encode(text, add_special_tokens=False).ids

        terms_file = Path(__file__).parent.parent / "terms.txt"
        report = build_terms_report(
            terms_file.name, load_term_list(terms_file), encode
        )
        assert report.total == 137
        assert report.applied == 37
        assert report.dropped[0] == "지식인사이드"
        assert len(report.dropped) == 100


from stt import transcribe  # noqa: E402


def test_build_timed_paragraphs_keeps_end_time():
    """A paragraph's end time is the end of its last segment."""
    segments = [
        seg(0.0, 3.0, "First sentence."),
        seg(3.2, 6.0, "Second sentence."),
        seg(10.0, 12.5, "New paragraph."),
    ]
    paras = transcribe.build_timed_paragraphs(segments, gap_s=2.0, max_chars=800)
    assert paras == [
        (0.0, 6.0, "First sentence. Second sentence."),
        (10.0, 12.5, "New paragraph."),
    ]


def test_build_timed_paragraphs_end_ignores_trailing_empty_segment():
    """An empty-text segment after the last text does not extend the end."""
    segments = [seg(0.0, 2.0, "Spoken."), seg(2.1, 9.0, "   ")]
    paras = transcribe.build_timed_paragraphs(segments, gap_s=2.0, max_chars=800)
    assert paras == [(0.0, 2.0, "Spoken.")]


def test_build_timed_paragraphs_invalid_params_raise():
    with pytest.raises(ValueError):
        transcribe.build_timed_paragraphs([], gap_s=0.0, max_chars=800)
    with pytest.raises(ValueError):
        transcribe.build_timed_paragraphs([], gap_s=2.0, max_chars=0)


def test_build_paragraphs_is_projection_of_timed():
    """build_paragraphs equals the timed result without the end times."""
    segments = [
        seg(0.0, 3.0, "First sentence."),
        seg(3.2, 6.0, "Second sentence."),
        seg(10.0, 12.5, "New paragraph."),
        seg(30.0, 31.0, "x" * 799 + "."),
        seg(31.5, 33.0, "After a long run."),
    ]
    timed = transcribe.build_timed_paragraphs(segments, gap_s=2.0, max_chars=800)
    plain = build_paragraphs(segments, gap_s=2.0, max_chars=800)
    assert plain == [(start, text) for start, _end, text in timed]


def _diarize_args(**overrides) -> SimpleNamespace:
    values = dict(
        voice_db=None, speakers=None, voice_threshold=0.5,
        min_speaker_seconds=0.0, gap=2.0, max_chars=800,
        no_timestamps=False,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _word(start: float, end: float, text: str) -> SimpleNamespace:
    return SimpleNamespace(start=start, end=end, word=text)


@pytest.fixture()
def fake_diarization(monkeypatch):
    """Replace WAV conversion and the pyannote call with fixed stand-ins."""
    from stt import diarize as dz

    turns = [
        dz.Turn(0.0, 3.0, "SPEAKER_00"),
        dz.Turn(3.0, 9.0, "SPEAKER_01"),
    ]
    monkeypatch.setattr(dz, "to_wav", lambda src, dst: dst)
    monkeypatch.setattr(dz, "diarize_wav", lambda *a, **k: turns)
    segments = [
        SimpleNamespace(
            start=0.0, end=2.0, text="Question.",
            words=[_word(0.0, 2.0, "Question. ")],
        ),
        SimpleNamespace(
            start=4.0, end=8.0, text="Answer.",
            words=[_word(4.0, 8.0, "Answer. ")],
        ),
    ]
    return segments


def test_diarized_blocks_split_from_rendering(
    fake_diarization, tmp_path, capsys
):
    """diarized_blocks returns Blocks; build_diarized_body renders them."""
    from stt import diarize as dz

    media = tmp_path / "talk.m4a"
    args = _diarize_args()
    blocks = transcribe.diarized_blocks(media, fake_diarization, args, None)
    assert [type(b) for b in blocks] == [dz.Block, dz.Block]
    assert [b.speaker for b in blocks] == ["SPEAKER_00", "SPEAKER_01"]
    assert [b.text for b in blocks] == ["Question.", "Answer."]
    assert capsys.readouterr().out == ""

    body = transcribe.build_diarized_body(
        media, fake_diarization, args, None
    )
    assert body == dz.render_diarized(blocks, timestamps=True)
    assert body.startswith("[00:00] " + dz.speaker_label("SPEAKER_00"))
    assert capsys.readouterr().out != ""


def test_diarized_blocks_without_words_raises(tmp_path):
    segments = [SimpleNamespace(start=0.0, end=1.0, text="x", words=None)]
    with pytest.raises(RuntimeError):
        transcribe.diarized_blocks(
            tmp_path / "talk.m4a", segments, _diarize_args(), None
        )


def _fake_model_with(segments):
    """Builds a model whose transcribe() yields `segments` for a 5-second file."""

    class _Model:
        def transcribe(self, audio, **kwargs):
            return iter(segments), SimpleNamespace(duration=5.0, language="en")

    return _Model()


def test_transcribe_file_allow_empty(tmp_path):
    """allow_empty=True returns an empty list; the default still raises."""
    media = tmp_path / "silent.m4a"
    model = _fake_model_with([])

    segments, duration, language = transcribe.transcribe_file(
        model, media, "en", None, 5, allow_empty=True
    )

    assert (segments, duration, language) == ([], 5.0, "en")
    with pytest.raises(RuntimeError) as default_error:
        transcribe.transcribe_file(model, media, "en", None, 5)
    assert "silent.m4a" in str(default_error.value)
