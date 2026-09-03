"""Read-only mapping review derived from DOCX, registry, and canonical mapping."""

from __future__ import annotations

import os
from pathlib import Path

from .csv_data import load_mapping, load_registry
from .docx_extract import scan_docx
from .matching import candidate_matches, format_decimal, normalize_text
from .models import ClaimOccurrence, ClaimRecord, ConfigError, MappingRecord, ProjectConfig

MAPPING_REVIEW_FIELDS = [
    "schema_version",
    "row_kind",
    "mapping_state",
    "orphan_reason",
    "occurrence_id",
    "part",
    "kind",
    "locator",
    "raw",
    "value",
    "unit",
    "current_context",
    "mapping_decision",
    "mapped_claim_id",
    "mapped_claim_status",
    "mapping_context",
    "confirmed_at",
    "candidate_count",
    "candidate_claim_id",
    "candidate_match",
    "candidate_is_mapped",
    "candidate_question",
    "candidate_metric",
    "candidate_claim_text",
    "candidate_value",
    "candidate_unit",
    "candidate_display_value",
    "candidate_run_id",
    "candidate_evidence_type",
    "candidate_source_file",
    "candidate_source_column",
    "candidate_filter",
]


def _empty_row() -> dict[str, str]:
    return dict.fromkeys(MAPPING_REVIEW_FIELDS, "")


def _mapping_state(occurrence: ClaimOccurrence, mapping: MappingRecord | None, claims_by_id) -> str:
    if mapping is None:
        return "unmapped"
    if normalize_text(mapping.context) != normalize_text(occurrence.context):
        return "stale_context"
    if mapping.decision == "ignored":
        return "ignored_current"
    if mapping.claim_id not in claims_by_id:
        return "confirmed_missing_claim"
    return "confirmed_current"


def _mapping_fields(
    row: dict[str, str], mapping: MappingRecord | None, claims_by_id: dict[str, ClaimRecord]
) -> None:
    if mapping is None:
        return
    row.update(
        {
            "mapping_decision": mapping.decision,
            "mapped_claim_id": mapping.claim_id,
            "mapped_claim_status": (
                claims_by_id[mapping.claim_id].status
                if mapping.claim_id in claims_by_id
                else "missing"
                if mapping.decision == "confirmed"
                else ""
            ),
            "mapping_context": mapping.context,
            "confirmed_at": mapping.confirmed_at,
        }
    )


def _candidate_fields(
    row: dict[str, str], claim: ClaimRecord, comparison: str, mapping: MappingRecord | None
) -> None:
    row.update(
        {
            "candidate_claim_id": claim.claim_id,
            "candidate_match": comparison,
            "candidate_is_mapped": str(
                mapping is not None
                and mapping.decision == "confirmed"
                and mapping.claim_id == claim.claim_id
            ).lower(),
            "candidate_question": claim.question,
            "candidate_metric": claim.metric,
            "candidate_claim_text": claim.claim_text,
            "candidate_value": format_decimal(claim.value),
            "candidate_unit": claim.unit,
            "candidate_display_value": claim.display_value,
            "candidate_run_id": claim.run_id,
            "candidate_evidence_type": claim.evidence_type,
            "candidate_source_file": claim.source_file,
            "candidate_source_column": claim.source_column,
            "candidate_filter": claim.filter_text,
        }
    )


def _without_windows_extended_prefix(value: str) -> str:
    """Collapse Windows extended drive/UNC spellings without touching the target."""

    folded = value.casefold()
    if folded.startswith("\\\\?\\unc\\"):
        return "\\\\" + value[8:]
    if folded.startswith("\\\\?\\"):
        return value[4:]
    return value


def _normalized_path(path: str | Path) -> Path:
    value = _without_windows_extended_prefix(str(path))
    absolute = os.path.abspath(os.path.normpath(value))
    return Path(_without_windows_extended_prefix(absolute))


def _path_identity(path: str | Path) -> str:
    return os.path.normcase(str(_normalized_path(path)))


def _same_existing_file(output: Path, protected: Path) -> bool:
    try:
        output_exists = output.exists()
    except OSError as exc:
        raise ConfigError("无法确认 mapping_review.csv 与输入文件的路径身份") from exc
    if not output_exists:
        return False
    try:
        protected_exists = protected.exists()
    except OSError as exc:
        raise ConfigError("无法确认 mapping_review.csv 与输入文件的路径身份") from exc
    if not protected_exists:
        return False
    try:
        return os.path.samefile(output, protected)
    except OSError as exc:
        raise ConfigError("无法确认 mapping_review.csv 与输入文件的路径身份") from exc


def _local_source_path(root: Path, value: str) -> Path:
    if value.casefold().startswith("\\\\?\\"):
        raise ConfigError("review 的 source_file 必须是项目内相对路径")
    source = Path(value)
    if source.is_absolute() or source.drive or source.root:
        raise ConfigError("review 的 source_file 必须是项目内相对路径")
    normalized_root = _normalized_path(root)
    normalized_source = _normalized_path(normalized_root / source)
    try:
        common = os.path.commonpath(
            (_path_identity(normalized_root), _path_identity(normalized_source))
        )
    except ValueError as exc:
        raise ConfigError("review 的 source_file 必须位于项目根目录内") from exc
    if os.path.normcase(common) != _path_identity(normalized_root):
        raise ConfigError("review 的 source_file 必须位于项目根目录内")
    return normalized_source


def _validate_output_collision(
    config: ProjectConfig, claims: list[ClaimRecord], output_path: str | Path
) -> None:
    root = Path(config.root)
    protected = [
        _normalized_path(config.manuscript),
        _normalized_path(config.registry),
        _normalized_path(config.mapping),
    ]
    for claim in claims:
        if claim.source_file:
            protected.append(_local_source_path(root, claim.source_file))
    output = _normalized_path(output_path)
    output_identity = _path_identity(output)
    if any(
        output_identity == _path_identity(item) or _same_existing_file(output, item)
        for item in protected
    ):
        raise ConfigError("mapping_review.csv 输出路径与只读输入路径冲突")


def mapping_review_rows(
    config: ProjectConfig, *, output_path: str | Path | None = None
) -> list[dict[str, str]]:
    """Parse every review input and return the complete deterministic report rows."""

    claims = load_registry(config.registry)
    if output_path is not None:
        _validate_output_collision(config, claims, output_path)
    scan = scan_docx(config.manuscript, ignore_years=config.ignore_years)
    mappings = load_mapping(config.mapping)
    claims_by_id = {claim.claim_id: claim for claim in claims}
    mappings_by_occurrence = {mapping.occurrence_id: mapping for mapping in mappings}
    occurrence_ids = {occurrence.occurrence_id for occurrence in scan.occurrences}
    ignored_ids = {ignored.occurrence_id for ignored in scan.ignored}
    rows: list[dict[str, str]] = []

    for occurrence in sorted(
        scan.occurrences,
        key=lambda item: (item.block.part, item.block.locator, item.occurrence_id),
    ):
        mapping = mappings_by_occurrence.get(occurrence.occurrence_id)
        candidates = candidate_matches(occurrence, claims)
        candidate_rows = candidates or [(None, "")]
        for claim, comparison in candidate_rows:
            row = _empty_row()
            row.update(
                {
                    "schema_version": "1",
                    "row_kind": "current_occurrence",
                    "mapping_state": _mapping_state(occurrence, mapping, claims_by_id),
                    "occurrence_id": occurrence.occurrence_id,
                    "part": occurrence.block.part,
                    "kind": occurrence.block.kind,
                    "locator": occurrence.block.locator,
                    "raw": occurrence.token.raw.strip(),
                    "value": format_decimal(occurrence.token.value),
                    "unit": occurrence.token.unit,
                    "current_context": occurrence.context,
                    "candidate_count": str(len(candidates)),
                }
            )
            _mapping_fields(row, mapping, claims_by_id)
            if claim is not None:
                _candidate_fields(row, claim, comparison, mapping)
            rows.append(row)

    for mapping in sorted(mappings, key=lambda item: item.occurrence_id):
        if mapping.occurrence_id in occurrence_ids:
            continue
        row = _empty_row()
        row.update(
            {
                "schema_version": "1",
                "row_kind": "orphan_mapping",
                "mapping_state": "orphan",
                "orphan_reason": (
                    "auto_ignored_in_current_scan"
                    if mapping.occurrence_id in ignored_ids
                    else "not_in_current_scan"
                ),
                "occurrence_id": mapping.occurrence_id,
                "candidate_count": "0",
            }
        )
        _mapping_fields(row, mapping, claims_by_id)
        rows.append(row)
    return rows
