from decimal import Decimal

import pytest

from modeling_evidence_auditor.normalize import (
    NonFiniteNumberError,
    extract_number_tokens,
    parse_decimal,
    parse_number_text,
)


def test_number_parser_uses_decimal_and_chinese_scale():
    assert parse_number_text("1.2 万人") == (Decimal("1.2"), "万人")
    assert parse_number_text("33.9％") == (Decimal("33.9"), "%")


def test_chinese_prose_without_space_before_number_is_extracted():
    included, ignored = extract_number_tokens("准确率为95%，成本为2.018亿元。")
    assert ignored == []
    assert [(item.value, item.unit) for item in included] == [
        (Decimal("95"), "%"),
        (Decimal("2.018"), "亿元"),
    ]


def test_range_endpoints_and_multiple_citations_are_not_negative_claims():
    included, ignored = extract_number_tokens("误差范围 1.2-2.4；参见文献[3,4]。")
    assert [item.value for item in included] == [Decimal("1.2"), Decimal("2.4")]
    assert [item.raw.strip() for item in ignored] == ["3", "4"]


def test_parenthesized_metric_value_remains_a_claim():
    included, ignored = extract_number_tokens("RMSE（1.23）和式（3）。")
    assert [item.raw.strip() for item in included] == ["1.23"]
    assert [item.raw.strip() for item in ignored] == ["3"]


def test_structural_numbers_and_years_are_ignored():
    included, ignored = extract_number_tokens(
        "3.1 模型建立；表 2 如下；参考文献[3]；2026年测试，RMSE 为 1.23。"
    )
    assert [item.raw.strip() for item in included] == ["1.23"]
    assert {item.ignored_rule for item in ignored} == {"I001", "I002"}


def test_section_marker_without_space_is_ignored():
    included, ignored = extract_number_tokens("3.1模型建立")
    assert included == []
    assert [item.raw.strip() for item in ignored] == ["3.1"]


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "+Infinity", "-Infinity"])
def test_decimal_parser_rejects_non_finite_values(value):
    with pytest.raises(NonFiniteNumberError, match="有限值"):
        parse_decimal(value)
