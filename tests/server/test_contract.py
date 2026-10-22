"""Contract tests: packaged schemas, request/response validation and languages."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
CONTRACTS = REPO / "specs" / "001-server-transcription" / "contracts"
PACKAGED = REPO / "src" / "stt" / "server" / "schemas"
SCHEMA_FILES = ("request.schema.json", "response.schema.json", "result.schema.json")

RID = "0b9a8c2e-5f1d-4c3a-9e7b-2d6f4a1c8e30"
OTHER_RID = "6f2e1d0c-9b8a-4e7d-8c6b-5a4f3e2d1c0b"


def transcribe_request(**overrides: Any) -> dict[str, Any]:
    """Builds a valid transcribe request and applies overrides (None removes a key)."""
    doc: dict[str, Any] = {
        "schema_version": 1,
        "kind": "transcribe",
        "request_id": RID,
        "requested_by": "owui-user-42",
        "audio": {"file": f"{RID}.audio.m4a"},
        "language": "ko",
        "diarize": False,
        "delete_audio": "after_job",
    }
    for key, value in overrides.items():
        if value is None:
            doc.pop(key)
        else:
            doc[key] = value
    return doc


def delete_request(**overrides: Any) -> dict[str, Any]:
    """Builds a valid delete request and applies overrides (None removes a key)."""
    doc: dict[str, Any] = {
        "schema_version": 1,
        "kind": "delete",
        "request_id": OTHER_RID,
        "requested_by": "owui-user-42",
        "target": RID,
        "scope": "result",
    }
    for key, value in overrides.items():
        if value is None:
            doc.pop(key)
        else:
            doc[key] = value
    return doc


def readme_json_blocks() -> list[dict[str, Any]]:
    """Returns the ```json blocks of contracts/README.md parsed in order."""
    text = (CONTRACTS / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
    return [json.loads(block) for block in blocks]


@pytest.mark.parametrize("name", SCHEMA_FILES)
def test_packaged_schema_matches_contract_bytes(name: str) -> None:
    assert (PACKAGED / name).read_bytes() == (CONTRACTS / name).read_bytes()


@pytest.mark.parametrize("name", ["request", "response", "result"])
def test_load_schema_returns_the_packaged_schema(name: str) -> None:
    from stt.server import contract

    expected = json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert contract.load_schema(name) == expected  # type: ignore[arg-type]


def test_readme_examples_validate() -> None:
    from stt.server import contract

    blocks = readme_json_blocks()
    assert len(blocks) == 4
    responses = [doc for doc in blocks if "updated_at" in doc]
    requests = [doc for doc in blocks if "updated_at" not in doc]
    assert len(responses) == 1
    assert len(requests) == 3
    for doc in requests:
        assert contract.validate_request(doc) is None
    assert contract.validate_response(responses[0]) is None


def test_valid_requests_pass() -> None:
    from stt.server import contract

    assert contract.validate_request(transcribe_request()) is None
    assert contract.validate_request(
        transcribe_request(
            audio={"from_request": OTHER_RID},
            hints=["Yersinia enterocolitica", "hepatitis B"],
            diarize=True,
            delete_audio="at_midnight",
        )
    ) is None
    assert contract.validate_request(delete_request(scope="everything")) is None


LONG_RID = RID.upper()

INVALID_REQUESTS = [
    ("schema_version_missing", transcribe_request(schema_version=None), "schema_version: field is required"),
    ("schema_version_two", transcribe_request(schema_version=2), "schema_version: unsupported value"),
    ("uppercase_uuid", transcribe_request(request_id=LONG_RID), "request_id: value has an invalid format"),
    ("space_in_requester", transcribe_request(requested_by="owui user"), "requested_by: value has an invalid format"),
    (
        "audio_file_and_from_request",
        transcribe_request(audio={"file": f"{RID}.audio.m4a", "from_request": OTHER_RID}),
        "audio: must match exactly one alternative",
    ),
    ("exe_extension", transcribe_request(audio={"file": f"{RID}.audio.exe"}), "audio: must match exactly one alternative"),
    ("upper_language", transcribe_request(language="KO"), "language: value has an invalid format"),
    ("empty_hints", transcribe_request(hints=[]), "hints: too few items"),
    ("duplicate_hints", transcribe_request(hints=["a", "a"]), "hints: items must be unique"),
    ("leading_space_hint", transcribe_request(hints=[" lead"]), "hints: value has an invalid format"),
    ("control_char_hint", transcribe_request(hints=["a\x07b"]), "hints: value has an invalid format"),
    ("long_hint", transcribe_request(hints=["x" * 101]), "hints: value is too long"),
    ("too_many_hints", transcribe_request(hints=[f"t{i}" for i in range(201)]), "hints: too many items"),
    ("empty_hint", transcribe_request(hints=[""]), "hints: value is too short"),
    ("delete_audio_never", transcribe_request(delete_audio="never"), "delete_audio: value is not allowed"),
    ("diarize_string", transcribe_request(diarize="yes"), "diarize: value has the wrong type"),
    ("unknown_field", {**transcribe_request(), "extra": 1}, "request: unknown field is not allowed"),
    ("delete_scope_all", delete_request(scope="all"), "scope: value is not allowed"),
    ("delete_target_missing", delete_request(target=None), "target: field is required"),
    ("kind_missing", transcribe_request(kind=None), "kind: field is required"),
    ("kind_unknown", transcribe_request(kind="purge"), "kind: value is not allowed"),
    ("not_an_object", ["x"], "request: value has the wrong type"),
]


@pytest.mark.parametrize(
    ("doc", "expected"),
    [pytest.param(doc, expected, id=name) for name, doc, expected in INVALID_REQUESTS],
)
def test_invalid_request_detail(doc: Any, expected: str) -> None:
    from stt.server import contract

    assert contract.validate_request(doc) == expected


def test_two_hundred_long_duplicate_hints_give_a_short_detail_without_hint_text() -> None:
    from stt.server import contract

    hint = "secret-term-" + "q" * 88
    assert len(hint) == 100
    detail = contract.validate_request(transcribe_request(hints=[hint] * 200))
    assert detail == "hints: items must be unique"
    assert len(detail) <= 500
    assert "secret-term" not in detail


def test_detail_never_echoes_unknown_field_names_or_values() -> None:
    from stt.server import contract

    detail = contract.validate_request({**transcribe_request(), "leaky_key_name": "leaky value"})
    assert detail is not None
    assert "leaky" not in detail


def valid_failed_response(**failure: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "transcribe",
        "request_id": RID,
        "state": "failed",
        "updated_at": "2026-03-02T01:00:00Z",
        "finished_at": "2026-03-02T01:00:00Z",
        "expires_at": "2026-03-03T00:58:00Z",
        "failure": failure,
    }


def test_failure_cause_rules() -> None:
    from stt.server import contract

    with_cause = valid_failed_response(
        reason="audio_deleted", detail="The recording was deleted.", cause="midnight"
    )
    assert contract.validate_response(with_cause) is None

    without_cause = copy.deepcopy(with_cause)
    del without_cause["failure"]["cause"]
    assert contract.validate_response(without_cause) is not None

    other_with_cause = valid_failed_response(
        reason="engine_error", detail="The engine failed.", cause="midnight"
    )
    assert contract.validate_response(other_with_cause) is not None

    other = valid_failed_response(reason="engine_error", detail="The engine failed.")
    assert contract.validate_response(other) is None


def test_validate_result() -> None:
    from stt.server import contract

    result = {
        "schema_version": 1,
        "request_id": RID,
        "transcript": "transcript.txt",
        "paragraphs": [
            {"start": 0.0, "end": 4.5, "speaker": None, "text": "Hello there.", "uncertain": False}
        ],
        "summary": {
            "model": "large-v3",
            "device": "cuda",
            "language": "en",
            "audio_duration_s": 4.5,
            "processing_s": 1.0,
            "diarized": False,
            "hints": None,
        },
    }
    assert contract.validate_result(result) is None

    broken = copy.deepcopy(result)
    broken["summary"]["device"] = "cpu"
    assert contract.validate_result(broken) == "summary.device: unsupported value"


def test_language_support() -> None:
    from stt.server import contract

    assert contract.language_supported("ko") is True
    assert contract.language_supported("xx") is False
    assert contract.language_supported("auto") is False
    assert isinstance(contract.LANGUAGE_CODES, frozenset)


def test_language_codes_match_faster_whisper() -> None:
    from faster_whisper.tokenizer import _LANGUAGE_CODES

    from stt.server import contract

    assert contract.LANGUAGE_CODES == frozenset(_LANGUAGE_CODES)
    assert len(contract.LANGUAGE_CODES) == 100


def test_contract_import_does_not_load_faster_whisper() -> None:
    code = (
        "import sys; import stt.server.contract; "
        "sys.exit(1 if 'faster_whisper' in sys.modules or 'torch' in sys.modules else 0)"
    )
    completed = subprocess.run([sys.executable, "-c", code], check=False)
    assert completed.returncode == 0


def test_built_wheel_contains_schemas(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not on PATH")
    source = tmp_path / "src_copy"
    source.mkdir()
    shutil.copy2(REPO / "pyproject.toml", source / "pyproject.toml")
    shutil.copy2(REPO / "README.md", source / "README.md")
    shutil.copytree(
        REPO / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info")
    )
    out_dir = tmp_path / "wheels"
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(out_dir), str(source)],
        check=True,
        capture_output=True,
    )
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
    for name in SCHEMA_FILES:
        assert f"stt/server/schemas/{name}" in names
