"""Small, serialisable domain objects used by the audit pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


class MeaError(Exception):
    """Base class for expected user-facing errors."""


class ConfigError(MeaError):
    """The project configuration is invalid."""


class InputError(MeaError):
    """An input file cannot be safely read or parsed."""


@dataclass(frozen=True)
class ProjectConfig:
    root: str
    config_path: str
    manuscript: str
    registry: str
    mapping: str
    output_dir: str
    ignore_years: bool = True
    require_frozen: bool = True


@dataclass(frozen=True)
class NumberToken:
    """A numeric token extracted from a document."""

    raw: str
    value: Decimal
    unit: str
    start: int
    end: int
    kind: str = "number"
    ignored_rule: str | None = None
    ignored_reason: str | None = None

    @property
    def normalized_value(self) -> str:
        return format(self.value, "f")


@dataclass(frozen=True)
class DocumentBlock:
    """A paragraph or table-cell paragraph with a stable-ish OOXML locator."""

    part: str
    kind: str
    locator: str
    text: str
    context: str
    para_id: str | None = None


@dataclass(frozen=True)
class ClaimOccurrence:
    """A non-ignored numeric token in a DOCX."""

    occurrence_id: str
    block: DocumentBlock
    token: NumberToken
    ordinal: int

    @property
    def locator(self) -> str:
        return f"{self.block.part}:{self.block.locator}"

    @property
    def context(self) -> str:
        return self.block.context

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "part": self.block.part,
            "kind": self.block.kind,
            "locator": self.locator,
            "text": self.block.text,
            "context": self.context,
            "raw": self.token.raw,
            "value": self.token.normalized_value,
            "unit": self.token.unit,
            "start": self.token.start,
            "end": self.token.end,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True)
class IgnoredOccurrence:
    """A numeric token deliberately excluded from the audit inventory."""

    occurrence_id: str
    block: DocumentBlock
    token: NumberToken
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "occurrence_id": self.occurrence_id,
            "part": self.block.part,
            "locator": f"{self.block.part}:{self.block.locator}",
            "raw": self.token.raw,
            "value": self.token.normalized_value,
            "unit": self.token.unit,
            "rule_id": self.token.ignored_rule or "I001",
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ClaimRecord:
    """One frozen result registered by the team."""

    claim_id: str
    question: str
    claim_text: str
    metric: str
    value: Decimal
    unit: str
    source_file: str
    source_column: str
    filter_text: str
    run_id: str
    status: str
    display_value: str = ""
    evidence_type: str = "model_output"
    source_sheet: str = ""
    source_cell: str = ""
    round_digits: int | None = None
    tolerance_abs: Decimal | None = None
    tolerance_rel: Decimal | None = None
    notes: str = ""


@dataclass(frozen=True)
class MappingRecord:
    occurrence_id: str
    claim_id: str
    decision: str
    context: str = ""
    confirmed_at: str = ""


@dataclass(frozen=True)
class EvidenceResolution:
    ok: bool
    message: str
    row: dict[str, str] = field(default_factory=dict)
    source_value: Decimal | None = None


@dataclass
class Finding:
    finding_id: str
    rule_id: str
    severity: str
    message: str
    occurrence_id: str = ""
    claim_id: str = ""
    locator: str = ""
    evidence_locator: str = ""
    actual: str = ""
    expected: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "occurrence_id": self.occurrence_id,
            "claim_id": self.claim_id,
            "locator": self.locator,
            "evidence_locator": self.evidence_locator,
            "actual": self.actual,
            "expected": self.expected,
            "suggestion": self.suggestion,
        }


@dataclass
class DocumentScan:
    blocks: list[DocumentBlock] = field(default_factory=list)
    occurrences: list[ClaimOccurrence] = field(default_factory=list)
    ignored: list[IgnoredOccurrence] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    occurrences: list[ClaimOccurrence]
    ignored: list[IgnoredOccurrence]
    findings: list[Finding]
    coverage: dict[str, Any]
    registry_count: int
    mapping_count: int
    passed: list[str] = field(default_factory=list)

    @property
    def blocking_count(self) -> int:
        return sum(1 for item in self.findings if item.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.findings if item.severity == "warning")
