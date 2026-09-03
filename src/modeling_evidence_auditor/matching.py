"""Shared deterministic matching rules for audit and mapping review."""

from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, DecimalException

from .models import ClaimOccurrence, ClaimRecord, InputError
from .normalize import normalize_unit


def normalize_text(value: str) -> str:
    """Normalize user-visible text for stable context and display comparisons."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def format_decimal(value: Decimal | None) -> str:
    """Render a decimal without scientific notation."""

    if value is None:
        return ""
    return format(value, "f")


def units_equal(actual: str, expected: str) -> bool:
    """Compare units using the same conservative normalization as CSV loading."""

    return normalize_unit(actual) == normalize_unit(expected)


def compare_value(actual: Decimal, claim: ClaimRecord) -> str:
    """Return exact, rounded, tolerance, or mismatch for a manuscript value."""

    numeric_inputs = {
        "论文值": actual,
        "登记值": claim.value,
        "tolerance_abs": claim.tolerance_abs,
        "tolerance_rel": claim.tolerance_rel,
    }
    for label, value in numeric_inputs.items():
        if value is not None and not value.is_finite():
            raise InputError(f"{label} 必须是有限数值")
    if actual == claim.value:
        return "exact"
    if claim.round_digits is not None:
        try:
            exponent = Decimal(1).scaleb(-claim.round_digits)
            expected_rounded = claim.value.quantize(exponent, rounding=ROUND_HALF_UP)
        except DecimalException as exc:
            raise InputError("round_digits 超出 Decimal 支持范围") from exc
        # Compare against the rounded target rather than rounding an arbitrary
        # manuscript value. Decimal-equivalent spellings such as 2.350 remain
        # valid; use display_value when exact textual precision matters.
        if actual == expected_rounded:
            return "rounded"
    try:
        difference = abs(actual - claim.value)
        if claim.tolerance_abs is not None and difference <= claim.tolerance_abs:
            return "tolerance"
        if claim.tolerance_rel is not None and claim.value != 0:
            relative = difference / abs(claim.value)
            if relative <= claim.tolerance_rel:
                return "tolerance"
    except DecimalException as exc:
        raise InputError("数值超出 Decimal 可比较范围") from exc
    return "mismatch"


def candidate_matches(
    occurrence: ClaimOccurrence, claims: list[ClaimRecord]
) -> list[tuple[ClaimRecord, str]]:
    """Return every frozen deterministic candidate, ordered by claim ID."""

    matches: list[tuple[ClaimRecord, str]] = []
    for claim in claims:
        comparison = compare_value(occurrence.token.value, claim)
        if (
            claim.status == "frozen"
            and units_equal(occurrence.token.unit, claim.unit)
            and comparison != "mismatch"
            and (
                not claim.display_value
                or normalize_text(occurrence.token.raw) == normalize_text(claim.display_value)
            )
        ):
            matches.append((claim, comparison))
    return sorted(matches, key=lambda item: item[0].claim_id)
