# 最小示例

该目录展示冻结结果表、证据 CSV 和映射表的结构。示例没有提交二进制 DOCX；可把自己的论文复制为 `paper/final.docx`，然后先运行：

```powershell
..\..\.venv\Scripts\mea.exe scan --config mea.toml
```

根据 `build/claims.csv` 中的 `occurrence_id` 填写 `state/claim_map.csv`，再运行 `mea audit --config mea.toml`。
