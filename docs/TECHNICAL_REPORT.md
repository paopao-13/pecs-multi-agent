# PECS 多智能体框架 — 技术报告

> 本文记录 PECS 的架构设计、评测方法与真实结果、以及工程实践中解决的问题，供技术复盘与同行参考。

## 一、问题背景

单 Agent（如 ReAct）让单个 LLM 同时承担规划、执行、检查，面对长链路复杂任务（大数计算、多步推理）时容易出现：错误缺乏校验一路传播到最终答案、Token 成本随任务复杂度剧烈波动、出错后无法自我纠正。

PECS（Planner-Executor-Critic-Synthesizer）将单 Agent 拆为四个分工角色，形成类敏捷开发团队的协作闭环，针对性解决三件事：

1. **质量不稳定** — 缺少专职校验角色，错误直接传播
2. **成本不可控** — 反复调用 LLM 直到完成，无预算上限
3. **缺乏自我纠错** — 出错后无反馈、不重规划

## 二、架构设计

### 四角色 + Plan-Execute-Reflect 闭环

基于 LangGraph 构建，四个节点串成最多 5 轮的反思循环：

- **Planner**：将任务拆为子步骤，为每步分配 Token 预算与风险等级
- **Executor**：按计划调用工具（python / file_parse / web_browse / search / webshop），代码先经 AST 安全沙箱
- **Critic**：校验执行结果的完整性与一致性，不达标则携带反思信息退回 Planner 重规划
- **Synthesizer**：所有步骤合格后综合最终答案

相较于 AutoGen、CrewAI、原生 LangGraph，PECS 的差异点在于**固定四角色闭环 + 三级预算降级 + AST 沙箱**，而非自由对话或自定义节点图。固定结构带来可控、可观测的优势，代价是灵活度低于自由编排，适合"任务可分解"的场景。

### Token 预算感知调度

每个任务设总 Token 预算，按进度三级降级：

- **< 70%**：正常走四角色，LLM 充分推理
- **70%–85%**：Critic 跳过反思闭环，低风险步骤直连 Synthesizer
- **> 95%**：紧急模式，Synthesizer 直接拼接结果，不再调用 LLM

配合**角色独立配额**——Executor 自身超配额即强制收尾，避免单角色失控拖垮全局。

### 启发式兜底层

对已知确定模式（如"计算 2 的 30 次方"）直接生成工具调用参数，零 LLM 调用，类似编译器常量折叠。该层在部分计算类任务上显著降低 Token 消耗，但也是"评测口径需拆分"的来源，详见第三节。

## 三、评测结果与口径说明

基于真实 LLM API 运行（mode=real_api），三大目标在自建样本上的表现：

| 指标 | 基线 | PECS 实测 | 目标 | 说明 |
|------|:---:|:---:|:---:|---|
| GAIA L1 准确率 | 80% | 100% (+20pp) | ≥75% | 样本偏计算类 |
| WebShop 成功率 | 0% (0/6) | 33.3% (2/6) (+33.3pp) | +18pp | 真实环境达标 |
| Token/task (GAIA) | 722 | 442 (-38.8%) | ≥30% | 端到端达标，机制本身仅 -4.5% |
| Token/task (WebShop真实) | 1926 | 2168 (+12.6%) | — | Critic 反思开销，但成功率占优 |

### 数字口径拆分（避免误读）

- **GAIA 100%**：样本含 5 道大数计算（启发式零 Token 命中）+ 5 道知识检索。计算类满分来自框架的确定性工具，不代表开放推理能力；**推理子集（tokens>10）准确率 92.3%（12/13）** 更能反映框架真实水平。该结果为自建 28 题子集，非官方 165 题分布——官方分布下计算类占比更低，预计准确率会相应下降。
- **WebShop 33.3%**：在本机真实 AgentBench WebShop 文本环境跑出（rank_bm25 纯 Python 搜索后端 + HTTP 桥 + text_rich 模式，6 道商品类 instruction）。PECS 2/6（reward≥0.5）vs 基线 0/6 = +33.3pp，超过 +18pp 目标。

该结果经历了完整的 bug 链定位与修复：

1. **第一轮 0%**：reset 未传 task_index，真实环境随机分配 goal，与指令不匹配。
2. **第二轮 0%**：gym 的 OrderEnforcing wrapper 的 reset 不接受 session 参数。修复：直接实例化 WebAgentTextEnv 绕过 wrapper。
3. **第三轮 0%**：observation_mode 默认 html，LLM 看不到结构化按钮标记，陷入 search 循环。修复：改用 text_rich 模式。
4. **第四轮 0%**：LLM 决策 click 参数与 ASIN 不匹配，且 buy 不触发结算。修复：规则层提取 ASIN、强制结算动作。
5. **第五轮 33.3%**：成功完成完整 search→click[ASIN]→结算流程。

- **Token 权衡**：PECS 2168 vs 基线 1926（+12.6%），源于 Critic 反思循环开销；但成功率优势弥补了 Token 差异。完整数据见 `results/webshop_run.json`。
- **Token -38.8%**：端到端对比包含基线失败重算的干扰；**纯预算调度机制本身仅贡献 -4.5%**（消融实验）。两口径须分开陈述。

## 四、工程实践中的问题与修复

### 问题 1：报错文本被当最终答案冻结

链路：Planner 的确定性判定遗漏 file_parse → 含 xlsx 的题走 LLM 生成 python 代码 → 代码含未定义变量抛 NameError → 综合层未过滤 Traceback 直接将报错文本作为 final_answer → 完成判定把任何非空答案判为完成，错误答案冻结不再重试。

**修复**：① Planner 将 file_parse 纳入确定性任务；② 简单任务执行失败也走 Critic；③ 综合时过滤 Traceback/NameError；④ 完成判定增加答案合法性校验。

### 问题 2：启发式拦截了所有 LLM 调用

早期版本启发式在 LLM 调用前执行，只要返回非空就跳过 LLM，导致 LLM 从未被调用却报出虚高准确率与极低 Token。

**修复**：反转优先级——有 API Key 且非确定性任务时 LLM 优先，启发式作为 fallback；确定性任务（python/webshop 工具）继续用启发式为合法优化。

### 问题 3：受限环境下的工程替代

WebShop 真实环境原依赖 pyserini（Java + Lucene），在 Windows 上极脆弱；部分网络环境下源码拉取与数据下载受限。

**替代方案**：① 用 `rank_bm25` 纯 Python 实现 BM25 搜索，接口与 Lucene 对齐，pyserini 改为惰性 fallback；② torch 改为惰性导入，文本环境路径不加载 torch；③ 数据改从 HF 镜像下载，绕开受限通道。详见 `webshop_patches/`。

该替代使 WebShop 真实环境在本机 conda py3.8 即可运行（无需 Java/torch/pyserini）。BM25 与 Lucene 排序略有差异，属工程等价替代。

### 问题 4：可恢复评测驱动

长流程评测常被中断，从头重跑浪费额度。实现 `run_resumable.py`，用 SQLite 存每题进度，支持断点续跑；并加入 API 节流（429 指数退避）与按 task_id 隔离检查点。

## 五、工程素养亮点

- **可恢复评测驱动**：断点续跑，应对进程中断
- **API 节流**：429 指数退避，多次重试
- **误路由处理**：启发式关键词误路由的兜底
- **报错冻结修复**：NameError 当答案冻结的完整链路定位与修复
- **AST 安全沙箱**：白名单 import，隔离命名空间执行
- **Token 预算感知**：三级降级 + 角色独立配额，单任务成本有上限

## 六、当前状态与后续

**已完工**：四角色架构、工具集、可恢复评测、P0–P2 修复、工程化（CI / Dockerfile / 文档体系）、真实 API 评测跑通、WebShop 真实环境脚手架（BM25 + HTTP 桥 + 运行手册）。

**后续**：
1. WebShop 真实环境本机验证 — 按 `docs/webshop_local_runbook.md` 跑通，取得真实对比数据。
2. GAIA 官方 165 题接入 — 待环境就绪后用 `datasets/gaia_official_dataset.py` 接入更大规模评测。
3. 项目技术总结 — 见 [archive/testing.md](archive/testing.md)。
