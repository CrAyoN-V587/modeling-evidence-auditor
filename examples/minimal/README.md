# 最小示例

该目录展示冻结结果表、证据 CSV 和映射表的结构。示例没有提交二进制 DOCX；可把自己的论文复制为 `paper/final.docx`，然后先运行：

```powershell
..\..\.venv\Scripts\mea.exe scan --config mea.toml
```

扫描完成后，生成派生复核表：

```powershell
..\..\.venv\Scripts\mea.exe review --config mea.toml
```

`build/mapping_review.csv` 会列出每个当前数字的全部确定性候选，以及未映射、上下文过期、显式忽略、claim 缺失、当前有效和孤立映射等状态。它不读取 `results/metrics.csv`，也不会修改或合并 `state/claim_map.csv`；请人工核对题号、指标、结果句、运行批次和证据位置后再编辑规范映射表。完成确认后运行：

```powershell
..\..\.venv\Scripts\mea.exe audit --config mea.toml
```

`review` 即使发现未映射或孤立项也退出 `0`；配置、DOCX 或 CSV 无法读取时退出 `2`。唯一候选仍不等于确认，不能自动让 `audit` PASS。
