"""batch_stt 모듈 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import batch_stt  # noqa: E402


def _touch(directory: Path, name: str) -> Path:
    """테스트용 빈 미디어 파일을 만든다."""
    path = directory / name
    path.write_bytes(b"")
    return path


class TestParseTxtList:
    """parse_txt_list 테스트."""

    def test_parse_txt_list_success(self):
        """주석과 빈 줄을 뺀 항목만 남는다."""
        text = "# 주석\n\n강연 A\n  강연 B  \n"
        items = batch_stt.parse_txt_list(text)
        assert [item.name for item in items] == ["강연 A", "강연 B"]

    def test_parse_txt_list_defaults_applied(self):
        """txt 항목은 전역 기본 옵션을 그대로 받는다."""
        items = batch_stt.parse_txt_list("강연 A\n")
        assert items[0].options["language"] == "ko"
        assert items[0].options["srt"] is False

    def test_parse_txt_list_empty(self):
        """유효한 줄이 없으면 ValueError."""
        with pytest.raises(ValueError):
            batch_stt.parse_txt_list("# 주석만\n\n")


class TestParseYamlList:
    """parse_yaml_list 테스트."""

    def test_parse_yaml_list_string_items(self):
        """문자열 항목과 defaults 병합이 동작한다."""
        text = (
            "defaults:\n  srt: true\n  beam: 1\n"
            "items:\n  - 강연 A\n  - 강연 B\n"
        )
        items = batch_stt.parse_yaml_list(text)
        assert [item.name for item in items] == ["강연 A", "강연 B"]
        assert items[0].options["srt"] is True
        assert items[0].options["beam"] == 1

    def test_parse_yaml_list_item_override(self):
        """항목별 옵션이 defaults를 덮어쓴다."""
        text = (
            "defaults:\n  srt: false\n"
            "items:\n"
            "  - title: 강연 A\n    srt: true\n"
            "  - path: data/강연 B.m4a\n"
        )
        items = batch_stt.parse_yaml_list(text)
        assert items[0].options["srt"] is True
        assert items[1].name == "data/강연 B.m4a"
        assert items[1].options["srt"] is False

    def test_parse_yaml_list_bare_sequence(self):
        """items 없이 목록만 있어도 읽는다."""
        items = batch_stt.parse_yaml_list("- 강연 A\n- 강연 B\n")
        assert len(items) == 2

    def test_parse_yaml_list_unknown_option(self):
        """모르는 옵션 이름이면 ValueError."""
        text = "items:\n  - title: 강연 A\n    quality: high\n"
        with pytest.raises(ValueError, match="quality"):
            batch_stt.parse_yaml_list(text)

    def test_parse_yaml_list_empty(self):
        """항목이 없으면 ValueError."""
        with pytest.raises(ValueError):
            batch_stt.parse_yaml_list("items: []\n")


class TestLoadList:
    """load_list 테스트."""

    def test_load_list_txt(self, tmp_path):
        """.txt 확장자는 텍스트 파서로 읽는다."""
        list_file = tmp_path / "list.txt"
        list_file.write_text("강연 A\n", encoding="utf-8")
        assert len(batch_stt.load_list(list_file)) == 1

    def test_load_list_yaml(self, tmp_path):
        """.yaml 확장자는 YAML 파서로 읽는다."""
        list_file = tmp_path / "list.yaml"
        list_file.write_text("items:\n  - 강연 A\n", encoding="utf-8")
        assert len(batch_stt.load_list(list_file)) == 1

    def test_load_list_missing(self, tmp_path):
        """파일이 없으면 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            batch_stt.load_list(tmp_path / "none.txt")

    def test_load_list_bad_extension(self, tmp_path):
        """지원하지 않는 확장자면 ValueError."""
        list_file = tmp_path / "list.json"
        list_file.write_text("[]", encoding="utf-8")
        with pytest.raises(ValueError):
            batch_stt.load_list(list_file)


class TestResolveName:
    """resolve_name 테스트."""

    def test_resolve_name_by_title(self, tmp_path):
        """확장자 없는 제목으로 실제 파일을 찾는다."""
        media = _touch(tmp_path, "강연 A.m4a")
        assert batch_stt.resolve_name("강연 A", tmp_path) == media

    def test_resolve_name_exact_filename(self, tmp_path):
        """확장자까지 적은 이름도 그대로 찾는다."""
        media = _touch(tmp_path, "강연 A.mp4")
        assert batch_stt.resolve_name("강연 A.mp4", tmp_path) == media

    def test_resolve_name_prefers_audio(self, tmp_path):
        """같은 제목의 영상·음원이 있으면 음원을 고른다."""
        _touch(tmp_path, "강연 A.mp4")
        audio = _touch(tmp_path, "강연 A.m4a")
        assert batch_stt.resolve_name("강연 A", tmp_path) == audio

    def test_resolve_name_subfolder(self, tmp_path):
        """하위 폴더 경로가 붙은 제목도 해석한다."""
        sub = tmp_path / "sub"
        sub.mkdir()
        media = _touch(sub, "강연 A.m4a")
        assert batch_stt.resolve_name("sub/강연 A", tmp_path) == media

    def test_resolve_name_missing(self, tmp_path):
        """해당 파일이 없으면 FileNotFoundError."""
        _touch(tmp_path, "다른 강연.m4a")
        with pytest.raises(FileNotFoundError):
            batch_stt.resolve_name("강연 A", tmp_path)

    def test_resolve_name_missing_dir(self, tmp_path):
        """폴더 자체가 없으면 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            batch_stt.resolve_name("없는폴더/강연", tmp_path)


class TestResolveItems:
    """resolve_items 테스트."""

    def test_resolve_items_success(self, tmp_path):
        """모든 항목이 실제 경로로 바뀐다."""
        _touch(tmp_path, "A.m4a")
        _touch(tmp_path, "B.mp4")
        items = batch_stt.parse_txt_list("A\nB\n")
        resolved = batch_stt.resolve_items(items, tmp_path)
        assert [p.name for p, _ in resolved] == ["A.m4a", "B.mp4"]

    def test_resolve_items_reports_all_failures(self, tmp_path):
        """없는 항목이 여럿이면 한 번에 모두 보고한다."""
        _touch(tmp_path, "A.m4a")
        items = batch_stt.parse_txt_list("A\nB\nC\n")
        with pytest.raises(FileNotFoundError) as excinfo:
            batch_stt.resolve_items(items, tmp_path)
        message = str(excinfo.value)
        assert "B" in message and "C" in message

    def test_resolve_items_rejects_duplicates(self, tmp_path):
        """같은 파일이 두 번 나오면 ValueError."""
        _touch(tmp_path, "A.m4a")
        items = batch_stt.parse_txt_list("A\nA.m4a\n")
        with pytest.raises(ValueError):
            batch_stt.resolve_items(items, tmp_path)


class TestGroupByOptions:
    """group_by_options 테스트."""

    def test_group_by_options_merges_same(self, tmp_path):
        """옵션이 같은 항목은 한 묶음이 된다."""
        options = dict(batch_stt.OPTION_DEFAULTS)
        pairs = [(Path("A.m4a"), options), (Path("B.m4a"), dict(options))]
        groups = batch_stt.group_by_options(pairs)
        assert len(groups) == 1
        assert len(groups[0][1]) == 2

    def test_group_by_options_splits_different(self):
        """옵션이 다르면 묶음이 나뉜다."""
        base = dict(batch_stt.OPTION_DEFAULTS)
        other = dict(base, srt=True)
        pairs = [(Path("A.m4a"), base), (Path("B.m4a"), other)]
        assert len(batch_stt.group_by_options(pairs)) == 2

    def test_group_by_options_keeps_order(self):
        """묶음 순서는 처음 등장 순서를 따른다."""
        base = dict(batch_stt.OPTION_DEFAULTS)
        other = dict(base, beam=1)
        pairs = [
            (Path("A.m4a"), other),
            (Path("B.m4a"), base),
            (Path("C.m4a"), dict(other)),
        ]
        groups = batch_stt.group_by_options(pairs)
        assert [p.name for p in groups[0][1]] == ["A.m4a", "C.m4a"]
        assert [p.name for p in groups[1][1]] == ["B.m4a"]


class TestBuildArgv:
    """build_argv 테스트."""

    def test_build_argv_includes_paths_and_defaults(self):
        """경로와 기본 옵션이 모두 인자로 들어간다."""
        argv = batch_stt.build_argv(
            [Path("A.m4a")], dict(batch_stt.OPTION_DEFAULTS), None, False
        )
        assert argv[0] == "A.m4a"
        assert "--model" in argv and "--language" in argv

    def test_build_argv_flags(self):
        """참/거짓 옵션은 플래그로만 붙는다."""
        options = dict(
            batch_stt.OPTION_DEFAULTS, srt=True, no_timestamps=True
        )
        argv = batch_stt.build_argv(
            [Path("A.m4a")], options, Path("out"), True
        )
        assert "--srt" in argv
        assert "--no-timestamps" in argv
        assert "--overwrite" in argv
        assert argv[argv.index("--output-dir") + 1] == "out"

    def test_build_argv_omits_absent_terms(self):
        """terms가 없으면 --terms를 넣지 않는다."""
        argv = batch_stt.build_argv(
            [Path("A.m4a")], dict(batch_stt.OPTION_DEFAULTS), None, False
        )
        assert "--terms" not in argv

    def test_build_argv_includes_terms(self):
        """terms가 있으면 경로와 함께 넣는다."""
        options = dict(batch_stt.OPTION_DEFAULTS, terms="terms.txt")
        argv = batch_stt.build_argv([Path("A.m4a")], options, None, False)
        assert argv[argv.index("--terms") + 1] == "terms.txt"


class TestRunGroups:
    """run_groups 테스트."""

    def test_run_groups_calls_transcribe(self, monkeypatch):
        """묶음마다 transcribe.main이 한 번씩 호출된다."""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            batch_stt.transcribe, "main", lambda argv: calls.append(argv)
        )
        base = dict(batch_stt.OPTION_DEFAULTS)
        groups = [(base, [Path("A.m4a")]), (base, [Path("B.m4a")])]
        assert batch_stt.run_groups(groups, None, False, False) == []
        assert len(calls) == 2
        assert calls[0][0] == "A.m4a"

    def test_run_groups_raises_without_keep_going(self, monkeypatch):
        """keep_going이 꺼져 있으면 첫 실패에서 멈춘다."""
        def boom(argv):
            raise RuntimeError("VRAM 부족")

        monkeypatch.setattr(batch_stt.transcribe, "main", boom)
        groups = [(dict(batch_stt.OPTION_DEFAULTS), [Path("A.m4a")])]
        with pytest.raises(RuntimeError):
            batch_stt.run_groups(groups, None, False, False)

    def test_run_groups_collects_failures(self, monkeypatch):
        """keep_going이 켜져 있으면 실패를 모아 돌려준다."""
        def boom(argv):
            raise RuntimeError("VRAM 부족")

        monkeypatch.setattr(batch_stt.transcribe, "main", boom)
        base = dict(batch_stt.OPTION_DEFAULTS)
        groups = [(base, [Path("A.m4a")]), (base, [Path("B.m4a")])]
        failures = batch_stt.run_groups(groups, None, False, True)
        assert len(failures) == 2


class TestMain:
    """main 통합 테스트."""

    def test_main_dry_run(self, tmp_path, monkeypatch, capsys):
        """--dry-run은 전사 없이 계획만 출력한다."""
        _touch(tmp_path, "강연 A.m4a")
        list_file = tmp_path / "list.txt"
        list_file.write_text("강연 A\n", encoding="utf-8")
        monkeypatch.setattr(
            batch_stt.transcribe, "main", lambda argv: pytest.fail("실행됨")
        )
        code = batch_stt.main([
            "--list", str(list_file), "--base-dir", str(tmp_path),
            "--dry-run",
        ])
        assert code == 0
        assert "강연 A.m4a" in capsys.readouterr().out

    def test_main_returns_one_on_failure(self, tmp_path, monkeypatch):
        """--keep-going으로 실패가 남으면 종료 코드가 1이다."""
        _touch(tmp_path, "강연 A.m4a")
        list_file = tmp_path / "list.txt"
        list_file.write_text("강연 A\n", encoding="utf-8")

        def boom(argv):
            raise RuntimeError("전사 실패")

        monkeypatch.setattr(batch_stt.transcribe, "main", boom)
        code = batch_stt.main([
            "--list", str(list_file), "--base-dir", str(tmp_path),
            "--keep-going",
        ])
        assert code == 1


class TestParseArgs:
    """parse_args 테스트."""

    def test_parse_args_requires_list(self):
        """--list가 없으면 종료한다."""
        with pytest.raises(SystemExit):
            batch_stt.parse_args([])

    def test_parse_args_defaults(self):
        """기본 base_dir은 data, dry_run은 꺼져 있다."""
        args = batch_stt.parse_args(["--list", "list.txt"])
        assert args.base_dir == Path("data")
        assert args.dry_run is False
        assert args.keep_going is False
