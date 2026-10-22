"""Edge case tests: duplicate request ids, unsafe inbox files, leftovers and the daily purge."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

import pytest

from stt.server import contract, intake, lifecycle
from stt.server.layout import Layout, hold
from stt.server.records import load_job

from .conftest import FakeClock, leftovers, leftovers_besides_recordings, make_wav, new_id, run_sweep, run_worker, run_worker_once, set_to_ctime_plus, write_delete_request
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


def _deeply_nested(path: Path) -> None:
    put_bytes(path, b"[" * 200_000)


@pytest.mark.parametrize(
    "write",
    [
        _broken_json,
        _deeply_nested,
        _not_utf8,
        pytest.param(
            _unreadable,
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root can read mode 000 files"),
        ),
        _oversized,
    ],
    ids=["broken_json", "deeply_nested", "not_utf8", "unreadable", "oversized"],
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


def test_directory_named_like_a_request_is_refused_and_removed(layout: Layout) -> None:
    request_id = new_id()
    (layout.inbox / f"{request_id}.json").mkdir()

    intake.run_once(layout, FakeClock().now)

    response = read_response(layout, request_id)
    assert contract.validate_response(response) is None
    assert response["kind"] == "invalid"
    assert inbox_names(layout) == []


def test_non_empty_directory_named_like_a_request_propagates_after_the_refusal(layout: Layout) -> None:
    request_id = new_id()
    folder = layout.inbox / f"{request_id}.json"
    folder.mkdir()
    (folder / "inner").write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        intake.run_once(layout, FakeClock().now)

    assert read_response(layout, request_id)["kind"] == "invalid"
    assert folder.is_dir()


def test_directory_with_non_uuid_name_is_removed_silently(
    layout: Layout, capfd: pytest.CaptureFixture[str]
) -> None:
    (layout.inbox / "foo.json").mkdir()

    intake.run_once(layout, FakeClock().now)

    assert len((capfd.readouterr().out).splitlines()) == 1
    assert inbox_names(layout) == []
    assert list(layout.outbox.iterdir()) == []


def test_edge_2_orphan_attachment_removed_after_one_hour(
    layout: Layout, wav: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    orphan = put_attachment(layout.inbox, f"{new_id()}.audio.wav", wav)
    half_written = put_bytes(layout.inbox / f"{new_id()}.json.tmp", b"{")
    old_but_new = put_attachment(layout.inbox, f"{new_id()}.audio.wav", wav)
    two_days_ago = old_but_new.stat().st_mtime - 2 * 86400
    os.utime(old_but_new, (two_days_ago, two_days_ago))
    capfd.readouterr()

    set_to_ctime_plus(clock, orphan, 59)
    run_sweep(layout, clock)

    assert sorted(path.name for path in layout.inbox.iterdir()) == sorted(
        [orphan.name, half_written.name, old_but_new.name]
    )
    assert capfd.readouterr().out == ""

    set_to_ctime_plus(clock, old_but_new, 59)
    run_sweep(layout, clock)

    assert old_but_new.exists()

    set_to_ctime_plus(clock, old_but_new, 61)
    run_sweep(layout, clock)

    assert inbox_names(layout) == []
    lines = capfd.readouterr().out.splitlines()
    assert len(lines) == 3
    assert len(set(lines)) == 1
    for name in (orphan.name, half_written.name, old_but_new.name):
        assert name.split(".")[0] not in lines[0]


def test_stale_temp_and_orphan_audio_removed_after_one_hour(
    layout: Layout, wav: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    request_id = new_id()
    staging = layout.outbox / f".{request_id}.tmp"
    staging.mkdir()
    (staging / "result.json").write_text("{}", encoding="utf-8")
    response_tmp = put_bytes(layout.outbox / f".{request_id}.json.0123abcd.tmp", b"{")
    record_tmp = layout.jobs / request_id / ".job.json.0123abcd.tmp"
    record_tmp.parent.mkdir()
    put_bytes(record_tmp, b"{")
    orphan_audio = layout.audio / request_id
    orphan_audio.mkdir()
    shutil.copyfile(wav, orphan_audio / "audio.wav")
    capfd.readouterr()
    kept = [staging, response_tmp, record_tmp, orphan_audio]

    with hold(layout.worker_lock):
        set_to_ctime_plus(clock, staging, 59)
        run_sweep(layout, clock)
        assert all(path.exists() for path in kept)
        assert capfd.readouterr().out == ""

        set_to_ctime_plus(clock, staging, 61)
        run_sweep(layout, clock)

    assert not any(path.exists() for path in kept)
    assert leftovers(layout.root) == []
    lines = capfd.readouterr().out.splitlines()
    assert len(lines) == 4
    assert all(request_id not in line for line in lines)


def test_an_unremovable_inbox_entry_does_not_stop_the_other_stale_cleanups(
    layout: Layout, wav: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    request_id = new_id()
    stuck = put_bytes(layout.inbox / f"{request_id}.json.tmp", b"{")
    record_tmp = layout.jobs / request_id / ".job.json.0123abcd.tmp"
    record_tmp.parent.mkdir()
    put_bytes(record_tmp, b"{")
    orphan_audio = layout.audio / request_id
    orphan_audio.mkdir()
    shutil.copyfile(wav, orphan_audio / "audio.wav")
    remove = lifecycle._remove

    def remove_all_but_inbox(path: Path) -> None:
        if path.parent == layout.inbox:
            raise PermissionError("inbox entry cannot be removed")
        remove(path)

    monkeypatch.setattr(lifecycle, "_remove", remove_all_but_inbox)

    with hold(layout.worker_lock):
        set_to_ctime_plus(clock, orphan_audio, 61)
        with pytest.raises(PermissionError):
            run_sweep(layout, clock)

    assert stuck.exists()
    assert not record_tmp.exists()
    assert not orphan_audio.exists()


def test_staging_folder_is_removed_at_once_when_no_worker_runs(layout: Layout) -> None:
    clock = FakeClock()
    staging = layout.outbox / f".{new_id()}.tmp"
    staging.mkdir()
    (staging / "transcript.txt").write_text("half published\n", encoding="utf-8")

    run_sweep(layout, clock)

    assert not staging.exists()


def _submit(layout: Layout, wav: Path, clock: FakeClock, **changes: object) -> str:
    request_id = new_id()
    put_attachment(layout.inbox, f"{request_id}.audio.wav", wav)
    put_body(layout.inbox, f"{request_id}.json", transcribe_body(request_id, **changes))
    intake.run_once(layout, clock.now)
    return request_id


def test_edge_3_daily_purge_holds_without_worker(
    layout: Layout, wav: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    morning = _submit(layout, wav, clock, hints=["alpha"])
    clock.set("2026-03-02T14:57:00Z")
    evening = _submit(layout, wav, clock, hints=["alpha"])
    clock.set("2026-03-02T14:58:10Z")
    late = _submit(layout, wav, clock, hints=["alpha"])
    capfd.readouterr()

    clock.set("2026-03-02T14:58:30Z")
    run_sweep(layout, clock)

    output = capfd.readouterr().out
    for request_id in (morning, evening):
        assert f"{request_id} failed audio_deleted" in output
        assert not (layout.outbox / f"{request_id}.json").exists()
        assert not (layout.jobs / request_id).exists()
        assert not (layout.audio / request_id).exists()
        assert not (layout.audio / f"{request_id}.json").exists()
    assert read_response(layout, late)["state"] == "queued"
    assert read_response(layout, late)["queue_position"] == 1
    assert (layout.audio / late / "audio.wav").is_file()

    clock.set("2026-03-03T14:58:30Z")
    run_sweep(layout, clock)

    assert f"{late} failed audio_deleted" in capfd.readouterr().out
    for folder in (layout.outbox, layout.jobs, layout.audio, layout.queue):
        assert list(folder.iterdir()) == []
    assert leftovers(layout.root) == []


def test_rejected_refused_and_delete_responses_expire_at_the_daily_purge(layout: Layout, wav: Path) -> None:
    clock = FakeClock()
    rejected = new_id()
    put_attachment(layout.inbox, f"{rejected}.audio.wav", wav)
    put_body(layout.inbox, f"{rejected}.json", transcribe_body(rejected, language="xx"))
    intake.run_once(layout, clock.now)
    clock.advance(30)
    refused = new_id()
    put_bytes(layout.inbox / f"{refused}.json", b"{")
    intake.run_once(layout, clock.now)
    clock.advance(30)
    done = _submit(layout, wav, clock)
    run_worker(layout, clock)
    clock.advance(30)
    answer_id = new_id()
    write_delete_request(layout.inbox, request_id=answer_id, requested_by="professor-a", target=done)
    intake.run_once(layout, clock.now)
    assert read_response(layout, answer_id)["kind"] == "delete"
    clock.set("2026-03-02T14:58:20Z")
    after = new_id()
    put_bytes(layout.inbox / f"{after}.json", b"{")
    intake.run_once(layout, clock.now)
    names = (rejected, refused, answer_id)
    assert {name: read_response(layout, name)["expires_at"] for name in names} == dict.fromkeys(
        names, "2026-03-02T14:58:00Z"
    )
    assert read_response(layout, after)["expires_at"] == "2026-03-03T14:58:00Z"

    clock.set("2026-03-02T14:57:59Z")
    run_sweep(layout, clock)
    assert all((layout.outbox / f"{name}.json").is_file() for name in names)

    clock.set("2026-03-02T14:58:00Z")
    run_sweep(layout, clock)
    assert not any((layout.outbox / f"{name}.json").exists() for name in names)
    assert (layout.outbox / f"{after}.json").is_file()


def test_edge_7_second_pass_on_pending_first_runs_after_it(layout: Layout, wav: Path) -> None:
    clock = FakeClock()
    first = new_id()
    put_attachment(layout.inbox, f"{first}.audio.wav", wav)
    put_body(layout.inbox, f"{first}.json", transcribe_body(first))
    intake.run_once(layout, clock.now)
    assert read_response(layout, first)["state"] == "queued"
    second = new_id()
    body = transcribe_body(second, audio={"from_request": first})
    put_body(layout.inbox, f"{second}.json", body)

    intake.run_once(layout, clock.now)

    queued = read_response(layout, second)
    assert contract.validate_response(queued) is None
    assert (queued["state"], queued["queue_position"]) == ("queued", 2)
    assert read_response(layout, first)["queue_position"] == 1
    assert run_worker_once(layout, clock)
    assert (read_response(layout, first)["state"], read_response(layout, second)["state"]) == ("done", "queued")
    assert read_response(layout, second)["queue_position"] == 1
    assert (layout.audio / first / "audio.wav").is_file()
    assert run_worker_once(layout, clock)
    assert read_response(layout, second)["state"] == "done"
    assert (layout.audio / first / "audio.wav").is_file()
    assert leftovers_besides_recordings(layout.root) == []


def _nested(depth: int) -> object:
    value: object = "x"
    for _ in range(depth):
        value = [value]
    return value


def test_deeply_nested_hints_are_refused_and_the_next_request_is_still_answered(layout: Layout, wav: Path) -> None:
    clock = FakeClock()
    nested_id, valid_id = new_id(), new_id()
    nested = put_body(layout.inbox, f"{nested_id}.json", transcribe_body(nested_id, hints=[_nested(400), _nested(400)]))
    os.utime(nested, (1, 1))
    put_attachment(layout.inbox, f"{valid_id}.audio.wav", wav)
    put_body(layout.inbox, f"{valid_id}.json", transcribe_body(valid_id))

    intake.run_once(layout, clock.now)

    refused = read_response(layout, nested_id)
    assert contract.validate_response(refused) is None
    assert refused["kind"] == "invalid"
    assert refused["failure"] == {"reason": "invalid_request", "detail": "request: value is nested too deeply"}
    assert read_response(layout, valid_id)["state"] == "queued"
    assert inbox_names(layout) == []
