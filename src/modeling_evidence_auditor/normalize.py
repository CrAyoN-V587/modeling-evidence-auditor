"""Deterministic number and unit extraction for Chinese/English prose."""

from __future__ import annotations

import re
from dataclasses import replace
from decimal import Decimal, InvalidOperation

from .models import NumberToken

_NUMBER_RE = re.compile(
    # Python's ``\w`` includes Chinese characters, so it would miss the very
    # common prose form ``准确率为95%``.  Only block ASCII identifier tails.
    r"(?<![A-Za-z0-9_])"
    r"(?P<number>[+-]?(?:(?:\d{1,3}(?:,\d{3})+)|\d+)(?:\.\d+)?|[+-]?\.\d+)"
    r"(?P<exponent>[eE][+-]?\d+)?"
    r"(?P<scale>\s*(?:万|亿)?)"
    r"\s*(?P<unit>%|％|‰|百分点|人次|人|次|个|件|元|万元|千米|公里|km|kg|吨|天|小时|h)?"
)

_UNIT_ALIASES = {
    "％": "%",
    "百分比": "%",
    "人次": "人次",
    "个": "个",
    "件": "件",
    "元": "元",
    "万元": "万元",
    "千米": "km",
    "公里": "km",
    "km": "km",
    "kg": "kg",
    "吨": "吨",
    "天": "天",
    "小时": "小时",
    "h": "小时",
    "人": "人",
    "次": "次",
    "百分点": "百分点",
    "%": "%",
    "‰": "‰",
}


def normalize_unit(value: str) -> str:
    """Return a conservative, comparable unit spelling."""

    cleaned = value.strip().replace(" ", "").replace("　", "")
    if not cleaned or cleaned == "1":
        return ""
    if cleaned[0:1] in {"万", "亿"} and len(cleaned) > 1:
        return cleaned[0] + _UNIT_ALIASES.get(cleaned[1:], cleaned[1:].lower())
    return _UNIT_ALIASES.get(cleaned, cleaned.lower())


def parse_decimal(value: str) -> Decimal:
    """Parse a decimal without binary floating point."""

    cleaned = value.strip().replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"无法解析数值：{value!r}") from exc


def parse_number_text(value: str) -> tuple[Decimal, str]:
    """Parse a single number expression and return normalized value/unit."""

    match = _NUMBER_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"无法解析数值表达式：{value!r}")
    number = match.group("number") + (match.group("exponent") or "")
    parsed = parse_decimal(number)
    scale = match.group("scale").strip()
    unit = normalize_unit(scale + (match.group("unit") or ""))
    return parsed, unit


def _is_date_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 5) : start]
    after = text[end : min(len(text), end + 7)]
    surrounding = before + text[start:end] + after
    return bool(
        re.search(r"\d{2,4}\s*[-/]\s*\d{1,2}(?:\s*[-/]\s*\d{1,2})?", surrounding)
        or re.search(r"\d{4}\s*年", surrounding)
    )


def classify_token(text: str, match: re.Match[str], *, ignore_years: bool = True) -> NumberToken:
    raw = match.group(0)
    number = match.group("number") + (match.group("exponent") or "")
    # A hyphen between two numbers is normally a range separator, not a
    # negative sign.  Keep the original span for the report but normalize the
    # second endpoint as a positive number.
    stripped_raw = raw.strip()
    if stripped_raw.startswith("-") and re.search(r"\d\s*$", text[: match.start()]):
        number = number.lstrip("-")
    value = parse_decimal(number)
    scale = match.group("scale").strip()
    unit = normalize_unit(scale + (match.group("unit") or ""))
    token = NumberToken(raw=raw, value=value, unit=unit, start=match.start(), end=match.end())

    if _is_date_context(text, match.start(), match.end()):
        return replace(token, ignored_rule="I002", ignored_reason="日期组成部分")
    if ignore_years and value == value.to_integral_value() and 1900 <= value <= 2100:
        if re.search(r"(?:^|[\s（(\[])(?:19|20)\d{2}(?:年|\s|$|[，。,])", text):
            return replace(token, ignored_rule="I002", ignored_reason="年份")

    left = text[max(0, match.start() - 12) : match.start()]
    right = text[match.end() : min(len(text), match.end() + 12)]
    around = left + raw + right
    if re.search(r"(?:图|表|式|问题|第|Figure|Table|Eq\.?)\s*$", left, re.IGNORECASE):
        return replace(token, ignored_rule="I001", ignored_reason="图表、公式或问题编号")
    if re.search(r"(?:^|\s)(?:Q|S|第)\s*$", left, re.IGNORECASE):
        return replace(token, ignored_rule="I001", ignored_reason="结构编号")
    open_square = max(text.rfind("[", 0, match.start()), text.rfind("【", 0, match.start()))
    close_candidates = [
        index
        for index in (text.find("]", match.end()), text.find("】", match.end()))
        if index >= 0
    ]
    close_square = min(close_candidates) if close_candidates else -1
    if open_square >= 0 and close_square >= 0 and open_square < match.start() < close_square:
        inside = text[open_square + 1 : close_square].strip()
        if re.fullmatch(r"\d+(?:\s*[,，]\s*\d+)*", inside):
            return replace(token, ignored_rule="I001", ignored_reason="引用编号")
    open_round = max(text.rfind("(", 0, match.start()), text.rfind("（", 0, match.start()))
    close_round_candidates = [
        index
        for index in (text.find(")", match.end()), text.find("）", match.end()))
        if index >= 0
    ]
    close_round = min(close_round_candidates) if close_round_candidates else -1
    if open_round >= 0 and close_round >= 0 and open_round < match.start() < close_round:
        inside = text[open_round + 1 : close_round].strip()
        if re.fullmatch(r"\d+(?:\s*[,，]\s*\d+)*", inside):
            return replace(token, ignored_rule="I001", ignored_reason="括号中的编号")
    # A leading decimal section marker such as “3.1 模型建立” is a
    # structural number, not a quantitative claim.
    if not left.strip() and re.match(
        r"^\s*\d+\.\d+(?:\.\d+)*\s*(?=[^\d.])", around
    ):
        return replace(token, ignored_rule="I001", ignored_reason="章节编号")
    if re.search(r"(?:版本|v)\s*$", left, re.IGNORECASE):
        return replace(token, ignored_rule="I001", ignored_reason="版本编号")
    if not unit and re.search(r"^\s*(?:章|节|页)\s*$", right):
        return replace(token, ignored_rule="I001", ignored_reason="章节或页码")
    return token


def extract_number_tokens(
    text: str, *, ignore_years: bool = True
) -> tuple[list[NumberToken], list[NumberToken]]:
    """Extract all candidates, splitting ignored structural tokens."""

    included: list[NumberToken] = []
    ignored: list[NumberToken] = []
    for match in _NUMBER_RE.finditer(text):
        token = classify_token(text, match, ignore_years=ignore_years)
        (ignored if token.ignored_rule else included).append(token)
    return included, ignored
