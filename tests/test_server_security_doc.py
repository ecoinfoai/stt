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

    Handles `systemd.services = { name = ...; }` and `systemd.services."name" = ...`, quoted
    names (a template such as `"stt-reader@"` cannot be a bare attribute), and any position
    relative to the other `systemd.*` sections. The text of an entry runs to the next entry,
    so helper calls such as `stage "..." { ... }` are included.
    """
    segment_start = re.compile(r"(?m)^    systemd\.services(?:\.|\s*=)")
    segment_end = re.compile(r'(?m)^    (?!systemd\.services)[A-Za-z][\w."@-]*\s*=')
    entry = re.compile(r'(?m)^(?: {4}systemd\.services\.| {6})"?(stt-[A-Za-z0-9@_-]+)"?\s*=')
    blocks: dict[str, str] = {}
    for start in segment_start.finditer(module):
        end = segment_end.search(module, start.end())
        segment = module[start.start() : end.start() if end else len(module)]
        entries = list(entry.finditer(segment))
        for position, found in enumerate(entries):
            stop = entries[position + 1].start() if position + 1 < len(entries) else len(segment)
            blocks[found.group(1)] = segment[found.end() : stop]
    assert blocks, "nix/module.nix has no stt-* entry in systemd.services"
    return blocks


def _setting(block: str, name: str) -> str | None:
    match = re.search(rf"\b{name}\s*=\s*(.*?);", block, re.S)
    return match.group(1).strip() if match else None


def _filter_tokens(value: str) -> tuple[set[str], set[str]]:
    """Splits a Nix list of filter strings into (allowed, denied) tokens.

    A string may hold several space-separated names; a leading `~` applies to all of them.
    """
    allowed: set[str] = set()
    denied: set[str] = set()
    for item in re.findall(r'"([^"]*)"', value):
        negated = item.startswith("~")
        names = item.lstrip("~").split()
        (denied if negated else allowed).update(names)
    return allowed, denied


def _units_from(module_text: str) -> dict[str, dict[str, object]]:
    """Returns, per unit that carries a SystemCallFilter, the settings the module gives it."""
    module = _strip_comments(module_text)
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
        allowed, denied = _filter_tokens(syscall_filter)
        units[name] = {"added": allowed - {BASE_GROUP}, "narrowed": denied, "mdwe": mdwe == "true"}
    return units


def _module_units() -> dict[str, dict[str, object]]:
    return _units_from(MODULE.read_text())


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
    """Reads a table cell as a set of names; a `~` prefix on a name is ignored."""
    if cell in ("", "-"):
        return set()
    return {name.lstrip("~") for name in cell.replace(",", " ").split()}


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


SYNTHETIC_MODULE = """
{ config, lib, ... }:
let
  hardening = {
    SystemCallFilter = [ "@system-service" ];
    MemoryDenyWriteExecute = true;
  };
  stage = description: serviceConfig: { serviceConfig = hardening // serviceConfig; };
in
{
  config = {
    systemd.services = {
      stt-intake = stage "intake" {
        ExecStart = "x";
      };

      "stt-reader@" = {
        serviceConfig = {
          SystemCallFilter = [
            "@system-service"
            "~@privileged"
            "~process_vm_readv process_vm_writev io_uring_setup"
          ];
          MemoryDenyWriteExecute = true;
        };
      };

      stt-sweep = stage "sweep" {
        ExecStart = "y";
      };
    }
    // lib.optionalAttrs true {
      stt-models = {
        serviceConfig = hardening // { ExecStart = "z"; };
      };
    };

    systemd.sockets = {
      stt-reader = {
        listenStreams = [ "/run/x" ];
      };
    };

    systemd.services."stt-extra@" = {
      serviceConfig.SystemCallFilter = [ "@system-service" "mount" ];
    };
  };
}
"""


def test_collector_reads_quoted_template_names_and_multi_token_filters():
    units = _units_from(SYNTHETIC_MODULE)
    assert set(units) == {"stt-intake", "stt-reader@", "stt-sweep", "stt-models", "stt-extra@"}
    assert units["stt-reader@"] == {
        "added": set(),
        "narrowed": {"@privileged", "process_vm_readv", "process_vm_writev", "io_uring_setup"},
        "mdwe": True,
    }
    for name in ("stt-intake", "stt-sweep", "stt-models"):
        assert units[name] == {"added": set(), "narrowed": set(), "mdwe": True}, name
    assert units["stt-extra@"]["added"] == {"mount"}
    assert units["stt-extra@"]["mdwe"] is False
