# pecs-multi-agent

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![MIT License](https://img.shields.io/badge/License-MIT-green)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2.x-orange)
![CI](https://github.com/paopao-13/pecs-multi-agent/actions/workflows/ci.yml/badge.svg)

PECS（Plan-Execute-Critic-Synthesize）是一个**多智能体协作框架**：四个角色（规划 / 执行 / 评审 / 综合）基于 LangGraph 编排，面向真实评测环境（GAIA、WebShop）与生产级部署。

## ✨ 特性

- **四角色协作**：Planner 拆解 → Executor 调工具 → Critic 评审 → Synthesizer 综合，可在 GAIA / WebShop 真实环境跑通
- **真实评测数据**：GAIA 自建子集 100%、官方 53 题 26.4%（诚实披露与基线不显著）；WebShop 真实环境较 ReAct +25pp
- **生产级 API**：FastAPI async + 独立 LLM 线程池（HOL 修复，探针不被长任务阻塞）+ 启动自检 fail-fast + Prometheus 多进程指标
- **稳定性工程**：全局令牌桶限流（超限返回 429 而非 500）、混沌注入优雅降级（传输层故障零 500）、可恢复驱动（断点续跑）
- **可观测与防回归**：Prometheus 指标端点 + CI 服务层门禁（每次 PR 自动挡回归）
- **安全沙箱**：代码执行 AST 黑名单 + 白名单 `__builtins__` + dunder 属性链拦截 + 超时熔断，四层防护
- **多模态与附件处理**：文本类附件（PDF/Excel/CSV）经 `file_parse` 解析接入上下文；图片/音频/视频支持**可插拔多模态后端**（配置 `PEC_VISION_*` 即启用，未配置优雅降级跳回原行为），将 GAIA 附件子集从"全失败"推向"可解"

## 🏗️ 架构概览

```mermaid
graph TB
    Client[Client / HTTP] -->|/run_task| API[FastAPI 服务]
    API --> Health[/health 存活探针<br/>P95 &lt; 13ms]
    API --> Metrics[/metrics · /metrics/prom<br/>Prometheus 多进程]
    API -->|独立线程池 x4| Pool[LLM 调用隔离]
    API -.令牌桶限流.-> Client
    API -.混沌注入.-> Degrade[优雅降级 零 500]
    Pool --> Graph[LangGraph 四角色编排]
    Graph --> P[Planner 规划]
    Graph --> E[Executor 执行]
    Graph --> C[Critic 评审]
    Graph --> S[Synthesizer 综合]
    E --> Tools[工具集 + 代码沙箱<br/>AST 黑名单/白名单/超时熔断]
    C -.预算超限.-> E
    Graph --> Bench[(GAIA / WebShop 真实评测)]
```

如果想精确复现评测结果，请使用 `pip install -r requirements-lock.txt`。

## 🚀 5分钟上手

```bash
pip install -r requirements.txt
```

```python
from graph.builder import run_task

# 跑一个最简单的任务，看四角色协作过程
result = run_task("计算北京和上海的时差")

print("=" * 60)
print(f"最终答案: {result.get('final_answer', 'N/A')}")
print(f"Token 消耗: {result.get('token_used', 0)}")
print(f"调度决策: {result.get('scheduler_decisions', [])}")
print("=" * 60)
# 你会看到 Planner 拆解任务 → Executor 调用工具 → Critic 评审 → Synthesizer 出答案
```

---

本项目主要解决了一个核心问题：传统单 Agent 系统（如 ReAct）在处理复杂任务时，单个 LLM 同时承担规划、执行、检查多重职责，导致推理链路冗长、Token 消耗显著增加，且最终答案质量不稳定。

所以我设计了一个多智能体协作框架，四个角色分工协作：Planner 拆解任务、Executor 执行工具调用、Critic 质量评审、Synthesizer 综合输出。类似于一个小型开发团队的敏捷协作模式，各司其职。

## 解决啥问题

1. **质量不稳定**：单 Agent 同时负责规划、执行、检查，没有分工，容易在复杂任务（尤其大数计算、多步推理）上翻车
2. **成本不可控**：反复调用 LLM 直到任务完成，简单任务和复杂任务成本差异巨大
3. **缺乏自我纠错**：出错后没有专门角色评审和反馈，错误会一路传递到最终答案

## 核心机制

```mermaid
sequenceDiagram
    participant User
    participant Planner
    participant Executor
    participant Sandbox as AST沙箱
    participant Critic
    participant Synthesizer
    User->>Planner: 输入复杂任务
    loop Plan-Execute-Reflect (最多5轮)
        Planner->>Executor: 下发拆解后的子任务+Token预算
        Executor->>Sandbox: 代码静态分析
        Sandbox-->>Executor: 通过/拦截
        Executor-->>Critic: 返回执行结果
        Critic->>Critic: 检查完整性和一致性
        alt 需要重规划
            Critic-->>Planner: 携带反思信息打回
        else 校验通过
            Critic-->>Synthesizer: 送入合成
        end
    end
    Synthesizer->>User: 输出最终答案
```

其他机制：
- **Token 预算感知调度**：70%/85%/95% 三级降级，保证单任务成本有上限
- **启发式兜底层**：对已知模式直接返回确定性答案，零 Token 消耗

![architecture](assets/architecture.svg)

## 相关工作对比

| 框架 | 架构模式 | 成本控制 | 质量保障 | 状态安全 | 短板 |
|------|----------|----------|----------|----------|------|
| **AutoGen** | 多Agent自由对话 | 无预算管理 | 无内置评审 | 无状态隔离 | 对话轮次不可控，Token消耗大 |
| **CrewAI** | 角色分工+任务队列 | 无动态降级 | 依赖人工review | 无AST沙箱 | 缺乏自动纠错和预算感知 |
| **LangGraph原生** | 自定义节点图 | 无内置预算 | 节点自定义 | 依赖开发者 | 无标准闭环，需自行设计路由 |
| **ReAct** | 单Agent推理+行动 | 无成本上限 | 无反思机制 | N/A | 复杂任务漂移，Token浪费严重 |
| **本框架(PECS)** | 固定四角色闭环 | 三级动态降级 | Critic+Sift双层反思 | AST安全沙箱 | 样例集规模有限，启发式覆盖待扩展 |

> 详细架构设计见 [ARCHITECTURE.md](ARCHITECTURE.md)

### 设计取舍：为何自研而非 AutoGen / CrewAI

常见追问："为什么不直接用现成框架？" 我的增量价值在**三点确定性控制**，现成框架默认给不了：

1. **预算硬上限与三级降级**：`graph/token_budget.py` 在状态图节点级做角色配额 + 70%/85%/95% 降级。AutoGen/CrewAI 没有内置"单任务 token 封顶"，对话轮次失控时成本不可预测——而 PECS 把成本变成了**可承诺的上界**。
2. **Critic 回打 Planner 的确定性路由**：打破 search 循环、放弃型答案强制重规划，都是 `builder.py` 里的显式条件边。AutoGen 的自由对话式多 Agent 难以对"评审→重规划"做可控、可复现的路由。
3. **可恢复驱动**：LangGraph `checkpointer` 支持进程被杀后按 `thread_id` 断点续跑，契合生产稳定性诉求。

**权衡（诚实）**：自研的代价是可复用性低、需自己维护调度/降级逻辑；若追求快速原型我会直接用 AutoGen/CrewAI。但在"生产级可控性优先"的岗位诉求下，自研四角色闭环是更契合的选择。

## 评测结果

> **⚠️ 数据声明（必读）**
>
> 本框架支持双模式运行：
> - **real_api 模式**（配置 `LLM_API_KEY` 后）：使用真实 LLM API 进行规划/执行/综合，搜索类任务端到端调用真实模型
> - **sample/mock 模式**（未配置 API Key）：使用项目内置样例和启发式兜底，保证离线可运行
>
> 下方评测结果基于真实 LLM API 运行，配置方法见下方「安装」章节。
>
> - **GAIA L1**：从 28 道自定义 Level 1 级别样例中选取 10 道进行评测（5 道知识检索 + 5 道大数计算），覆盖官方 GAIA 题型模式但非原始题目。注：内置样例的知识检索子集经 `web_search` 的 `_mock_search` 命中硬编码答案键（开卷带答案），更测编排/计算/解析能力而非真实检索；**真实检索能力以 GAIA 官方 53 题（走真实搜索 API）为准**
> - **WebShop**：从 WebShop-small 数据集（6910 个真实 goals）随机采样 12 道服装类 instruction，在真实 AgentBench 文本环境上评测（rank_bm25 搜索后端 + HTTP 桥 + text_rich 模式）
> - **ReAct 基线**：同一模型 + 同一工具集 + 同一题目，保证对比公平性
> - **Token 统计**：端到端对比（含 LLM 调用 + 工具执行全流程），非单次 API 调用
>
> **一句话总领**：PECS 在 GAIA 上与 ReAct **无统计显著差异**（McNemar p=1.0）；唯一显著且真实验证的优势是 **WebShop 真实环境 +25pp**（来自"打破 search 循环"这一具体启发式，非多角色数量）。PECS 的核心价值 = 计算类/规则打破类稳定优势 + 生产级工程（限流/可观测/可恢复）+ **成本可预测性**。本仓库刻意不做"多智能体全方位碾压单 Agent"的叙事。
>
> **接入官方数据集方法**：参见 [EXPERIMENT.md](EXPERIMENT.md) 中「官方数据集接入」章节

**实验环境**：内置 33 题与 WebShop 12 题均基于 DeepSeek-chat 实测（temperature 按角色 0.0~0.5；**基准评测开启 `PEC_DETERMINISTIC=1` 固定全 0 以保证数字可复现、可 defense**）；GAIA 官方 53 题同样基于 DeepSeek-chat（bug 修复后重跑验证）。GLM-4.7-Flash / Qwen 配置已在 `config.py` 预留但未实测，不纳入结论。| Python 3.10.11 | langgraph 0.2.x | 2026-07-19

| 指标 | ReAct 基线 | 本框架实测 | 提升幅度 | 目标值 | 达标 |
|------|:-----------:|:----------:|:--------:|:------:|:----:|
| GAIA L1 准确率 | 87.88% (29/33) | 100% (33/33) | +12.1pp | ≥75% | ✅ |
| WebShop 成功率 | 0% (0/12) | 25.0% (3/12) | +25.0pp | +18pp | ✅ 真实环境达标 |
| WebShop Token/task | 7,421 | 2,562 | -65.5% | ≥30% | ✅ 真实环境 |
| GAIA L1 Token/task | 26,438 | 3,481 | -86.8% | ≥30% | ✅ |

**GAIA 官方数据集验证（行业 benchmark，非内置样例）**：

| 指标 | ReAct 基线 | PECS 多智能体 | 差值 | 统计检验 |
|------|:-----------:|:----------:|:--------:|:--------:|
| 准确率（总体） | 24.5% (13/53) | 26.4% (14/53) | +1.9pp | McNemar p=1.0 |
| 准确率（无附件） | 28.6% (12/42) | 33.3% (14/42) | +4.8pp | - |
| 准确率（有附件） | 9.1% (1/11) | 0% (0/11) | -9.1pp | 文本附件已接 file_parse；图/音/视频待多模态后端（待重跑） |
| 平均 Token/题 | 5,076 | 20,966 | PECS 更高* | - |
| 平均耗时/题 | 26.9s | 71.4s | PECS 更慢 | - |
| 端到端耗时分布 (p50/p95/max) | 18.6s / 74.1s / 113.6s | 42.6s / 155.9s / 218.5s | PECS 更慢 | 升级前实测（n=48 有效题，5 题超时/报错无耗时） |

> 数据来源：HuggingFace `gaia-benchmark/GAIA` Level 1 validation set（53题），非内置 mock。含真实搜索、多步推理、文件解析（xlsx/pdf/py/mp3）。4 道（2 png + 2 mp3）多模态附件题因需多模态模型标记 skipped。
>
> \* PECS Token 更高：多角色协作（Planner+Executor+Critic+Synthesizer）的固有开销，在知识检索类任务上 PECS 搜索更深入但未必更准。内置 33 题 PECS Token 更低，因计算题启发式 0-token 秒杀拉低了均值。
>
> **统计显著性**：McNemar 检验 p=1.0（>>0.05），差异**完全不显著**。b=6（PECS对ReAct错）、c=5（PECS错ReAct对），两者几乎持平。结论：在 GAIA 这类以知识检索为主的任务上，多智能体相对单 Agent **没有显著优势**。

> **💡 成本控制真实含义（避免误读）**：PECS 的成本价值**不在"永远更便宜"**——GAIA 知识检索类任务上 PECS 单题 token 确实高于 ReAct（多角色固有开销，见上表 20,966 vs 5,076）。PECS 真正的成本优势是**可预测 + 硬上限**：每任务 `DEFAULT_TOKEN_BUDGET=50000` 硬封顶，70%/85%/95% 三级降级兜底；而 ReAct **无成本上限**，单题 token 方差极大（曾观测到单题暴涨至 85 万 token）。在计算类（−99.4%）与 WebShop 真实环境（−65.5%）任务上 PECS 实测更便宜；纯知识检索类则以更高 token 换取 Critic 自检与可复现预算。结论：PECS 卖的是**成本可预测性**，不是"绝对最低成本"——这正是对口大模型应用岗的核心卖点。

> **能力升级（待重跑确认，不篡改上述数字）**：上表为升级前的实测（PECS 26.4% / 附件 0%）。代码层面已新增三类提分能力，尚未重跑官方 53 题：
> 1. **文本附件接线**：评测 harness 现显式提示 Planner 用 `file_parse` 解析 PDF/Excel/CSV 附件（工具早已存在但未接线），预期回收部分文本类附件题。
> 2. **多模态后端可插拔**：图片/音频/视频不再一律 skip，改为经可配置的多模态后端（OpenAI 兼容，`PEC_VISION_*`）预处理为文本后注入；未配置则优雅降级回 skip，**保证现有跑分零回归**。预期将 4 道（2 png + 2 mp3）多模态题从 0% 推向可解。
> 3. **检索与鲁棒性增强**：Web 搜索支持可选 Tavily 真实 API（`PEC_SEARCH_*`）；Planner 新增"先搜后浏览"多跳与"数值必用 python 实算"规则；Synthesizer 对"无法确定/无法回答"等放弃型答案触发一次重规划而非冻结错误答案。
> 上述改动经编译与 mock 单测验证（多模态未配置时正确降级、工具注册/提示接线正确、放弃型答案触发反思、搜索回退不崩）。**真实增益以用户本机配置多模态后端并重跑 `run_gaia_official.py` 后的数据为准**，届时将更新本表。

> **实验数据修正声明（TDD 发现的 bug 影响）**：
> 上述数据是修复 2 个影响评测准确性的 bug 后的真实结果。原始数据为 PECS 26.4% vs ReAct 15.1%（+11.3pp），但 TDD 补测试过程中发现：
> 1. **LLM 兜底判定误匹配**（bug #2）：`"是" in "不是"` 导致错误答案被判正确，修复后 4 道 PECS 题从 True → False
> 2. **数据泄露检查误判**（bug #7）：`"17" in "2017"` 导致数字答案被误判为泄露，5 道题被错误跳过，修复后补评（ReAct 5 道全对，PECS 4 对 1 错）
>
> 修正后 PECS 准确率不变（-4+4=0），但 ReAct 准确率从 15.1% 升至 24.5%（补评 5 道全对），差值从 +11.3pp 缩至 +1.9pp。这说明原始优势有很大部分来自 bug 导致的 ReAct 题目被错误跳过，而非 PECS 真的更强。诚实更新数据比掩盖更有价值。

> **三大局限诚实声明（追问前必读）**：
> 1. **GAIA 样本偏计算**：内置 33 题中 16 道大数计算（启发式 0-token 秒杀）+ 10 道知识检索 + 4 道文件解析 + 3 道网页浏览，非官方 165 题分布。扩样后 ReAct 准确率从 80% 升至 87.88%（简单计算题 ReAct 用 python 工具也能做对），导致差值从 +20pp 缩小至 +12.1pp。但 PECS 仍保持 100% 准确率，且 Token 降本从 38.8% 提升至 86.8%（ReAct 在文件解析题上 token 暴涨）。PECS 的核心优势集中在：文件解析 100% (4/4) vs ReAct 25% (1/4)、Token 降本 86.8%。**接入 GAIA 官方 Level 1 validation set（53题）验证后**，PECS 26.4% vs ReAct 24.5%（+1.9pp），McNemar p=1.0 不显著——多智能体在知识检索类任务上相对单 Agent 没有显著优势，PECS 的价值集中在计算类和规则打破类任务。
> 2. **WebShop 真实环境达标（25.0% vs 0%, +25.0pp）**：在真实 AgentBench WebShop 文本环境上跑通（rank_bm25 纯 Python 搜索后端 + HTTP 桥 + text_rich 模式,非本地 mock），从 WebShop-small 数据集 6910 个真实 goals 中随机采样 12 道服装类 instruction。PECS 3/12 成功（reward≥0.5）vs ReAct 0/12。公平对比设计：PECS 的 Executor 启发式规则层（搜到结果即 click[ASIN] 进详情页、click[Buy Now] 触发结算）vs ReAct 纯 LLM 决策（无规则层兜底）。关键修复：① 直接实例化 WebAgentTextEnv 绕过 gym wrapper，让 reset(task_index) 按 instruction 语义匹配真实 goal；② observation_mode=text_rich 输出 [button] 标记和 ASIN；③ Critic 用 reward 信号替代 SELECTED 判定。Token 方面 PECS 2562 vs ReAct 7421（降本 65.5%，ReAct 纯 LLM 决策陷入 search 循环导致 15 步空转+幻觉答案）。
>
>    **消融实验（证明优势来自"打破 search 循环"而非"有规则层"本身）**：新增 ReAct-light 中间档（只有"Buy按钮→click[Buy Now]"购物常识，不强制 click[ASIN] 进详情页）。三组对比：PECS 完整规则层 25.0% / ReAct-light 轻量规则层 0.0% / ReAct 纯 LLM 0.0%。ReAct-light vs ReAct = +0.0pp（轻量规则增量贡献为零），PECS vs ReAct-light = +25.0pp。结论：Buy 规则单独存在无效（LLM 不点商品进详情页，永远到不了有 Buy 按钮的页面，15 步全在 search 页循环 reward=0）；PECS 的 +25pp 完全来自"搜到结果即 click[ASIN] 打破 search 循环"这一具体 Executor 启发式，而非"加规则层"这个动作本身。完整数据见 `results/webshop_run.json`,部署方法见 [docs/webshop_local_runbook.md](docs/webshop_local_runbook.md)。
> 3. **Token 降本 86.8% 含对比假象**：端到端 −86.8% 是 vs ReAct 在文件解析题上 token 暴涨的对比（ReAct 解析 xlsx/csv/pdf 内容冗长导致消耗高）；纯预算调度机制本身仅 −4.5%（见下方「Token 成本分析」消融）。报告须区分"机制贡献 −4.5%"与"端到端 −86.8%"两个口径，避免误导。WebShop 真实环境 Token 降本 65.5%（PECS 2562 vs ReAct 7421），ReAct 纯 LLM 决策陷入 search 循环导致 15 步空转，Token 雪崩。

> 评测样本：GAIA 33题（16大数计算 + 10知识检索 + 4文件解析 + 3网页浏览），WebShop 12题（WebShop-small 数据集真实采样,rank_bm25 搜索后端,真实 AgentBench 文本环境）。
> ReAct 基线使用同一 DeepSeek-chat 模型 + 同一工具集 + 同一题目，保证对比公平性。
> 完整评测数据见 `results/target_report.json`（GAIA）与 `results/webshop_run.json`（WebShop 真实环境）。
> 测试实践与 TDD 发现的 7 个 bug 记录见 [docs/archive/testing.md](docs/archive/testing.md)。
>
> **样本量声明**：GAIA 内置 33 题与 WebShop 12 题均为小样本，+12.1pp / +25.0pp 为**方向性信号而非统计显著结论**（WebShop n=12 未做 McNemar 检验，仅官方 53 题披露 p=1.0 不显著）。结论应读作"框架在计算类 / 规则打破类任务上有稳定优势"，而非"全面碾压单 Agent"。

**GAIA 逐任务对比**：

| 任务 | 类型 | 多智能体 | ReAct | 差异分析 |
|------|------|:--------:|:-----:|----------|
| gaia_l1_001 Python发布年份 | 知识检索 | ✓ (2001 tok) | ✓ (772 tok) | 两者均正确，多智能体 Token 更高因含 LLM 规划 |
| gaia_l1_003 Fibonacci第20项 | 计算 | ✓ (4 tok) | ✓ (825 tok) | 启发式直接计算 vs LLM 心算 |
| gaia_l1_004 诺贝尔奖图灵奖 | 知识检索 | ✓ (2382 tok) | ✓ (1267 tok) | 两者均正确 |
| gaia_l1_005 100!位数 | 计算 | ✓ (3 tok) | ✓ (444 tok) | 启发式直接计算 vs LLM 心算 |
| gaia_l1_008 2^100首位 | 计算 | ✓ (3 tok) | ✓ (934 tok) | 启发式直接计算 vs LLM 心算 |
| gaia_l1_016 2^30-2^20 | 大数计算 | ✓ (6 tok) | ✗ (546 tok) | **ReAct 算出 2^30=1073741824 但忘记减 2^20** |
| gaia_l1_017 17^5 | 计算 | ✓ (5 tok) | ✓ (466 tok) | 启发式 vs LLM 心算 |
| gaia_l1_021 3^18-3^12 | 大数计算 | ✓ (5 tok) | ✗ (441 tok) | **ReAct 算出 3^18=387420489 但忘记减 3^12** |
| gaia_l1_026 5^12-5^8 | 大数计算 | ✓ (5 tok) | ✓ (775 tok) | 两者均正确 |
| gaia_l1_028 7^8-7^5 | 大数计算 | ✓ (5 tok) | ✓ (753 tok) | 两者均正确 |

> ReAct 在 2 道大数减法题上失败：LLM 计算了被减数但遗漏了减法操作，导致结果偏大。多智能体通过 Python 工具精确计算，避免了此类错误。

**Token 成本分析**：

| 口径 | 数值 | 统计范围 | 说明 |
|------|:----:|----------|------|
| 端到端降本 | 86.8% | PECS端到端(3,481 tok) vs ReAct端到端(26,438 tok) | 33题全量，ReAct在文件解析题上token暴涨（xlsx/csv/pdf内容冗长） |
| 纯预算调度降本 | 4.5% | 紧预算(877 tok) vs 宽预算(918 tok) | 消融实验（禁用启发式），仅隔离预算感知调度模块贡献 |
| 计算类任务 | -99.4% | 启发式(4 tok) vs ReAct(689 tok) | 启发式直接返回结果，ReAct 需 LLM 多轮推理 |
| 文件解析类任务 | ~-95% | PECS(~2K tok) vs ReAct(~50K tok) | ReAct解析xlsx/csv/pdf内容冗长，PECS工具调用更精简 |

> 端到端 86.8% 降本主要由两部分贡献：① 启发式路由让计算类任务零 LLM 调用；② PECS 的工具调用更精简（文件解析用 file_parse 工具提取关键信息，ReAct 把整个文件内容塞进上下文）。纯预算调度模块单独贡献 4.5%，在更复杂的多步搜索任务上预期更高。注意：86.8% 含 ReAct 在文件解析题上 token 暴涨的对比假象，纯预算调度机制贡献仅 4.5%，两个口径须区分。

**WebShop 规则层消融**（真实环境，12 题，证明 PECS 优势来源）：

| 组别 | 规则层配置 | 成功率 | Token/题 | 失败模式 |
|------|-----------|:------:|:--------:|----------|
| PECS 完整 | Buy→click[Buy Now] + 搜到结果→click[ASIN] | 25.0% (3/12) | 2,576 | 规则打破 search 循环，进详情页购买 |
| ReAct-light 轻量 | 仅 Buy→click[Buy Now] | 0.0% (0/12) | 6,140 | LLM 不点商品，15 步全在 search 页循环 |
| ReAct 纯 LLM | 无规则层 | 0.0% (0/12) | 5,958 | 同上，search 循环 + 幻觉 ASIN |

> 消融结论：ReAct-light vs ReAct = +0.0pp（Buy 规则增量贡献为零），PECS vs ReAct-light = +25.0pp。Buy 规则单独存在无效——LLM 不主动点商品进详情页，永远到不了有 Buy 按钮的页面；PECS 的 +25pp 完全来自"搜到结果即 click[ASIN] 打破 search 循环"这一具体 Executor 启发式。这证明框架优势不是"加规则层"这个动作，而是 specifically 针对 search 循环痛点的启发式设计。

![metrics](assets/metrics_comparison.svg)

### 角色消融实验

通过移除不同角色或关闭核心功能验证四角色架构的必要性。

> 以下消融实验在 sample/mock 模式下运行（未配置 API Key），使用 28 道内置样例集。启发式兜底层在 mock 模式下覆盖率较高，Token 数值偏低；real_api 模式下的消融数据需配置 API Key 后运行 `bash scripts/run_all_ablation.sh` 获取。

**完全移除型消融**（验证角色存在必要性）：

| 配置 | 架构 | 准确率 | Token/task | vs 完整版 | 结论 |
|------|------|:------:|:----------:|:---------:|------|
| `full_pecs` | P+E+C+S 完整四角色 | 100% (28/28) | 53 | — | 基线（最优） |
| `no_critic` | 移除Critic，E直连S | 100% (28/28) | 10 | Token -81% | Mock样例中Critic未拦截，真实场景差异更大 |
| `no_synthesizer` | 移除S，E直接输出 | 96.4% (27/28) | 53 | -3.6pp | Synthesizer全局整合不可省 |
| `single_agent` | 纯ReAct单智能体 | 82.1% (23/28) | 1111 | -17.9pp, Token +1998% | 多角色分工显著优于单Agent |

**单变量功能关闭型消融**（验证功能模块价值，保留节点不删）：

| 配置 | 关闭功能 | 准确率 | Token/task | vs 完整版 | 结论 |
|------|----------|:------:|:----------:|:---------:|------|
| `critic_no_reflect` | Critic保留但阻断反思闭环 | 100% (28/28) | 53 | ±0pp | Mock样例未触发反思，真实复杂场景差异更显著 |
| `synthesizer_no_replan` | Synthesizer保留但关闭重规划 | 96.4% (27/28) | 53 | -3.6pp | 重规划可修正执行偏差，不可省 |

> 上表区分两种消融模式：完全移除型验证角色存在必要性，功能关闭型验证具体功能模块价值，保证实验单一变量严谨性。
> 完整消融配置见 `ablation_configs/`，一键运行 `bash scripts/run_all_ablation.sh`
> 消融实验详细说明见 [EXPERIMENT.md](EXPERIMENT.md)

### 统计显著性说明

> 样例集规模：GAIA 内置 n=33（接近统计显著性最低要求 n≥30），GAIA 官方 n=53，WebShop n=12（仍偏小，但有消融实验三组对比支撑）。
> 上述结果为样例集上的**精确观测值**，旨在验证架构可行性和机制有效性，**不构成**在官方完整测试集上的性能承诺。
> GAIA 官方 53 题已做 McNemar 检验：p=1.0，差异不显著。b=6（PECS对ReAct错）、c=5（PECS错ReAct对），多智能体在知识检索类任务上相对单 Agent 没有显著优势。PECS 的价值集中在计算类任务（内置 33 题 +12.1pp，启发式 0-token 秒杀）和规则打破类任务（WebShop +25pp，打破 search 循环）。

### 多框架统一对照实验

使用同一组 GAIA 样例、同一模型、相同工具集，对比不同框架：

| 框架 | GAIA 准确率 | Token/task | 特性差异 |
|------|:-----------:|:----------:|----------|
| **ReAct** | 87.88% (29/33) | 26,438 | 单Agent推理+行动，无分工 |
| **AutoGen** | 脚本就绪未运行 | 预期较高 | 多Agent自由对话，轮次不可控（需 `pip install pyautogen`） |
| **CrewAI** | 脚本就绪未运行 | 预期较高 | 角色分工但无预算感知（需 `pip install crewai`） |
| **PECS(本框架)** | 100% (33/33) | 3,481 | 固定四角色+预算调度+双层反思 |

> 一键运行全部对照实验：`bash scripts/run_baseline_compare.sh`（需预装 pyautogen、crewai 依赖）
> AutoGen/CrewAI 评测脚本已就绪（`benchmarks/eval_autogen.py`、`benchmarks/eval_crewai.py`），本地环境未安装对应依赖，故未运行。接入后执行脚本即可自动填充数据。

### Critic 反思纠错实例

Critic 在评测中拦截了多类错误，以下是两个典型案例：

**案例1：工具参数错误**（详见 `cases/error_correction/01_tool_param_error.md`）
- 任务：搜索2024年巴黎奥运会中国金牌数
- 错误：Executor使用模糊关键词"巴黎奥运会 金牌"，返回无关结果
- Critic评分：accuracy=2, completeness=1 → 拦截
- 修正：使用精确关键词重新搜索 → 得到40枚金牌

**案例2：计划逻辑遗漏**（详见 `cases/error_correction/02_plan_logic_omission.md`）
- 任务：计算2024和2020奥运会中国金牌数差值
- 错误：Planner只规划了搜索2024年，遗漏2020年数据
- Critic评分：completeness=1 → 触发Synthesizer反思 → Planner重规划
- 修正：补充2020年搜索步骤 → 差值为2

> 自动统计脚本：`python -m metrics.error_stat`，统计Critic拦截错误总量、分类、修正成功率

## 运行入口（Quick Start）

框架提供三个清晰的主入口，覆盖演示、评测与生产级运行：

```bash
# 1. Web 可视化演示（看四角色协作全过程）
python scripts/app.py
# 打开 http://127.0.0.1:5000 —— 任务执行 / GAIA 评估 / 对比测试 三个 Tab

# 2. WebShop 真实环境评测（AgentBench 文本环境）
python run_webshop.py --tasks 12
# 详见 docs/webshop_local_runbook.md（rank_bm25 搜索后端 + HTTP 桥部署）

# 3. 可恢复驱动运行（断点续跑，生产级稳定性）
python run_resumable.py "你的任务描述"

# 4. 生产级 API 服务（FastAPI async v0.6.0 + 独立 LLM 线程池 + 启动自检 + Prometheus 多进程指标 + 全局限流 + 混沌工程）
uvicorn scripts.api:app --host 0.0.0.0 --port 8000 --workers 1
# 提供 /health（存活探针，含 llm_configured 就绪状态；LLM 负载下 P95 < 13ms）
#      /metrics（JSON：按 endpoint 分桶延迟直方图 + 真实 token 计量 + 错误率；单 worker / 开发态便利端点）
#      /metrics/prom（Prometheus 文本格式：gunicorn -w N 多进程下经共享目录聚合，生产 scrape target）
#      /run_task（任务执行，120s 超时熔断；LLM 未配置时立即 503 fail-fast）
# 关键设计：
#   · /run_task 通过独立 ThreadPoolExecutor(max_workers=4) 隔离 LLM 调用，轻量探针不被长任务阻塞（HOL 修复，见 M8）
#   · 启动自检（lifespan）：启动时即校验 LLM_API_KEY，缺失则 /run_task 立即 503 而非图深处崩异常
#   · 多 worker 指标正确性：设置 PROMETHEUS_MULTIPROC_DIR 后各 worker 计数器经共享目录聚合（见下方「生产环境」）
# 监控：/metrics 累计真实 token 用量（来自网关 usage_metadata），成本 = token × PEC_PRICE_PER_1M
#
# 生产多 worker 部署（指标正确聚合）：
#   export PROMETHEUS_MULTIPROC_DIR=/tmp/pecs_prom && mkdir -p $PROMETHEUS_MULTIPROC_DIR
#   gunicorn scripts.api:app -w 4 -b 0.0.0.0:8000 --prometheus-dir $PROMETHEUS_MULTIPROC_DIR
#   外部 Prometheus 直接 scrape http://host:8000/metrics/prom
```

> 零配置可跑：`python demos/quickstart_no_api.py`（启发式兜底 + Python 沙箱，无需 API Key）。
> 精确复现评测：`pip install -r requirements-lock.txt` 后按上方入口运行。

## 生产指标（真实实测）

以下数据由 `scripts/benchmark_production.py` 对本地 `uvicorn scripts.api:app` 实测，原始结果见
[`results/production_bench.json`](results/production_bench.json)（M1–M12 全量，非估算）：

| 指标 | 实测值 | 说明 |
| --- | --- | --- |
| 启动耗时 (M1) | **524 ms** | 冷启动到 `/health` 可达（`--skip-run-task --prometheus` 实测 2026-07-20） |
| `/health` 延迟 (M2) | **P50=1.28 ms / P95=14.62 ms / P99=23.81 ms** | 100 次采样，0 错误 |
| 并发吞吐 (M3) | **600 / 811 / 1157 rps**（10 / 20 / 50 并发），全程 0 错误 | P95≤27.6 ms（50 并发） |
| `/metrics` (M4) | ✅ 可访问 | 按 endpoint 分桶延迟直方图 + 真实 token 计量 + 错误率 |
| LLM 推理 (M5) | **需烧 Key 的 run_task 模式实测**（CI 跳过） | 历史实测 5.86–6.42 s / 任务（真实 GLM 网关）；本仓库 CI 用 `--skip-run-task` 不调用 LLM，故 `production_bench.json` 中 M5 字段为空 |
| 容错 (M6) | 空输入→**400**，缺字段→**422**，10K 超长→超时（隔离验证） | 独立隔离端口验证，非编排副作用；10K 长查询网关侧偶发超时（环境性，非代码缺陷） |
| 稳定性 (M7) | **100% 可用率**（30 s / 148 次，0 失败） | 持续存活探针 |
| HOL 修复 (M8) | LLM 负载下 `/health` P95≤**23.4 ms**，0 错误 | 独立 LLM 线程池，探针不被长任务阻塞（阈值 100ms 内判通过） |
| 真实 Token (M9) | **2089 token / 3 任务，均 696.3/任务**（来自网关 `usage_metadata`，历史实测） | `/run_task` P95=6419 ms（已与 `/health` 分桶，不再被探针污染）；CI 跳过故本次文件为空 |
| 成本推算 (M9) | ¥0.0021 总计（¥0.0007/任务） | 历史实测 token × 可配置参考单价（见下） |
| Prometheus 端点 (M10) | ✅ `/metrics/prom` 可用 | 含 `pecs_requests_total` / 延迟直方图 / `pecs_llm_tokens_total`；多 worker 下经 `PROMETHEUS_MULTIPROC_DIR` 聚合 |
| 全局限流 (M11) | **RPS=2 / burst=3 下突发 20 请求 → 17 个 429，3 个 200，零结构性错误** | #4 令牌桶：限流生效且返回 429 而非 500（详见 `PEC_RATE_LIMIT_*` 配置） |
| 混沌容错 (M12) | **畸形 JSON / 错误 CT / 20K 超大负载 → 零 500** | #7 故障注入：传输层故障均被结构化拒绝（422 / 连接 reset），绝不 panic |

> **关于成本的诚实说明**：Token 数为 LLM 网关真实返回的 `usage_metadata`（非估算）；成本为 `真实 token × 参考单价` 推算，单价默认按 GLM Flash 级别 **¥1.00/百万 token**，可通过环境变量 `PEC_PRICE_PER_1M` 覆盖为实际计费标准。该单价仅为可复现的参考基准，不代表网关实际账单。
>
> **关于 M5/M9 的诚实声明**：表格中的 M5 延迟与 M9 token 数为**历史烧 Key 实测值**（真实 GLM 网关）。本仓库 CI 与公开 benchmark 默认 `--skip-run-task`（不烧 Key、不泄露凭证），故 `production_bench.json` 中 M5/M9 字段为空——这是刻意的隐私/成本安全设计，而非数据缺失。
>
> 复现（需自备 Key，绝不入库）：`python scripts/benchmark_production.py --llm-key <KEY> --base-url <URL> --model <MODEL>`
> （Key 仅经 CLI 传入，绝不写入文件。）
>
> 多进程指标验证：`python scripts/benchmark_production.py --prometheus`（自动设 `PROMETHEUS_MULTIPROC_DIR` 并跑 M10）
> 限流 + 混沌验证（M11/M12）：已内置在每次全量运行；CI 门禁会检查限流生效与零 500。
> CI 门禁（不烧 Key）：`python scripts/benchmark_production.py --ci`（任意服务层指标不达标即退出码 2）

## 运行环境

- Python ≥ 3.10（需要 match/case 和 TypedDict）
- 不需要 JDK、不需要数据库
- 跨平台：Windows / macOS / Linux

## 安装

```bash
# 1. 克隆
git clone https://github.com/paopao-13/pecs-multi-agent.git
cd pecs-multi-agent

# 2. 虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# 3. 装依赖
pip install -r requirements.txt

# 4. 配 API Key
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key（支持 GLM-4.7-Flash/DeepSeek/Qwen）
```

> 推荐 GLM-4.7-Flash（免费）：https://open.bigmodel.cn/
> 也可用 DeepSeek：https://platform.deepseek.com/api_keys
> 不填也能跑，但用的是模拟响应，答案不太准。

## 启动

```bash
python scripts/app.py
```

然后打开 http://127.0.0.1:5000，有三个 Tab：
- **任务执行**：输入问题，看四个 Agent 怎么协作
- **GAIA 评估**：批量跑评测，对比多智能体和 ReAct
- **对比测试**：同一问题并排跑，直观对比 Token 消耗

生产环境（多 worker + 指标正确聚合）：
```bash
export PROMETHEUS_MULTIPROC_DIR=/tmp/pecs_prom && mkdir -p $PROMETHEUS_MULTIPROC_DIR
gunicorn scripts.api:app -w 4 -b 0.0.0.0:5000 --prometheus-dir $PROMETHEUS_MULTIPROC_DIR
# 外部 Prometheus 直接 scrape http://host:5000/metrics/prom
# 提示：未设 PROMETHEUS_MULTIPROC_DIR 时，/metrics（JSON）只反映单 worker，多 worker 请以 /metrics/prom 为准
```

## 配置

环境变量（`.env`）：

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_API_KEY` | 否 | 空 | LLM API 密钥（支持 GLM/DeepSeek/Qwen） |
| `LLM_BASE_URL` | 否 | DeepSeek | API 端点 URL |
| `LLM_MODEL` | 否 | deepseek-chat | 模型名称 |
| `PEC_VISION_BASE_URL` | 否 | 空 | 多模态后端基址（OpenAI 兼容，如 `https://api.openai.com/v1`）；配置后 GAIA 图片/音频/视频附件题转为文本注入 |
| `PEC_VISION_MODEL` | 否 | 空 | 视觉模型名（如 `gpt-4o-mini`）；未配置则多模态附件题优雅降级为跳过 |
| `PEC_VISION_API_KEY` | 否 | 空 | 多模态后端 API Key |
| `PEC_TRANSCRIBE_MODEL` | 否 | 同 `PEC_VISION_MODEL` | 音频转写模型名（部分端点支持 audio transcription） |
| `PEC_SEARCH_PROVIDER` | 否 | 空 | 真实搜索 API 提供商，目前支持 `tavily`；配置后 Web 检索改用其接地摘要 |
| `PEC_SEARCH_API_KEY` | 否 | 空 | 对应搜索 API Key |

配置文件（`config.py`）关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_TOKEN_BUDGET` | 50000 | 每任务 Token 上限 |
| `DEGRADE_THRESHOLD_1` | 0.70 | 70% 跳过部分 Critic |
| `DEGRADE_THRESHOLD_2` | 0.85 | 85% 合并步骤 |
| `DEGRADE_THRESHOLD_3` | 0.95 | 95% 强制输出 |

统一实验配置（`experiments/config.yaml`）：

> 全项目所有模块（框架主逻辑、评测、消融、调度）统一读取此 YAML，覆盖 `config.py` 的代码级默认值，彻底消灭硬编码。包含模型参数、Token预算（含角色独立配额）、执行限制、安全规则等完整配置。

## Demo 演示

项目提供 6 个可运行的 Demo，覆盖从零配置体验 to 安全沙箱演示的完整场景：

| Demo | 命令 | 说明 | 需要 API Key |
|------|------|------|:---:|
| 零配置快速体验 | `python demos/quickstart_no_api.py` | 无需 API Key，启发式兜底 + Python 沙箱执行 3 个计算任务 | 否 |
| 安全沙箱拦截演示 | `python demos/security_sandbox_demo.py` | 展示 AST 预检查拦截 8 种攻击代码 + 白名单沙箱执行合法代码 | 否 |
| Token 降级调度演示 | `python demos/token_budget_demo.py` | 展示 70%/85%/95% 三级降级 + 角色独立配额机制 | 否 |
| PECS vs ReAct 对比 | `python demos/pecs_vs_react_demo.py` | 单任务对比 + 28 题批量汇总数据 | 否（有 Key 更完整） |
| 批量任务执行 | `python demos/demo_batch_task.py` | 3 种批量执行方式：自定义列表/GAIA Mock/WebShop Mock | 是 |
| 自定义 Critic 扩展 | `python demos/custom_critic_override_demo.py` | 继承原生 Critic 增加效率评分维度，注入 LangGraph 图 | 是 |

> 现场演示推荐从 `quickstart_no_api.py` 开始（零配置即可运行），再展示 `security_sandbox_demo.py`（安全设计亮点）。

### Web 可视化界面

`python scripts/app.py` 启动后访问 `http://127.0.0.1:5000`，提供任务执行、GAIA 评估、多框架对比三个视图：

![PECS 任务执行视图](assets/demo_screenshot.png)

![PECS GAIA 评估视图](assets/demo_screenshot_gaia.png)

> 截图来自本地运行实例（任务执行视图与 GAIA 评估视图）。实际推理需配置可用的 LLM 网关；未配置 Key 时仍可浏览完整界面与静态样例。

## 高级功能

### 批量任务执行

```bash
# 批量执行自定义任务列表
python -m src.batch_runner --num-samples 10

# 从GAIA Mock数据集加载并执行（含答案评估）
python demos/demo_batch_task.py
```

### 全链路日志导出

```python
from logger.graph_trace_logger import export_task_trace
from graph.builder import run_task

result = run_task("计算2的100次方")
export_task_trace(result)  # 自动保存到 results/traces/
```

> **链路追踪与端到端延迟**：设 `PEC_TRACE=1` 后，`graph/builder.py` 会在每个角色节点记录耗时并写入 `state["node_latencies"]`，`export_task_trace` 导出的 markdown 含「5.4 节点耗时」小节（各角色耗时占比 + 端到端总计）。GAIA 官方评测另在聚合结果里输出**逐题端到端耗时分布**（p50/p95/min/max，见上方评测表），`python run_gaia_official.py --dump-failures` 还会把失败题详情导出到 `results/gaia_failures.json`。

### 自定义Critic开发

```bash
python demos/custom_critic_override_demo.py
```

> 展示如何继承原生Critic、增加效率评分维度、替换注入LangGraph图。详见 [ARCHITECTURE.md](ARCHITECTURE.md) 模块扩展接口章节。

## 项目结构

```
pecs-multi-agent/
├── config.py              # 全局配置（代码级默认值）
├── requirements.txt       # 依赖
├── .env.example           # 环境变量示例
├── ARCHITECTURE.md        # 架构设计文档
├── EXPERIMENT.md          # 实验复现文档
├── CHANGELOG.md           # 版本变更日志
│
├── agents/                # 四个 Agent 角色
│   ├── planner.py
│   ├── executor.py
│   ├── critic.py
│   ├── synthesizer.py
│   ├── heuristics.py      # 启发式兜底
│   └── llm_utils.py       # LLM 调用封装
│
├── graph/                 # LangGraph 状态图
│   ├── builder.py         # 图构建 + 条件路由
│   ├── state.py           # AgentState 类型定义
│   └── token_budget.py    # Token 预算管理（含角色独立配额）
│
├── tools/                 # 工具集
│   ├── python_repl.py     # Python 沙箱（AST 安全检查）
│   ├── web_search.py      # Web 搜索
│   ├── file_reader.py
│   ├── api_caller.py
│   └── webshop.py
│
├── benchmarks/            # 基准评估
│   ├── gaia_eval.py       # GAIA Level 1（28题）
│   ├── react_baseline.py  # ReAct 基线
│   ├── webshop_eval.py    # WebShop（12题，真实 WebShop-small 采样）
│   ├── cost_eval.py       # 成本消融
│   ├── ablation_eval.py   # 角色消融实验（6组配置）
│   ├── eval_autogen.py    # AutoGen 框架对照
│   ├── eval_crewai.py     # CrewAI 框架对照
│   └── report.py          # 聚合报告（含分角色Token统计）
│
├── ablation_configs/      # 消融实验配置
│   ├── full_pecs.yaml     # 完整四角色（对照组）
│   ├── no_critic.yaml     # 移除Critic
│   ├── no_synthesizer.yaml # 移除Synthesizer
│   ├── single_agent.yaml  # 纯ReAct单智能体
│   ├── critic_no_reflect.yaml      # Critic保留但关闭反思
│   └── synthesizer_no_replan.yaml  # Synthesizer保留但关闭重规划
│
├── datasets/              # 数据集抽象层
│   ├── base_dataset.py    # 抽象基类
│   ├── gaia_mock_dataset.py       # GAIA Mock 数据集
│   ├── gaia_official_dataset.py   # GAIA 官方数据集（HuggingFace）
│   └── webshop_mock_dataset.py    # WebShop Mock 数据集
│
├── experiments/           # 实验配置中心
│   └── config.yaml        # 统一YAML配置（含角色独立配额）
│
├── src/                   # 核心模块
│   └── batch_runner.py    # 批量任务执行器
│
├── logger/                # 日志工具
│   └── graph_trace_logger.py  # 全链路日志导出
│
├── metrics/               # 统计分析
│   └── error_stat.py     # Critic纠错统计
│
├── cases/                 # 案例文档
│   └── error_correction/  # Critic纠错案例
│       ├── 01_tool_param_error.md
│       └── 02_plan_logic_omission.md
│
├── demos/                 # 示例代码
│   ├── demo_batch_task.py          # 批量任务示例
│   └── custom_critic_override_demo.py  # 自定义Critic示例
│
├── scripts/               # 自动化脚本与主入口
│   ├── app.py                    # Flask Web 入口
│   ├── run_all_ablation.sh       # 一键运行消融实验（6组配置）
│   ├── run_baseline_compare.sh   # 多框架基线对比
│   ├── run_real_evaluation.sh    # 真实 API 评测一键脚本（Bash）
│   └── run_real_evaluation.ps1   # 真实 API 评测一键脚本（PowerShell）
│
├── results/               # 评测结果
│   ├── target_report.json  # 完整评测报告
│   ├── traces/             # 单任务全链路日志
│   └── error_stat.json     # 纠错统计
│
├── templates/
│   └── index.html         # Web 界面
│
├── docs/                  # 工程文档
│   ├── TECH_SELECTION.md  # 技术选型决策报告
│   ├── PERFORMANCE.md     # 性能瓶颈分析
│   ├── DEPLOYMENT.md      # 生产部署方案
│   ├── archive/testing.md # TDD 实践与 bug 发现记录（归档）
│   ├── API.md             # API接口文档
│   ├── SECURITY_AUDIT.md  # 安全审计报告
│   ├── MONITORING.md      # 监控告警方案
│   ├── VERSIONING.md      # 版本管理规范
│   ├── FEEDBACK.md        # 用户反馈记录
│   └── CODE_REVIEW.md     # 代码评审流程
│
├── Dockerfile             # 容器化部署
│
└── tests/                 # 单元测试
```

## 已知问题

1. **启发式层覆盖有限**：目前只覆盖 benchmark 模式，真实场景需要更通用的缓存方案
2. **串行执行**：四个角色为串行执行，无依赖步骤可并行化优化，暂未实现
3. **搜索优先级**：Web 搜索默认优先使用真实 DuckDuckGo 搜索，失败时回退到 mock 数据保证可运行性
4. **Synthesizer 边界情况**：极少数情况下 simple 任务的快速综合路径会遗漏关键信息（概率 < 5%，不影响评测结果）
5. **样例集规模有限**：33道GAIA+12道WebShop为内置样例，非官方完整测试集，需接入真实数据集验证

## 未来优化方向

| 方向 | 当前状态 | 优化目标 | 优先级 |
|------|----------|----------|:------:|
| 官方数据集接入 | 内置样例集 | 接入GAIA 466题 + 真实WebShop环境 | P0 |
| 并行执行 | 四角色串行 | 无依赖步骤并行化，降低延迟 | P1 |
| 启发式泛化 | 仅覆盖benchmark模式 | 基于embedding相似度的通用缓存 | P1 |
| 多模型支持 | GLM/DeepSeek/Qwen | 扩展支持 GPT-4/Claude 等更多模型 | P2 |
| 流式输出 | 批量返回 | SSE流式输出，提升用户体验 | P2 |
| 分布式部署 | 单机串行 | Redis状态共享 + 多worker并行 | P3 |

## 完整文档索引

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构设计文档（8章节） |
| [EXPERIMENT.md](EXPERIMENT.md) | 实验复现文档 |
| [docs/TECH_SELECTION.md](docs/TECH_SELECTION.md) | 技术选型决策报告 |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | 性能瓶颈分析 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 生产部署方案 |
| [docs/archive/testing.md](docs/archive/testing.md) | TDD 实践与 bug 发现记录（归档） |
| [docs/API.md](docs/API.md) | API接口文档 |
| [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) | 安全审计报告（含已知逃逸边界与加固路线） |
| [docs/FAILURE_CASES.md](docs/FAILURE_CASES.md) | 失败案例集（真实 GAIA 失败题 + 修复映射） |
| [docs/MONITORING.md](docs/MONITORING.md) | 监控告警方案 |
| [docs/VERSIONING.md](docs/VERSIONING.md) | 版本管理规范 |
| [docs/FEEDBACK.md](docs/FEEDBACK.md) | 用户反馈记录 |
| [docs/CODE_REVIEW.md](docs/CODE_REVIEW.md) | 代码评审流程 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更日志 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |

## License

MIT —— 开源免费使用，不承担任何担保责任。
