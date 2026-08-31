from __future__ import annotations

import pytest

from modeling_evidence_auditor.audit import audit_project
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
