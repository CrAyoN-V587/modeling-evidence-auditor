# modeling-evidence-auditor 项目规则

- 遵循上级 `D:\MyAttempt\AGENTS.md`；本文只补充本项目约定。
- 文档和用户可见输出使用中文，代码标识符使用英文。
- 核心审计保持离线、确定性、只读；不得修改输入 DOCX、CSV 或生成未经请求的联网行为。
- 先修改直接相关文件，再运行对应测试；不要顺手重构相邻项目。
- `claim_id`、`occurrence_id`、`run_id` 是公开数据契约，修改前需更新 README、示例和测试。
- 项目级不创建 `.codex` 目录；本地环境使用 `.venv`，构建产物进入 `build/` 或 `dist/`。
