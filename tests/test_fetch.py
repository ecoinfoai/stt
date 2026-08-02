"""fetch 모듈 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from stt import fetch


class TestParseUrlList:
    """parse_url_list 테스트."""

    def test_parse_url_list_success(self):
        """주석과 빈 줄을 뺀 URL만 남는다."""
        text = (
            "# 강연 목록\n\n"
            "https://www.youtube.com/watch?v=aaa\n"
            "  https://youtu.be/bbb  \n"
        )
        assert fetch.parse_url_list(text) == [
            "https://www.youtube.com/watch?v=aaa",
            "https://youtu.be/bbb",
        ]

    def test_parse_url_list_drops_duplicates(self):
        """같은 URL이 두 번 나오면 한 번만 남는다."""
        text = "https://youtu.be/aaa\nhttps://youtu.be/aaa\n"
        assert fetch.parse_url_list(text) == ["https://youtu.be/aaa"]

    def test_parse_url_list_rejects_non_url(self):
        """http로 시작하지 않는 줄이 있으면 ValueError."""
        with pytest.raises(ValueError, match="3"):
            fetch.parse_url_list(
                "https://youtu.be/aaa\n# c\n영상 제목만 적음\n"
            )

    def test_parse_url_list_empty(self):
        """유효한 URL이 없으면 ValueError."""
        with pytest.raises(ValueError):
            fetch.parse_url_list("# 주석만\n\n")


class TestLoadUrls:
    """load_urls 테스트."""

    def test_load_urls_success(self, tmp_path):
        """파일에서 URL 목록을 읽는다."""
        list_file = tmp_path / "urls.txt"
        list_file.write_text(
            "https://youtu.be/aaa\n", encoding="utf-8"
        )
        assert fetch.load_urls(list_file) == ["https://youtu.be/aaa"]

    def test_load_urls_missing(self, tmp_path):
        """파일이 없으면 FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            fetch.load_urls(tmp_path / "none.txt")


class TestFindFfmpeg:
    """find_ffmpeg 테스트."""

    def test_find_ffmpeg_on_path(self, monkeypatch):
        """PATH에 있으면 그 경로를 돌려준다."""
        monkeypatch.setattr(
            fetch.shutil, "which", lambda name: "/usr/bin/ffmpeg"
        )
        assert fetch.find_ffmpeg() == "/usr/bin/ffmpeg"

    def test_find_ffmpeg_in_venv(self, monkeypatch, tmp_path):
        """PATH에 없어도 파이썬 옆에 있으면 찾는다."""
        monkeypatch.setattr(fetch.shutil, "which", lambda name: None)
        binary = tmp_path / "ffmpeg"
        binary.write_bytes(b"")
        monkeypatch.setattr(fetch.sys, "executable", str(tmp_path / "python"))
        assert fetch.find_ffmpeg() == str(binary)

    def test_find_ffmpeg_absent(self, monkeypatch, tmp_path):
        """어디에도 없으면 None."""
        monkeypatch.setattr(fetch.shutil, "which", lambda name: None)
        monkeypatch.setattr(fetch.sys, "executable", str(tmp_path / "python"))
        assert fetch.find_ffmpeg() is None


class TestRequireTools:
    """require_tools 테스트."""

    def test_require_tools_ok(self, monkeypatch):
        """모듈과 ffmpeg가 모두 있으면 통과한다."""
        monkeypatch.setattr(fetch, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            fetch.importlib.util, "find_spec", lambda name: object()
        )
        fetch.require_tools()

    def test_require_tools_missing_yt_dlp(self, monkeypatch):
        """yt-dlp 모듈이 없으면 uv sync 안내와 함께 오류."""
        monkeypatch.setattr(fetch, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        monkeypatch.setattr(
            fetch.importlib.util, "find_spec", lambda name: None
        )
        with pytest.raises(RuntimeError, match="uv sync"):
            fetch.require_tools()

    def test_require_tools_missing_ffmpeg(self, monkeypatch):
        """ffmpeg가 없으면 설치 안내와 함께 오류."""
        monkeypatch.setattr(fetch, "find_ffmpeg", lambda: None)
        monkeypatch.setattr(
            fetch.importlib.util, "find_spec", lambda name: object()
        )
        with pytest.raises(RuntimeError, match="ffmpeg"):
            fetch.require_tools()


class TestBuildCommand:
    """build_command 테스트."""

    def _args(self, **overrides):
        """기본 인자 묶음을 만든다."""
        args = fetch.parse_args(["--urls", "urls.txt"])
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_build_command_runs_module(self, tmp_path):
        """PATH가 아니라 지금 파이썬의 yt_dlp 모듈을 실행한다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert command[0] == fetch.sys.executable
        assert command[1:3] == ["-m", "yt_dlp"]

    def test_build_command_ffmpeg_location(self, tmp_path, monkeypatch):
        """찾은 ffmpeg 경로를 yt-dlp에 알려준다."""
        monkeypatch.setattr(fetch, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert command[command.index("--ffmpeg-location") + 1] == (
            "/usr/bin/ffmpeg"
        )

    def test_build_command_audio_by_default(self, tmp_path):
        """기본은 음원 추출이다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert "-x" in command
        assert command[command.index("--audio-format") + 1] == "m4a"

    def test_build_command_video_option(self, tmp_path):
        """--video면 음원 추출 옵션을 넣지 않는다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"],
            self._args(out_dir=tmp_path, video=True),
        )
        assert "-x" not in command

    def test_build_command_never_expands_playlists(self, tmp_path):
        """?list= 파라미터가 붙어도 그 영상 하나만 받는다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa?list=PLxxx"],
            self._args(out_dir=tmp_path),
        )
        assert "--no-playlist" in command
        assert "--yes-playlist" not in command

    def test_build_command_writes_info_json(self, tmp_path):
        """메타데이터 저장 옵션은 항상 들어간다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert "--write-info-json" in command

    def test_build_command_output_template_has_id(self, tmp_path):
        """파일 이름 틀에 영상 ID가 들어간다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert "%(id)s" in command[command.index("-o") + 1]

    def test_build_command_archive_path(self, tmp_path):
        """아카이브 파일 경로가 출력 폴더 기준으로 붙는다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        archive = command[command.index("--download-archive") + 1]
        assert archive == str(tmp_path / "archive.txt")

    def test_build_command_sleep_defaults(self, tmp_path):
        """차단을 피하는 대기 옵션이 기본으로 붙는다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert "--sleep-requests" in command
        assert "--sleep-interval" in command

    def test_build_command_auto_subs_opt_in(self, tmp_path):
        """--auto-subs를 줄 때만 자동자막을 받는다."""
        plain = fetch.build_command(
            ["https://youtu.be/aaa"], self._args(out_dir=tmp_path)
        )
        assert "--write-auto-subs" not in plain
        with_subs = fetch.build_command(
            ["https://youtu.be/aaa"],
            self._args(out_dir=tmp_path, auto_subs=True),
        )
        assert "--write-auto-subs" in with_subs

    def test_build_command_cookies(self, tmp_path):
        """브라우저 쿠키 옵션을 그대로 전달한다."""
        command = fetch.build_command(
            ["https://youtu.be/aaa"],
            self._args(out_dir=tmp_path, cookies_from_browser="chrome"),
        )
        assert command[command.index("--cookies-from-browser") + 1] == (
            "chrome"
        )

    def test_build_command_urls_last(self, tmp_path):
        """URL은 명령 끝에 붙는다."""
        urls = ["https://youtu.be/aaa", "https://youtu.be/bbb"]
        command = fetch.build_command(urls, self._args(out_dir=tmp_path))
        assert command[-2:] == urls


class TestParseArgs:
    """parse_args 테스트."""

    def test_parse_args_requires_urls(self):
        """--urls가 없으면 종료한다."""
        with pytest.raises(SystemExit):
            fetch.parse_args([])

    def test_parse_args_defaults(self):
        """기본 출력 폴더는 data, 음원 형식은 m4a."""
        args = fetch.parse_args(["--urls", "urls.txt"])
        assert args.out_dir == Path("data")
        assert args.audio_format == "m4a"
        assert args.video is False
        assert args.auto_subs is False


class TestMain:
    """main 통합 테스트."""

    def test_main_dry_run_skips_download(self, tmp_path, monkeypatch,
                                         capsys):
        """--dry-run은 명령만 보여주고 실행하지 않는다."""
        list_file = tmp_path / "urls.txt"
        list_file.write_text("https://youtu.be/aaa\n", encoding="utf-8")
        monkeypatch.setattr(fetch, "require_tools", lambda: None)
        monkeypatch.setattr(
            fetch.subprocess, "run",
            lambda *a, **k: pytest.fail("실행되면 안 됨"),
        )
        code = fetch.main([
            "--urls", str(list_file), "--out-dir", str(tmp_path),
            "--dry-run",
        ])
        assert code == 0
        assert "yt_dlp" in capsys.readouterr().out

    def test_main_returns_yt_dlp_exit_code(self, tmp_path, monkeypatch):
        """yt-dlp 종료 코드를 그대로 돌려준다."""
        list_file = tmp_path / "urls.txt"
        list_file.write_text("https://youtu.be/aaa\n", encoding="utf-8")
        monkeypatch.setattr(fetch, "require_tools", lambda: None)

        class _Result:
            returncode = 2

        monkeypatch.setattr(
            fetch.subprocess, "run", lambda *a, **k: _Result()
        )
        code = fetch.main([
            "--urls", str(list_file), "--out-dir", str(tmp_path),
        ])
        assert code == 2

    def test_main_creates_out_dir(self, tmp_path, monkeypatch):
        """출력 폴더가 없으면 만든다."""
        list_file = tmp_path / "urls.txt"
        list_file.write_text("https://youtu.be/aaa\n", encoding="utf-8")
        target = tmp_path / "data"
        monkeypatch.setattr(fetch, "require_tools", lambda: None)
        fetch.main([
            "--urls", str(list_file), "--out-dir", str(target),
            "--dry-run",
        ])
        assert target.is_dir()
