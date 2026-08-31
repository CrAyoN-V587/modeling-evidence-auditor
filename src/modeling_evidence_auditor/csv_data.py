"""UTF-8 CSV contracts for frozen claims, mappings, and evidence rows."""

from __future__ import annotations

import csv
import re
from decimal import Decimal
from pathlib import Path

from .models import (
    ClaimRecord,
    EvidenceResolution,
    InputError,
    MappingRecord,
)
from .normalize import normalize_unit, parse_decimal, parse_number_text

REGISTRY_REQUIRED = {
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
}
MAPPING_REQUIRED = {"occurrence_id", "claim_id", "decision", "context"}
ALLOWED_STATUSES = {"draft", "frozen", "retired"}
ALLOWED_EVIDENCE_TYPES = {"observed", "constructed", "assumed", "model_output", "derived"}


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = [item.strip() for item in (reader.fieldnames or [])]
            if not headers:
                raise InputError(f"CSV 缺少表头：{path}")
            rows = [
                {(key or "").strip(): (value or "").strip() for key, value in row.items()}
                for row in reader
            ]
            return headers, rows
    except UnicodeDecodeError as exc:
        raise InputError(f"CSV 必须是 UTF-8：{path}") from exc
    except OSError as exc:
        raise InputError(f"无法读取 CSV {path}：{exc}") from exc
    except csv.Error as exc:
        raise InputError(f"CSV 格式错误 {path}：{exc}") from exc


def _optional_decimal(row: dict[str, str], key: str) -> Decimal | None:
    value = row.get(key, "").strip()
    if not value:
        return None
    try:
        return parse_decimal(value)
    except ValueError as exc:
        raise InputError(f"{key} 不是有效数值：{value}") from exc


def load_registry(path: str | Path) -> list[ClaimRecord]:
    source = Path(path)
    if not source.is_file():
        raise InputError(f"找不到冻结登记表：{source}")
    headers, rows = _read_rows(source)
    missing = sorted(REGISTRY_REQUIRED - set(headers))
    if missing:
        raise InputError(f"冻结登记表缺少字段：{', '.join(missing)}")
    records: list[ClaimRecord] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        claim_id = row.get("claim_id", "").strip()
        if not claim_id:
            raise InputError(f"冻结登记表第 {row_number} 行缺少 claim_id")
        if claim_id in seen:
            raise InputError(f"冻结登记表重复 claim_id：{claim_id}")
        seen.add(claim_id)
        value_text = row.get("value", "")
        try:
            value, value_unit = parse_number_text(value_text)
        except ValueError:
            try:
                value = parse_decimal(value_text)
                value_unit = ""
            except ValueError as exc:
                raise InputError(
                    f"冻结登记表第 {row_number} 行 value 无法解析：{value_text!r}"
                ) from exc
        explicit_unit = normalize_unit(row.get("unit", ""))
        unit = explicit_unit or value_unit
        round_digits: int | None = None
        if row.get("round_digits", "").strip():
            try:
                round_digits = int(row["round_digits"])
            except ValueError as exc:
                raise InputError(f"第 {row_number} 行 round_digits 不是整数") from exc
            if round_digits < 0:
                raise InputError(f"第 {row_number} 行 round_digits 不能为负数")
        tolerance_abs = _optional_decimal(row, "tolerance_abs")
        tolerance_rel = _optional_decimal(row, "tolerance_rel")
        if tolerance_abs is not None and tolerance_abs < 0:
            raise InputError(f"第 {row_number} 行 tolerance_abs 不能为负数")
        if tolerance_rel is not None and tolerance_rel < 0:
            raise InputError(f"第 {row_number} 行 tolerance_rel 不能为负数")
        if round_digits is not None and (tolerance_abs is not None or tolerance_rel is not None):
            raise InputError(f"第 {row_number} 行不能同时设置 round_digits 和 tolerance")
        status = row.get("status", "").strip().lower()
        if status not in ALLOWED_STATUSES:
            raise InputError(f"第 {row_number} 行 status 无效：{status or '(空)'}")
        evidence_type = (row.get("evidence_type", "model_output") or "model_output").strip()
        if evidence_type not in ALLOWED_EVIDENCE_TYPES:
            raise InputError(f"第 {row_number} 行 evidence_type 无效：{evidence_type}")
        source_file = row.get("source_file", "").strip()
        notes = row.get("notes", "").strip()
        if evidence_type == "assumed":
            if not source_file and not notes:
                raise InputError(f"第 {row_number} 行 assumed 记录缺少 notes")
        elif not source_file:
            raise InputError(f"第 {row_number} 行 {evidence_type} 记录缺少 source_file")
        records.append(
            ClaimRecord(
                claim_id=claim_id,
                question=row.get("question", ""),
                claim_text=row.get("claim_text", ""),
                metric=row.get("metric", ""),
                value=value,
                unit=unit,
                source_file=source_file,
                source_column=row.get("source_column", ""),
                filter_text=row.get("filter", ""),
                run_id=row.get("run_id", ""),
                status=status,
                display_value=row.get("display_value", ""),
                evidence_type=evidence_type,
                source_sheet=row.get("source_sheet", ""),
                source_cell=row.get("source_cell", ""),
                round_digits=round_digits,
                tolerance_abs=tolerance_abs,
                tolerance_rel=tolerance_rel,
                notes=notes,
            )
        )
    return records


def load_mapping(path: str | Path) -> list[MappingRecord]:
    source = Path(path)
    if not source.is_file():
        return []
    headers, rows = _read_rows(source)
    missing = sorted(MAPPING_REQUIRED - set(headers))
    if missing:
        raise InputError(f"映射表缺少字段：{', '.join(missing)}")
    result: list[MappingRecord] = []
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        occurrence_id = row.get("occurrence_id", "")
        if not occurrence_id:
            raise InputError(f"映射表第 {row_number} 行缺少 occurrence_id")
        if occurrence_id in seen:
            raise InputError(f"映射表重复 occurrence_id：{occurrence_id}")
        seen.add(occurrence_id)
        decision = row.get("decision", "").strip().lower()
        if decision not in {"confirmed", "ignored"}:
            raise InputError(f"映射表第 {row_number} 行 decision 无效：{decision}")
        claim_id = row.get("claim_id", "").strip()
        context = row.get("context", "").strip()
        if not context:
            raise InputError(f"映射表第 {row_number} 行缺少 context，无法检测文稿编辑")
        if decision == "confirmed" and not claim_id:
            raise InputError(f"映射表第 {row_number} 行 confirmed 缺少 claim_id")
        if decision == "ignored" and claim_id:
            raise InputError(f"映射表第 {row_number} 行 ignored 的 claim_id 必须为空")
        result.append(
            MappingRecord(
                occurrence_id=occurrence_id,
                claim_id=claim_id,
                decision=decision,
                context=context,
                confirmed_at=row.get("confirmed_at", ""),
            )
        )
    return result


def parse_filter(value: str) -> dict[str, str]:
    """Parse the intentionally small `column=value;column2=value2` syntax."""

    result: dict[str, str] = {}
    if not value.strip():
        return result
    pieces = re.split(r"[;,]", value)
    for piece in pieces:
        if not piece.strip():
            continue
        if "=" not in piece:
            raise InputError(f"filter 必须使用 列=值 形式：{value!r}")
        key, expected = piece.split("=", 1)
        key = key.strip()
        if not key:
            raise InputError(f"filter 缺少列名：{value!r}")
        result[key] = expected.strip()
    return result


def resolve_csv_evidence(root: Path, claim: ClaimRecord) -> EvidenceResolution:
    """Resolve a frozen claim to exactly one evidence CSV row."""

    if not claim.source_file:
        return EvidenceResolution(False, "登记项没有 source_file")
    if Path(claim.source_file).is_absolute() or Path(claim.source_file).drive:
        return EvidenceResolution(False, "source_file 必须是相对路径")
    source = (root / claim.source_file).resolve()
    try:
        source.relative_to(root.resolve())
    except ValueError:
        return EvidenceResolution(False, "source_file 逃逸项目根目录")
    if source.suffix.lower() != ".csv":
        return EvidenceResolution(False, "MVP 证据文件必须是 UTF-8 CSV")
    if not source.is_file():
        return EvidenceResolution(False, f"证据文件不存在：{claim.source_file}")
    try:
        headers, rows = _read_rows(source)
        filters = parse_filter(claim.filter_text)
    except InputError as exc:
        return EvidenceResolution(False, str(exc))
    column = claim.source_column or "value"
    if column not in headers:
        return EvidenceResolution(False, f"证据 CSV 缺少 source_column：{column}")
    missing_filter_columns = sorted(set(filters) - set(headers))
    if missing_filter_columns:
        return EvidenceResolution(
            False, f"证据 CSV 缺少筛选列：{', '.join(missing_filter_columns)}"
        )
    matches = [
        row
        for row in rows
        if all(row.get(key, "") == expected for key, expected in filters.items())
    ]
    if not matches:
        return EvidenceResolution(False, "证据 CSV 找不到符合 filter 的行")
    if len(matches) > 1:
        return EvidenceResolution(False, f"证据 CSV 有 {len(matches)} 行符合 filter，无法唯一定位")
    row = matches[0]
    value_text = row.get(column, "")
    try:
        value, _ = parse_number_text(value_text)
    except ValueError:
        try:
            value = parse_decimal(value_text)
        except ValueError:
            return EvidenceResolution(False, f"证据单元格不是数值：{value_text!r}")
    return EvidenceResolution(True, "ok", row=row, source_value=value)
