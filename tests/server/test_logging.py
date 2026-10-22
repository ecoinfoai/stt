"""FR-037: logs carry request ids and states only, never hints, requesters or transcript text."""

from __future__ import annotations

from pathlib import Path

import pytest

from stt.server import intake
from stt.server.layout import Layout

from stt.server import lifecycle
from stt.server.layout import hold
from stt.server.records import load_job

from .conftest import (
    FakeClock,
    make_wav,
    new_id,
    run_sweep,
    run_worker,
    set_to_ctime_plus,
    write_delete_request,
)
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


STRAY_NAME = "zzstrayname"


def test_deletes_and_sweeps_never_log_hints_requester_transcript_or_file_names(
    layout: Layout, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    wav = make_wav(tmp_path / "source.wav", 2.0)

    def submit(**changes: object) -> str:
        request_id = new_id()
        put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
        put_body(
            layout.inbox,
            f"{request_id}.json",
            transcribe_body(request_id, requested_by=REQUESTER, hints=[HINT], **changes),
        )
        intake.run_once(layout, clock.now)
        return request_id

    def delete(target: str, scope: str) -> None:
        write_delete_request(layout.inbox, request_id=new_id(), requested_by=REQUESTER, target=target, scope=scope)
        intake.run_once(layout, clock.now)

    clock.set("2026-03-02T14:10:00Z")
    results = submit(delete_audio="at_midnight")
    everything = submit(delete_audio="at_midnight")
    run_worker(layout, clock)
    delete(results, "result")
    delete(everything, "everything")
    midnight = submit(delete_audio="at_midnight")
    dead = submit(delete_audio="after_job")
    with hold(layout.state_lock):
        lifecycle.start(layout, load_job(layout, dead), clock.now())
    (layout.work / dead / "out").mkdir(parents=True)
    make_wav(layout.work / dead / "out" / "chunk.wav", 0.1)
    stray = put_attachment(layout.inbox, f"{STRAY_NAME}.audio.wav", wav)
    (layout.inbox / f"{STRAY_NAME}.json.tmp").write_bytes(b"{")

    run_sweep(layout, clock)
    clock.set("2026-03-02T15:00:30Z")
    run_sweep(layout, clock)
    clock.set("2026-03-03T00:59:00Z")
    run_sweep(layout, clock)
    set_to_ctime_plus(clock, stray, 61)
    run_sweep(layout, clock)

    captured = capfd.readouterr()
    output = captured.out + captured.err
    for secret in (HINT, REQUESTER, TRANSCRIPT_LINE, STRAY_NAME):
        assert secret not in output
    assert f"{dead} failed interrupted" in output
    assert f"{midnight} failed audio_deleted" in output
    assert list(layout.inbox.iterdir()) == []
