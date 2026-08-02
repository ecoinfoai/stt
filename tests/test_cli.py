"""stt.cli 서브커맨드 디스패치 테스트."""
from __future__ import annotations

import pytest

from stt import cli


@pytest.fixture()
def spy(monkeypatch):
    """세 서브커맨드의 main을 호출 기록용 스텁으로 바꾼다."""
    calls: dict[str, list[str]] = {}

    def record(name: str):
        def inner(argv):
            calls[name] = list(argv)
            return 0
        return inner

    for name in cli.COMMANDS:
        monkeypatch.setitem(cli.COMMANDS, name, record(name))
    return calls


class TestDispatch:
    """서브커맨드가 해당 모듈 main으로 전달되는지 확인한다."""

    def test_transcribe_receives_remaining_args(self, spy):
        """'stt transcribe'는 나머지 인자를 그대로 넘긴다."""
        assert cli.main(["transcribe", "a.mp4", "--srt"]) == 0
        assert spy["transcribe"] == ["a.mp4", "--srt"]

    def test_fetch_receives_remaining_args(self, spy):
        """'stt fetch'는 나머지 인자를 그대로 넘긴다."""
        assert cli.main(["fetch", "--urls", "urls.txt"]) == 0
        assert spy["fetch"] == ["--urls", "urls.txt"]

    def test_batch_receives_remaining_args(self, spy):
        """'stt batch'는 나머지 인자를 그대로 넘긴다."""
        assert cli.main(["batch", "--list", "list.txt"]) == 0
        assert spy["batch"] == ["--list", "list.txt"]

    def test_return_code_propagates(self, monkeypatch):
        """서브커맨드의 종료 코드를 그대로 돌려준다."""
        monkeypatch.setitem(cli.COMMANDS, "batch", lambda argv: 1)
        assert cli.main(["batch"]) == 1


class TestUsage:
    """도움말과 잘못된 입력 처리."""

    def test_no_arguments_prints_usage_to_stderr(self, capsys):
        """인자가 없으면 사용법을 stderr로 알리고 2를 돌려준다."""
        assert cli.main([]) == 2
        assert "transcribe" in capsys.readouterr().err

    def test_help_prints_usage_to_stdout(self, capsys):
        """-h/--help는 사용법을 stdout으로 내고 0을 돌려준다."""
        assert cli.main(["--help"]) == 0
        out = capsys.readouterr().out
        assert "fetch" in out and "batch" in out

    def test_unknown_command_reports_choices(self, capsys):
        """모르는 서브커맨드는 쓸 수 있는 이름을 알리고 2를 돌려준다."""
        assert cli.main(["convert"]) == 2
        err = capsys.readouterr().err
        assert "convert" in err
        assert "transcribe" in err


class TestSubcommandHelp:
    """각 서브커맨드의 도움말이 자기 이름을 밝히는지 확인한다."""

    @pytest.mark.parametrize("name", ["fetch", "transcribe", "batch"])
    def test_help_shows_full_program_name(self, name, capsys):
        """'stt <name> --help'의 usage 줄에 서브커맨드가 보인다."""
        with pytest.raises(SystemExit) as exit_info:
            cli.main([name, "--help"])
        assert exit_info.value.code == 0
        assert f"stt {name}" in capsys.readouterr().out
