"""metadata 모듈 테스트."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from stt import metadata

SAMPLE_INFO: dict = {
    "id": "abc123XYZ_1",
    "title": "이민화교수 '4차 산업혁명' 15분 강연",
    "webpage_url": "https://www.youtube.com/watch?v=abc123XYZ_1",
    "channel": "KCERN",
    "channel_url": "https://www.youtube.com/@kcern",
    "uploader": "KCERN",
    "upload_date": "20160512",
    "duration": 931,
    "tags": ["4차 산업혁명", "기업가정신", "창조경제"],
    "categories": ["Education"],
    "description": "설명 본문",
    "chapters": [
        {"start_time": 0.0, "title": "도입"},
        {"start_time": 185.0, "title": "유니콘"},
    ],
}

SAMPLE_RUN = metadata.RunInfo(
    model="large-v3",
    language="ko",
    duration_s=931.0,
    transcribed="2026-08-02",
)


class TestFormatUploadDate:
    """format_upload_date 테스트."""

    def test_format_upload_date_success(self):
        """8자리 숫자를 YYYY-MM-DD로 바꾼다."""
        assert metadata.format_upload_date("20160512") == "2016-05-12"

    def test_format_upload_date_empty(self):
        """값이 없으면 빈 문자열."""
        assert metadata.format_upload_date(None) == ""

    def test_format_upload_date_unexpected(self):
        """형식이 다르면 원래 값을 그대로 둔다."""
        assert metadata.format_upload_date("2016") == "2016"


class TestFormatChapters:
    """format_chapters 테스트."""

    def test_format_chapters_success(self):
        """시작 초를 MM:SS로 바꾼다."""
        result = metadata.format_chapters(SAMPLE_INFO["chapters"])
        assert result[0] == {"time": "00:00", "title": "도입"}
        assert result[1]["time"] == "03:05"

    def test_format_chapters_none(self):
        """챕터가 없으면 빈 목록."""
        assert metadata.format_chapters(None) == []

    def test_format_chapters_skips_broken(self):
        """제목이 없는 항목은 건너뛴다."""
        assert metadata.format_chapters([{"start_time": 0.0}]) == []


class TestSpeakerCandidates:
    """speaker_candidates 테스트."""

    def test_speaker_candidates_from_title(self):
        """제목의 '이름 교수' 형태에서 이름을 뽑는다."""
        found = metadata.speaker_candidates(
            "이민화교수 '4차 산업혁명' 15분 강연", "KCERN"
        )
        assert "이민화" in found

    def test_speaker_candidates_with_space(self):
        """직함 앞에 공백이 있어도 찾는다."""
        found = metadata.speaker_candidates("홍길동 박사 특강", "채널")
        assert "홍길동" in found

    def test_speaker_candidates_includes_channel(self):
        """단서가 없으면 채널 이름을 후보로 남긴다."""
        found = metadata.speaker_candidates("제목만 있음", "KCERN")
        assert found == ["KCERN"]

    def test_speaker_candidates_empty(self):
        """단서도 채널도 없으면 빈 목록."""
        assert metadata.speaker_candidates("제목", "") == []


class TestBuildMeta:
    """build_meta 테스트."""

    def test_build_meta_confirmed_fields(self):
        """기계로 확정되는 항목이 채워진다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        assert meta["source_url"] == SAMPLE_INFO["webpage_url"]
        assert meta["language"] == "ko"
        assert meta["duration"] == "15:31"
        assert meta["model"] == "faster-whisper large-v3"
        assert meta["transcribed"] == "2026-08-02"

    def test_build_meta_leaves_human_fields_empty(self):
        """사람이 판단할 항목은 비워 둔다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        for key in ("title", "speaker", "institution", "source_type",
                    "recorded", "course"):
            assert meta[key] == ""
        assert meta["topics"] == []

    def test_build_meta_published_not_recorded(self):
        """게시일은 recorded가 아니라 _meta.published에 넣는다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        assert meta["recorded"] == ""
        assert meta["_meta"]["published"] == "2016-05-12"

    def test_build_meta_candidates(self):
        """후보값이 _meta.candidates에 들어간다."""
        candidates = metadata.build_meta(
            SAMPLE_INFO, SAMPLE_RUN
        )["_meta"]["candidates"]
        assert "이민화" in candidates["speaker"]
        assert candidates["institution"] == ["KCERN"]
        assert "4차 산업혁명" in candidates["topics"]

    def test_build_meta_provenance_covers_filled_values(self):
        """채워진 값에는 근거가 함께 기록된다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        provenance = meta["_meta"]["provenance"]
        assert "info.json" in provenance["source_url"]
        assert provenance["duration"]

    def test_build_meta_chapters(self):
        """챕터가 _meta에 옮겨진다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        assert meta["_meta"]["chapters"][0]["title"] == "도입"

    def test_build_meta_without_info(self):
        """info.json이 없어도 전사 정보만으로 만들어진다."""
        meta = metadata.build_meta(None, SAMPLE_RUN)
        assert meta["source_url"] == ""
        assert meta["language"] == "ko"
        assert meta["duration"] == "15:31"
        assert meta["_meta"]["candidates"] == {}

    def test_build_meta_template_version(self):
        """템플릿 버전이 기록된다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        assert meta["_meta"]["template_version"] == "1.2"


class TestLoadInfoJson:
    """load_info_json 테스트."""

    def test_load_info_json_success(self, tmp_path):
        """미디어 옆 info.json을 읽는다."""
        media = tmp_path / "강연 [abc].m4a"
        media.write_bytes(b"")
        info = tmp_path / "강연 [abc].info.json"
        info.write_text('{"id": "abc"}', encoding="utf-8")
        assert metadata.load_info_json(media) == {"id": "abc"}

    def test_load_info_json_absent(self, tmp_path):
        """파일이 없으면 None을 돌려준다."""
        media = tmp_path / "강연.m4a"
        media.write_bytes(b"")
        assert metadata.load_info_json(media) is None

    def test_load_info_json_broken_raises(self, tmp_path):
        """내용이 깨졌으면 오류를 낸다."""
        media = tmp_path / "강연.m4a"
        media.write_bytes(b"")
        (tmp_path / "강연.info.json").write_text(
            "{깨진", encoding="utf-8"
        )
        with pytest.raises(ValueError):
            metadata.load_info_json(media)


class TestWriteMetaYaml:
    """write_meta_yaml 테스트."""

    def test_write_meta_yaml_roundtrip(self, tmp_path):
        """쓴 내용을 다시 읽으면 같은 값이 나온다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        target = tmp_path / "강연 [abc].meta.yaml"
        metadata.write_meta_yaml(target, meta)
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert loaded["source_url"] == SAMPLE_INFO["webpage_url"]
        assert loaded["_meta"]["published"] == "2016-05-12"

    def test_write_meta_yaml_keeps_korean(self, tmp_path):
        """한글이 이스케이프되지 않고 그대로 저장된다."""
        meta = metadata.build_meta(SAMPLE_INFO, SAMPLE_RUN)
        target = tmp_path / "meta.yaml"
        metadata.write_meta_yaml(target, meta)
        assert "4차 산업혁명" in target.read_text(encoding="utf-8")

    def test_write_meta_yaml_has_guide_comment(self, tmp_path):
        """맨 위에 사용 안내 주석이 붙는다."""
        target = tmp_path / "meta.yaml"
        metadata.write_meta_yaml(
            target, metadata.build_meta(None, SAMPLE_RUN)
        )
        assert target.read_text(encoding="utf-8").startswith("#")


class TestMetaPathFor:
    """meta_path_for 테스트."""

    def test_meta_path_for_beside_media(self):
        """출력 폴더가 없으면 원본 옆 경로를 만든다."""
        result = metadata.meta_path_for(Path("data/강연 [abc].m4a"), None)
        assert result == Path("data/강연 [abc].meta.yaml")

    def test_meta_path_for_output_dir(self):
        """출력 폴더가 있으면 그 안에 만든다."""
        result = metadata.meta_path_for(
            Path("data/강연.m4a"), Path("out")
        )
        assert result == Path("out/강연.meta.yaml")


class TestWriteForMedia:
    """write_for_media 테스트."""

    def test_write_for_media_with_info(self, tmp_path):
        """info.json이 있으면 그 값이 담긴 파일이 생긴다."""
        media = tmp_path / "강연 [abc].m4a"
        media.write_bytes(b"")
        (tmp_path / "강연 [abc].info.json").write_text(
            '{"webpage_url": "https://youtu.be/abc"}', encoding="utf-8"
        )
        path = metadata.write_for_media(media, None, SAMPLE_RUN)
        assert path.name == "강연 [abc].meta.yaml"
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["source_url"] == "https://youtu.be/abc"

    def test_write_for_media_without_info(self, tmp_path):
        """info.json이 없어도 전사 정보만으로 만들어진다."""
        media = tmp_path / "강연.m4a"
        media.write_bytes(b"")
        path = metadata.write_for_media(media, None, SAMPLE_RUN)
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["source_url"] == ""
        assert loaded["model"] == "faster-whisper large-v3"

    def test_write_for_media_output_dir(self, tmp_path):
        """출력 폴더를 주면 그 안에 만든다."""
        media = tmp_path / "강연.m4a"
        media.write_bytes(b"")
        out = tmp_path / "out"
        out.mkdir()
        path = metadata.write_for_media(media, out, SAMPLE_RUN)
        assert path.parent == out
