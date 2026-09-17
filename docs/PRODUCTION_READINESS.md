# 生产就绪度清单

> **这份文档回答一个问题**：这个项目凭什么不叫 demo？
>
> 业界常用八条线划分 demo 与生产级：**超时、成本上限、幂等、重试、限流、审计日志、评测回归、灰度回滚**。八条缺一条，就还是 demo。本文逐条给出：能力说明 → 代码位置 → 配置开关 → **可执行的验证命令** → 已知边界。
>
> **使用方式**：面试前跑一遍末尾的「一键自检」；被问到某项时，直接翻到对应小节，给出代码位置与验证命令——比口头描述可信。

| # | 能力 | 状态 | 一句话说明 |
|:-:|---|:-:|---|
| 1 | 超时控制 | ✅ | 工具 15s / 任务 300s / LLM 调用 120s 三层，全部有显式数值 |
| 2 | 成本上限 | ✅ | 50000 token 硬封顶 + 70/85/95% 三级降级，ReAct 侧无上限作为对照 |
| 3 | 幂等 | ✅ | 只读工具按 `thread_id+工具+参数哈希` 缓存；跨进程可选共享（2026-09-17 修复了 thread_id 未注入 state 导致的跨租户串味，见第 3 节） |
| 4 | 重试 | ✅ | 最多 3 次，指数退避 + 等额抖动（jitter），避免重试风暴 |
| 5 | 限流 | ✅ | 令牌桶，超限返回 429（非 500），支持跨进程共享计数 |
| 6 | 审计日志 | ✅ | 结构化 JSON 日志 + trace_id 全链路贯穿 + 入参摘要脱敏 |
| 7 | 评测集回归 | ✅ | 63 条用例（含 21 条对抗），CI 零消耗门禁 + 覆盖率门禁 |
| 8 | 灰度回滚 | ✅ | Prompt 版本注册表，运行时切换，重启回落配置值 |

**⚠️ 一个必须知道的前提**：工具加固类能力（熔断/幂等/权限白名单）受 `RUN_MODE` 控制，**默认 `eval` 模式全部关闭**（保证评测行为与历史逐字一致）。演示这些能力需 `RUN_MODE=business`。

---

## 1. 超时控制

**能力**：三层超时各管一段，避免"某个环节卡住拖垮整个服务"。

| 层 | 默认值 | 作用域 | 超时后行为 |
|---|---|---|---|
| 工具调用 | `15s` | 单次工具执行 | 返回结构化错误，记入熔断计数 |
| LLM 单次调用 | `120s` | 单次 LLM 请求 | 归为可重试异常（见第 4 节） |
| 任务整体 | `300s` | 一次 `/run_task` | 返回 `success=false` + 明确超时说明 |

**代码位置**

| 关注点 | 位置 |
|---|---|
| 配置读取 | `config.py:204`（`TOOL_TIMEOUT_SEC`） |
| 工具超时实现 | `tools/wrapper.py:run_with_timeout`（线程池 + `future.result(timeout=)`） |
| 任务级超时 | `scripts/api.py:104`（`RUN_TASK_TIMEOUT_S`）+ `asyncio.wait_for` |
| 评测单题硬超时 | `benchmarks/gaia_official.py:_run_with_deadline`（守护线程 + join，补 Windows 无 `SIGALRM`） |

**配置开关**：`TOOL_TIMEOUT_SEC`（15）、`PEC_RUN_TASK_TIMEOUT`（300）、`LLM_CALL_DEADLINE`（120）

**验证命令**

```bash
python -m pytest tests/test_wrapper.py -q -k timeout --basetemp=.pytest_tmp
```

**已知边界**（重要，别被问倒）
超时是「**放弃等待**」而非「真正中断」——同步图 + Windows 无 `SIGALRM` + CPython 无跨线程强杀 API，所以超时后工作线程仍会跑完。真强杀需要子进程或进程池，成本高于收益。**死循环型工具仍会占用一个线程直到结束**。

---

## 2. 成本上限

**能力**：每个任务有硬预算与三级降级，成本**可预测**（这是相对 ReAct 的核心差异）。

| 阈值 | 触发比例 | 降级动作 |
|---|---|---|
| L1 | 70% | 缩减计划粒度 |
| L2 | 85% | 跳过非关键步骤 |
| L3 | 95% | 强制收敛出答案 |

**代码位置**：`config.py:97`（`DEFAULT_TOKEN_BUDGET=50000`）、`config.py:25-29`（`DEGRADE_THRESHOLD_1/2/3`）、`graph/builder.py`（预算检查与降级分支）

**配置开关**：`DEFAULT_TOKEN_BUDGET`（50000）、`token_budget.degrade_threshold_*`（YAML，可通过 `experiments/*.yaml` 覆盖）

**验证命令**

```bash
python -m pytest tests/test_token_budget.py -q --basetemp=.pytest_tmp
```

**已知边界**
"成本上限"保证的是**不超支**，不是**最便宜**。纯知识检索类任务上 PECS 单题 token 高于 ReAct（多角色固有开销，实测 20,966 vs 5,076）——它的价值是用可预测性换成本方差。对外表述切勿写成"永远更省"。

---

## 3. 幂等

**能力**：只读工具同一 `thread_id` + 同参数重复调用，直接返回缓存，不重复执行（不重复烧额度、不重复产生副作用）。

**为什么只对只读工具生效**：`search` / `web_browse` / `file_read` / `file_parse` / `multimodal` 无副作用可安全缓存；`python`（可写文件）、`api_call`（可能是 POST）、`webshop`（产生选择动作）**故意不缓存**——缓存会掩盖副作用。

**幂等键**：`thread_id | 工具名 | 参数哈希(SHA1 前 16 位)`

> **2026-09-17 修复：此前这个键的租户隔离是失效的。**
>
> `idempotency_key` 的注释一直承诺"不同 thread_id 之间不串味""键里天然带租户边界"，
> 但 `thread_id` **从未真正进入 `AgentState`**——`agents/executor.py` 取的是
> `state.get("thread_id", "-")`，而 `AgentState` 没有该字段 → 恒为 `"-"`
> → 所有任务的幂等键退化成 `-|工具|参数哈希`，**不同租户的相同查询会命中同一个缓存
> （跨租户数据串味）**。
>
> 根因链路：`RunTaskRequest.thread_id`（真值）只被塞进 LangGraph 的
> `config["configurable"]`（供 checkpoint 持久化），而 `executor_node(state)` 的
> 签名没有 config 参数，节点内只能从 state 取值 → 真值与消费方从未交汇。
> `AgentState.get()` 实现为 `getattr(self, key, default)`，缺字段**静默返回默认值**，
> 不报错——与本项目此前修复的"工具异常被误判成功"同属静默失败模式。
>
> 为何此前没被发现：① `TOOL_IDEMPOTENT_ENABLED` 默认 `false`，eval 模式完全静默；
> ② 所有幂等用例**硬编码** `context={"thread_id": "t1"}`，绕过了 state 取值断点
> （验证的是"给定 thread_id 时 wrapper 正确"，而缺陷是"thread_id 没传进 state"）。
>
> 修复：`AgentState` 新增 `thread_id: str = "-"` 字段，经 `create_initial_state`
> 透传（`graph/builder.py` 两条分支 + `scripts/api.py`）。回归保护见
> `tests/test_thread_id_propagation.py`（真链路）与
> `tests/test_tool_wrapper.py::TestIdempotentIsolationEndToEnd`（端到端隔离）。

**代码位置**：`tools/wrapper.py:idempotency_key` / `_idempotent_lookup` / `_idempotent_store`、`tools/wrapper_state.py`（跨进程实现，复用 `idem_cache` 表）

**配置开关**：`TOOL_IDEMPOTENT_ENABLED`（默认 `false`）、`PEC_SHARED_STATE_DB`（设则跨进程共享）、`PEC_IDEM_TTL_SEC`（共享模式 TTL，默认 600）

**验证命令**

```bash
# 单元层（wrapper 内部契约）
python -m pytest tests/test_wrapper.py tests/test_wrapper_state.py -q -k "idempot" --basetemp=.pytest_tmp

# 真链路：create_initial_state → AgentState → executor_node → 工具 context
# 这条最重要——历史上的 bug 恰恰出在"thread_id 没传进 state"，
# 而只测 wrapper 的用例（硬编码 context）永远抓不到。
python -m pytest tests/test_thread_id_propagation.py -q --basetemp=.pytest_tmp

# 端到端隔离：不同 thread_id 不共享缓存、同一 thread_id 命中缓存
python -m pytest tests/test_tool_wrapper.py -q -k "IsolationEndToEnd" --basetemp=.pytest_tmp
```

**已知边界**
① 进程内模式上限 512 条 LRU，不跨进程；② 跨进程需显式配 `PEC_SHARED_STATE_DB`；③ 租户隔离依赖 `thread_id` 的 `<tenant>-<后缀>` 命名约定，不额外存储归属。

---

## 4. 重试

**能力**：LLM 调用失败最多重试 3 次，**指数退避 + 等额抖动（equal jitter）**——抖动是为了避免多任务同时重试造成尖峰（重试风暴）。

**错误分类决定"该不该重试"**（实测驱动的改造）：

| 类别 | 状态码 | 行为 |
|---|---|---|
| 可重试 | 408 / 409 / 425 / 429 / 500 / 502 / 503 / 504 | 退避后重试，最多 3 次 |
| 终止 | 400 / 401 / 403 / 404 / 405 / 413 / 415 / 422 | 立即返回，**不白等退避时长** |
| 终止（关键词兜底） | 余额不足 / 凭据无效 / 上下文超长 / 内容违规 | 同上 |
| 未知错误 | — | **默认不重试**（宁可少等，不可白等） |

**判定顺序**：显式 HTTP 状态码 → 终止类关键词 → 可重试关键词 → 默认终止。

**代码位置**：`agents/llm_utils.py:classify_llm_error`（分类）、`agents/llm_utils.py`（退避与抖动，约 201 行处说明 equal jitter）、`config.py:115`（`MAX_RETRIES=3`）

**配置开关**：`MAX_RETRIES`（3，YAML `execution.max_retries`）、`PEC_RETRY_CLASSIFY=0`（一键回退旧的纯关键词行为）

**验证命令**

```bash
python -m pytest tests/test_llm_retry_classify.py -q --basetemp=.pytest_tmp
```

**改造前的实测缺陷（已修复，保留记录以便面试时讲清）**

纯关键词子串匹配带来两类错误：

| 场景 | 改造前 | 改造后 |
|---|---|---|
| **500 服务端错误** | **一次都不重试**（消息不含关键词）——瞬时故障被当永久故障 | 重试 3 次 |
| **上下文超长**（含 "limit"） | **重试 3 次**，白等约 56s（8+16+32），而重试必然还是超长 | 立即返回 |
| **参数名含 limit** | 同样被误判为可重试 | 立即返回 |
| **余额不足** | 重试 3 次（余额不会自己恢复） | 立即返回 |
| 401 / 403 / 400 / 422 | 不重试（本来就是对的） | 不重试 |

> 这条改造值得在面试里讲：它不是"加功能"，而是**先用实测数据证伪自己的假设**——我原本以为缺陷是"4xx 也重试"，实测发现恰恰相反（4xx 都不重试），真正的缺陷是"500 不重试 + limit 假阳性"。

---

## 5. 限流

**能力**：全局令牌桶。超限返回 **429（Too Many Requests）而非 500**——429 是"请稍后重试"，500 是"服务坏了"，语义完全不同，前者不会触发上游熔断。

**代码位置**：`scripts/api.py:189`（`_RATE_LIMIT_RPS` / `_RATE_LIMIT_BURST`）、`scripts/api.py:_rate_limit_dep`、`tools/rate_store.py:StateStore.consume`（跨进程令牌桶，`BEGIN IMMEDIATE` 保证读-改-写原子）

**配置开关**：`PEC_RATE_LIMIT_RPS`（0=关闭）、`PEC_RATE_LIMIT_BURST`、`PEC_SHARED_STATE_DB`（跨进程）

**验证命令**

```bash
python -m pytest tests/test_rate_store.py -q --basetemp=.pytest_tmp
```

**已知边界**
默认 `RPS=0` 即关闭（避免本地开发被限流）。多 worker 部署**必须**配 `PEC_SHARED_STATE_DB`，否则每个 worker 各持一份计数，实际额度被放大 N 倍。SQLite 写锁在数千 QPS 下会成为瓶颈，届时应换 Redis adapter（`StateStore` 接口不变，替换后端即可）。

---

## 6. 审计日志

**能力**：每次工具调用输出**结构化 JSON 日志**（非自由文本），可按 `trace_id` 串起整条链路。

**固定字段**：`trace_id` / `thread_id` / `node` / `tool` / `duration_ms` / `ok` / `error_type` / `args_digest`

**关键设计**
- **入参脱敏**：只记参数摘要（截断 200 字符），不落完整值——避免大段文本打爆日志，也减少敏感信息落盘
- **trace_id 全链路**：HTTP 中间件生成 → 响应头 `X-Trace-Id` 回传 → 工作线程重新绑定（**`run_in_executor` 不传播 contextvars，必须显式绑定**）→ 工具日志携带
- **防日志注入**：上游传入的 `X-Trace-Id` 做字符白名单校验，拒绝换行与超长值

**代码位置**：`tools/wrapper.py:log_tool_call`、`logger/trace_context.py`、`scripts/api.py:_trace_middleware`

**配置开关**：无（默认开启）

**验证命令**

```bash
# 日志字段与 trace_id 传递
python -m pytest tests/test_wrapper.py tests/test_trace_context.py -q -k "log or trace" --basetemp=.pytest_tmp

# 运行态：看响应头是否回传 trace_id
curl -s -D - -o /dev/null localhost:8000/health | grep -i x-trace-id
```

**已知边界**
没有审计日志的**持久化与轮转**（只输出到 stdout，由部署侧收集）。生产级应接 Loki / ELK 并设保留期——这是刻意的边界，不是遗漏。

---

## 7. 评测集回归

**能力**：**63 条**评测用例 + CI 零消耗门禁，改动后能自动发现能力倒退。

| 档位 | 条数 | 消耗 | 何时跑 |
|---|:-:|---|---|
| `ci` | 22 | **零 LLM** | 每次提交（CI 自动） |
| `nightly` | 41 | 真实 LLM | 改 prompt / 换模型 / 调工具后按需 |

**用例分布**：15 正常 / 24 边界 / 21 对抗（提示注入、越权工具调用、超长输入、凭据索取等）

**代码位置**：`evals/eval_cases.jsonl`（用例）、`evals/run_eval.py`（runner）、`.github/workflows/ci.yml`（门禁）

**配置开关**：`--mode ci|nightly`、`--limit N`、`--cases <path>`、`--out <report>`

**验证命令**

```bash
# ci 档：零消耗，必须 100%（对抗用例零容忍）
python evals/run_eval.py --mode ci

# nightly 档：先看分布，再决定跑不跑（省额度）
python evals/run_eval.py --mode nightly --dry-run

# 覆盖率门禁（CI 同款）
python -m pytest -m "not slow and not requires_api and not requires_api_key" \
  --cov=tools --cov=metrics --cov=logger --cov-fail-under=60 --basetemp=.pytest_tmp
```

**已知边界**
① 覆盖率门禁**不含 `agents/`**（四角色分支依赖真实 LLM，强行覆盖会让测试变成烧钱且不稳定的负担）；② 对抗用例的 `must_not_leak` 断言在 nightly 档，未纳入 CI；③ 门禁取 60%（实际 63%），目的是**防倒退**不是追数字。

---

## 8. 灰度回滚

**能力**：Prompt 改坏了能**运行时秒级回滚**，无需重启、无需重新部署。

**机制（覆盖式设计）**：代码内基线不变；`prompts/v{N}/<role>.txt` 存在时按角色覆盖。因此"回滚"= 把生效版本切回基线版本，**不需要回滚代码**。

| 操作 | 命令 |
|---|---|
| 查看当前版本与各角色来源 | `GET /admin/prompt/status` |
| 运行时切换 | `POST /admin/prompt/rollback?target=v0` |
| 启动时指定版本 | 环境变量 `PEC_PROMPT_VERSION` |

**语义**：切换改的是**进程内状态**（立即生效）；进程重启后回落到环境变量值——这正是「**临时回滚**」的正确语义，永久回滚应改 `.env` 并重启。

**权限**：仅 `PEC_ADMIN_TENANTS`（默认 `tenant_jixiang`）租户可操作；非法版本号 400、越权 403、未认证 401。

**代码位置**：`tools/prompt_registry.py`、`scripts/api.py:/admin/prompt/*`、`agents/{planner,critic,executor,synthesizer}.py`（各 3 行接入）

**验证命令**

```bash
python -m pytest tests/test_prompt_registry.py -q --basetemp=.pytest_tmp

# 运行态
curl -s localhost:8000/health | python -c "import sys,json;print('prompt_version =', json.load(sys.stdin)['prompt_version'])"
```

**已知边界**
① 灰度是**进程粒度**的（给部分 worker 注入不同 `PEC_PROMPT_VERSION`），**无按请求概率灰度**——本项目 QPS 由 LLM 时延主导，进程级切分已足够；② 空/缺失覆盖文件视为无效并回退基线（防止误放空 prompt 打崩 LLM 调用）。

---

## 一键自检

```bash
bash scripts/readiness_check.sh
```

或手动执行（**注意 `--basetemp` 不可省**：项目有批量删除守卫，缺省临时目录会在清理阶段触发保护导致退出码异常）：

```bash
PY="C:/Users/jx/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

$PY -m pytest -q -m "not slow and not requires_api and not requires_api_key" \
    --basetemp=.pytest_tmp --cov=tools --cov=metrics --cov=logger --cov-fail-under=60

$PY evals/run_eval.py --mode ci
```

期望输出：`532 passed` + `覆盖率 ≥ 60%` + `ci 档 22/22 通过`。

---

## 与"真正的生产系统"还差什么（诚实说明）

八项就绪能力**都已具备**，但以下是刻意留白的部分——面试时主动说，比被挖出来强：

| 缺口 | 现状 | 若要做 |
|---|---|---|
| ~~部署件未实跑~~ **已实跑并修复** | 首次实跑即失败：`python:3.11-slim` 与 lock（生成于 3.13，`scipy==1.18.0` 要求 >=3.12）不兼容，镜像构建不出来 | ✅ 已修（基础镜像改 3.13-slim）+ CI `docker-build` job 持续守护（构建/启动/探活/无凭据断言） |
| ~~四角色测试覆盖偏低~~ **已补** | 已用 mock LLM 覆盖降级路径，`agents/` 55–74% → 58–76% | **决策：不纳入覆盖率门禁**（见下方说明）——不是遗漏，是权衡后的选择 |

> **为什么 `agents/` 不进覆盖率门禁**（2026-09-12 决策）
>
> 现状：`agents/` 已有 mock 测试覆盖（58–76%），但门禁仍只取 `tools/metrics/logger`（实测 **63% ≥ 60%**，留 3pp 缓冲）。
> 引用覆盖率时请注意区分：**63% 是门禁范围**，含 `agents` 的全仓总覆盖约 **65%**。
>
> 理由：
> 1. **门禁该守的是"退化信号"，而不是"数字好看"**。`tools/metrics/logger` 是纯逻辑层，覆盖率下降 5pp 基本等价于"真有人删了测试"；而 `agents/` 的分支大量与 LLM 交互形态耦合，覆盖率波动可能只反映"这次改动多写了几个分支"，信号噪声比高。
> 2. **强行拉高会诱导写无价值用例**。为把 58% 堆到 75%，最省力的做法是补一堆"调用一次、断言不抛异常"的用例——它们能刷数字，但抓不到真问题（本次真正有价值的 3 个缺陷，都是针对**降级路径**的断言挖出来的，与覆盖率百分比无关）。
> 3. **真正该守的东西已经用别的方式守住了**：四角色的关键边界（异常收敛、失败前缀检测、非法 action 过滤、零 LLM 路径）都有具名断言，回归时会直接失败——这比一个百分比阈值更精确。
>
> 若将来 `agents/` 引入更多**纯逻辑**（如新的启发式、确定性路径），可以只把那些模块单独纳入 `--cov`，而不是整包。
| **无多副本压测** | 跨进程状态（SQLite）已实现但未在真实多 worker 下压测 | 起 4 worker + 压测，验证限流/熔断计数不放大 |
| **无持久化审计** | 日志仅 stdout | 接 Loki/ELK + 保留期策略 |
| **无密钥轮换/配额** | 鉴权只有 Key 校验 + 租户隔离 | 密钥哈希索引 + `hmac.compare_digest` + 配额计费 |
| **重试不分类** | 4xx 也重试（浪费额度） | 按错误类型分流：可重试 vs 终止 |
| **灰度仅进程粒度** | 无按请求概率灰度 | 分流中间件 + 一致性校验 |

**一句话口径**：*"Agent 编排与成本控制做扎实、八项生产就绪能力具备、工程化约 60 分；剩下 40 分我清楚差在哪，也列得出改造路径。"*
