"""Deterministic claim matching and evidence audit rules."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from .config import safe_project_path
from .csv_data import load_mapping, load_registry, resolve_csv_evidence
from .docx_extract import scan_docx
from .models import (
    AuditResult,
    ClaimOccurrence,
    ClaimRecord,
    ConfigError,
    Finding,
    ProjectConfig,
)
from .normalize import normalize_unit


def _clean_context(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _clean_display(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def _units_equal(actual: str, expected: str) -> bool:
    return normalize_unit(actual) == normalize_unit(expected)


def _compare_value(actual: Decimal, claim: ClaimRecord) -> str:
    if actual == claim.value:
        return "exact"
    if claim.round_digits is not None:
        exponent = Decimal(1).scaleb(-claim.round_digits)
        try:
            expected_rounded = claim.value.quantize(exponent, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            expected_rounded = claim.value
        # Compare against the rounded target rather than rounding an arbitrary
        # manuscript value. Decimal-equivalent spellings such as 2.350 remain
        # valid; use display_value when exact textual precision matters.
        if actual == expected_rounded:
            return "rounded"
    difference = abs(actual - claim.value)
    if claim.tolerance_abs is not None and difference <= claim.tolerance_abs:
        return "tolerance"
    if claim.tolerance_rel is not None:
        if claim.value == 0:
            relative = Decimal("Infinity")
        else:
            relative = difference / abs(claim.value)
        if relative <= claim.tolerance_rel:
            return "tolerance"
    return "mismatch"


def _candidate_suggestions(
    occurrence: ClaimOccurrence, claims: list[ClaimRecord]
) -> list[ClaimRecord]:
    return [
        claim
        for claim in claims
        if claim.status == "frozen"
        and _units_equal(occurrence.token.unit, claim.unit)
        and _compare_value(occurrence.token.value, claim) != "mismatch"
        and (
            not claim.display_value
            or _clean_display(occurrence.token.raw) == _clean_display(claim.display_value)
        )
    ]


def _finding(
    rule_id: str,
    severity: str,
    message: str,
    *,
    occurrence: ClaimOccurrence | None = None,
    claim: ClaimRecord | None = None,
    evidence_locator: str = "",
    actual: str = "",
    expected: str = "",
    suggestion: str = "",
) -> Finding:
    return Finding(
        finding_id="",
        rule_id=rule_id,
        severity=severity,
        message=message,
        occurrence_id=occurrence.occurrence_id if occurrence else "",
        claim_id=claim.claim_id if claim else "",
        locator=occurrence.locator if occurrence else "",
        evidence_locator=evidence_locator,
        actual=actual,
        expected=expected,
        suggestion=suggestion,
    )


def assign_finding_ids(findings: list[Finding]) -> list[Finding]:
    """Assign stable IDs after deterministic source/rule sorting."""

    severity_order = {"error": 0, "warning": 1, "info": 2}
    ordered = sorted(
        findings,
        key=lambda item: (
            severity_order.get(item.severity, 9),
            item.locator,
            item.occurrence_id,
            item.rule_id,
            item.claim_id,
            item.message,
        ),
    )
    counters: dict[str, int] = defaultdict(int)
    for item in ordered:
        counters[item.rule_id] += 1
        item.finding_id = f"MEA-{item.rule_id}-{counters[item.rule_id]:03d}"
    return ordered


def _safe_registry_source(root: Path, claim: ClaimRecord) -> str:
    try:
        safe_project_path(root, claim.source_file, label=f"{claim.claim_id}.source_file")
    except ConfigError as exc:
        return str(exc)
    location = claim.source_file
    if claim.source_column:
        location += f"::{claim.source_column}"
    if claim.filter_text:
        location += f"?{claim.filter_text}"
    return location


def audit_project(config: ProjectConfig) -> AuditResult:
    """Run the complete MVP audit without modifying any input."""

    scan = scan_docx(config.manuscript, ignore_years=config.ignore_years)
    claims = load_registry(config.registry)
    mappings = load_mapping(config.mapping)
    claim_by_id = {item.claim_id: item for item in claims}
    mapping_by_occurrence = {item.occurrence_id: item for item in mappings}
    occurrence_by_id = {item.occurrence_id: item for item in scan.occurrences}
    findings = list(scan.warnings)
    for ignored in scan.ignored:
        findings.append(
            Finding(
                finding_id="",
                rule_id=ignored.token.ignored_rule or "I001",
                severity="info",
                message=f"已排除数字 {ignored.token.raw.strip()!r}：{ignored.reason}",
                occurrence_id=ignored.occurrence_id,
                locator=f"{ignored.block.part}:{ignored.block.locator}",
                actual=ignored.token.normalized_value,
                suggestion="如该数字实际上是模型结果，请将其放入普通结果句并建立映射。",
            )
        )

    pass_candidates: list[str] = []
    observed_by_claim: dict[str, set[str]] = defaultdict(set)
    root = Path(config.root)
    for occurrence in scan.occurrences:
        mapping = mapping_by_occurrence.get(occurrence.occurrence_id)
        if mapping is None:
            suggestions = _candidate_suggestions(occurrence, claims)
            hint = ", ".join(item.claim_id for item in suggestions) or "无"
            findings.append(
                _finding(
                    "E001",
                    "error",
                    f"数字 {occurrence.token.raw.strip()!r} 没有已确认的证据映射",
                    occurrence=occurrence,
                    actual=f"{occurrence.token.normalized_value} {occurrence.token.unit}".strip(),
                    suggestion=f"在 claim_map.csv 中确认映射；确定性候选：{hint}。",
                )
            )
            if suggestions:
                findings.append(
                    _finding(
                        "W001",
                        "warning",
                        f"发现 {len(suggestions)} 个确定性规则候选，但工具不会自动 PASS",
                        occurrence=occurrence,
                        actual=hint,
                        suggestion="人工确认 metric、题号和证据位置后再写入 decision=confirmed。",
                    )
                )
            continue
        if _clean_context(mapping.context) != _clean_context(occurrence.context):
            findings.append(
                _finding(
                    "E007",
                    "error",
                    "映射上下文与当前文稿不一致，映射可能已过期",
                    occurrence=occurrence,
                    claim=claim_by_id.get(mapping.claim_id),
                    suggestion="重新扫描文稿并人工确认新的 occurrence_id。",
                )
            )
            continue
        if mapping.decision == "ignored":
            findings.append(
                _finding(
                    "I002",
                    "info",
                    "该候选由 claim_map.csv 显式标记为 ignored",
                    occurrence=occurrence,
                    claim=claim_by_id.get(mapping.claim_id),
                    suggestion="保留忽略理由，提交前人工复核。",
                )
            )
            continue
        claim = claim_by_id.get(mapping.claim_id)
        if claim is None:
            findings.append(
                _finding(
                    "E002",
                    "error",
                    f"映射引用不存在的 claim_id：{mapping.claim_id}",
                    occurrence=occurrence,
                    suggestion="在 frozen_numbers.csv 增加该 claim_id，或修正 claim_map.csv。",
                )
            )
            continue
        if config.require_frozen and claim.status.lower() != "frozen":
            findings.append(
                _finding(
                    "E005",
                    "error",
                    f"登记项状态为 {claim.status or '(空)'}，不是 frozen",
                    occurrence=occurrence,
                    claim=claim,
                    expected="frozen",
                    actual=claim.status,
                    suggestion="确认结果、补齐证据后再将登记项冻结。",
                )
            )
        if not claim.run_id:
            findings.append(
                _finding(
                    "E006",
                    "error",
                    "登记项缺少 run_id，无法确认结果批次",
                    occurrence=occurrence,
                    claim=claim,
                    suggestion="为该结果填写与证据文件一致的 run_id。",
                )
            )
        evidence = (
            None
            if claim.evidence_type == "assumed" and not claim.source_file
            else resolve_csv_evidence(root, claim)
        )
        evidence_locator = _safe_registry_source(root, claim)
        if evidence is not None and not evidence.ok:
            findings.append(
                _finding(
                    "E002",
                    "error",
                    f"证据无法定位：{evidence.message}",
                    occurrence=occurrence,
                    claim=claim,
                    evidence_locator=evidence_locator,
                    suggestion="修正 source_file、source_column、filter，并确认文件为 UTF-8 CSV。",
                )
            )
        elif evidence is not None:
            row_run_id = evidence.row.get("run_id", "").strip()
            if claim.run_id and not row_run_id:
                findings.append(
                    _finding(
                        "E006",
                        "error",
                        "证据行缺少非空 run_id，无法确认结果批次",
                        occurrence=occurrence,
                        claim=claim,
                        evidence_locator=evidence_locator,
                        expected=claim.run_id,
                        suggestion="在证据 CSV 中加入与冻结登记一致的 run_id。",
                    )
                )
            elif row_run_id and claim.run_id and row_run_id != claim.run_id:
                findings.append(
                    _finding(
                        "E006",
                        "error",
                        f"证据行 run_id={row_run_id!r} 与登记项 run_id={claim.run_id!r} 不一致",
                        occurrence=occurrence,
                        claim=claim,
                        evidence_locator=evidence_locator,
                        actual=row_run_id,
                        expected=claim.run_id,
                        suggestion="重新导出同一运行批次的证据，或修正登记项。",
                    )
                )
            if evidence.source_value is not None and evidence.source_value != claim.value:
                findings.append(
                    _finding(
                        "E002",
                        "error",
                        "证据单元格数值与冻结登记值不一致",
                        occurrence=occurrence,
                        claim=claim,
                        evidence_locator=evidence_locator,
                        actual=_format_decimal(evidence.source_value),
                        expected=_format_decimal(claim.value),
                        suggestion="重新冻结结果，确保证据 CSV 和登记表来自同一次运行。",
                    )
                )
        if not _units_equal(occurrence.token.unit, claim.unit):
            findings.append(
                _finding(
                    "E004",
                    "error",
                    "单位不一致：论文为 "
                    f"{occurrence.token.unit or '(无单位)'}，登记为 {claim.unit or '(无单位)'}",
                    occurrence=occurrence,
                    claim=claim,
                    actual=occurrence.token.unit,
                    expected=claim.unit,
                    suggestion="统一论文显示单位和冻结登记表单位。",
                )
            )
        comparison = _compare_value(occurrence.token.value, claim)
        observed_by_claim[claim.claim_id].add(_format_decimal(occurrence.token.value))
        if claim.display_value and _clean_display(occurrence.token.raw) != _clean_display(
            claim.display_value
        ):
            findings.append(
                _finding(
                    "E003",
                    "error",
                    "论文数值的显示形式与 display_value 不一致",
                    occurrence=occurrence,
                    claim=claim,
                    actual=_clean_display(occurrence.token.raw),
                    expected=_clean_display(claim.display_value),
                    suggestion="按冻结登记的显示值统一摘要、正文、表格和结论。",
                )
            )
        if comparison == "mismatch":
            findings.append(
                _finding(
                    "E003",
                    "error",
                    "论文数值与冻结登记值不一致",
                    occurrence=occurrence,
                    claim=claim,
                    actual=_format_decimal(occurrence.token.value),
                    expected=_format_decimal(claim.value),
                    suggestion="检查论文是否仍引用旧模型结果，或更新冻结登记并重新复核全文。",
                )
            )
        elif comparison in {"rounded", "tolerance"}:
            reason = "舍入规则" if comparison == "rounded" else "显式容差"
            findings.append(
                _finding(
                    "W002",
                    "warning",
                    f"论文数值通过{reason}匹配，但建议人工复核显示精度",
                    occurrence=occurrence,
                    claim=claim,
                    actual=_format_decimal(occurrence.token.value),
                    expected=_format_decimal(claim.value),
                )
            )
        if comparison != "mismatch":
            pass_candidates.append(occurrence.occurrence_id)

    # A mapping row that no longer exists in the current document is itself
    # evidence of an edit-induced stale mapping.  Do not silently discard it.
    for mapping in mappings:
        if mapping.occurrence_id not in occurrence_by_id:
            findings.append(
                Finding(
                    finding_id="",
                    rule_id="E007",
                    severity="error",
                    message="映射引用的 occurrence_id 在当前文稿中不存在，映射已过期",
                    occurrence_id=mapping.occurrence_id,
                    claim_id=mapping.claim_id,
                    locator=mapping.occurrence_id,
                    suggestion="重新运行 scan 并删除或修正过期映射。",
                )
            )

    conflicting_claim_ids: set[str] = set()
    for claim_id, values in sorted(observed_by_claim.items()):
        if len(values) > 1:
            conflicting_claim_ids.add(claim_id)
            claim = claim_by_id[claim_id]
            first_occurrence = next(
                occurrence
                for occurrence in scan.occurrences
                if occurrence.occurrence_id in mapping_by_occurrence
                and mapping_by_occurrence[occurrence.occurrence_id].claim_id == claim_id
            )
            findings.append(
                _finding(
                    "E009",
                    "error",
                    f"同一 claim_id 在论文中出现多个不兼容值：{', '.join(sorted(values))}",
                    occurrence=first_occurrence,
                    claim=claim,
                    actual=", ".join(sorted(values)),
                    expected=_format_decimal(claim.value),
                    suggestion="统一摘要、正文、表格和结论中的数字，并重新审计。",
                )
            )

    findings = assign_finding_ids(findings)
    blocked_occurrences = {
        item.occurrence_id for item in findings if item.severity == "error" and item.occurrence_id
    }
    passed = sorted(
        occurrence_id
        for occurrence_id in set(pass_candidates) - blocked_occurrences
        if mapping_by_occurrence[occurrence_id].claim_id not in conflicting_claim_ids
    )
    coverage = {
        "parts_scanned": sorted({block.part for block in scan.blocks}),
        "blocks_scanned": len(scan.blocks),
        "occurrences": len(scan.occurrences),
        "ignored": len(scan.ignored),
        "unsupported": sorted(scan.unsupported),
    }
    return AuditResult(
        occurrences=scan.occurrences,
        ignored=scan.ignored,
        findings=findings,
        coverage=coverage,
        registry_count=len(claims),
        mapping_count=len(mappings),
        passed=passed,
    )
