from __future__ import annotations

import json

from modeling_evidence_auditor.cli import main
from modeling_evidence_auditor.docx_extract import scan_docx

from .conftest import make_project, write_evidence, write_mapping, write_registry
from .test_docx_and_audit import _mapping_for, _registry_row


def test_cli_exit_codes_and_stable_read_only_reports(tmp_path, capsys):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
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
    _mapping_for(project, scan, ["C001"])
    inputs = [
        project / "paper" / "final.docx",
        project / "state" / "frozen_numbers.csv",
        project / "state" / "claim_map.csv",
        project / "results" / "metrics.csv",
    ]
    before = {path: path.read_bytes() for path in inputs}
    assert main(["audit", "--config", str(project / "mea.toml")]) == 0
    report = project / "build" / "audit.json"
    first = report.read_bytes()
    assert main(["audit", "--config", str(project / "mea.toml")]) == 0
    assert report.read_bytes() == first
    assert {path: path.read_bytes() for path in inputs} == before
    report_data = json.loads(report.read_text(encoding="utf-8"))
    assert report_data["findings"] == []

    # A failed audit produces a deterministic finding that can be explained.
    write_mapping(project, [])
    assert main(["audit", "--config", str(project / "mea.toml")]) == 1
    failed = json.loads(report.read_text(encoding="utf-8"))
    finding_id = failed["findings"][0]["finding_id"]
    assert main(["explain", finding_id, "--report", str(report)]) == 0
    assert finding_id in capsys.readouterr().out


def test_cli_audit_returns_one_for_unmapped_and_two_for_invalid_config(tmp_path):
    project = make_project(tmp_path, ["RMSE 为 1.23。"])
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
    assert main(["audit", "--config", str(project / "mea.toml")]) == 1
    bad_config = project / "bad.toml"
    bad_config.write_text(
        'schema_version = 1\nmanuscript = "../escape.docx"\nregistry = "state/frozen_numbers.csv"\n'
        'mapping = "state/claim_map.csv"\noutput_dir = "build"\n',
        encoding="utf-8",
    )
    assert main(["doctor", "--config", str(bad_config)]) == 2
    assert main(["scan", "--config", str(project / "mea.toml"), "--out", "../outside"]) == 2


def test_init_creates_template_without_overwriting_existing_files(tmp_path):
    project = tmp_path / "new-project"
    assert main(["init", str(project)]) == 0
    assert (project / "mea.toml").is_file()
    assert main(["init", str(project)]) == 2
