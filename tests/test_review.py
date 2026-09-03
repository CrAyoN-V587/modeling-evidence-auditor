from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

import modeling_evidence_auditor.report as report_module
import modeling_evidence_auditor.review as review_module
from modeling_evidence_auditor.cli import main
from modeling_evidence_auditor.docx_extract import scan_docx
from modeling_evidence_auditor.review import (
    MAPPING_REVIEW_FIELDS,
    _path_identity,
    _same_existing_file,
    _without_windows_extended_prefix,
)

from .conftest import make_project, write_config, write_docx, write_mapping, write_registry
from .test_docx_and_audit import _registry_row


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _by_raw(scan):
    return {item.token.raw.strip(): item for item in scan.occurrences}


def test_review_exact_contract_states_candidates_determinism_and_read_only(tmp_path):
    project = make_project(tmp_path, ["A 为 1，B 为 2，C 为 3，D 为 4，E 为 5，F 为 6。"])
    scan = scan_docx(project / "paper" / "final.docx")
    occurrences = _by_raw(scan)
    write_registry(
        project,
        [
            _registry_row("C001-A", "1", metric="A", run_id="run-a"),
            _registry_row("C001-B", "1", metric="另一个指标", run_id="run-b"),
            _registry_row("C002", "2", metric="B", round_digits="0"),
            _registry_row("C003", "3", metric="C"),
            _registry_row("C005", "55", metric="E"),
            _registry_row("C099", "99", metric="已删除"),
        ],
    )
    write_mapping(
        project,
        [
            {
                "occurrence_id": occurrences["2"].occurrence_id,
                "claim_id": "C002",
                "decision": "confirmed",
                "context": "旧上下文",
                "confirmed_at": "2026-08-31T10:00:00+08:00",
            },
            {
                "occurrence_id": occurrences["3"].occurrence_id,
                "claim_id": "",
                "decision": "ignored",
                "context": occurrences["3"].context,
                "confirmed_at": "2026-08-31T10:01:00+08:00",
            },
            {
                "occurrence_id": occurrences["4"].occurrence_id,
                "claim_id": "MISSING",
                "decision": "confirmed",
                "context": occurrences["4"].context,
                "confirmed_at": "2026-08-31T10:02:00+08:00",
            },
            {
                "occurrence_id": occurrences["5"].occurrence_id,
                "claim_id": "C005",
                "decision": "confirmed",
                "context": "  Ａ 为 1，Ｂ 为 2，Ｃ 为 3，Ｄ 为 4，Ｅ 为 5，Ｆ 为 6。  ",
                "confirmed_at": "2026-08-31T10:03:00+08:00",
            },
            {
                "occurrence_id": "body:p-99999999:n1",
                "claim_id": "C099",
                "decision": "confirmed",
                "context": "已删除的 99",
                "confirmed_at": "2026-08-31T10:04:00+08:00",
            },
        ],
    )
    inputs = [
        project / "paper" / "final.docx",
        project / "state" / "frozen_numbers.csv",
        project / "state" / "claim_map.csv",
    ]
    before = {path: path.read_bytes() for path in inputs}

    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    report = project / "build" / "mapping_review.csv"
    first = report.read_bytes()
    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    assert report.read_bytes() == first
    assert b"\r\n" not in first
    assert not first.startswith(b"\xef\xbb\xbf")
    assert {path: path.read_bytes() for path in inputs} == before
    assert sorted(path.name for path in (project / "build").iterdir()) == ["mapping_review.csv"]

    rows = _read_rows(report)
    assert list(rows[0]) == MAPPING_REVIEW_FIELDS
    assert [row["row_kind"] for row in rows[-1:]] == ["orphan_mapping"]
    assert {row["mapping_state"] for row in rows} == {
        "unmapped",
        "stale_context",
        "ignored_current",
        "confirmed_missing_claim",
        "confirmed_current",
        "orphan",
    }

    duplicate_rows = [row for row in rows if row["raw"] == "1"]
    assert [row["candidate_claim_id"] for row in duplicate_rows] == ["C001-A", "C001-B"]
    assert {row["candidate_count"] for row in duplicate_rows} == {"2"}
    assert {row["candidate_metric"] for row in duplicate_rows} == {"A", "另一个指标"}
    assert {row["candidate_run_id"] for row in duplicate_rows} == {"run-a", "run-b"}
    assert {row["candidate_is_mapped"] for row in duplicate_rows} == {"false"}

    stale = next(row for row in rows if row["raw"] == "2")
    assert stale["candidate_claim_id"] == "C002"
    assert stale["candidate_is_mapped"] == "true"
    assert stale["mapping_context"] == "旧上下文"
    ignored = next(row for row in rows if row["raw"] == "3")
    assert ignored["candidate_claim_id"] == "C003"
    assert ignored["candidate_is_mapped"] == "false"
    confirmed_non_candidate = next(row for row in rows if row["raw"] == "5")
    assert confirmed_non_candidate["mapping_state"] == "confirmed_current"
    assert confirmed_non_candidate["mapped_claim_id"] == "C005"
    assert confirmed_non_candidate["candidate_count"] == "0"
    assert confirmed_non_candidate["candidate_claim_id"] == ""
    zero = next(row for row in rows if row["raw"] == "6")
    assert zero["candidate_count"] == "0"
    assert all(not zero[field] for field in MAPPING_REVIEW_FIELDS[18:])
    missing = next(row for row in rows if row["raw"] == "4")
    assert missing["mapping_state"] == "confirmed_missing_claim"
    assert missing["mapped_claim_status"] == "missing"
    assert next(row for row in rows if row["raw"] == "3")["mapped_claim_status"] == ""
    orphan = rows[-1]
    assert orphan["orphan_reason"] == "not_in_current_scan"
    assert orphan["occurrence_id"] == "body:p-99999999:n1"
    assert orphan["mapping_decision"] == "confirmed"
    assert orphan["mapped_claim_id"] == "C099"
    assert orphan["mapped_claim_status"] == "frozen"
    assert orphan["mapping_context"] == "已删除的 99"
    assert orphan["confirmed_at"] == "2026-08-31T10:04:00+08:00"
    assert all(not orphan[field] for field in ("part", "kind", "locator", "raw", "value", "unit"))
    assert all(not orphan[field] for field in MAPPING_REVIEW_FIELDS[18:])


@pytest.mark.parametrize("collision", ["manuscript", "registry", "mapping", "source_file"])
def test_review_rejects_case_normalized_output_collisions_without_modifying_inputs(
    tmp_path, collision
):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    manuscript = "paper/final.docx"
    registry = "state/frozen_numbers.csv"
    mapping = "state/claim_map.csv"
    output_dir = "build"
    row = _registry_row("C001", "1.23", metric="RMSE")

    if collision == "source_file":
        output_dir = "results"
        row["source_file"] = "RESULTS/./MAPPING_REVIEW.CSV"
    write_registry(project, [row])

    report = project / output_dir / "mapping_review.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    if collision == "manuscript":
        report.write_bytes((project / manuscript).read_bytes())
        manuscript = f"{output_dir}/mapping_review.csv"
    elif collision == "registry":
        report.write_bytes((project / registry).read_bytes())
        registry = f"{output_dir}/mapping_review.csv"
    elif collision == "mapping":
        report.write_bytes((project / mapping).read_bytes())
        mapping = f"{output_dir}/mapping_review.csv"
    else:
        report.write_bytes(b"must remain untouched\n")

    (project / "mea.toml").write_text(
        "schema_version = 1\n"
        f'manuscript = "{manuscript}"\n'
        f'registry = "{registry}"\n'
        f'mapping = "{mapping}"\n'
        f'output_dir = "{output_dir}"\n'
        "ignore_years = true\n"
        "require_frozen = true\n",
        encoding="utf-8",
        newline="",
    )
    protected = {
        path: path.read_bytes()
        for path in {
            project / manuscript,
            project / registry,
            project / mapping,
            project / "state" / "frozen_numbers.csv",
            report,
        }
        if path.is_file()
    }

    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert {path: path.read_bytes() for path in protected} == protected
    assert not (report.parent / "mapping_review.csv.tmp").exists()
    assert list(report.parent.glob(".mapping_review.csv.*.tmp")) == []


def test_review_replace_failure_preserves_old_report_and_cleans_unique_temp(tmp_path, monkeypatch):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    write_registry(project, [_registry_row("C001", "1.23", metric="RMSE")])
    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    report = project / "build" / "mapping_review.csv"
    before = report.read_bytes()
    temporary_paths: list[Path] = []

    def fail_replace(source, destination):
        temporary_paths.append(Path(source))
        assert Path(destination) == report
        assert Path(source).is_file()
        raise OSError("simulated replace failure")

    monkeypatch.setattr(report_module.os, "replace", fail_replace)
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert report.read_bytes() == before
    assert len({path.name for path in temporary_paths}) == 2
    assert all(not path.exists() for path in temporary_paths)
    assert not (report.parent / "mapping_review.csv.tmp").exists()


def test_windows_extended_drive_source_alias_cannot_overwrite_normal_output(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    report = project / "results" / "mapping_review.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"evidence must remain untouched\n")
    extended_source = "\\\\?\\" + str(report)
    row = _registry_row("C001", "1.23", metric="RMSE")
    row["source_file"] = extended_source
    write_registry(project, [row])

    assert main(
        ["review", "--config", str(project / "mea.toml"), "--out", "results"]
    ) == 2
    assert report.read_bytes() == b"evidence must remain untouched\n"
    assert list(report.parent.glob(".mapping_review.csv.*.tmp")) == []


@pytest.mark.parametrize(
    "source_file",
    [
        r"\\server\share\evidence.csv",
        r"\\?\UNC\server\share\evidence.csv",
        r"D:drive-relative-evidence.csv",
    ],
)
def test_review_rejects_nonlocal_source_without_probing_it(
    tmp_path, monkeypatch, source_file
):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    row = _registry_row("C001", "1.23", metric="RMSE")
    row["source_file"] = source_file
    write_registry(project, [row])
    report = project / "build" / "mapping_review.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"old report\n")
    probed_nonlocal: list[str] = []
    samefile_calls: list[tuple[object, object]] = []
    original_exists = Path.exists
    original_stat = Path.stat
    original_samefile = review_module.os.path.samefile

    def is_nonlocal(path) -> bool:
        text = str(path).casefold()
        return text.startswith("\\\\") or text.startswith("d:drive-relative")

    def spy_exists(path):
        if is_nonlocal(path):
            probed_nonlocal.append(f"exists:{path}")
        return original_exists(path)

    def spy_stat(path, *args, **kwargs):
        if is_nonlocal(path):
            probed_nonlocal.append(f"stat:{path}")
        return original_stat(path, *args, **kwargs)

    def spy_samefile(left, right):
        samefile_calls.append((left, right))
        return original_samefile(left, right)

    monkeypatch.setattr(Path, "exists", spy_exists)
    monkeypatch.setattr(Path, "stat", spy_stat)
    monkeypatch.setattr(review_module.os.path, "samefile", spy_samefile)
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert probed_nonlocal == []
    assert samefile_calls == []
    assert report.read_bytes() == b"old report\n"


def test_missing_output_short_circuits_before_protected_metadata_probe(tmp_path, monkeypatch):
    output = tmp_path / "missing" / "mapping_review.csv"
    protected = Path(r"\\server\share\evidence.csv")
    exists_calls: list[Path] = []
    original_exists = Path.exists

    def spy_exists(path):
        exists_calls.append(path)
        if path == protected:
            raise AssertionError("protected path must not be probed")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", spy_exists)
    assert _same_existing_file(output, protected) is False
    assert exists_calls == [output]


def test_review_rejects_relative_source_escape_without_samefile_probe(tmp_path, monkeypatch):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    row = _registry_row("C001", "1.23", metric="RMSE")
    row["source_file"] = "../outside/evidence.csv"
    write_registry(project, [row])
    report = project / "build" / "mapping_review.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"old report\n")

    def fail_samefile(left, right):
        raise AssertionError("samefile must not run for an escaping source")

    monkeypatch.setattr(review_module.os.path, "samefile", fail_samefile)
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert report.read_bytes() == b"old report\n"


def test_windows_extended_unc_prefix_is_normalized_without_network_access():
    extended = r"\\?\UNC\Server\Share\folder\..\mapping_review.csv"
    normal = r"\\Server\Share\mapping_review.csv"
    assert _without_windows_extended_prefix(extended) == (
        r"\\Server\Share\folder\..\mapping_review.csv"
    )
    assert _path_identity(extended) == _path_identity(normal)


def test_windows_extended_drive_identity_matches_nonexistent_normal_path(tmp_path):
    normal = tmp_path / "does-not-exist" / "mapping_review.csv"
    extended = "\\\\?\\" + str(normal)
    assert not normal.exists()
    assert _path_identity(extended) == _path_identity(normal)


def test_review_uses_samefile_to_reject_hard_link_alias(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    report = project / "results" / "mapping_review.csv"
    alias = project / "results" / "evidence-alias.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"hard-linked evidence\n")
    os.link(report, alias)
    row = _registry_row("C001", "1.23", metric="RMSE")
    row["source_file"] = "results/evidence-alias.csv"
    write_registry(project, [row])

    assert main(
        ["review", "--config", str(project / "mea.toml"), "--out", "results"]
    ) == 2
    assert report.read_bytes() == b"hard-linked evidence\n"
    assert alias.read_bytes() == b"hard-linked evidence\n"


def test_review_handles_samefile_oserror_without_overwriting(tmp_path, monkeypatch):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    write_registry(project, [_registry_row("C001", "1.23", metric="RMSE")])
    report = project / "build" / "mapping_review.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(b"old report\n")

    def fail_samefile(left, right):
        raise OSError("simulated samefile failure")

    monkeypatch.setattr(review_module.os.path, "samefile", fail_samefile)
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert report.read_bytes() == b"old report\n"


def test_review_reports_exact_rounded_and_tolerance_matches(tmp_path):
    project = make_project(tmp_path, ["A 为 1.23，B 为 2.35，C 为 10.01%。"])
    write_registry(
        project,
        [
            _registry_row("C001", "1.23", metric="A", display_value="1.23"),
            _registry_row("C002", "2.345", metric="B", round_digits="2"),
            _registry_row("C003", "10", metric="C", unit="%", tolerance_abs="0.02"),
        ],
    )
    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    rows = _read_rows(project / "build" / "mapping_review.csv")
    assert {row["candidate_claim_id"]: row["candidate_match"] for row in rows} == {
        "C001": "exact",
        "C002": "rounded",
        "C003": "tolerance",
    }


def test_review_marks_mapping_to_auto_ignored_token_as_orphan(tmp_path):
    project = make_project(tmp_path, ["2026年结果待更新。"])
    scan = scan_docx(project / "paper" / "final.docx")
    ignored = scan.ignored[0]
    write_registry(project, [_registry_row("C2026", "2026", metric="年份")])
    write_mapping(
        project,
        [
            {
                "occurrence_id": ignored.occurrence_id,
                "claim_id": "C2026",
                "decision": "confirmed",
                "context": ignored.block.context,
                "confirmed_at": "2026-08-31T11:00:00+08:00",
            }
        ],
    )
    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    rows = _read_rows(project / "build" / "mapping_review.csv")
    assert rows == [
        {
            **dict.fromkeys(MAPPING_REVIEW_FIELDS, ""),
            "schema_version": "1",
            "row_kind": "orphan_mapping",
            "mapping_state": "orphan",
            "orphan_reason": "auto_ignored_in_current_scan",
            "occurrence_id": ignored.occurrence_id,
            "mapping_decision": "confirmed",
            "mapped_claim_id": "C2026",
            "mapped_claim_status": "frozen",
            "mapping_context": ignored.block.context,
            "confirmed_at": "2026-08-31T11:00:00+08:00",
            "candidate_count": "0",
        }
    ]


def test_review_moved_number_keeps_old_orphan_and_new_unmapped(tmp_path):
    project = make_project(tmp_path, ["值为 7。"])
    old = scan_docx(project / "paper" / "final.docx").occurrences[0]
    write_registry(project, [_registry_row("C007", "7")])
    write_mapping(
        project,
        [
            {
                "occurrence_id": old.occurrence_id,
                "claim_id": "C007",
                "decision": "confirmed",
                "context": old.context,
                "confirmed_at": "",
            }
        ],
    )
    write_docx(project, ["这里没有数字。", "值为 7。"])

    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    rows = _read_rows(project / "build" / "mapping_review.csv")
    assert [(row["row_kind"], row["mapping_state"]) for row in rows] == [
        ("current_occurrence", "unmapped"),
        ("orphan_mapping", "orphan"),
    ]
    assert rows[0]["occurrence_id"] != old.occurrence_id
    assert rows[1]["occurrence_id"] == old.occurrence_id


def test_review_succeeds_without_mapping_or_evidence_and_unique_candidate_is_not_pass(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    write_registry(project, [_registry_row("C001", "1.23", metric="RMSE")])
    (project / "state" / "claim_map.csv").unlink()

    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    row = _read_rows(project / "build" / "mapping_review.csv")[0]
    assert row["mapping_state"] == "unmapped"
    assert row["candidate_count"] == "1"
    assert row["candidate_claim_id"] == "C001"
    assert row["candidate_is_mapped"] == "false"
    assert main(["audit", "--config", str(project / "mea.toml")]) == 1
    audit = json.loads((project / "build" / "audit.json").read_text(encoding="utf-8"))
    assert audit["passed_occurrences"] == []
    assert any(item["rule_id"] == "W001" for item in audit["findings"])


def test_review_errors_do_not_overwrite_existing_report(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    valid_registry = [_registry_row("C001", "1.23", metric="RMSE")]
    write_registry(project, valid_registry)
    report = project / "build" / "mapping_review.csv"
    report.parent.mkdir()
    report.write_bytes(b"existing report\n")

    (project / "state" / "frozen_numbers.csv").write_text(
        "claim_id,value\nC001,1.23,extra\n", encoding="utf-8", newline=""
    )
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert report.read_bytes() == b"existing report\n"

    write_registry(project, valid_registry)
    bad_config = project / "bad.toml"
    bad_config.write_text("schema_version = 2\n", encoding="utf-8", newline="")
    assert main(["review", "--config", str(bad_config)]) == 2
    assert report.read_bytes() == b"existing report\n"

    missing_docx_config = project / "missing-docx.toml"
    missing_docx_config.write_text(
        'schema_version = 1\nmanuscript = "paper/missing.docx"\n'
        'registry = "state/frozen_numbers.csv"\nmapping = "state/claim_map.csv"\n'
        'output_dir = "build"\n',
        encoding="utf-8",
        newline="",
    )
    assert main(["review", "--config", str(missing_docx_config)]) == 2
    assert report.read_bytes() == b"existing report\n"

    write_config(project)
    assert main(["review", "--config", str(project / "mea.toml"), "--out", "../outside"]) == 2
    assert report.read_bytes() == b"existing report\n"
