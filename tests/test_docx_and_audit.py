from __future__ import annotations

from pathlib import Path

from modeling_evidence_auditor.audit import audit_project
from modeling_evidence_auditor.config import load_project_config
from modeling_evidence_auditor.docx_extract import scan_docx

from .conftest import (
    inject_revision_insert,
    make_project,
    write_docx,
    write_evidence,
    write_mapping,
    write_registry,
)


def _registry_row(
    claim_id: str,
    value: str,
    *,
    metric: str = "metric",
    unit: str = "",
    run_id: str = "run-1",
    status: str = "frozen",
    display_value: str = "",
    round_digits: str = "",
    tolerance_abs: str = "",
    tolerance_rel: str = "",
    evidence_type: str = "model_output",
    notes: str = "",
) -> dict[str, str]:
    return {
        "claim_id": claim_id,
        "question": "问题一",
        "claim_text": f"{metric} 为 {value}",
        "metric": metric,
        "value": value,
        "unit": unit,
        "source_file": "results/metrics.csv",
        "source_column": "value",
        "filter": f"model=baseline;split=test;metric={metric}",
        "run_id": run_id,
        "status": status,
        "display_value": display_value,
        "evidence_type": evidence_type,
        "round_digits": round_digits,
        "tolerance_abs": tolerance_abs,
        "tolerance_rel": tolerance_rel,
        "notes": notes,
    }


def _mapping_for(project: Path, scan, claim_ids: list[str], *, context_override: str = ""):
    rows = []
    for occurrence, claim_id in zip(scan.occurrences, claim_ids, strict=True):
        rows.append(
            {
                "occurrence_id": occurrence.occurrence_id,
                "claim_id": claim_id,
                "decision": "confirmed",
                "context": context_override or occurrence.context,
                "confirmed_at": "2026-08-31T12:00:00+08:00",
            }
        )
    write_mapping(project, rows)


def test_real_docx_scan_covers_table_and_unsupported_objects(tmp_path):
    project = tmp_path / "中文项目"
    project.mkdir()
    path = write_docx(
        project,
        ["3.1 模型建立", "2026年结果：RMSE 为 1.23，参考文献[3]。"],
        [["MAPE 为 2.5%", "表 2"]],
        unsupported=True,
    )
    scan = scan_docx(path)
    assert any(item.token.raw.strip() == "1.23" for item in scan.occurrences)
    assert any(item.token.raw.strip() == "2.5%" for item in scan.occurrences)
    assert all(item.token.raw.strip() not in {"3.1", "2026", "3", "2"} for item in scan.occurrences)
    assert any(item.rule_id == "W003" for item in scan.warnings)
    assert scan.unsupported


def test_revision_insert_is_scanned_and_reported_truthfully(tmp_path):
    project = tmp_path / "revision-project"
    project.mkdir()
    path = write_docx(project, ["正文结果为 1.0。"])
    inject_revision_insert(path, "插入结果为 9.9。")
    scan = scan_docx(path)
    assert any(item.token.raw.strip() == "9.9" for item in scan.occurrences)
    revision = next(item for item in scan.warnings if item.rule_id == "W004")
    assert "已计入数值清单" in revision.message


def test_exact_round_and_tolerance_audit(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23，MAE 为 2.35，MAPE 为 10.01%。"])
    scan = scan_docx(project / "paper" / "final.docx")
    assert [item.token.raw.strip() for item in scan.occurrences] == ["1.23", "2.35", "10.01%"]
    write_registry(
        project,
        [
            _registry_row("C001", "1.23", metric="RMSE"),
            _registry_row("C002", "2.345", metric="MAE", round_digits="2"),
            _registry_row("C003", "10", metric="MAPE", unit="%", tolerance_abs="0.02"),
        ],
    )
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "RMSE",
                "value": "1.23",
                "run_id": "run-1",
            },
            {
                "model": "baseline",
                "split": "test",
                "metric": "MAE",
                "value": "2.345",
                "run_id": "run-1",
            },
            {
                "model": "baseline",
                "split": "test",
                "metric": "MAPE",
                "value": "10",
                "run_id": "run-1",
            },
        ],
    )
    _mapping_for(project, scan, ["C001", "C002", "C003"])
    result = audit_project(load_project_config(project / "mea.toml"))
    rules = [item.rule_id for item in result.findings]
    assert result.blocking_count == 0
    assert rules.count("W002") == 2
    assert set(result.passed) == {item.occurrence_id for item in scan.occurrences}


def test_stale_mapping_is_blocking_before_evidence_is_trusted(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(project, [_registry_row("C001", "1.23", metric="RMSE", run_id="run-1")])
    write_evidence(
        project,
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "RMSE",
                "value": "1.23",
                "run_id": "run-2",
            }
        ],
    )
    _mapping_for(project, scan, ["C001"], context_override="旧版 RMSE")
    result = audit_project(load_project_config(project / "mea.toml"))
    rules = {item.rule_id for item in result.findings}
    assert "E007" in rules
    assert result.passed == []


def test_ambiguous_candidate_is_never_auto_passed(tmp_path):
    project = make_project(tmp_path, ["结果分别为 1.23 和 1.23。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(
        project,
        [_registry_row("C001", "1.23", metric="A"), _registry_row("C002", "1.23", metric="B")],
    )
    result = audit_project(load_project_config(project / "mea.toml"))
    assert result.blocking_count == 2
    assert all(item.occurrence_id not in result.passed for item in scan.occurrences)
    assert sum(item.rule_id == "W001" for item in result.findings) == 2


def test_explicit_ignored_candidate_does_not_become_error(tmp_path):
    project = make_project(tmp_path, ["草稿中的 1.23 已明确忽略，2026年为年份。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(project, [_registry_row("C001", "1.23")])
    candidate = next(item for item in scan.occurrences if item.token.raw.strip() == "1.23")
    write_mapping(
        project,
        [
            {
                "occurrence_id": candidate.occurrence_id,
                "claim_id": "",
                "decision": "ignored",
                "context": candidate.context,
                "confirmed_at": "",
            }
        ],
    )
    result = audit_project(load_project_config(project / "mea.toml"))
    assert result.blocking_count == 0
    assert not any(item.rule_id == "E001" for item in result.findings)
    assert any(item.rule_id == "I002" for item in result.findings)


def test_display_value_is_enforced_and_overprecise_rounding_does_not_pass(tmp_path):
    project = make_project(tmp_path, ["A 为 1.230，B 为 2.349。"])
    scan = scan_docx(project / "paper" / "final.docx")
    write_registry(
        project,
        [
            _registry_row("C001", "1.23", metric="A", display_value="1.23"),
            _registry_row("C002", "2.345", metric="B", round_digits="2"),
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
                "value": "2.345",
                "run_id": "run-1",
            },
        ],
    )
    _mapping_for(project, scan, ["C001", "C002"])
    result = audit_project(load_project_config(project / "mea.toml"))
    assert sum(item.rule_id == "E003" for item in result.findings) == 2
    assert result.passed == []


def test_ignored_mapping_becomes_stale_after_context_change(tmp_path):
    project = make_project(tmp_path, ["草稿值为 999。"])
    scan = scan_docx(project / "paper" / "final.docx")
    occurrence = scan.occurrences[0]
    write_registry(project, [_registry_row("C001", "999")])
    write_mapping(
        project,
        [
            {
                "occurrence_id": occurrence.occurrence_id,
                "claim_id": "",
                "decision": "ignored",
                "context": "草稿值为 1.23。",
                "confirmed_at": "",
            }
        ],
    )
    result = audit_project(load_project_config(project / "mea.toml"))
    assert any(item.rule_id == "E007" for item in result.findings)
    assert not any(item.rule_id == "I002" for item in result.findings)
