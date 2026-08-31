"""Project configuration and path-safety helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .models import ConfigError, ProjectConfig


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"配置项必须是布尔值：{value!r}")
    return value


def safe_project_path(root: Path, value: str, *, label: str) -> Path:
    """Resolve a project-relative path and reject absolute/path-traversal input."""

    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} 不能为空")
    # os.path.isabs catches Windows drive and UNC paths on Windows.  The
    # second check also handles a Windows path supplied to another platform.
    if os.path.isabs(value) or Path(value).drive or value.startswith(("\\\\", "/")):
        raise ConfigError(f"{label} 必须是相对项目根目录的路径：{value}")
    candidate = (root / Path(value)).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ConfigError(f"{label} 不能逃逸项目根目录：{value}") from exc
    return candidate


def load_project_config(config_path: str | Path) -> ProjectConfig:
    """Load and validate a UTF-8 TOML project configuration."""

    import tomllib

    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"无法读取配置文件 {path}：{exc}") from exc
    if raw.get("schema_version", 1) != 1:
        raise ConfigError("只支持 schema_version = 1")

    root = path.parent
    manuscript = safe_project_path(root, raw.get("manuscript", ""), label="manuscript")
    registry = safe_project_path(root, raw.get("registry", ""), label="registry")
    mapping = safe_project_path(root, raw.get("mapping", ""), label="mapping")
    output_dir = safe_project_path(root, raw.get("output_dir", "build"), label="output_dir")
    return ProjectConfig(
        root=str(root),
        config_path=str(path),
        manuscript=str(manuscript),
        registry=str(registry),
        mapping=str(mapping),
        output_dir=str(output_dir),
        ignore_years=_as_bool(raw.get("ignore_years"), True),
        require_frozen=_as_bool(raw.get("require_frozen"), True),
    )


def write_default_project(target: Path) -> list[Path]:
    """Create the explicit, non-destructive `mea init` starter files."""

    files = {
        target / "mea.toml": (
            "schema_version = 1\n"
            'manuscript = "paper/final.docx"\n'
            'registry = "state/frozen_numbers.csv"\n'
            'mapping = "state/claim_map.csv"\n'
            'output_dir = "build"\n'
            "ignore_years = true\n"
            "require_frozen = true\n"
        ),
        target / "state" / "frozen_numbers.csv": (
            "claim_id,question,claim_text,metric,value,unit,source_file,source_column,filter,"
            "run_id,status,display_value,evidence_type,round_digits,tolerance_abs,tolerance_rel,notes\n"
            "C001,问题一,请填写论文中的结果句,metric,0,,results/metrics.csv,value,,run-1,frozen,0,"
            "model_output,,,,\n"
        ),
        target / "state" / "claim_map.csv": (
            "occurrence_id,claim_id,decision,context,confirmed_at\n"
        ),
        target / "paper" / ".gitkeep": "",
        target / "results" / ".gitkeep": "",
    }
    existing = [path for path in files if path.exists()]
    if existing:
        names = ", ".join(str(item.relative_to(target)) for item in existing)
        raise ConfigError(f"init 不覆盖已有文件：{names}")
    target.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
    return list(files)
