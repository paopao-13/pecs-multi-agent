# Changelog

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
