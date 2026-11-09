"""Checks that docs/server-security.md matches the unit hardening in nix/module.nix.

Both files are read as text (no Nix evaluation, no ``tomllib``); the document is read
only through its English markers, column names and status values.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "nix" / "module.nix"
DOC = ROOT / "docs" / "server-security.md"
UV_LOCK = ROOT / "uv.lock"
FLAKE_LOCK = ROOT / "flake.lock"

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


ROLES = {"decodes-recordings", "cli-only", "decoded-audio-only"}
POLICY_SOURCES = {"ffmpeg-security", "pyav-releases", "nixpkgs-ffmpeg"}
ADVISORY_DAYS = 7
AV_COMPONENT = re.compile(r"av \(Python (3\.\d+)\)")
SINCE_CELL = re.compile(r"(\d{4}-\d{2}-\d{2}) \(([0-9a-f]{7,40})\)")
NIXPKGS_COMPONENT = "nixpkgs ffmpeg"


def _iso_date(cell: str) -> date:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cell), cell
    return date.fromisoformat(cell)


def _since_date(cell: str) -> date:
    found = SINCE_CELL.fullmatch(cell)
    assert found, cell
    return date.fromisoformat(found.group(1))


def _locked_av_versions(lock_text: str) -> dict[str, str]:
    """Maps "3.10" / "3.11" / "3.12" to the av version locked for that interpreter.

    The `av` packages are told apart by their resolution markers, not by order or by the
    absence of another marker: each entry names the interpreter series it serves.
    """
    versions: dict[str, str] = {}
    for block in lock_text.split("[[package]]")[1:]:
        if not re.search(r'(?m)^name = "av"$', block):
            continue
        version = re.search(r'(?m)^version = "([^"]+)"$', block)
        assert version, "uv.lock av entry has no version"
        markers = {
            "3.10": "python_full_version < '3.11'",
            "3.11": "python_full_version == '3.11.*'",
            "3.12": "python_full_version == '3.12.*'",
        }
        served = [name for name, marker in markers.items() if marker in block]
        assert len(served) == 1, f"av {version.group(1)} serves {served}"
        interpreter = served[0]
        assert interpreter not in versions, f"two av entries for Python {interpreter}"
        versions[interpreter] = version.group(1)
    return versions


def _locked_nixpkgs_rev(lock_text: str) -> str:
    found = re.search(r'"nixpkgs": \{\s*"locked": \{[^{}]*?"rev": "([0-9a-f]{40})"', lock_text)
    assert found, "flake.lock has no locked nixpkgs revision"
    return found.group(1)


def _missing_months(dates: list[date]) -> list[str]:
    """Returns the YYYY-MM months absent between the earliest and the latest date."""
    present = {(day.year, day.month) for day in dates}
    year, month = min(present)
    last = max(present)
    missing = []
    while (year, month) < last:
        month += 1
        if month == 13:
            year, month = year + 1, 1
        if (year, month) not in present:
            missing.append(f"{year:04d}-{month:02d}")
    return missing


def _advisory_gaps(rows: list[dict[str, str]]) -> list[str]:
    """Returns the advisories updated later than 7 days after publication without a reason."""
    gaps = []
    for row in rows:
        published = _iso_date(row["published"])
        updated = None if row["updated"] in ("", "-") else _iso_date(row["updated"])
        if row["reason"] not in ("", "-"):
            continue
        if updated is None or updated > published + timedelta(days=ADVISORY_DAYS):
            gaps.append(row["advisory"])
    return gaps


def _policy_marker() -> dict[str, str]:
    found = re.search(r"(?m)^<!-- stt:policy ([^>]*?) -->$", DOC.read_text())
    assert found, "docs/server-security.md has no stt:policy marker"
    pairs = [item.partition("=") for item in found.group(1).split()]
    return {key: value for key, _, value in pairs}


@pytest.fixture(scope="module")
def version_rows() -> list[dict[str, str]]:
    rows = _table("stt:versions")
    assert rows, "stt:versions table has no rows"
    return rows


def test_av_versions_match_uv_lock_for_every_interpreter_series(version_rows):
    locked = _locked_av_versions(UV_LOCK.read_text())
    assert set(locked) == {"3.10", "3.11", "3.12"}, locked
    documented = {
        found.group(1): row["version"]
        for row in version_rows
        if (found := AV_COMPONENT.fullmatch(row["component"]))
    }
    assert documented == locked


def test_nixpkgs_row_matches_the_flake_lock_commit(version_rows):
    rows = [row for row in version_rows if row["component"] == NIXPKGS_COMPONENT]
    assert len(rows) == 1, rows
    assert rows[0]["version"] == _locked_nixpkgs_rev(FLAKE_LOCK.read_text())
    assert rows[0]["pinned_in"] == "flake.lock"


def test_every_version_row_is_filled_and_dated_in_iso_form(version_rows):
    for row in version_rows:
        name = row["component"]
        for column in ("version", "pinned_in", "since", "last_updated"):
            assert row[column] not in ("", "-"), (name, column)
        assert row["role"] in ROLES, (name, row["role"])
        assert row["pinned_in"] in ("uv.lock", "flake.lock"), name
        assert _since_date(row["since"]) <= _iso_date(row["last_updated"]), name


def test_policy_marker_names_the_owner_cadence_deadline_and_sources():
    policy = _policy_marker()
    assert policy.get("owner") == "operator"
    assert policy.get("cadence") == "monthly"
    assert policy.get("advisory_days") == str(ADVISORY_DAYS)
    assert set(policy.get("sources", "").split(",")) == POLICY_SOURCES


def test_monthly_checks_have_a_row_and_no_missing_month():
    rows = _table("stt:monthly-checks")
    assert rows, "stt:monthly-checks table has no rows"
    for row in rows:
        assert row["sources"] not in ("", "-"), row["date"]
        assert row["result"] not in ("", "-"), row["date"]
    assert _missing_months([_iso_date(row["date"]) for row in rows]) == []


def test_every_advisory_was_applied_within_seven_days_or_has_a_reason():
    assert _advisory_gaps(_table("stt:advisories")) == []


def test_recording_decoders_were_compared_on_or_after_their_last_update(version_rows):
    decoders = [row for row in version_rows if row["role"] == "decodes-recordings"]
    assert decoders, "no component with role decodes-recordings"
    for row in decoders:
        assert _iso_date(row["comparison"]) >= _iso_date(row["last_updated"]), row["component"]


def test_missing_months_finds_gaps_across_a_year_boundary():
    days = [date(2026, 11, 9), date(2027, 1, 3)]
    assert _missing_months(days) == ["2026-12"]
    assert _missing_months([date(2026, 11, 9), date(2026, 11, 30)]) == []


def test_advisory_gaps_apply_the_seven_day_rule_and_the_reason_escape():
    base = {"advisory": "A", "component": "c", "published": "2026-04-13"}
    assert _advisory_gaps([{**base, "updated": "2026-04-20", "reason": "-"}]) == []
    assert _advisory_gaps([{**base, "updated": "2026-04-21", "reason": "-"}]) == ["A"]
    assert _advisory_gaps([{**base, "updated": "-", "reason": "-"}]) == ["A"]
    assert _advisory_gaps([{**base, "updated": "-", "reason": "text"}]) == []


def test_advisory_dates_must_be_iso_even_when_a_reason_is_given():
    base = {"advisory": "A", "component": "c", "reason": "text"}
    with pytest.raises(AssertionError):
        _advisory_gaps([{**base, "published": "13 April 2026", "updated": "-"}])
    with pytest.raises(AssertionError):
        _advisory_gaps([{**base, "published": "2026-04-13", "updated": "next week"}])
