# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)，变更记录格式参考 [Keep a Changelog](https://keepachangelog.com/)。

## [Unreleased]

### Added
- **GAIA 官方数据集本地镜像支持**：新增 `scripts/download_gaia.py`（纯 HTTP 直连下载，**零删除**，支持大小校验与断点续下）；`GAIAOfficialDataset` 支持 `local_dir` 参数 / `PEC_GAIA_LOCAL_DIR` 环境变量，命中时走**本地只读**路径，完全不触碰 huggingface_hub 缓存机制（可复现、可离线、CI 友好）。`data/gaia/` 已加入 `.gitignore`（门控数据严禁入库）。
- **Office 附件解析**（`tools/file_parser.py`）：支持 `.docx` 与 `.pptx` —— 用**标准库** `zipfile` + `ElementTree` 解析 OOXML（`word/document.xml` 的 `<w:p>/<w:t>`；`ppt/slides/slideN.xml` 的 `<a:p>/<a:t>`，按数字自然序排页），**零新增依赖**。旧版二进制 `.doc`/`.ppt` 明确报错，不再静默回退成乱码。
- **数据目录白名单** `PEC_DATA_ALLOW_DIR`（`tools/path_guard.py`）：显式配置的目录豁免于敏感路径 / 隐藏文件规则。不配置时行为与之前完全一致。
- **多模态后端实测打通（网关自带 vision 模型）**：`GET /models` 枚举到 4 个视觉模型（`glm-5.2/5.3-vision`、`deepseek-v4-flash/pro-vision`），与主 LLM 同 key 同端点，GAIA 的 2 道图片附件题由此具备作答条件（11 道附件题 = 2 图 + 2 音频 + 7 文档）。图片转录输出上限改为可配置 `PEC_VISION_MAX_TOKENS`（默认 1500 → **3000**：实测整页截图转录到 1500 就被截断，而题目要的数据常在页面更深处）。音频转写端点该网关不支持，2 道 mp3 题仍按降级跳过。
- **评测单题硬超时 Windows 兜底**（`benchmarks/gaia_official.py:_run_with_deadline`）：守护线程 + `join(timeout)`，补上 Windows 缺失 `SIGALRM` 的盲区。
- **LLM 调用整体墙钟上界** `LLM_CALL_DEADLINE`（`agents/llm_utils.py`，默认 120s，设 `0` 关闭）：约束**一次 `call_llm` 的全部重试总耗时**，到点后不再发起新尝试、且跳过会越界的退避等待。

### Fixed
- **门控数据集在受限网络下无法拉取**：定位并规避 `snapshot_download()` 整仓拉取的两个坑——① `HF_ENDPOINT` 指向镜像时 `/resolve/` 会 308 跳回 `huggingface.co`，跨域重定向**丢掉 `Authorization` 头**，门控文件必然 401；② 119 个文件逐个创建/删除 `.locks`/`*.incomplete`，累计删除次数触发宿主环境的**批量删除保护**（阈值 50/轮）而被中断。两者均在 `scripts/download_gaia.py` 与 `datasets/gaia_official_dataset.py` 的文档字符串中记录成因与规避方式。
- **🔴 `.docx`/`.pptx` 静默解析失败（既有缺陷，非 P0 回归）**：旧的分发只有 pdf/xlsx/csv/image，Office Open XML 落到 `_parse_text` 回退 → **直接吐 ZIP 二进制**（`PK\x03\x04…`）。工具返回 success，LLM 却拿到乱码，属于**静默错误**。修复后实测：GAIA 的 `.docx` 读出 65 段含 `Gift Assignments` 表格，`.pptx` 读出 8 页（crayfish / nematodes / isopods / eels / Yeti crab / Spider crab…）。
- **🔴 数据目录被路径守卫静默拒解析（实测导致附件子集 0 分）**：`FORBIDDEN_PREFIXES` 含 `C:\Users`，而 Windows 上用户数据（含 HuggingFace 默认缓存 `~\.cache`，还命中「隐藏文件」规则）就在其下。实证：历史 GAIA 官方 53 题的 **11 道附件题全部 0 分**，预测文本原话为「所有尝试读取附件…均因权限限制而失败（错误：禁止访问系统敏感路径）」——**26.4% 完全来自 42 道无附件题（14/42 = 33.3%）**。修复路径：数据放到非禁区目录（本地镜像在 `D:`），或经 `PEC_DATA_ALLOW_DIR` 显式豁免。⚠️ 该发现意味着 26.4% 是「附件链路带 bug」下的数字，重跑后预计上升；**数字本身暂不更新，待实测重跑后再统一修订。**
- **单题超时在 Windows 从未生效**：原先仅靠 `signal.SIGALRM`，Windows 无此信号 ⇒ `hasattr` 判定整段跳过。实证：LLM 网关「只连不发」时单题空转 15 分钟（`faulthandler` 栈 dump 定位在 `agents/llm_utils.py:152` → `ssl.py read`），53 题串行评测随时被拖死且无提示。现已用守护线程兜底。
- **更正一项归因**：此前把上述挂起归给 `tools/web_search.py` 的 DuckDuckGo 调用，经全线程栈 dump 证伪 —— `duckduckgo_search` 的 `DDGS.__init__` **默认就有 `timeout=10`**。仍将超时显式化并做成可配置（`PEC_SEARCH_TIMEOUT`，默认 10 与库默认一致），以免将来依赖库的默认值。
- **视觉后端「假成功」显式化**（`tools/multimodal.py`）：网关侧图片解码失败时，视觉模型实际收到占位文本而非图片，回复形如「…[图片内容描述失败]…请重新上传…」（英文提问时为「the image didn't come through / no image data」）——旧逻辑会把这段客套话当附件描述注入题面（工具显示成功、实际零信息）。现检测中英占位标记并返回 `[多模态处理失败]`，评测侧按 `multimodal_skip` 显式记录。实测 GAIA `cca530fc`（棋盘图）在该网关 3 个 vision 模型 + PNG/JPEG 重编码后均持续如此，属网关侧限制，已记为已知缺陷。
- **大图分块转录**（`tools/multimodal.py`）：实测视觉模型对大图只"看到"一部分——`finish_reason=stop`、远未到 `max_tokens`（8000 也一样）就宣称「content cuts off here」（GAIA `9318445f` 的 1726×842 截图转录到中部即止）。**不是 token 上限，是模型的输入分辨率截断**。超过 `PEC_VISION_TILE_MAX_W/H`（默认 1400/1100px）时自动切成带 12% 重叠的分块逐块转录再合并，任一块解码失败即整体失败（宁缺毋假）。端到端实证：分块后左块转录显著变完整，但该网关对右侧分块仍解码失败，此题（gold 为 17 项分数列表）**仍未答对**——记为网关侧已知缺陷，诚实披露。
- **排查工具坑（记录备忘）**：用 Python 标准库 `urllib` 直连该网关会稳定收到 **Cloudflare 1010（HTTP 403）**——是 UA 黑名单（`Python-urllib/*`）而非凭据问题，换浏览器 UA 或 `requests` / openai SDK 即恢复。用它做连通性诊断会误判「key 失效」。
- **`call_llm` 缺少整体上界（上述 15 分钟挂起的根因）**：`get_llm()` 的 `timeout=60` 只约束**单次 HTTP 请求**，而一次 `llm.invoke()` 内部还有 openai SDK 自己的 `max_retries=2`（最多 3 次请求）⇒ 单次 invoke 最长 180s；外层再重试 3 次 + 8/16/32s 退避 ⇒ **最坏约 9 分钟且无整体上界**。新增 `LLM_CALL_DEADLINE` 后协程/同步路径均按墙钟收敛。⚠️ 它只能阻止「发起新尝试」，无法中断已飞行中的那次 —— 真正的硬边界仍由 `_run_with_deadline()` 提供，两者构成纵深防御。
- **🔴 超长输入的保护形同虚设（可被单人触发的 DoS 面）**：`MAX_QUERY_CHARS` 默认 10000，而实测 10000 字符的 query 会完整跑一遍四角色图、**耗时 30028ms 并占满 worker**（`results/production_bench.json` 里 `long_query_10k` 早就记录了 `status_code=0, error=timed out`）。LLM 线程池只有 4 个（`scripts/api.py:101`）⇒ 4 个这样的请求即让服务无响应。更糟的是基准**只记录不断言**，CI 一直绿。修复：上限 10000 → 4000（同时改 `config.py` 默认值与 `experiments/config.yaml`，注意优先级为 env > YAML > 代码默认值）；基准用例从「硬编码 10000」改为「上限 + 1」并强制断言 413、纳入 M6 门禁。
- **重试退避无抖动**：固定 8/16/32s 会让所有被限流的请求在同一时刻重试，形成同步重试风暴（自我 DDoS）。改为等额抖动 `base/2 + random(0, base/2)`，保留 base/2 下限以免退避退化到 0。
- **🔴 mock 检索数据污染生产路径**：`tools/web_search.py` 原本**无条件**优先命中 31 个预置答案键，命中即返回 canned text，真实 API 根本不会被调用。这是为内置 33 题「开卷可复现」设计的评测工具，上了生产就是返回编造内容。现限定为**仅 eval 模式**生效；非 eval 模式下真实检索无结果时返回明确的「未检索到」，不再回落 mock。
- **`/run_task` 默认超时 120s → 300s**：实测附件题端到端 248.7s（.docx）/ 260.4s（.pptx），120s 会把**刚修好的题系统性判超时**，把「能力不够」与「时间不够」混为一谈。
- **测试标记静默失效**：`pytest.ini` 注册 `requires_api`，而 conftest 与实际用例用的是 `requires_api_key`；CI 过滤的是前者，两个标记的用例从未被真正排除（靠 conftest 自动 skip 兜住）。现 CI 同时排除两者，`pytest.ini` 标注 `requires_api` 为历史别名。

### Changed
- `datasets/gaia_official_dataset.py` 抽出 `_ingest()`（字段归一化）与 `_load_local()`（本地镜像读取），在线与离线两条路径共用同一归一化逻辑，避免格式漂移。
- **评测单题默认超时 120s → 300s**（`benchmarks/gaia_official.py` / `run_gaia_official.py --timeout`）：修复 Office 解析后实测两道附件题分别耗时 248.7s / 260.4s，沿用 120s 会**系统性地把刚修好的题再杀一遍**。
- 单元测试 290 → **302 passed**（新增 `tests/test_office_parse.py` 6 例、`tests/test_gaia_official.py` 硬超时 3 例、`tests/test_llm_call_deadline.py` 3 例）。

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
