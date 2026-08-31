# modeling-evidence-auditor

`modeling-evidence-auditor`（命令 `mea`）检查 Word 数学建模论文中的重要数值是否能追溯到冻结结果和证据 CSV。

它适合“模型已经跑完、论文正在反复修改、提交前需要逐项复核”的阶段。工具不联网、不调用大模型、不修改原始文件，也不试图替代模型验证；它只审计论文数字与登记证据之间的机械一致性。

## 快速开始

需要 Python 3.12 或更高版本。Windows PowerShell 示例：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\mea.exe init .
# 将论文放入 paper\final.docx，并填写 state\frozen_numbers.csv
.\.venv\Scripts\mea.exe doctor --config mea.toml
.\.venv\Scripts\mea.exe scan --config mea.toml
.\.venv\Scripts\mea.exe audit --config mea.toml
```

`audit` 的退出码为：

- `0`：审计完成，没有阻断问题；
- `1`：审计完成，但存在 `E` 类阻断问题；
- `2`：配置、路径、ZIP/XML 或 CSV 无法读取。

## 数据契约

配置文件 `mea.toml` 中的路径必须是相对项目根目录的路径：

```toml
schema_version = 1
manuscript = "paper/final.docx"
registry = "state/frozen_numbers.csv"
mapping = "state/claim_map.csv"
output_dir = "build"
ignore_years = true
require_frozen = true
```

冻结登记表至少包含：

```text
claim_id,question,claim_text,metric,value,unit,source_file,source_column,filter,run_id,status
```

推荐的可选字段包括 `display_value`、`evidence_type`、`round_digits`、`tolerance_abs`、`tolerance_rel` 和 `notes`。`status` 为 `frozen` 才能通过严格审计。

`display_value` 非空时表示论文中允许出现的完整数字形式，例如 `33.9%`；工具会同时检查显示形式和数值。`round_digits` 与两种容差不能同时设置。`万`、`亿` 属于显式单位的一部分，工具不会把 `2.018亿元` 静默换算为 `201800000元`。

证据文件使用 UTF-8 CSV。`source_column` 指向数值列；`filter` 使用简单的 `列=值;列2=值2` 形式筛选行。证据行必须包含与冻结记录一致的非空 `run_id`。例如：

```csv
claim_id,question,claim_text,metric,value,unit,source_file,source_column,filter,run_id,status,display_value,round_digits,evidence_type
C001,问题一,测试集RMSE为 1.23,RMSE,1.23,,results/metrics.csv,RMSE,model=baseline;split=test,run-2026-01,frozen,1.23,,model_output
```

论文位置和登记项的确认关系放在 `claim_map.csv`：

```text
occurrence_id,claim_id,decision,context,confirmed_at
body:p-00112233:n1,C001,confirmed,测试集RMSE为 1.23,2026-08-31T12:00:00+08:00
```

只有 `decision=confirmed` 的映射可以参与 PASS；模糊候选永远只能产生阻断结果。`decision=ignored` 表示队员明确确认该数字不是需要审计的结果。两种决定都必须保存非空 `context`；文稿上下文变化后映射会以 `E007` 失效。

## 命令

```text
mea init PROJECT_DIR
mea doctor --config mea.toml
mea scan --config mea.toml [--out build]
mea audit --config mea.toml [--out build]
mea explain FINDING_ID --report build/audit.json
```

`scan` 生成稳定排序的 `claims.csv`；`audit` 额外生成 `audit.json` 和 `audit.md`。所有输出都必须落在项目根目录内。

## 规则摘要

- `E001`：重要数字没有已确认的登记映射；
- `E002`：证据文件、列或筛选行不存在或不可解析；
- `E003`：论文值与冻结值不一致；
- `E004`：单位不一致；
- `E005`：登记项未冻结；
- `E006`：证据行或论文使用的 `run_id` 不一致；
- `E007`：文稿编辑使旧映射失效；
- `E009`：同一登记项在论文中出现互相矛盾的值；
- `W001`：存在确定性候选但尚未人工确认，不能自动 PASS；
- `W002`：通过显式舍入位数或容差匹配，建议复核显示精度；
- `W003`：浮动对象、公式、文本框、嵌套表格、脚注等 OOXML 覆盖缺口；
- `W004`：文稿包含修订内容，提取结果需要人工复核；
- `I001`–`I002`：结构编号、引用编号、年份或显式忽略项等信息。

## 已知限制

当前版本读取 DOCX 正文、页眉页脚和顶层表格。文本框、图片中的文字、嵌入图表、公式对象、修订删除内容和嵌套表格会被报告为覆盖警告，不会被默默当作已审计。当前只解析 CSV，不会重算 Excel 公式，也不会执行用户代码。

## 开发

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp
.\.venv\Scripts\python.exe -m ruff check . --no-cache
.\.venv\Scripts\python.exe -m build
```

项目采用 MIT License。当前版本是 MVP，发布前仍需在真实匿名论文和不同 Word 版本上补充人工覆盖验证。
