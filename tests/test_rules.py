from __future__ import annotations

import pytest

from modeling_evidence_auditor.audit import audit_project
from modeling_evidence_auditor.cli import main
from modeling_evidence_auditor.config import load_project_config
from modeling_evidence_auditor.csv_data import load_mapping, load_registry
from modeling_evidence_auditor.docx_extract import scan_docx
from modeling_evidence_auditor.models import InputError

from .conftest import (
    make_project,
    write_csv,
    write_evidence,
    write_mapping,
    write_registry,
)
from .test_docx_and_audit import _mapping_for, _registry_row


def test_mismatch_unit_unfrozen_and_missing_source_rules(tmp_path):
    project = make_project(tmp_path, ["A 为 1.20，B 为 2%，C 为 3，D 为 4。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(
        project,
        [
            _registry_row("C001", "1.23", metric="A"),
            _registry_row("C002", "2", metric="B", unit="人"),
            _registry_row("C003", "3", metric="C", status="draft"),
            {
                **_registry_row("C004", "4", metric="D"),
                "source_file": "results/not-found.csv",
            },
        ],
    )
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "A",
                "value": "1.23",
                "run_id": "run-1",
            },
            {
                "model": "baseline",
                "split": "test",
                "metric": "B",
                "value": "2",
                "run_id": "run-1",
            },
            {
                "model": "baseline",
                "split": "test",
                "metric": "C",
                "value": "3",
                "run_id": "run-1",
            },
        ],
    )
    _mapping_for(project, scan, ["C001", "C002", "C003", "C004"])
    result = audit_project(load_project_config(project / "mea.toml"))
    rules = {item.rule_id for item in result.findings}
    assert {"E002", "E003", "E004", "E005"}.issubset(rules)
    assert result.passed == []


def test_same_claim_with_incompatible_occurrences_triggers_e009(tmp_path):
    project = make_project(tmp_path, ["摘要结果为 1.23，正文结果为 1.24。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(project, [_registry_row("C001", "1.23", metric="RMSE")])
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "RMSE",
                "value": "1.23",
                "run_id": "run-1",
            }
        ],
    )
    _mapping_for(project, scan, ["C001", "C001"])
    result = audit_project(load_project_config(project / "mea.toml"))
    assert any(item.rule_id == "E009" for item in result.findings)


def test_orphan_mapping_is_reported_as_stale(tmp_path):
    project = make_project(tmp_path, ["结果为 1.23。"])
    write_registry(project, [_registry_row("C001", "1.23")])
    write_mapping(
        project,
        [
            {
                "occurrence_id": "body:p-99999999:n1",
                "claim_id": "C001",
                "decision": "confirmed",
                "context": "已经删除的段落",
                "confirmed_at": "",
            }
        ],
    )
    result = audit_project(load_project_config(project / "mea.toml"))
    assert any(item.rule_id == "E007" for item in result.findings)


def test_missing_evidence_run_id_is_blocking_and_not_passed(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(project, [_registry_row("C001", "1.23", metric="RMSE")])
    write_csv(
        project / "results" / "metrics.csv",
        ["model", "split", "metric", "value"],
        [{"model": "baseline", "split": "test", "metric": "RMSE", "value": "1.23"}],
    )
    _mapping_for(project, scan, ["C001"])
    result = audit_project(load_project_config(project / "mea.toml"))
    assert any(item.rule_id == "E006" for item in result.findings)
    assert result.passed == []


def test_mismatched_evidence_run_id_is_blocking_and_not_passed(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(
        project,
        [_registry_row("C001", "1.23", metric="RMSE", run_id="frozen-run")],
    )
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "RMSE",
                "value": "1.23",
                "run_id": "other-run",
            }
        ],
    )
    _mapping_for(project, scan, ["C001"])
    result = audit_project(load_project_config(project / "mea.toml"))
    assert any(item.rule_id == "E006" for item in result.findings)
    assert result.passed == []


def test_distinct_claims_may_use_distinct_verified_runs(tmp_path):
    project = make_project(tmp_path, ["问题一结果为 1.23，问题二结果为 4.56。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(
        project,
        [
            _registry_row("C001", "1.23", metric="A", run_id="run-a"),
            _registry_row("C002", "4.56", metric="B", run_id="run-b"),
        ],
    )
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "A",
                "value": "1.23",
                "run_id": "run-a",
            },
            {
                "model": "baseline",
                "split": "test",
                "metric": "B",
                "value": "4.56",
                "run_id": "run-b",
            },
        ],
    )
    _mapping_for(project, scan, ["C001", "C002"])
    result = audit_project(load_project_config(project / "mea.toml"))
    assert not any(item.rule_id == "E006" for item in result.findings)
    assert set(result.passed) == {item.occurrence_id for item in scan.occurrences}


def test_registry_and_mapping_reject_ambiguous_audit_contracts(tmp_path):
    project = make_project(tmp_path, ["结果为 1.23。"])
    write_registry(
        project,
        [
            _registry_row(
                "C001",
                "1.23",
                round_digits="2",
                tolerance_abs="0.01",
            )
        ],
    )
    with pytest.raises(InputError, match="同时设置"):
        load_registry(project / "state" / "frozen_numbers.csv")

    write_mapping(
        project,
        [
            {
                "occurrence_id": "body:p-00000001:n1",
                "claim_id": "",
                "decision": "ignored",
                "context": "",
                "confirmed_at": "",
            }
        ],
    )
    with pytest.raises(InputError, match="缺少 context"):
        load_mapping(project / "state" / "claim_map.csv")


def test_w005_requires_a_current_context_valid_confirmed_mapping(tmp_path):
    project = make_project(tmp_path, ["候选 1，忽略 2，过期 3，缺失引用 5。"])
    scan = scan_docx(project / "paper" / "final.docx")
    occurrences = {item.token.raw.strip(): item for item in scan.occurrences}
    write_registry(
        project,
        [
            _registry_row("C-CANDIDATE", "1"),
            _registry_row("C-IGNORED", "2"),
            _registry_row("C-STALE", "3"),
            _registry_row("C-ORPHAN", "4"),
            _registry_row("C-MISSING-DOES-NOT-COUNT", "5"),
        ],
    )
    write_mapping(
        project,
        [
            {
                "occurrence_id": occurrences["2"].occurrence_id,
                "claim_id": "",
                "decision": "ignored",
                "context": occurrences["2"].context,
                "confirmed_at": "",
            },
            {
                "occurrence_id": occurrences["3"].occurrence_id,
                "claim_id": "C-STALE",
                "decision": "confirmed",
                "context": "旧上下文",
                "confirmed_at": "",
            },
            {
                "occurrence_id": "body:p-99999999:n1",
                "claim_id": "C-ORPHAN",
                "decision": "confirmed",
                "context": "已删除的 4",
                "confirmed_at": "",
            },
            {
                "occurrence_id": occurrences["5"].occurrence_id,
                "claim_id": "MISSING",
                "decision": "confirmed",
                "context": occurrences["5"].context,
                "confirmed_at": "",
            },
        ],
    )
    result = audit_project(load_project_config(project / "mea.toml"))
    assert {item.claim_id for item in result.findings if item.rule_id == "W005"} == {
        "C-CANDIDATE",
        "C-IGNORED",
        "C-STALE",
        "C-ORPHAN",
        "C-MISSING-DOES-NOT-COUNT",
    }


def test_valid_confirmed_mapping_suppresses_w005_even_when_audit_has_e003_e004_e006(tmp_path):
    project = make_project(tmp_path, ["结果为 1%。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(
        project,
        [_registry_row("C001", "2", unit="人", run_id="frozen-run")],
    )
    write_csv(
        project / "results" / "metrics.csv",
        ["model", "split", "metric", "value"],
        [{"model": "baseline", "split": "test", "metric": "metric", "value": "2"}],
    )
    _mapping_for(project, scan, ["C001"])
    result = audit_project(load_project_config(project / "mea.toml"))
    rules = {item.rule_id for item in result.findings}
    assert {"E003", "E004", "E006"}.issubset(rules)
    assert "W005" not in rules


def test_w005_only_applies_to_unused_frozen_claims_and_does_not_block(tmp_path):
    project = make_project(tmp_path, ["本段没有数值。"])
    write_registry(
        project,
        [
            _registry_row("C-FROZEN", "1"),
            _registry_row("C-DRAFT", "2", status="draft"),
            _registry_row("C-RETIRED", "3", status="retired"),
        ],
    )
    result = audit_project(load_project_config(project / "mea.toml"))
    assert [(item.rule_id, item.claim_id) for item in result.findings] == [
        ("W005", "C-FROZEN")
    ]
    assert result.blocking_count == 0
    assert main(["audit", "--config", str(project / "mea.toml")]) == 0


@pytest.mark.parametrize(
    ("field", "non_finite"),
    [
        ("value", "Infinity"),
        ("value", "-Infinity"),
        ("value", "sNaN"),
        ("tolerance_abs", "Infinity"),
        ("tolerance_abs", "NaN"),
        ("tolerance_rel", "-Infinity"),
    ],
)
def test_registry_rejects_non_finite_decimals_before_review_or_audit(
    tmp_path, field, non_finite
):
    project = make_project(tmp_path, ["结果为 1。"])
    row = _registry_row("C001", "0")
    row[field] = non_finite
    write_registry(project, [row])

    with pytest.raises(InputError, match="有限数值"):
        load_registry(project / "state" / "frozen_numbers.csv")
    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert main(["audit", "--config", str(project / "mea.toml")]) == 2
    assert not (project / "build").exists()


@pytest.mark.parametrize("source_value", ["Infinity", "-Infinity", "NaN", "sNaN"])
def test_audit_rejects_non_finite_evidence_while_review_does_not_read_it(
    tmp_path, source_value
):
    project = make_project(tmp_path, ["结果为 1。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(project, [_registry_row("C001", "1")])
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "metric",
                "value": source_value,
                "run_id": "run-1",
            }
        ],
    )
    _mapping_for(project, scan, ["C001"])

    assert main(["review", "--config", str(project / "mea.toml")]) == 0
    with pytest.raises(InputError, match="有限数值"):
        audit_project(load_project_config(project / "mea.toml"))
    assert main(["audit", "--config", str(project / "mea.toml")]) == 2
    assert not (project / "build" / "audit.json").exists()


def test_extreme_round_digits_becomes_input_error_instead_of_decimal_exception(tmp_path):
    project = make_project(tmp_path, ["结果为 1。"])
    write_registry(
        project,
        [_registry_row("C001", "2", round_digits="999999999999999999999")],
    )

    assert main(["review", "--config", str(project / "mea.toml")]) == 2
    assert main(["audit", "--config", str(project / "mea.toml")]) == 2


def test_audit_keeps_e002_semantics_for_absolute_source_file(tmp_path):
    project = make_project(tmp_path, ["结果为 1。"])
    scan = scan_docx(project / "paper" / "final.docx")
    evidence = write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "metric",
                "value": "1",
                "run_id": "run-1",
            }
        ],
    )
    row = _registry_row("C001", "1")
    row["source_file"] = str(evidence)
    write_registry(project, [row])
    _mapping_for(project, scan, ["C001"])

    result = audit_project(load_project_config(project / "mea.toml"))
    assert any(item.rule_id == "E002" for item in result.findings)
    assert main(["audit", "--config", str(project / "mea.toml")]) == 1
