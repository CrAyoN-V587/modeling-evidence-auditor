"""Create a disposable real DOCX+CSV fixture for local CLI smoke checks.

This script is intentionally a development/example helper.  The runtime
package itself never imports python-docx or writes a manuscript.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from docx import Document

from modeling_evidence_auditor.docx_extract import scan_docx


def _write_csv(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def create_fixture(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    document_path = target / "paper" / "final.docx"
    document_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_paragraph("测试集 RMSE 为 1.23。")
    document.save(document_path)
    scan = scan_docx(document_path)
    occurrence = scan.occurrences[0]
    _write_csv(
        target / "results" / "metrics.csv",
        ["model", "split", "metric", "value", "run_id"],
        [
            {
                "model": "baseline",
                "split": "test",
                "metric": "RMSE",
                "value": "1.23",
                "run_id": "smoke-1",
            }
        ],
    )
    _write_csv(
        target / "state" / "frozen_numbers.csv",
        [
            "claim_id",
            "question",
            "claim_text",
            "metric",
            "value",
            "unit",
            "source_file",
            "source_column",
            "filter",
            "run_id",
            "status",
            "display_value",
            "evidence_type",
            "round_digits",
            "tolerance_abs",
            "tolerance_rel",
            "notes",
        ],
        [
            {
                "claim_id": "C001",
                "question": "问题一",
                "claim_text": "测试集 RMSE 为 1.23",
                "metric": "RMSE",
                "value": "1.23",
                "unit": "",
                "source_file": "results/metrics.csv",
                "source_column": "value",
                "filter": "model=baseline;split=test;metric=RMSE",
                "run_id": "smoke-1",
                "status": "frozen",
                "display_value": "1.23",
                "evidence_type": "model_output",
                "round_digits": "",
                "tolerance_abs": "",
                "tolerance_rel": "",
                "notes": "",
            }
        ],
    )
    _write_csv(
        target / "state" / "claim_map.csv",
        ["occurrence_id", "claim_id", "decision", "context", "confirmed_at"],
        [
            {
                "occurrence_id": occurrence.occurrence_id,
                "claim_id": "C001",
                "decision": "confirmed",
                "context": occurrence.context,
                "confirmed_at": "",
            }
        ],
    )
    (target / "mea.toml").write_text(
        "schema_version = 1\n"
        'manuscript = "paper/final.docx"\n'
        'registry = "state/frozen_numbers.csv"\n'
        'mapping = "state/claim_map.csv"\n'
        'output_dir = "build"\n'
        "ignore_years = true\n"
        "require_frozen = true\n",
        encoding="utf-8",
        newline="",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    create_fixture(args.target.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
