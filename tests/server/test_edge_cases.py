"""Edge case tests: duplicate request ids and unsafe inbox files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

import pytest

from stt.server import contract, intake
from stt.server.layout import Layout
from stt.server.records import load_job

from .conftest import FakeClock, make_wav, new_id
from .inbox_helpers import inbox_names, make_layout, put_attachment, put_body, put_bytes, read_response, transcribe_body


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


@pytest.fixture
def wav(tmp_path: Path) -> Path:
    return make_wav(tmp_path / "source.wav", 1.0)


def test_edge_1_duplicate_request_id_refused(layout: Layout, wav: Path) -> None:
    clock = FakeClock()
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id))
    intake.run_once(layout, clock.now)
    first = read_response(layout, request_id)
    assert first["state"] == "queued"

    clock.advance(60)
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, language="en"))
    intake.run_once(layout, clock.now)

    second = read_response(layout, request_id)
    assert contract.validate_response(second) is None
    assert second["duplicate_refused_at"] == "2026-03-02T01:01:00Z"
    for key in ("state", "queue_position", "accepted_at", "expires_at", "audio"):
        assert second[key] == first[key]
    assert inbox_names(layout) == []
    job = load_job(layout, request_id)
    assert job.language == "ko"
    assert job.state == "queued"
    assert job.duplicate_refused_at == clock.now()
    assert len(list(layout.queue.iterdir())) == 1


def test_edge_1_duplicate_of_refused_request_adds_field_to_refusal(layout: Layout) -> None:
    clock = FakeClock()
    request_id = new_id()
    put_bytes(layout.inbox / f"{request_id}.json", b"{")
    intake.run_once(layout, clock.now)
    first = read_response(layout, request_id)
    assert first["kind"] == "invalid"

    clock.advance(30)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id))
    intake.run_once(layout, clock.now)

    second = read_response(layout, request_id)
    assert contract.validate_response(second) is None
    assert second == {**first, "duplicate_refused_at": "2026-03-02T01:00:30Z"}
    assert inbox_names(layout) == []
    assert not (layout.jobs / request_id).exists()


def test_unsafe_inbox_files_refused_without_reading(layout: Layout, wav: Path, tmp_path: Path) -> None:
    clock = FakeClock()
    request_id = new_id()
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"zz": "secretvalue"}), encoding="utf-8")
    (layout.inbox / f"{request_id}.json").symlink_to(secret)

    intake.run_once(layout, clock.now)

    response = read_response(layout, request_id)
    assert response["kind"] == "invalid"
    assert response["failure"]["reason"] == "invalid_request"
    assert not any("secretvalue" in path.read_text(encoding="utf-8") for path in layout.outbox.rglob("*.json"))
    assert inbox_names(layout) == []
    assert json.loads(secret.read_text(encoding="utf-8")) == {"zz": "secretvalue"}


def _link_symlink(target: Path, link: Path) -> None:
    link.symlink_to(target)


def _link_hardlink(target: Path, link: Path) -> None:
    os.link(target, link)


def _link_fifo(target: Path, link: Path) -> None:
    os.mkfifo(link, 0o640)


@pytest.mark.parametrize("make_link", [_link_symlink, _link_hardlink, _link_fifo], ids=["symlink", "hardlink", "fifo"])
def test_unsafe_attachment_fails_invalid_request_and_target_stays(
    layout: Layout, wav: Path, tmp_path: Path, make_link: Callable[[Path, Path], None]
) -> None:
    request_id = new_id()
    target = tmp_path / "target.wav"
    target.write_bytes(wav.read_bytes())
    expected = target.read_bytes()
    make_link(target, layout.inbox / f"{request_id}.audio.wav")
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id))

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["state"] == "failed"
    assert response["failure"]["reason"] == "invalid_request"
    assert "accepted_at" not in response
    assert inbox_names(layout) == []
    assert target.read_bytes() == expected
    assert not (layout.jobs / request_id).exists()


def _broken_json(path: Path) -> None:
    put_bytes(path, b'{"schema_version": 1,')


def _not_utf8(path: Path) -> None:
    put_bytes(path, b"\xff\xfe{}")


def _unreadable(path: Path) -> None:
    put_bytes(path, json.dumps(transcribe_body(path.stem)).encode("utf-8"), 0o000)


def _oversized(path: Path) -> None:
    put_bytes(path, b" " * (1024 * 1024 + 1))


@pytest.mark.parametrize(
    "write",
    [
        _broken_json,
        _not_utf8,
        pytest.param(
            _unreadable,
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode 000 files"),
        ),
        _oversized,
    ],
    ids=["broken_json", "not_utf8", "unreadable", "oversized"],
)
def test_every_inbox_json_is_consumed(layout: Layout, write: Callable[[Path], None]) -> None:
    request_id = new_id()
    write(layout.inbox / f"{request_id}.json")

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "invalid"
    assert response["failure"]["reason"] == "invalid_request"
    assert list(layout.inbox.glob("*.json")) == []
