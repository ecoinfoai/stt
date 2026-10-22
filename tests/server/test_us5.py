"""US5 intake rejection tests: invalid requests are answered with a reason and leave nothing behind."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stt.server import contract, intake
from stt.server.layout import Layout

from .conftest import FakeClock, make_wav, new_id
from .inbox_helpers import inbox_names, make_layout, put_attachment, put_body, put_bytes, read_response, transcribe_body


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 1.0)


def _drop_language(body: dict[str, Any]) -> None:
    del body["language"]


def _unknown_delete_audio(body: dict[str, Any]) -> None:
    body["delete_audio"] = "never"


def _empty_hints(body: dict[str, Any]) -> None:
    body["hints"] = []


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (_drop_language, "language: field is required"),
        (_unknown_delete_audio, "delete_audio: value is not allowed"),
        (_empty_hints, "hints: too few items"),
    ],
    ids=["missing_field", "unknown_value", "empty_hints"],
)
def test_us5_6_invalid_request_refused_with_reason(
    layout: Layout, wav: Path, mutate: Any, detail: str
) -> None:
    request_id = new_id()
    body = transcribe_body(request_id)
    mutate(body)
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "invalid"
    assert response["request_id"] == request_id
    assert response["failure"] == {"reason": "invalid_request", "detail": detail}
    assert inbox_names(layout) == []
    assert not (layout.jobs / request_id).exists()


@pytest.mark.parametrize("case", ["id_mismatch", "no_attachment", "unknown_language"])
def test_us5_6_semantic_failures_become_failed_status(layout: Layout, wav: Path, case: str) -> None:
    request_id = new_id()
    body = transcribe_body(request_id)
    if case == "id_mismatch":
        body["request_id"] = new_id()
    if case == "unknown_language":
        body["language"] = "xx"
    if case != "no_attachment":
        put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "transcribe"
    assert response["request_id"] == request_id
    assert response["state"] == "failed"
    assert "accepted_at" not in response
    assert response["failure"]["reason"] == "invalid_request"
    assert inbox_names(layout) == []
    assert not (layout.jobs / request_id).exists()
    assert list(layout.audio.iterdir()) == []
    assert list(layout.queue.iterdir()) == []


def test_us5_6_attachment_of_another_request_survives(layout: Layout, wav: Path) -> None:
    request_id = new_id()
    other_id = new_id()
    body = transcribe_body(request_id, audio={"file": f"{other_id}.audio.wav"})
    put_attachment(layout.inbox, f"{other_id}.audio.wav", wav)
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert response["state"] == "failed"
    assert response["failure"]["reason"] == "invalid_request"
    assert inbox_names(layout) == [f"{other_id}.audio.wav"]


@pytest.mark.parametrize("name", ["foo", "0B9A8C2E-5F1D-4C3A-9E7B-2D6F4A1C8E30"])
def test_us5_6_non_uuid_name_dropped_silently_with_fixed_log_line(
    layout: Layout, wav: Path, capfd: pytest.CaptureFixture[str], name: str
) -> None:
    put_bytes(layout.inbox / f"{name}.json", b"{}")
    put_attachment(layout.inbox, f"{name}.audio.wav", wav)

    intake.run_once(layout, FakeClock().now)

    captured = capfd.readouterr()
    lines = (captured.out + captured.err).splitlines()
    assert len(lines) == 1
    assert name.lower() not in lines[0].lower()
    assert inbox_names(layout) == []
    assert list(layout.outbox.iterdir()) == []
