"""The `mea` command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audit import audit_project
from .config import load_project_config, safe_project_path, write_default_project
from .csv_data import load_mapping, load_registry
from .docx_extract import scan_docx
from .models import ConfigError, InputError, MeaError
from .report import (
    write_audit_json,
    write_audit_markdown,
    write_claims_csv,
    write_mapping_review_csv,
)
from .review import mapping_review_rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mea",
        description="离线、确定性的 DOCX 数值证据审计器",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="创建项目配置和 CSV 模板")
    init.add_argument("project", nargs="?", default=".", help="目标项目目录")

    for name, help_text in (
        ("doctor", "检查配置、输入文件和可解析能力"),
        ("scan", "提取 DOCX 数值清单"),
        ("review", "生成映射复核工作表"),
        ("audit", "执行完整证据审计"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--config", default="mea.toml", help="项目配置 TOML")
        if name in {"scan", "review", "audit"}:
            command.add_argument("--out", help="相对项目根目录的报告目录")

    explain = sub.add_parser("explain", help="解释 audit.json 中的一条发现")
    explain.add_argument("finding_id", help="例如 MEA-E003-001")
    explain.add_argument("--report", default="build/audit.json", help="audit.json 路径")
    return parser


def _output_dir(config, override: str | None) -> Path:
    if override is None:
        return Path(config.output_dir)
    return safe_project_path(Path(config.root), override, label="--out")


def _cmd_init(project: str) -> int:
    target = Path(project).expanduser().resolve()
    try:
        files = write_default_project(target)
    except (OSError, ConfigError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"已创建项目模板：{target}")
    for path in files:
        print(f"  {path.relative_to(target)}")
    return 0


def _cmd_doctor(config_path: str) -> int:
    try:
        config = load_project_config(config_path)
        manuscript = Path(config.manuscript)
        registry = Path(config.registry)
        if not manuscript.is_file():
            raise InputError(f"找不到 manuscript：{manuscript}")
        if not registry.is_file():
            raise InputError(f"找不到 registry：{registry}")
        scan = scan_docx(manuscript, ignore_years=config.ignore_years)
        records = load_registry(registry)
        mappings = load_mapping(config.mapping)
    except (MeaError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print("doctor 通过")
    print(f"  论文：{manuscript}")
    print(f"  冻结记录：{len(records)}")
    print(f"  映射记录：{len(mappings)}")
    print(f"  数值候选：{len(scan.occurrences)}；排除：{len(scan.ignored)}")
    if not Path(config.mapping).is_file():
        print("  警告：claim_map.csv 尚不存在，audit 将把未映射数字报告为 E001")
    if scan.warnings:
        print(f"  覆盖警告：{len(scan.warnings)}（详见 audit 报告）")
    return 0


def _cmd_scan(config_path: str, override: str | None) -> int:
    try:
        config = load_project_config(config_path)
        scan = scan_docx(config.manuscript, ignore_years=config.ignore_years)
        output = _output_dir(config, override)
        output.mkdir(parents=True, exist_ok=True)
        write_claims_csv(output / "claims.csv", scan)
    except (MeaError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"scan 完成：{len(scan.occurrences)} 个候选，{len(scan.ignored)} 个排除")
    print(f"claims.csv：{output / 'claims.csv'}")
    if scan.warnings:
        print(f"覆盖警告：{len(scan.warnings)}")
    return 0


def _cmd_audit(config_path: str, override: str | None) -> int:
    try:
        config = load_project_config(config_path)
        result = audit_project(config)
        output = _output_dir(config, override)
        output.mkdir(parents=True, exist_ok=True)
        write_claims_csv(output / "claims.csv", result)
        write_audit_json(output / "audit.json", config, result)
        write_audit_markdown(output / "audit.md", config, result)
    except (MeaError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(
        f"audit 完成：{len(result.occurrences)} 个候选，"
        f"{result.blocking_count} 个错误，{result.warning_count} 个警告"
    )
    print(f"报告目录：{output}")
    return 1 if result.blocking_count else 0


def _cmd_review(config_path: str, override: str | None) -> int:
    try:
        config = load_project_config(config_path)
        output = _output_dir(config, override)
        report = output / "mapping_review.csv"
        rows = mapping_review_rows(config, output_path=report)
        write_mapping_review_csv(report, rows)
    except (MeaError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    current_count = len(
        {row["occurrence_id"] for row in rows if row["row_kind"] == "current_occurrence"}
    )
    orphan_count = sum(row["row_kind"] == "orphan_mapping" for row in rows)
    print(f"review 完成：{current_count} 个当前数字，{orphan_count} 个孤立映射")
    print(f"mapping_review.csv：{report}")
    return 0


def _cmd_explain(finding_id: str, report_path: str) -> int:
    path = Path(report_path).expanduser().resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"错误：无法读取报告 {path}：{exc}", file=sys.stderr)
        return 2
    findings = data.get("findings", [])
    found = next((item for item in findings if item.get("finding_id") == finding_id), None)
    if found is None:
        print(f"未找到发现：{finding_id}", file=sys.stderr)
        return 2
    print(json.dumps(found, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        return _cmd_init(args.project)
    if args.command == "doctor":
        return _cmd_doctor(args.config)
    if args.command == "scan":
        return _cmd_scan(args.config, args.out)
    if args.command == "review":
        return _cmd_review(args.config, args.out)
    if args.command == "audit":
        return _cmd_audit(args.config, args.out)
    if args.command == "explain":
        return _cmd_explain(args.finding_id, args.report)
    print(f"未知命令：{args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
