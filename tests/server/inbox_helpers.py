"""Helpers shared by the intake tests: raw inbox writers and response readers."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from stt.server.layout import Layout


def make_layout(root: Path) -> Layout:
    """Creates the folders of a state root and returns its layout."""
    layout = Layout(root)
    layout.ensure_layout()
    return layout


def transcribe_body(request_id: str, **changes: Any) -> dict[str, Any]:
    """Builds a valid transcribe request body whose attachment is ``<request_id>.audio.wav``."""
    body: dict[str, Any] = {
        "schema_version": 1,
        "kind": "transcribe",
        "request_id": request_id,
        "requested_by": "professor-a",
        "audio": {"file": f"{request_id}.audio.wav"},
        "language": "ko",
        "diarize": False,
    }
    body.update(changes)
    return body


def put_bytes(path: Path, data: bytes, mode: int = 0o640) -> Path:
    """Writes `data` to `path` with exactly `mode`."""
    path.write_bytes(data)
    os.chmod(path, mode)
    return path


def put_body(inbox: Path, name: str, body: dict[str, Any]) -> Path:
    """Writes `body` as JSON to ``inbox/<name>``."""
    return put_bytes(inbox / name, json.dumps(body).encode("utf-8"))


def put_attachment(inbox: Path, name: str, source: Path) -> Path:
    """Copies the recording `source` to ``inbox/<name>``."""
    target = inbox / name
    shutil.copyfile(source, target)
    os.chmod(target, 0o640)
    return target


def read_response(layout: Layout, request_id: str) -> dict[str, Any]:
    """Reads ``outbox/<request_id>.json``."""
    return json.loads((layout.outbox / f"{request_id}.json").read_text(encoding="utf-8"))


def inbox_names(layout: Layout) -> list[str]:
    """Lists the names left in the inbox, sorted."""
    return sorted(path.name for path in layout.inbox.iterdir())
