"""Checks that docs/server-security.md matches the unit hardening in nix/module.nix.

Both files are read as text (no Nix evaluation, no ``tomllib``); the document is read
only through its English markers, column names and status values.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "nix" / "module.nix"
DOC = ROOT / "docs" / "server-security.md"

REQUIRED_UNITS = ("stt-intake", "stt-worker", "stt-sweep", "stt-models")
BASE_GROUP = "@system-service"
MDWE_STATES = {"on", "off", "pending"}


def _balanced_block(text: str, open_index: int) -> str:
    """Returns the text between the brace at ``open_index`` and its matching brace."""
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index]
    raise AssertionError("unbalanced braces in nix/module.nix")


def _strip_comments(text: str) -> str:
    return re.sub(r"(?m)#.*$", "", text)


def _hardening_block(module: str) -> str:
    match = re.search(r"(?m)^  hardening = \{", module)
    assert match, "nix/module.nix has no `hardening` bundle"
    return _balanced_block(module, match.end() - 1)


def _unit_blocks(module: str) -> dict[str, str]:
    """Maps every `stt-*` entry of `systemd.services` to its definition text.

    The text runs to the next entry, so helper calls such as `stage "..." { ... }` are included.
    """
    section = re.search(r"systemd\.services = \{", module)
    assert section, "nix/module.nix has no systemd.services section"
    services = _balanced_block(module, section.end() - 1)
    entries = list(re.finditer(r"(?m)^( {6})(stt-[a-z@-]+) = ", services))
    blocks: dict[str, str] = {}
    for position, entry in enumerate(entries):
        end = entries[position + 1].start() if position + 1 < len(entries) else len(services)
        blocks[entry.group(2)] = services[entry.end() : end]
    return blocks


def _setting(block: str, name: str) -> str | None:
    match = re.search(rf"\b{name}\s*=\s*(.*?);", block, re.S)
    return match.group(1).strip() if match else None


def _quoted(value: str) -> list[str]:
    return re.findall(r'"([^"]*)"', value)


def _module_units() -> dict[str, dict[str, object]]:
    """Returns, per unit that carries a SystemCallFilter, the settings the module gives it."""
    module = _strip_comments(MODULE.read_text())
    hardening = _hardening_block(module)
    units: dict[str, dict[str, object]] = {}
    for name, block in _unit_blocks(module).items():
        uses_bundle = re.search(r"\bhardening\b|\bstage\b", block) is not None
        sources = [hardening, block] if uses_bundle else [block]
        syscall_filter = None
        mdwe = None
        for source in sources:
            found = _setting(source, "SystemCallFilter")
            if found is not None:
                syscall_filter = found
            found = _setting(source, "MemoryDenyWriteExecute")
            if found is not None:
                mdwe = found
        if syscall_filter is None:
            continue
        entries = _quoted(syscall_filter)
        units[name] = {
            "added": {entry for entry in entries if not entry.startswith("~") and entry != BASE_GROUP},
            "narrowed": {entry for entry in entries if entry.startswith("~")},
            "mdwe": mdwe == "true",
        }
    return units


def _table(marker: str) -> list[dict[str, str]]:
    """Parses the markdown table that follows ``<!-- marker -->`` into rows keyed by column name."""
    lines = DOC.read_text().splitlines()
    start = lines.index(f"<!-- {marker} -->")
    rows = []
    header: list[str] | None = None
    for line in lines[start + 1 :]:
        if not line.startswith("|"):
            if header is not None:
                break
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif not set("".join(cells)) <= set("-: "):
            assert len(cells) == len(header), line
            rows.append(dict(zip(header, cells)))
    return rows


def _items(cell: str) -> set[str]:
    return set() if cell in ("", "-") else set(cell.replace(",", " ").split())


@pytest.fixture(scope="module")
def module_units() -> dict[str, dict[str, object]]:
    return _module_units()


@pytest.fixture(scope="module")
def doc_rows() -> dict[str, dict[str, str]]:
    return {row["unit"]: row for row in _table("stt:syscall-table")}


def test_every_filtered_unit_has_a_row_in_the_syscall_table(module_units, doc_rows):
    expected = set(REQUIRED_UNITS) | set(module_units)
    assert expected <= set(doc_rows), sorted(expected - set(doc_rows))
    assert set(REQUIRED_UNITS) <= set(module_units), sorted(set(REQUIRED_UNITS) - set(module_units))


def test_added_syscall_groups_match_the_table_and_carry_evidence(module_units, doc_rows):
    for name, settings in module_units.items():
        row = doc_rows[name]
        assert _items(row["added"]) == settings["added"], name
        if settings["added"]:
            assert row["reason"] not in ("", "-"), name
            assert row["observed"] not in ("", "-"), name


def test_narrowed_syscall_groups_match_the_table(module_units, doc_rows):
    for name, settings in module_units.items():
        assert _items(doc_rows[name]["narrowed"]) == settings["narrowed"], name


def test_mdwe_state_matches_the_module_and_off_has_evidence(module_units, doc_rows):
    for name, settings in module_units.items():
        row = doc_rows[name]
        assert row["mdwe"] in MDWE_STATES, (name, row["mdwe"])
        if row["mdwe"] == "pending":
            assert settings["mdwe"] is True, name
        else:
            assert (row["mdwe"] == "on") == settings["mdwe"], name
        if row["mdwe"] == "off":
            assert row["mdwe_evidence"] not in ("", "-"), name
