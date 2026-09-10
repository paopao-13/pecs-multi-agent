# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)，变更记录格式参考 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

### Added
- **GAIA 官方数据集本地镜像支持**：新增 `scripts/download_gaia.py`（纯 HTTP 直连下载，**零删除**，支持大小校验与断点续下）；`GAIAOfficialDataset` 支持 `local_dir` 参数 / `PEC_GAIA_LOCAL_DIR` 环境变量，命中时走**本地只读**路径，完全不触碰 huggingface_hub 缓存机制（可复现、可离线、CI 友好）。`data/gaia/` 已加入 `.gitignore`（门控数据严禁入库）。

### Fixed
- **门控数据集在受限网络下无法拉取**：定位并规避 `snapshot_download()` 整仓拉取的两个坑——① `HF_ENDPOINT` 指向镜像时 `/resolve/` 会 308 跳回 `huggingface.co`，跨域重定向**丢掉 `Authorization` 头**，门控文件必然 401；② 119 个文件逐个创建/删除 `.locks`/`*.incomplete`，累计删除次数触发宿主环境的**批量删除保护**（阈值 50/轮）而被中断。两者均在 `scripts/download_gaia.py` 与 `datasets/gaia_official_dataset.py` 的文档字符串中记录成因与规避方式。

### Changed
- `datasets/gaia_official_dataset.py` 抽出 `_ingest()`（字段归一化）与 `_load_local()`（本地镜像读取），在线与离线两条路径共用同一归一化逻辑，避免格式漂移。

## [0.6.1] - 2026-09-10

P0 生产化加固：依赖故障显式化 + 工具层容错 + 成本可观测 + 内容管线/回放 + 跨平台路径校验。

### Added
- **工具包装器**（`tools/wrapper.py`）：单工具超时、异常分类、结构化日志、熔断、幂等、**权限白名单**；由 `TOOL_WRAPPER_ENABLED` 总开关控制，关闭时逐字回退到改造前路径（eval 模式默认关闭、business 模式默认开启），保证增量改动可无损回退。
- **运行模式画像**：eval / business 差异化默认值；单条 query 长度护栏（对齐 10K 超长用例）与 prompt 注入护栏。
- **成本归因**（`metrics/cost_attribution.py`）：token 按 **角色 / 工具 / 迭代** 三个维度拆分。
- **内容管线与链路回放**：`tools/content_pipeline.py` 与 `demos/content_pipeline_demo.py`；基于 SQLite 检查点的 trace 回放端点。
- **跨平台路径安全守卫**（`tools/path_guard.py`）：分隔符归一后**同时比对原始路径与 `realpath`** 的 POSIX + Windows 前缀族，供 `file_reader` / `file_parser` 复用，替换两份重复的内联实现。

### Changed
- **启动自检升级为真实鉴权探测**：由「是否配置了 key」改为 `GET /models`（只看鉴权结论、不等模型生成）。凭据被拒（401/403）→ `llm_configured=False`，`/health` 暴露 `llm_reason`，`/run_task` 启动期即 fail-fast（503）；探测**未获结论**（超时/网络异常/端点不支持）按「可用」放行——所用模型多为 reasoning 模型，单次生成实测 4.8~38.4s 剧烈波动，用固定超时等生成会把「模型只是慢」误判为「依赖不可用」。设 `PEC_SKIP_LLM_PROBE=1` 可跳过探测。
- `/run_task` 依赖故障显式化：LLM 调用失败且任务零步骤时返回 `success=False` + 明确 error，不再用 `success=True` + 空答案掩盖故障；失败原因经 `AgentState.llm_error` 由 Planner / Synthesizer 上报。
- 单元测试 134 → **290 passed**（eval / business 双模式均绿）；CI 新增 `unit-test` job（此前 CI 只有 benchmark 门禁）。

### Fixed
- 敏感路径黑名单原先使用 `os.path.realpath(path).startswith(prefix)`，导致**每条规则只在自身平台生效**（Linux 上 `C:\Windows` 规则失效、Windows 上 `/etc` 规则失效）——这是 CI 在 ubuntu runner 上失败的根因。改为同时比对原始输入与 realpath（只查 realpath 会随平台失效，只查原始输入可被 symlink 绕过）。

### Removed
- `results/gaia_official_run.json`（陈旧快照，与 `gaia_official_react.json` 的 ReAct 结果自相矛盾，且运行时无消费方）。

## [0.6.0] - 2026-07-20

### Added
- **全局限流**：令牌桶，由 `PEC_RATE_LIMIT_RPS` / `PEC_RATE_LIMIT_BURST` 配置（均为 0 时关闭），以 FastAPI `Depends` 形式应用于 `/metrics`、`/metrics/prom`、`/run_task`；`/health` 豁免（探针不应被限流误杀）。超限返回 429，计数 `RATE_LIMITED` 暴露至 JSON 与 Prometheus。
- **故障注入 / 混沌**：`PEC_CHAOS=1` + `PEC_CHAOS_TOKEN` 启用 `/admin/chaos`（GET 查看 / POST 切换）；`llm_down` 模式使 `/run_task` 返回结构化错误而非 500。任何传输层故障（畸形 JSON / 错误 Content-Type / 超大负载）一律结构化拒绝（400/422），不 panic。

## [0.5.0] - 2026-07-20

### Added
- 启动自检（lifespan）：服务启动即校验 LLM 配置，`/health` 暴露 `llm_configured`；未配置时 `/run_task` 立即返回 503（fail-fast），而非等到首个任务在图深处抛出难懂异常。
- `/metrics/prom`：基于 `prometheus_client` 多进程模式，`gunicorn -w N` 下各 worker 计数经共享目录聚合，供外部 Prometheus scrape。

## [0.4.2] - 2026-07-20

### Added
- `/metrics` 按 endpoint 分桶延迟（`/health`、`/metrics`、`/run_task` 的 p50/p95/p99 分别统计）。
- `/metrics` 真实 token 计量：累计真实 LLM 任务数与总 token 数（取自 LLM 网关 `usage_metadata`）。

## [0.4.1] - 2026-07-20

### Changed
- LLM 调用改用**独立线程池**，与默认 executor 解耦，避免长耗时任务占满默认池导致轻量请求（如参数校验）排队等待（HOL 变体）。

## [0.4.0] - 2026-07-20

### Added
- **生产级 FastAPI 服务**（`scripts/api.py`）：`/health`、`/metrics`、`/run_task`，与 Flask demo（`scripts/app.py`）并存。
- **GAIA 官方数据集接入**：评测从内置示例集升级到官方 **Level 1 validation set（53 题）**——PECS 26.4%（14/53）vs ReAct 24.5%（13/53），McNemar 检验 p=1.0（差异不显著，如实披露）。
- **WebShop 真实环境评测**：接入真实环境桥（BM25 后端）与公平 ReAct 基线——PECS 25%（3/12）vs ReAct 0%（0/12），+25.0pp；端到端 token 降低 65.5%。
- **生产指标基准**：`benchmark_production.py` 采集服务延迟 / 吞吐 / 容错 / 稳定性（M1~M9），提供 `--ci` 门禁与 `--prometheus` 验证；CI 增加 benchmark job。
- Agent 核心逻辑、启发式路由、GAIA 答案判定、conftest 跳过逻辑等单元测试补齐。

### Changed
- 入口改为 async，LLM 同步调用经 `loop.run_in_executor` 卸载到线程池，避免长耗时任务阻塞 `/health` 等请求；`/run_task` 增加超时保护（默认 120s），超时返回结构化错误而非无限挂起。

### Fixed
- `webshop_interact` 调用 `call_llm` 时 `max_tokens` 参数错误（真实环境评测 0% 的根因）。
- 成功判定截断、Windows 路径空格截断、LLM 兜底判定误匹配等缺陷。

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
- GitHub Actions CI 流水线（benchmark 门禁 + pytest；不含 linter）
- pyproject.toml 打包配置，支持 `pip install -e .`
- .env.example 环境变量模板

### Changed
- 仓库重命名为 `pecs-multi-agent`，更精准体现四角色架构（Planner-Executor-Critic-Synthesizer）
- README 架构描述替换为 Mermaid 时序图

## [0.1.0] - 2026-07-09

### Added
- 基于 LangGraph 的四角色（Planner/Executor/Critic/Synthesizer）多智能体协作框架
- Plan-Execute-Reflect 循环实现，最多 5 轮反思
- GAIA Level 1 基准评测支持，内置 28 题示例集实测 100%（28/28；自建示例集，非官方数据集）
- AgentBench WebShop 任务评测支持
- Token 预算感知调度与三级降级（70%/85%/95%）
- AST 安全沙箱
- Flask Web 可视化界面
