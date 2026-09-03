"""Stable CSV, JSON, and Markdown report writers."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from . import __version__
from .models import AuditResult, ClaimOccurrence, DocumentScan, IgnoredOccurrence, ProjectConfig
from .review import MAPPING_REVIEW_FIELDS

CLAIMS_FIELDS = [
    "occurrence_id",
    "part",
    "kind",
    "locator",
    "text",
    "context",
    "raw",
    "value",
    "unit",
    "status",
    "ignore_rule",
    "ignore_reason",
]


def _sorted_tokens(
    occurrences: Iterable[ClaimOccurrence], ignored: Iterable[IgnoredOccurrence]
) -> list[tuple[str, str, str, str, str, str, str, str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str, str, str, str, str, str, str, str]] = []
    for item in occurrences:
        rows.append(
            (
                item.occurrence_id,
                item.block.part,
                item.block.kind,
                item.block.locator,
                item.block.text,
                item.block.context,
                item.token.raw.strip(),
                item.token.normalized_value,
                item.token.unit,
                "candidate",
                "",
                "",
            )
        )
    for item in ignored:
        rows.append(
            (
                item.occurrence_id,
                item.block.part,
                item.block.kind,
                item.block.locator,
                item.block.text,
                item.block.context,
                item.token.raw.strip(),
                item.token.normalized_value,
                item.token.unit,
                "ignored",
                item.token.ignored_rule or "I001",
                item.reason,
            )
        )
    return sorted(rows, key=lambda row: (row[1], row[3], row[0]))


def write_claims_csv(path: str | Path, scan: DocumentScan | AuditResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CLAIMS_FIELDS)
        for row in _sorted_tokens(scan.occurrences, scan.ignored):
            writer.writerow(row)


def write_mapping_review_csv(path: str | Path, rows: list[dict[str, str]]) -> None:
    """Atomically replace the derived review CSV after all rows are available."""

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=MAPPING_REVIEW_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        handle = os.fdopen(descriptor, "w", encoding="utf-8", newline="")
        descriptor = -1
        with handle:
            handle.write(output.getvalue())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _relative_config(config: ProjectConfig) -> dict[str, str | bool]:
    root = Path(config.root)
    return {
        "manuscript": str(Path(config.manuscript).relative_to(root)).replace("\\", "/"),
        "registry": str(Path(config.registry).relative_to(root)).replace("\\", "/"),
        "mapping": str(Path(config.mapping).relative_to(root)).replace("\\", "/"),
        "output_dir": str(Path(config.output_dir).relative_to(root)).replace("\\", "/"),
        "ignore_years": config.ignore_years,
        "require_frozen": config.require_frozen,
    }


def audit_json(config: ProjectConfig, result: AuditResult) -> dict[str, object]:
    counts = Counter(item.severity for item in result.findings)
    return {
        "schema_version": 1,
        "tool": "modeling-evidence-auditor",
        "version": __version__,
        "config": _relative_config(config),
        "summary": {
            "result": "fail" if result.blocking_count else "pass",
            "occurrences": len(result.occurrences),
            "ignored": len(result.ignored),
            "registry_count": result.registry_count,
            "mapping_count": result.mapping_count,
            "passed": len(result.passed),
            "errors": counts.get("error", 0),
            "warnings": counts.get("warning", 0),
            "infos": counts.get("info", 0),
        },
        "coverage": result.coverage,
        "occurrences": [item.to_dict() for item in result.occurrences],
        "passed_occurrences": result.passed,
        "ignored_occurrences": [item.to_dict() for item in result.ignored],
        "findings": [item.to_dict() for item in result.findings],
    }


def write_audit_json(path: str | Path, config: ProjectConfig, result: AuditResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(audit_json(config, result), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(content + "\n", encoding="utf-8", newline="")


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def write_audit_markdown(path: str | Path, config: ProjectConfig, result: AuditResult) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter(item.severity for item in result.findings)
    lines = [
        "# modeling-evidence-auditor 审计报告",
        "",
        f"- 配置：`{_md(Path(config.config_path).name)}`",
        f"- 论文：`{_md(Path(config.manuscript).relative_to(config.root))}`",
        f"- 证据登记：`{_md(Path(config.registry).relative_to(config.root))}`",
        "- 候选数字："
        f"{len(result.occurrences)}；已排除：{len(result.ignored)}；已通过：{len(result.passed)}",
        "- 阻断错误："
        f"{counts.get('error', 0)}；警告：{counts.get('warning', 0)}；"
        f"信息：{counts.get('info', 0)}",
        "",
        "## 覆盖范围",
        "",
        "扫描部分："
        + (
            ", ".join(f"`{_md(part)}`" for part in result.coverage.get("parts_scanned", []))
            or "无"
        ),
        f"扫描段落/单元格：{result.coverage.get('blocks_scanned', 0)}",
        "未完整支持对象："
        + (
            ", ".join(f"`{_md(item)}`" for item in result.coverage.get("unsupported", []))
            or "无"
        ),
        "",
        "## 发现",
        "",
    ]
    if not result.findings:
        lines.append("没有发现需要处理的问题。")
    else:
        lines.extend(
            [
                "| ID | 级别 | 规则 | 位置 | 证据 | 实际/预期 | 信息 | 建议 |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for finding in result.findings:
            lines.append(
                "| "
                + " | ".join(
                    _md(value)
                    for value in (
                        finding.finding_id,
                        finding.severity,
                        finding.rule_id,
                        finding.locator or "-",
                        finding.evidence_locator or "-",
                        f"{finding.actual or '-'} / {finding.expected or '-'}",
                        finding.message,
                        finding.suggestion or "-",
                    )
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## 说明",
            "",
            "本报告由确定性规则生成；工具不会自动确认模糊候选，也不会修改输入文件。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
