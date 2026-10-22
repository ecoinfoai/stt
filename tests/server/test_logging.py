"""FR-037: logs carry request ids and states only, never hints, requesters or transcript text."""

from __future__ import annotations

from pathlib import Path

import pytest

from stt.server import intake
from stt.server.layout import Layout

from .conftest import FakeClock, make_wav, new_id, run_worker
from .fake_engine import TRANSCRIPT_LINE
from .inbox_helpers import make_layout, put_attachment, put_body, transcribe_body
from .test_engine import PLAIN_SEGMENTS, Harness

HINT = "zzhintword"
REQUESTER = "zzrequester"


@pytest.fixture
def layout(root: Path) -> Layout:
    return make_layout(root)


def test_logs_never_carry_hints_requester_or_transcript(
    layout: Layout,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    gpu_present: None,
    pinned_models: Path,
) -> None:
    clock = FakeClock()
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", make_wav(tmp_path / "source.wav", 2.0))
    body = transcribe_body(request_id, requested_by=REQUESTER, hints=[HINT])
    put_body(layout.inbox, f"{request_id}.json", body)

    intake.run_once(layout, clock.now)
    run_worker(layout, clock)
    harness = Harness(tmp_path, monkeypatch)
    assert harness.run(hints=[HINT]) == 0

    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert HINT not in output
    assert REQUESTER not in output
    assert TRANSCRIPT_LINE not in output
    for segment in PLAIN_SEGMENTS:
        assert segment.text.strip() not in output
    assert request_id in output
    assert "queued" in output
    assert "running" in output
    assert "done" in output
