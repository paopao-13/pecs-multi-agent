# pecs-multi-agent

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![MIT License](https://img.shields.io/badge/License-MIT-green)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2.x-orange)
![CI](https://github.com/paopao-13/pecs-multi-agent/actions/workflows/ci.yml/badge.svg)

PECS（Plan-Execute-Critic-Synthesize）是一个**多智能体协作框架**：四个角色（规划 / 执行 / 评审 / 综合）基于 LangGraph 编排，面向真实评测环境（GAIA、WebShop）与生产级部署。

## 核心数据

| 维度 | 实测结果 | 诚实说明 |
|---|---|---|
| **GAIA 官方 L1**（53 题） | PECS **26.4%** vs ReAct 24.5% | **McNemar p=1.0，差异不显著**——多智能体在知识检索类任务上相对单 Agent 无显著优势，本仓库不掩盖这一点 |
| **GAIA 自建子集**（33 题） | PECS **100%** vs ReAct 87.9% | +12.1pp，但样本偏计算类（16/33 为大数计算），属方向性信号 |
| **WebShop 真实环境** | PECS **25%** vs ReAct **0%** | **+25pp，本仓库唯一显著优势**；来源是"打破 search 循环"这一具体启发式，非"角色多所以强" |
| **Token 成本** | WebShop 端到端 **−65.5%**、自建 GAIA **−86.8%** | 卖点是**可预测 + 硬上限**（50000 token 封顶 + 70/85/95 三级降级），**不是"绝对最便宜"**——纯知识检索类 PECS 单题 token 反而更高 |
| **工程质量** | **532** 单测 · 评测集 **63** 条 · CI **三门禁全绿** | 覆盖率：**门禁范围 63%**（tools/metrics/logger，阈值 60%）/ 含 agents 的全仓总覆盖约 65%。门禁含单测+覆盖率 / 评测集 ci 档 / **Docker 构建与探活** |

## 项目定位（成熟度如实说明）

这是一个**求职作品集项目**，当前成熟度 = **工程化 PoC+**，不是生产系统。

- **已做扎实的**：四角色编排、成本控制与可预测性、八项生产就绪能力（超时/成本上限/幂等/重试/限流/审计日志/评测回归/灰度回滚）、532 个单测与 CI 三门禁
- **明确没做的**：分布式部署与多副本压测、按请求粒度的灰度、密钥轮换与配额计费
- **已由 CI 实跑验证的**：`Dockerfile` 构建 + 容器启动 + `/health` 探活 + 镜像内无凭据断言（本机无 Docker，改由 CI runner 完成——见 `.github/workflows/ci.yml` 的 `docker-build` job）
- **面试时可辩护的口径**：说"Agent 编排与成本控制做扎实、工程化约 60 分，剩下 40 分我清楚差在哪并给得出改造路径"，比假装是生产系统更站得住

## ✨ 特性

- **四角色协作**：Planner 拆解 → Executor 调工具 → Critic 评审 → Synthesizer 综合，可在 GAIA / WebShop 真实环境跑通
- **真实评测数据**：GAIA 自建子集 100%、官方 53 题 26.4%（诚实披露与基线不显著）；WebShop 真实环境较 ReAct +25pp
- **生产级 API**：FastAPI async + 独立 LLM 线程池（HOL 修复，探针不被长任务阻塞）+ 启动自检 fail-fast + Prometheus 多进程指标
- **稳定性工程**：全局令牌桶限流（超限返回 429 而非 500）、混沌注入优雅降级（传输层故障零 500）、可恢复驱动（断点续跑）
- **可观测与防回归**：Prometheus 指标端点 + CI 服务层门禁（每次 PR 自动挡回归）
- **安全沙箱**：代码执行 AST 黑名单 + 白名单 `__builtins__` + dunder 属性链拦截 + 超时熔断，四层防护
- **多模态与附件处理**：文本类附件经 `file_parse` 解析接入上下文，支持 **PDF / Excel / CSV / Word(.docx) / PowerPoint(.pptx)**（后两者用标准库 `zipfile` + `ElementTree` 解析 OOXML，**零新增依赖**；旧版二进制 `.doc/.ppt` 明确报错而非静默乱码）；图片/音频/视频支持**可插拔多模态后端**（配置 `PEC_VISION_*` 即启用，未配置优雅降级跳回原行为），将 GAIA 附件子集从"全失败"推向"可解"

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
> **接入官方数据集方法**（GAIA 是 HuggingFace **门控数据集**，需先在数据集页申请访问许可并配置 `HF_TOKEN`）：
>
> ```bash
> # 推荐：先离线下载到本地镜像，再让评测只读该目录
> python scripts/download_gaia.py          # 直连 HF 下载 2023/validation + level1 parquet 到 data/gaia
> export PEC_GAIA_LOCAL_DIR=data/gaia
> python run_gaia_official.py              # 默认评测 GAIA L1 validation 53 题，生成 results/gaia_official_*.json
> ```
>
> 为什么推荐本地镜像：`GAIAOfficialDataset` 原本用 `snapshot_download()` 整仓拉取（119 个文件），在受限网络下会踩两个坑——① `HF_ENDPOINT` 指向镜像时 `/resolve/` 会 308 跳回 `huggingface.co`，而跨域重定向会**丢掉 `Authorization` 头**，门控文件必然 401；② 逐文件创建/删除 `.locks`/`*.incomplete` 会触发宿主环境的**批量删除保护**（阈值 50/轮）而被中断。`scripts/download_gaia.py` 改为纯 HTTP 直连下载（零删除），`GAIAOfficialDataset` 在检测到 `PEC_GAIA_LOCAL_DIR` 时走**本地只读**路径，完全不触碰 HF 缓存机制（可复现、可离线、CI 友好）。历史方案说明见 [docs/archive/EXPERIMENT.md](docs/archive/EXPERIMENT.md) 中「官方数据集接入」章节

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

> **能力升级（已修复，但未能重跑——原因如下，不篡改上表数字）**：上表为升级前的实测（PECS 26.4% / 附件题 0%）。此后代码层面已修复四类影响附件题得分的问题：
> 1. **Office 附件解析**（`tools/file_parser.py`）：`.docx`/`.pptx` 用标准库解析 OOXML，修掉原先把二进制当文本读、静默吐出 ZIP 乱码的错误。
> 2. **数据目录白名单**（`PEC_DATA_ALLOW_DIR`）：原先 `C:\Users` 被敏感路径规则拦截，导致附件子集整批失败。
> 3. **单题硬超时**（`benchmarks/gaia_official.py:_run_with_deadline`）：补上 Windows 缺失 `SIGALRM` 的盲区，单题上限从 120s 提到 300s（实测附件题需 250–260s，原值会系统性误杀）。
> 4. **视觉后端**（`PEC_VISION_*`，OpenAI 兼容）：实测可用 `glm-5.2-vision` 转录图片；2 道 mp3 因该网关不支持音频转写仍按降级跳过；1 道棋盘图因**网关侧图片解码失败**（已知缺陷，非本地代码问题）未解。
>
> **为什么没有更新这两个数字**：升级后已尝试重跑官方 53 题，但当前网络环境下**外网检索链路不可达**——实测 `duckduckgo.com` 与 `en/zh.wikipedia.org` 全部连接超时，`web_search` 只能回落到"未找到"，`web_browser` 抓取 15s 超时。GAIA 大量题目依赖检索，此状态下重跑得到的是**因网络失败而系统性偏低的无效数字**，跑完反而会污染上表这个可辩护的基线。
>
> 因此本仓库选择**保留 26.4% 并如实披露其边界**，而不是换一个自己都不信的数字。若要复现升级后的真实增益：配置可达的检索后端（`SEARCH_PROVIDER=tavily` + `SEARCH_API_KEY`，实测 `api.tavily.com` 可达）或换到可访问国外站点的网络环境，再执行
> `python run_gaia_official.py --dump-failures`（**注意**：`--num N` 小样本试跑务必加 `--out <临时目录>`，否则会覆写权威结果文件）。

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
> 消融实验详细说明见 [docs/archive/EXPERIMENT.md](docs/archive/EXPERIMENT.md)

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
# 1. 启动 API 服务（四角色协作全过程由 /run_task 驱动）
python -m uvicorn scripts.api:app --host 127.0.0.1 --port 8000
# 打开 http://127.0.0.1:8000/docs 查看交互文档；/health 探活、/metrics 指标

# 2. WebShop 真实环境评测（AgentBench 文本环境）
python run_webshop.py --tasks 12
# 详见 docs/webshop_local_runbook.md（rank_bm25 搜索后端 + HTTP 桥部署）

# 3. 可恢复驱动运行（断点续跑，生产级稳定性）
python run_resumable.py "你的任务描述"

# 4. 生产级 API 服务（FastAPI async v0.6.1 + 独立 LLM 线程池 + 启动自检 + Prometheus 多进程指标 + 全局限流 + 混沌工程）
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
python -m uvicorn scripts.api:app --host 127.0.0.1 --port 8000
```

然后打开 http://127.0.0.1:8000/docs，可用端点包括：
- **任务执行**：输入问题，看四个 Agent 怎么协作
- **GAIA 评估**：批量跑评测，对比多智能体和 ReAct
- **对比测试**：同一问题并排跑，直观对比 Token 消耗

生产环境（多 worker + 指标正确聚合）：
```bash
export PROMETHEUS_MULTIPROC_DIR=/tmp/pecs_prom && mkdir -p $PROMETHEUS_MULTIPROC_DIR
gunicorn scripts.api:app -w 4 -b 0.0.0.0:8000 --timeout 300 --prometheus-dir $PROMETHEUS_MULTIPROC_DIR
# 外部 Prometheus 直接 scrape http://host:8000/metrics/prom
# 提示：未设 PROMETHEUS_MULTIPROC_DIR 时，/metrics（JSON）只反映单 worker，多 worker 请以 /metrics/prom 为准
#
# 两个必须注意的参数：
#   --timeout 300 ：附件类任务实测 248~260s，沿用默认 120s 会系统性误杀
#   多 worker 限流：进程内令牌桶各 worker 各算一份，额度会被放大 N 倍。
#                   设 PEC_SHARED_STATE_DB=/path/state.sqlite 后计数落到 SQLite，
#                   多进程共享（实测 4 进程 240 次请求放行 100 次，进程内为 400 次）
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
| `PEC_VISION_MAX_TOKENS` | 否 | 3000 | 图片转录输出上限；整页截图转录 1500 会被截断，题目要的数据常在页面更深处 |
| `PEC_SEARCH_PROVIDER` | 否 | 空 | 真实搜索 API 提供商，目前支持 `tavily`；配置后 Web 检索改用其接地摘要 |
| `PEC_SEARCH_API_KEY` | 否 | 空 | 对应搜索 API Key |
| `RUN_MODE` | 否 | `eval` | 运行模式：`eval` 关闭工具加固（行为等同改造前）/ `business` 全部开启 |
| `PECS_API_KEYS` | 否 | 空 | **API 鉴权**，`"key1:tenant_a,key2:tenant_b"` 格式；**未配置时鉴权自动关闭**（本地开发/CI/评测不受影响）。开启后 `/run_task`、`/api/replay/{id}`、`/metrics*` 需带 `X-API-Key` 头（`/health*` 永远免 Key） |
| `PEC_ADMIN_TENANTS` | 否 | `tenant_jixiang` | 允许调用管理端点（`/admin/prompt/*`）的租户，逗号分隔 |
| `PEC_SHARED_STATE_DB` | 否 | 空 | 跨进程共享的服务状态（SQLite 路径）：限流令牌桶、**熔断计数、幂等缓存**三合一。**多 worker 部署时必填**——未设置时全部是进程内状态，熔断阈值与幂等在多 worker 下形同虚设 |
| `PEC_IDEM_TTL_SEC` | 否 | 600 | 共享幂等缓存的过期时间（秒），仅 `PEC_SHARED_STATE_DB` 启用时生效 |
| `PEC_PROMPT_VERSION` | 否 | `v0` | 启动时的 Prompt 版本（v0 = 代码内基线；`prompts/v{N}/<role>.txt` 存在时覆盖）。运行时可用 `POST /admin/prompt/rollback?target=v{N}` 即时切换（重启后回落本值） |
| `PEC_EGRESS_ALLOW_PRIVATE` | 否 | 空 | 置 `1` 允许 `api_call` 访问内网地址（仅本地开发，生产勿开） |
| `MAX_QUERY_CHARS` | 否 | 10000 | 单条 query 字符上限，超限直接 413 拦截、不进入 LLM |
| `PEC_CHECKPOINT_DB` | 否 | `results/checkpoints.sqlite` | 断点续跑 / 链路回放所用的 SQLite 检查点文件 |
| `PEC_SKIP_LLM_PROBE` | 否 | 空 | 置 `1` 跳过启动期的 LLM 真实探测（离线 / 测试环境）；跳过时退回「key 非空即就绪」|
| `PEC_LLM_PROBE_TIMEOUT` | 否 | 15 | 启动期 LLM 探测超时（秒），超时视为未就绪 |
| `LLM_CALL_TIMEOUT` | 否 | 60 | **单次 HTTP 请求**超时（秒），传给 ChatOpenAI 客户端 |
| `LLM_CALL_DEADLINE` | 否 | 120 | **一次 `call_llm` 全部重试**的墙钟总预算（秒）；到点后不再发起新尝试、跳过越界退避。设 `0` 关闭（保留旧行为）。它无法中断已飞行中的那次，硬边界仍由评测侧 `_run_with_deadline()` 兜底 |
| `LLM_MIN_GAP` | 否 | 3.0 | 两次 LLM 调用的最小间隔（秒），规避 RPM 限制 |
| `PEC_SEARCH_TIMEOUT` | 否 | 10 | DuckDuckGo 检索超时（秒），与 `duckduckgo_search` 库默认值一致，显式化以防依赖库改默认 |

配置文件（`config.py`）关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_TOKEN_BUDGET` | 50000 | 每任务 Token 上限 |
| `DEGRADE_THRESHOLD_1` | 0.70 | 70% 跳过部分 Critic |
| `DEGRADE_THRESHOLD_2` | 0.85 | 85% 合并步骤 |
| `DEGRADE_THRESHOLD_3` | 0.95 | 95% 强制输出 |
| `TOOL_WRAPPER_ENABLED` | False（business 为 True） | 工具统一包装器总开关（超时/异常分类/结构化日志） |
| `TOOL_TIMEOUT_SEC` | 15 | 单工具超时秒数 |
| `TOOL_BREAKER_ENABLED` / `_THRESHOLD` / `_RESET_SEC` | False / 3 / 60 | 熔断：连续失败达阈值即熔断，60s 后半开 |
| `TOOL_IDEMPOTENT_ENABLED` | False | 幂等缓存（**仅只读工具**：search/web_browse/file_read/file_parse/multimodal） |
| `TOOL_PERMISSION_ENABLED` / `TOOL_PERMISSION_MAP` | False / `{}` | 权限白名单：节点→允许工具，越权返回 PERMISSION_DENIED 且不执行 |

统一实验配置（`experiments/config.yaml`）：

> 全项目所有模块（框架主逻辑、评测、消融、调度）统一读取此 YAML，覆盖 `config.py` 的代码级默认值，彻底消灭硬编码。包含模型参数、Token预算（含角色独立配额）、执行限制、安全规则等完整配置。

### 运行模式：eval / business

工具加固能力集中在 **一套开关** 下，默认全部关闭，通过 `RUN_MODE` 一键切换档案：

| 模式 | 用途 | 工具加固 |
|------|------|----------|
| `eval`（默认） | 跑 GAIA / WebShop 评测，要求可复现 | 全部关闭 → **行为与改造前逐字一致** |
| `business` | 面向生产 | 超时 / 熔断 / 幂等 / 权限白名单 全部打开 |

开关取值优先级：**环境变量 > business 模式覆盖 > YAML > 代码默认值**。
即环境变量永远最高，`RUN_MODE=business` 下仍可用环境变量逐项关掉某个能力：

```bash
RUN_MODE=business python scripts/api.py                  # 全开
RUN_MODE=business TOOL_BREAKER_ENABLED=0 python scripts/api.py   # 只关熔断
```

**主动披露的局限**：熔断计数与幂等缓存都在进程内存里，**仅单进程有效**；
`scripts/api.py` 以多 worker 运行时各进程互不共享（跨进程需落到 Redis/SQLite，
本期未做）。工具超时是「放弃等待」而非「真正中断线程」（Windows 无 `signal.alarm`，
项目图是同步的），死循环工具仍会占用工作线程。


### 依赖故障显式化（消除静默失败）

LLM 是外部依赖，会失效（key 过期 / 余额耗尽 / 服务不可达）。此类故障若被「静默吞掉」，
上游只会看到一个 `success=True` 的**空答案**，无从区分「任务本身无解」与「依赖挂掉」。
本项目在两层把它显式化：

1. **启动期真实探测**：`lifespan` 自检不再只看「有没有填 key」，而是复用生产调用路径
   真打一次极小 LLM 请求（`_probe_llm`）。
   - 探测通过 → `llm_configured=True`，服务进入就绪。
   - 探测失败 / 超时 → `llm_configured=False`，`/health` 同时暴露 `llm_reason`
     （含具体原因，如 `401 ... api key is invalid`），`/run_task` **启动期即 fail-fast 503**，
     不会空跑消耗线程池。

2. **运行期失败上报**：`call_llm` 重试耗尽后统一返回 `[LLM调用失败] <原因>` 前缀；
   `call_llm_json` 检测到该前缀**显式抛出** `LLMInvocationError`（而非抛出误导性的
   `JSONDecodeError`）；Planner / Synthesizer 将失败写入 `AgentState.llm_error`。
   `/run_task` 据此判定：**LLM 失败且任务零步骤** → `success=False` + 明确 `error`，
   不再返回 `200 + 空答案`。

```bash
# 真实探测（默认）：key 失效时启动即报未就绪
python -m uvicorn scripts.api:app --port 8000
curl localhost:8000/health          # → {"llm_configured": false, "llm_reason": "... 401 ..."}

# 离线 / 测试：跳过探测（key 非空即视为就绪）
PEC_SKIP_LLM_PROBE=1 python -m uvicorn scripts.api:app --port 8000
```

> 边界说明：若 LLM 失败但**启发式已兜底产出步骤**（`step_count > 0`），仍按 `success=True`
> 返回——这属于设计内的「依赖降级」，而非「空跑」。只有零步骤 + LLM 失败才判为失败。


## Demo 演示

项目提供 7 个可运行的 Demo，覆盖从零配置体验 to 安全沙箱演示的完整场景：

| Demo | 命令 | 说明 | 需要 API Key |
|------|------|------|:---:|
| 零配置快速体验 | `python demos/quickstart_no_api.py` | 无需 API Key，启发式兜底 + Python 沙箱执行 3 个计算任务 | 否 |
| 安全沙箱拦截演示 | `python demos/security_sandbox_demo.py` | 展示 AST 预检查拦截 8 种攻击代码 + 白名单沙箱执行合法代码 | 否 |
| Token 降级调度演示 | `python demos/token_budget_demo.py` | 展示 70%/85%/95% 三级降级 + 角色独立配额机制 | 否 |
| AI 内容生成 Pipeline | `python demos/content_pipeline_demo.py` | 文案生成 → 批量生成（预算三级降级）→ LLM 自动评测 → A/B 选优 → 成本归因 | 否 |
| PECS vs ReAct 对比 | `python demos/pecs_vs_react_demo.py` | 单任务对比 + 33 题批量汇总数据 | 否（有 Key 更完整） |
| 批量任务执行 | `python demos/demo_batch_task.py` | 3 种批量执行方式：自定义列表/GAIA Mock/WebShop Mock | 是 |
| 自定义 Critic 扩展 | `python demos/custom_critic_override_demo.py` | 继承原生 Critic 增加效率评分维度，注入 LangGraph 图 | 是 |

> 现场演示推荐从 `quickstart_no_api.py` 开始（零配置即可运行），再展示 `security_sandbox_demo.py`（安全设计亮点）。

### Web 界面

当前唯一入口是 FastAPI 服务（Flask 演示应用 `scripts/app.py` 已废弃删除）：

```bash
python -m uvicorn scripts.api:app --host 127.0.0.1 --port 8000
# http://127.0.0.1:8000/docs  交互式 API 文档
# http://127.0.0.1:8000/health  探活（含 llm_configured / prompt_version / shared_state）
# http://127.0.0.1:8000/metrics 指标（鉴权开启后需带 X-API-Key）
```

下方截图来自**已废弃的历史 Flask 演示界面**，仅作能力展示，入口已由上述
FastAPI 服务取代（历史界面提供的三个视图，现分别对应 `/run_task`、
GAIA 评测脚本 `run_gaia_official.py`、消融实验脚本 `run_all_ablation.sh`）：

![PECS 任务执行视图](assets/demo_screenshot.png)

![PECS GAIA 评估视图](assets/demo_screenshot_gaia.png)

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

### 成本归因与链路回放

**成本归因**：把一次任务的 token 消耗拆到「角色 / 工具 / 轮次」，回答「钱花在哪」。
数据全部来自既有的 `role_token_used` / `budget_events` / `results`，不新增埋点。

```python
from metrics.cost_attribution import attribute_cost, render_report

state = run_task("计算2的100次方")
print(render_report(state))          # 人类可读报告
report = attribute_cost(state)       # 结构化 dict（可直接 json.dumps）
```

报告含**一致性校验**（各角色之和 vs 总消耗，偏差 ≥1% 会显式提示「需检查埋点」）。
`/run_task` 的成功响应已自带 `cost_report` 字段，无需另开接口。

**链路回放**：带 `thread_id` 调用 `/run_task` 即会持久化到 SQLite 检查点，
之后可回放完整链路（**读取既有数据，不重跑图**，避免二次计费与副作用）：

```bash
curl -X POST localhost:8000/run_task \
  -H 'Content-Type: application/json' \
  -d '{"query": "计算2的100次方", "thread_id": "demo-1"}'

curl localhost:8000/api/replay/demo-1
# → {thread_id, state{...}, cost_report{...}, trace_markdown}
```

**链路追踪（trace_id）**：每个请求由中间件生成 `trace_id`，回写到响应头
`X-Trace-Id` 与 `/run_task` 响应体 `trace_id` 字段；整条链路的工具调用日志、
链路 Markdown 都带同一个 id，可从日志反查全链路。

```bash
# 上游系统串联：传入合法 X-Trace-Id 即被复用（非法值会被拒绝并重新生成，防日志注入）
curl -X POST localhost:8000/run_task \
  -H 'Content-Type: application/json' \
  -H 'X-Trace-Id: upstream-request-9527' \
  -d '{"query": "计算2的100次方"}'
# 响应头：X-Trace-Id: upstream-request-9527
```

> 实现要点：`run_in_executor` **不会**把 `contextvars` 传播到工作线程，
> 因此图执行入口（`_execute_graph`）必须重新 `bind_trace_id`，
> 否则链路日志里全是占位符 `-`。`logger/trace_context.py` 封装了这部分语义。

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
├── CHANGELOG.md           # 版本变更日志
├── CONTRIBUTING.md        # 贡献指南
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
│   ├── web_browser.py     # 网页抓取与正文提取
│   ├── file_reader.py     # 文本文件读取（含路径安全校验）
│   ├── file_parser.py     # PDF / Excel / CSV / 图片解析
│   ├── multimodal.py      # 多模态附件预处理（可插拔后端）
│   ├── api_caller.py
│   ├── webshop.py
│   ├── path_guard.py      # 路径安全守卫（敏感目录/隐藏文件，跨平台生效）
│   ├── wrapper.py         # 工具统一包装器（超时/异常分类/熔断/幂等/权限）
│   └── content_pipeline.py # AI 内容生成 Pipeline 工具（仅 business 模式注册）
│
├── benchmarks/            # 基准评估
│   ├── gaia_eval.py       # GAIA Level 1（33题）
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
│   ├── error_stat.py      # Critic纠错统计
│   └── cost_attribution.py # 成本归因（按角色/工具/轮次拆分）
│
├── cases/                 # 案例文档
│   └── error_correction/  # Critic纠错案例
│       ├── 01_tool_param_error.md
│       └── 02_plan_logic_omission.md
│
├── demos/                 # 示例代码
│   ├── quickstart_no_api.py            # 零配置快速体验
│   ├── content_pipeline_demo.py        # AI 内容生成 Pipeline（无需 API）
│   ├── demo_batch_task.py              # 批量任务示例
│   └── custom_critic_override_demo.py  # 自定义Critic示例
│
├── scripts/               # 自动化脚本与主入口
│   ├── api.py                    # FastAPI 服务（/run_task、/metrics、/api/replay、/admin/prompt/*）
│   ├── readiness_check.sh        # 生产就绪度一键自检（环境/单测门禁/评测门禁）
│   ├── download_gaia.py          # GAIA 官方数据集本地镜像下载器（绕开 HF 缓存机制）
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
├── docs/                  # 工程文档
│   ├── TECHNICAL_REPORT.md       # 技术报告（设计取舍 / 实验结论 / 局限）
│   ├── SECURITY_AUDIT.md         # 安全审计报告（含已知逃逸边界）
│   ├── FAILURE_CASES.md          # 失败案例集（真实 GAIA 失败题 + 修复映射）
│   ├── FIX_PLAN.md               # 缺陷修复计划与状态
│   ├── GAIA_SCORE_UPGRADE_SPEC.md # GAIA 提分能力规格说明
│   ├── GAIA_RERUN_PROMPT.md      # GAIA 本机重跑操作指引
│   ├── LOCAL_EXEC_CHECKLIST.md   # 本机执行检查清单
│   ├── webshop_local_runbook.md  # WebShop 本地部署与运行手册
│   ├── VERSIONING.md             # 版本管理规范（SemVer / Tag / Release 流程）
│   └── archive/                  # 历史归档（选型/性能/部署/API/监控/反馈/评审/实验复现等）
│
├── Dockerfile             # 容器化部署
│
└── tests/                 # 单元测试
```

## 风险与局限

> 原则：**能验证的写数字，没验证的写明"未验证"，修不动的写清边界**。以下每条都能在代码或文档里找到出处。

### 一、技术局限（已知边界，非缺陷）

| # | 局限 | 具体表现 | 为何这样取舍 / 后续解法 |
|---|---|---|---|
| 1 | **工具超时是"放弃等待"而非真正中断** | `run_with_timeout` 超时后工作线程仍在跑（同步图 + Windows 无 `SIGALRM` + CPython 无跨线程强杀） | 真正强杀需子进程或进程池，成本高于收益。死循环型工具仍会占一个线程直到结束 |
| 2 | **跨进程状态需显式配置** | 限流 / 熔断 / 幂等默认在进程内；多 worker 部署**必须**设 `PEC_SHARED_STATE_DB`，否则阈值与缓存各自为政（形同虚设） | 已提供 SQLite 跨进程实现（标准库、零新依赖）；未设时行为与旧版一致，属"可选启用"而非默认加固 |
| 3 | **灰度是进程粒度** | 靠给部分 worker 注入不同 `PEC_PROMPT_VERSION` 实现流量切分，**无按请求概率灰度** | 本项目 QPS 由 LLM 时延主导（秒级），进程级切分已足够；按请求灰度需引入分流中间件与一致性校验 |
| 4 | **鉴权是传输层** | 只有 API Key 校验 + 租户隔离，**无配额、无计费、无密钥轮换、无审计流水** | 常量时间比较用小表字典实现；密钥量级上千时应改哈希索引 + `hmac.compare_digest` |
| 5 | **租户隔离依赖命名约定** | `thread_id` 必须形如 `<tenant>-<后缀>`，越权返回 404（不泄露存在性） | 引入额外存储做归属查询成本更高；代价是客户端需遵守约定，已在配置章节说明 |
| 6 | **幂等仅对只读工具生效** | `python` / `api_call` / `webshop` 等有副作用的工具不进缓存 | 缓存副作用会掩盖真实执行；这是刻意的安全取舍 |

### 二、未验证项（诚实标注，未做的事不假装做了）

| # | 项 | 现状 | 风险 |
|---|---|---|---|
| 7 | ~~`Dockerfile` 未实跑~~ **已实跑并修复** | 首次实跑即失败：基础镜像 `python:3.11-slim` 与 `requirements-lock.txt`（生成于 Python 3.13，含 `scipy==1.18.0` 要求 >=3.12）不兼容，**镜像根本构建不出来**——这个缺陷靠读代码发现不了 | **已修复**（基础镜像改 3.13-slim）并由 CI 的 `docker-build` job 持续守护：构建 → 启动 → `/health` 探活 → 断言镜像内无 `.env` |
| 8 | ~~agents 四角色覆盖率偏低~~ **已补 mock 测试** | 原 55–74%；现 `critic 63% / executor 58% / llm_utils 67% / planner 76% / synthesizer 67%` | 已用 mock LLM 覆盖四角色的降级路径（不烧额度），并**实测暴露 3 个真实缺陷**（工具异常被误判成功 / executor 未收敛异常 / synthesizer 失败文本当答案）——见 `tests/test_agent_nodes.py` |
| 9 | **`tools/webshop*` 覆盖 26%** | 383 行几乎无单测 | 依赖第三方环境，未纳入门禁 |
| 10 | **GAIA 跑分是"修复前"的数字** | 四类附件链路缺陷（Office 解析 / 数据目录白名单 / Windows 硬超时 / 视觉后端）已修复但**未能重跑**，原因见下条 | 26.4% 是**偏低**的数字。不换数字的理由与复现路径见「评测结果」章节 |
| 11 | **3 道多模态题仍不可解** | 上表中 4 道（2 png + 2 mp3）在跑分时因缺多模态后端**全部 skipped**；配好 `PEC_VISION_*` 后，**2 道 mp3 仍不可解**（该网关不支持音频转写）、**1 道棋盘图不可解**（网关侧图片解码失败），另 1 道图片题已具备作答条件 | 属**外部网关能力限制**，非本地代码问题——已实现可插拔后端 + 不可用时优雅降级，换用支持音频转写的端点即可推进 |

### 三、环境阻塞（非代码问题，但影响可复现性）

| # | 阻塞 | 实测证据 | 影响 |
|---|---|---|---|
| 12 | **外网检索不可达** | `duckduckgo.com` 与 `en/zh.wikipedia.org` 连接超时；`web_search` 只能回落"未找到"，`web_browser` 抓取 15s 超时 | **GAIA 全量重跑无法进行**（大量题依赖检索）。此状态下重跑会得到因网络失败而系统性偏低的无效数字。可行解：`api.tavily.com` 实测可达，配置 `SEARCH_PROVIDER=tavily` 即恢复搜索 |
| 13 | **`github.com:443` 不稳定** | `git push` 常需多次重试（实测最多 10 次），`api.github.com` 稳定 | 只影响 CI 触发时效，不影响代码质量 |

### 四、模型与成本风险

| # | 风险 | 说明 |
|---|---|---|
| 14 | **评测受 LLM 网关能力约束** | 当前网关 13 个模型全为 reasoning 型（先输出 reasoning_content，"慢"≠"不可用"）；无音频转写能力。换网关后部分结论需重测 |
| 15 | **单题 token 方差仍在** | 硬上限 50000 + 三级降级兜住了总额，但单题波动仍大于固定工作流——这是 Agent 固有成本，不是实现缺陷 |
| 16 | **小样本结论不等于统计显著** | 自建 33 题 +12.1pp、WebShop 12 题 +25pp 均为**方向性信号**；仅官方 53 题做了 McNemar 检验（p=1.0 不显著）。不应读作"全面碾压单 Agent" |

## 下一步

> 排序依据：**对求职深挖的价值 × 面试被问到的概率**，不是工程完整性。

### P0 — 影响面试直接发挥

| # | 任务 | 为什么做 | 验收标准 | 状态 |
|---|---|---|---|---|
| 1 | ~~补 `PRODUCTION_READINESS.md`~~ | 八项生产就绪能力代码里全有，但缺一处集中说明；面试官不会自己翻代码找 | 八项逐项给出：能力 → 代码位置 → 配置开关 → 验证命令 → 已知边界 | ✅ **已完成** → [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) |
| 2 | **打磨面试叙事口径** | 26.4% 不显著这件事必须能主动讲清，而不是被问到时辩解 | 能一句话说清"哪个数字显著、哪个不显著、为什么"，以及"剩下 40 分差在哪" | 待做 |

> 生产就绪度已可直接验证：`bash scripts/readiness_check.sh` 一键输出环境现状 + 单测覆盖门禁 + 评测集 ci 档，退出码可用于 CI。

### P1 — 被追问时能答上

| # | 任务 | 为什么做 | 验收标准 | 状态 |
|---|---|---|---|---|
| 3 | ~~agents 四角色 mock 测试~~ | ~~"LLM 代码怎么测"是高频题~~ | mock LLM 测节点降级路径（不烧额度），覆盖率 55–74% → 58–76% | ✅ **已完成**（+22 例；**实测暴露 3 个真实缺陷并修复**，比覆盖率本身更有价值） |
| 4 | ~~`Dockerfile` 实跑验证~~ | ~~部署件不能只做静态核对~~ | ~~CI 加 build job~~ | ✅ **已完成**（CI `docker-build` job；首次实跑即发现并修掉 3.11/3.13 不兼容） |
| 5 | ~~重试策略按错误分类~~ | ~~当前 4xx（参数错/鉴权错）也重试，纯浪费额度~~ **实测发现原描述有误**：4xx 本来就不重试，真正的缺陷是 **500 不重试（漏判）** + "limit" 关键词假阳性导致上下文超长白等 56s | HTTP 状态码优先 + 收紧关键词兜底 + 未知默认不重试；注入 500 重试 3 次、上下文超长只调用 1 次 | ✅ **已完成** |

### P2 — 提升竞争力（时间充裕再做）

| # | 任务 | 说明 |
|---|---|---|
| 6 | **GAIA 全量重跑** | 前置条件：可达的检索后端或代理。跑前必须备份 + 用 `--out` 防覆写。预期增量主要来自 7 道文档附件题 |
| 7 | **评测集扩容** | 63 → 100+ 条，聚焦多轮对话与长上下文场景 |
| 8 | **异步/消息队列实战** | 补简历短板项：任务队列 + Worker 池 + 任务状态外置 |

### 明确不做的（避免过度工程化）

| 项 | 不做的理由 |
|---|---|
| 接入 Redis / Kubernetes | 无真实多副本压测场景，加了也只是"配置展示"，面试一问压测数据就露馅 |
| 自研可观测 SDK | 现有 Prometheus + trace_id 已够用；应优先 OpenTelemetry 而非自造 |
| 按请求概率灰度 | 单实例服务无流量切分需求，进程粒度已足够 |


## 完整文档索引

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构设计文档 |
| [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) | **生产就绪度清单**（八项逐项：代码位置 / 开关 / 验证命令 / 已知边界） |
| [docs/TECHNICAL_REPORT.md](docs/TECHNICAL_REPORT.md) | 技术报告（设计取舍 / 实验结论 / 局限） |
| [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md) | 安全审计报告（含已知逃逸边界与加固路线） |
| [docs/FAILURE_CASES.md](docs/FAILURE_CASES.md) | 失败案例集（真实 GAIA 失败题 + 修复映射） |
| [docs/FIX_PLAN.md](docs/FIX_PLAN.md) | 缺陷修复计划与状态 |
| [docs/GAIA_SCORE_UPGRADE_SPEC.md](docs/GAIA_SCORE_UPGRADE_SPEC.md) | GAIA 提分能力规格说明 |
| [docs/GAIA_RERUN_PROMPT.md](docs/GAIA_RERUN_PROMPT.md) | GAIA 本机重跑操作指引 |
| [docs/LOCAL_EXEC_CHECKLIST.md](docs/LOCAL_EXEC_CHECKLIST.md) | 本机执行检查清单 |
| [docs/webshop_local_runbook.md](docs/webshop_local_runbook.md) | WebShop 本地部署与运行手册 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更日志 |
| [docs/VERSIONING.md](docs/VERSIONING.md) | 版本管理规范（SemVer / Tag / Release 流程） |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 贡献指南 |

历史归档文档（早期版本的选型 / 方案 / 评审记录，保留以呈现工程演化过程）：

| 文档 | 说明 |
|------|------|
| [docs/archive/](docs/archive/) | 归档目录：技术选型、性能分析、部署方案、API 文档、监控告警、用户反馈、代码评审、可行性分析、实现计划等 |
| [docs/archive/EXPERIMENT.md](docs/archive/EXPERIMENT.md) | 实验复现文档 |
| [docs/archive/testing.md](docs/archive/testing.md) | TDD 实践与 bug 发现记录 |

## License

MIT —— 开源免费使用，不承担任何担保责任。
