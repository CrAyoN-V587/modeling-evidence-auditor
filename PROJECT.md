# modeling-evidence-auditor

## 目标

`modeling-evidence-auditor`（CLI：`mea`）是一个离线、确定性、只读的 Word 数值证据审计器。它把论文中的重要数值与 `frozen_numbers.csv`、证据 CSV 和 `run_id` 对齐，帮助数学建模团队在模型更新和提交前发现过期数字、单位错误、未登记结果和支撑材料不一致。

它不判断模型是否科学，不重跑任意代码，不自动改写论文，也不依赖大模型或网络。

## 当前状态

- v0.2.0 已实现：DOCX + UTF-8 CSV，支持 `init`、`doctor`、`scan`、`review`、`audit`、`explain`。
- `review` 在不读取证据文件的前提下生成 `mapping_review.csv`，完整展示当前数字、确定性候选、映射状态和孤立映射；它不会修改 `claim_map.csv`。
- review 仅接受项目内相对 `source_file`，不会探测绝对、drive-relative、UNC 或逃逸路径；输出会在写入前排除与 DOCX、registry、mapping 或本地 source 路径/文件身份碰撞，并通过同目录唯一临时文件原子替换旧报告。audit 对绝对 source 的既有 E002 语义不变。
- `audit` 增加非阻断 `W005`，用于发现未被当前有效 confirmed 映射使用的 frozen 登记项。
- registry 与证据中的 Decimal 输入必须有限，非有限值按输入错误退出，不能进入候选或 PASS。
- 输入文件默认只读，报告写入项目根目录内的 `build/`。
- 支持 Python 3.12–3.14；当前开发环境以 Python 3.12 为基线。

## 最近验证

- `.\.venv\Scripts\python.exe -m ruff check . --no-cache`：通过。
- `.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-v02-full`：65 passed。
- `.\.venv\Scripts\python.exe -m build`：成功生成 0.2.0 sdist 和 wheel。
- `examples\create_smoke_fixture.py` 生成真实 DOCX 后，`mea scan`、`mea review`、`mea audit` 均退出 0；复核表为 `confirmed_current/exact`，审计为 1 个 PASS、0 错误、0 警告。
- 当前执行环境的系统临时目录没有列举权限，因此默认 pytest 临时目录会失败；项目验证使用项目内 `.pytest-tmp`，该目录已忽略。

## 项目结构

```text
src/modeling_evidence_auditor/  核心包和 CLI
tests/                          真实 DOCX fixture 与回归测试
examples/minimal/               可复制的最小项目模板
```

## 本地开发

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp
.\.venv\Scripts\python.exe -m ruff check . --no-cache
.\.venv\Scripts\python.exe -m build
```

## 典型使用

```powershell
.\.venv\Scripts\mea.exe init .
.\.venv\Scripts\mea.exe doctor --config mea.toml
.\.venv\Scripts\mea.exe scan --config mea.toml
.\.venv\Scripts\mea.exe review --config mea.toml
.\.venv\Scripts\mea.exe audit --config mea.toml
.\.venv\Scripts\mea.exe explain MEA-E003-001 --report build/audit.json
```

审计退出码：`0` 表示没有阻断问题，`1` 表示审计完成但存在阻断问题，`2` 表示配置或输入无法处理。`review` 成功固定退出 `0`，输入失败退出 `2`，不使用退出码 `1`。

## 后续边界

自动写入或导入映射、候选置信度或语义排序、LLM/联网、证据预审、PDF、图片 OCR、XLSX 公式重算、自动改稿、模型正确性判断、引用真实性判断和云端协作不属于当前版本。新增能力必须保留当前离线和只读默认行为。
