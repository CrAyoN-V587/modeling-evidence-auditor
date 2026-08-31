"""Read-only DOCX OOXML extraction with explicit coverage diagnostics."""

from __future__ import annotations

import re
import zipfile
from collections.abc import Iterable
from pathlib import Path

from lxml import etree

from .models import ClaimOccurrence, DocumentBlock, DocumentScan, Finding, IgnoredOccurrence
from .normalize import extract_number_tokens

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = {"w": W_NS, "w14": W14_NS, "m": M_NS}

_UNSUPPORTED_TAGS = {
    "txbxContent": "文本框内容",
    "drawing": "浮动对象或图形",
    "pict": "旧式图形对象",
    "object": "嵌入对象",
    "oMath": "公式对象",
    "oMathPara": "公式段落",
    "ins": "修订插入内容",
    "del": "修订删除内容",
    "instrText": "域代码",
    "sdt": "结构化文档标签",
    "customXml": "自定义 XML 内容",
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _part_name(zip_name: str) -> str:
    if zip_name == "word/document.xml":
        return "body"
    match = re.fullmatch(r"word/(header|footer)(\d+)\.xml", zip_name)
    if match:
        return f"{match.group(1)}{match.group(2)}"
    return zip_name.replace("/", ":")


def _text_from_node(node: etree._Element) -> str:
    pieces: list[str] = []

    def visit(current: etree._Element, blocked: bool = False) -> None:
        local = _local_name(current.tag) if isinstance(current.tag, str) else ""
        if local in {"txbxContent", "drawing", "pict", "object", "oMath", "oMathPara", "del"}:
            blocked = True
        if local == "t" and not blocked and current.text:
            pieces.append(current.text)
        elif local == "tab" and not blocked:
            pieces.append(" ")
        elif local in {"br", "cr"} and not blocked:
            pieces.append("\n")
        for child in current:
            visit(child, blocked)

    visit(node)
    return "".join(pieces)


def _clean_context(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _para_id(paragraph: etree._Element) -> str | None:
    return paragraph.attrib.get(f"{{{W14_NS}}}paraId") or paragraph.attrib.get(
        f"{{{W_NS}}}paraId"
    )


def _block_from_paragraph(
    paragraph: etree._Element,
    *,
    part: str,
    locator: str,
    kind: str,
    ignore_years: bool,
) -> tuple[list[ClaimOccurrence], list[IgnoredOccurrence], DocumentBlock | None]:
    text = _text_from_node(paragraph)
    context = _clean_context(text)
    if not context:
        return [], [], None
    para_id = _para_id(paragraph)
    block = DocumentBlock(
        part=part,
        kind=kind,
        locator=locator,
        text=text,
        context=context,
        para_id=para_id,
    )
    included, ignored = extract_number_tokens(text, ignore_years=ignore_years)
    all_tokens = sorted([*included, *ignored], key=lambda item: (item.start, item.end))
    token_index = {id(token): index + 1 for index, token in enumerate(all_tokens)}
    occurrences = [
        ClaimOccurrence(
            occurrence_id=f"{part}:{locator}:n{token_index[id(token)]}",
            block=block,
            token=token,
            ordinal=token_index[id(token)],
        )
        for token in included
    ]
    ignored_occurrences = [
        IgnoredOccurrence(
            occurrence_id=f"{part}:{locator}:n{token_index[id(token)]}",
            block=block,
            token=token,
            reason=token.ignored_reason or "结构性数字",
        )
        for token in ignored
    ]
    return occurrences, ignored_occurrences, block


def _direct_children(node: etree._Element, local: str) -> Iterable[etree._Element]:
    return (child for child in node if _local_name(child.tag) == local)


def _table_blocks(
    table: etree._Element,
    *,
    part: str,
    table_index: int,
    ignore_years: bool,
) -> tuple[list[ClaimOccurrence], list[IgnoredOccurrence], list[DocumentBlock]]:
    occurrences: list[ClaimOccurrence] = []
    ignored: list[IgnoredOccurrence] = []
    blocks: list[DocumentBlock] = []
    rows = list(_direct_children(table, "tr"))
    for row_index, row in enumerate(rows):
        cells = list(_direct_children(row, "tc"))
        for cell_index, cell in enumerate(cells):
            paragraphs = list(_direct_children(cell, "p"))
            for paragraph_index, paragraph in enumerate(paragraphs):
                locator = f"table{table_index}:r{row_index}:c{cell_index}:p{paragraph_index}"
                found, found_ignored, block = _block_from_paragraph(
                    paragraph,
                    part=part,
                    locator=locator,
                    kind="table-cell",
                    ignore_years=ignore_years,
                )
                occurrences.extend(found)
                ignored.extend(found_ignored)
                if block:
                    blocks.append(block)
    return occurrences, ignored, blocks


def _coverage_findings(root: etree._Element, part: str) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    unsupported: list[str] = []
    for tag, label in _UNSUPPORTED_TAGS.items():
        count = len(root.xpath(f".//w:{tag}", namespaces=NS))
        if tag.startswith("oMath"):
            count += len(root.xpath(f".//m:{tag}", namespaces=NS))
        if count:
            unsupported.append(f"{part}:{label}({count})")
            rule = "W004" if tag in {"ins", "del"} else "W003"
            if tag == "ins":
                message = (
                    f"{part} 包含修订插入内容；其中可见文本已计入数值清单，"
                    "但工具不能判断修订是否已被接受"
                )
                suggestion = "提交前在 Word 中接受或拒绝全部修订，再重新审计。"
            elif tag == "del":
                message = f"{part} 包含修订删除内容；删除文本未计入数值清单"
                suggestion = "提交前在 Word 中接受或拒绝全部修订，再重新审计。"
            else:
                message = f"{part} 包含未完整审计的{label}，相关内容可能未计入数值清单"
                suggestion = "将重要数字放入普通段落或顶层表格，并人工复核未覆盖对象。"
            findings.append(
                Finding(
                    finding_id="",
                    rule_id=rule,
                    severity="warning",
                    message=message,
                    locator=part,
                    suggestion=suggestion,
                )
            )
    nested_tables = len(root.xpath(".//w:tc//w:tbl", namespaces=NS))
    if nested_tables:
        unsupported.append(f"{part}:嵌套表格({nested_tables})")
        findings.append(
            Finding(
                finding_id="",
                rule_id="W003",
                severity="warning",
                message=f"{part} 包含 {nested_tables} 个嵌套表格，MVP 只审计顶层表格单元格",
                locator=part,
                suggestion="将需要审计的数字移到顶层表格或普通段落。",
            )
        )
    return findings, unsupported


def _parse_xml(data: bytes, name: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
    try:
        return etree.fromstring(data, parser=parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValueError(f"无法解析 DOCX XML {name}：{exc}") from exc


def scan_docx(path: str | Path, *, ignore_years: bool = True) -> DocumentScan:
    """Extract supported DOCX text and report unsupported OOXML explicitly."""

    source = Path(path)
    if not source.is_file():
        raise ValueError(f"找不到 DOCX：{source}")
    if source.suffix.lower() != ".docx":
        raise ValueError(f"文稿必须是 .docx：{source}")
    scan = DocumentScan()
    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"无法读取 DOCX 压缩包 {source}：{exc}") from exc
    with archive:
        archive_names = set(archive.namelist())
        names = sorted(
            name
            for name in archive_names
            if re.fullmatch(r"word/(?:document|header\d+|footer\d+)\.xml", name)
        )
        if "word/document.xml" not in names:
            raise ValueError("DOCX 缺少 word/document.xml")
        for unsupported_name, label in (
            ("word/footnotes.xml", "脚注"),
            ("word/endnotes.xml", "尾注"),
        ):
            if unsupported_name in archive_names:
                scan.unsupported.append(f"{unsupported_name.replace('/', ':')}:{label}")
                scan.warnings.append(
                    Finding(
                        finding_id="",
                        rule_id="W003",
                        severity="warning",
                        message=f"DOCX 包含未扫描的{label}部分，相关数字不会进入数值清单",
                        locator=unsupported_name,
                        suggestion="将需要审计的数字复制到正文或顶层表格。",
                    )
                )
        for xml_name in names:
            part = _part_name(xml_name)
            root = _parse_xml(archive.read(xml_name), xml_name)
            coverage, unsupported = _coverage_findings(root, part)
            scan.warnings.extend(coverage)
            scan.unsupported.extend(unsupported)
            container = root.find("w:body", namespaces=NS) if part == "body" else root
            if container is None:
                continue
            paragraph_index = 0
            table_index = 0
            for child in container:
                local = _local_name(child.tag)
                if local == "p":
                    para_id = _para_id(child)
                    paragraph_index += 1
                    locator = f"p-{para_id or paragraph_index:0>8}"
                    found, found_ignored, block = _block_from_paragraph(
                        child,
                        part=part,
                        locator=locator,
                        kind="paragraph",
                        ignore_years=ignore_years,
                    )
                    scan.occurrences.extend(found)
                    scan.ignored.extend(found_ignored)
                    if block:
                        scan.blocks.append(block)
                elif local == "tbl":
                    table_index += 1
                    found, found_ignored, blocks = _table_blocks(
                        child,
                        part=part,
                        table_index=table_index,
                        ignore_years=ignore_years,
                    )
                    scan.occurrences.extend(found)
                    scan.ignored.extend(found_ignored)
                    scan.blocks.extend(blocks)
    return scan
