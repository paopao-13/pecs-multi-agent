# Changelog

## [0.3.0] - 2026-07-19

### Changed
- 仓库结构整理，突出主入口与核心文档：
  - `app.py` 等脚本迁入 `scripts/`，Web 入口现为 `scripts/app.py`
  - 16 份阶段性工程文档（可行性分析、实现计划、实验记录等）归档至 `docs/archive/`，根目录 `docs/` 仅保留 `TECHNICAL_REPORT.md` 与 `webshop_local_runbook.md`
  - `results/` 仅跟踪 `gaia_run.json` 与 `webshop_run.json` 两个聚合结果，其余中间产物移出版本控制
- README 同步修正失效路径引用（`scripts/app.py`、`docs/archive/testing.md`）
- WebShop 真实环境补丁文件独立为 `webshop_patches/`（因 `webshop/` 整体被 `.gitignore` 排除，补丁需单独跟踪）

### Security
- 全仓库审计并清除明文 API Key 与网关账号痕迹（含 git 历史重写）
- `lingshucode` 网关配置统一替换为 `<你的网关 Base URL>` / `<你的_API_KEY>` 占位符

## [0.2.0] - 2026-07-12

### Added
- 工程化补充：LICENSE、CONTRIBUTING.md、CODE_OF_CONDUCT.md
- GitHub Actions CI 流水线（自动跑 black/isort/flake8/pytest）
- pyproject.toml 打包配置，支持 `pip install -e .`
- .env.example 环境变量模板

### Changed
- 仓库重命名为 `pecs-multi-agent`，更精准体现四角色架构（Planner-Executor-Critic-Synthesizer）
- README 架构描述替换为 Mermaid 时序图

## [0.1.0] - 2026-07-09

### Added
- 基于 LangGraph 的四角色（Planner/Executor/Critic/Synthesizer）多智能体协作框架
- Plan-Execute-Reflect 循环实现，最多 5 轮反思
- GAIA Level 1 基准评测支持，实测准确率 100%（28/28）
- AgentBench WebShop 任务评测支持
- Token 预算感知调度与三级降级（70%/85%/95%）
- AST 安全沙箱
- Flask Web 可视化界面
