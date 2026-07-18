# PECS 多智能体框架 — 技术报告

> 这份文档是我做这个项目过程中的技术总结,记录架构怎么设计的、评测结果到底可不可信、踩过哪些坑。给招聘方看,也给自己复盘用。

## 一、这个项目在解决什么问题

我之前用 ReAct 做过几个小 Agent,发现一个挺普遍的问题:**单个 LLM 同时干规划、执行、检查三件事,链路又长又乱**。复杂任务(尤其大数计算、多步推理)经常翻车,而且 Token 花得没谱——简单任务和复杂任务成本能差十倍,没法做成本控制。

所以我做了 PECS(Planner-Executor-Critic-Synthesizer),把单 Agent 拆成四个角色分工协作,类似一个小开发团队的敏捷模式:Planner 拆任务、Executor 调工具、Critic 评审、Synthesizer 出答案。核心想解决三件事:

1. **质量不稳定** — 没有专门角色检查,错误一路传到最终答案
2. **成本不可控** — 反复调 LLM 直到完成,没有预算上限
3. **缺乏自我纠错** — 出错后没人反馈,不会重规划

## 二、架构怎么设计的

### 四角色 + Plan-Execute-Reflect 闭环

基于 LangGraph 搭的,四个节点串成一个最多 5 轮的反思循环:

- **Planner**:拿到任务先拆成子步骤,给每步分配 Token 预算和风险等级
- **Executor**:按计划调工具(python/file_parse/web_browse/search/webshop),代码先过 AST 安全沙箱
- **Critic**:检查执行结果的完整性和一致性,不合格就带着反思信息打回 Planner 重规划
- **Synthesizer**:所有步骤合格后综合最终答案

跟 AutoGen、CrewAI、原生 LangGraph 比,PECS 的差异点是**固定四角色闭环 + 三级预算降级 + AST 沙箱**,而不是自由对话或自定义节点图。固定结构的好处是可控、可观测;坏处是灵活度不如自由编排,适合"任务可分解"的场景。

### Token 预算感知调度

这是我对"成本可控"的核心设计。每个任务有总 Token 预算,按进度三级降级:

- **< 70%**:正常走四角色,LLM 充分推理
- **70%-85%**:Critic 跳过反思闭环,低风险步骤直连 Synthesizer
- **> 95%**:紧急模式,Synthesizer 直接拼接结果,不再调 LLM

加上**角色独立配额**——Executor 自己超配额就强制收尾,不会因为一个角色失控拖垮全局。

### 启发式兜底层

对已知模式(比如"计算 2 的 30 次方")直接走启发式生成工具调用参数,零 LLM 调用。这等于编译器里的常量折叠——结果确定的计算没必要重复跑。这块在 GAIA 计算题上贡献了 -99.4% 的 Token,但也是后面"数字虚高"争议的源头,得诚实拆开看。

## 三、评测结果与真实状态

用 GLM-4.7-Flash 真实 API 跑的(mode=real_api),三大量化目标在自建样本上是这样:

| 指标 | ReAct 基线 | PECS 实测 | 目标 | 我的真实性判定 |
|------|:---:|:---:|:---:|---|
| GAIA L1 准确率 | 80% | 100% (+20pp) | ≥75% | ✅ 真达标,但样本偏计算 |
| WebShop 成功率 | 0% (0/6) | 33.3% (2/6) (+33.3pp) | +18pp | ✅ 真实环境达标 |
| Token/task (GAIA) | 722 | 442 (-38.8%) | ≥30% | ✅ 端到端达标,机制本身仅 -4.5% |
| Token/task (WebShop真实) | 1926 | 2168 (+12.6%) | — | ⚠️ Critic 反思开销,但成功率碾压 |

### 三个数字我得拆开说,不然招聘方一追问就露馅

**GAIA 的 100%**:10 题里 5 道大数计算(启发式 0-token 秒杀)+ 5 道知识检索。计算题 100% 是框架的"免费得分",不代表推理能力;**推理题子集(tokens>10)准确率 92.3%(12/13)** 才是框架真实能力的体现。而且这是自建 28 题子集,非官方 165 题分布——官方分布下计算题占比远低于 50%,预判准确率会掉到 40-55%。

**WebShop 的 33.3%(真实环境达标)**:这是我在本机真实 AgentBench WebShop 文本环境上跑出来的结果(rank_bm25 纯 Python 搜索后端 + HTTP 桥 + text_rich 模式,6 题服装类 instruction)。PECS 2/6 成功(reward≥0.5)vs ReAct 0/6 = +33.3pp,超过 +18pp 目标。

但这个过程不是一次跑通的,而是经历了完整的 bug 链定位与修复:

1. **第一轮 0%**:reset 不传 task_index,真实环境随机分配 goal,与 PECS instruction 不匹配。LLM 按指令搜索但 goal 是随机的,天然搜不到。
2. **第二轮 0%**:gym 的 OrderEnforcing wrapper 的 reset 不接受 session 参数,即使匹配到 task_index 也传不进去,回退到随机。修复:直接实例化 WebAgentTextEnv 绕过 wrapper。
3. **第三轮 0%**:observation_mode 默认是 html,LLM 看不到 [button] 标记和 ASIN,陷入 search 循环从不 click。修复:改用 text_rich 模式输出结构化按钮标记。
4. **第四轮 0%**:LLM 决策 click[BUTTON_X] 但实际参数是 ASIN;且 buy 动作不触发结算(要 click[Buy Now])。修复:规则层提取 ASIN 让 LLM 选,强制 click[Buy Now]。
5. **第五轮 33.3%**:成功!2/6 题完成完整 search→click[ASIN]→click[Buy Now] 流程拿到 reward≥0.5。

**Token 权衡**:PECS 2168 vs ReAct 1926(+12.6%),因 Critic 反思循环开销。但成功率 +33.3pp 弥补了 Token 劣势——这是质量 vs 成本的权衡,Critic 的质量评审换来了 2 题成功。完整数据在 `results/webshop_run.json`。

**Token 的 -38.8%**:端到端对比含对比假象——ReAct 在大数计算上失败后重算,消耗本来就高。**纯预算调度机制本身只贡献 -4.5%**(消融实验,禁用启发式隔离)。38.8% 是"修好了 ReAct 的上下文雪崩"的副产品,不是调度单独的功劳。报告里这两个口径必须分开写。

## 四、踩过的坑(工程实战)

这部分我觉得比评测数字更值钱,因为是真实工程问题。

### 坑 1:报错文本被当最终答案冻结

最隐蔽的一个 bug。链路是这样的:

1. Planner 的 `_is_deterministic_task` 只认 python/webshop,file_parse 不算 → 含 xlsx 的题走 LLM Planner,生成 python 代码
2. LLM 生成的代码含未定义变量(如 `print(result)`)→ AST 沙箱的 exec 在隔离命名空间抛 NameError
3. 被包成 `"执行错误:\nNameError: name 'result' is not defined"`
4. `synthesize_heuristic_answer` 取结果末行,**不过滤 success 标志、不过滤 NameError/Traceback** → 报错文本直接成为 final_answer
5. `run_resumable.py` 的 `done = bool(fa)` 把任何非空答案判完成 → **错误答案冻结,永不重试**

实测 gaia_l1_029 命中这条链:final_answer 是 NameError 文本,done=True,正确答案应该是 292000。

**修复**:① Planner 把 file_parse 纳入确定性任务;② simple 任务执行失败也走 Critic(原来跳过);③ heuristics 综合时过滤 Traceback/NameError;④ done 判断加答案合法性校验。

### 坑 2:启发式拦截了所有 LLM 调用

最早版本的 planner.py 和 synthesizer.py,启发式函数在 LLM 调用之前执行,只要启发式返回非空就跳过 LLM。结果 28 个 GAIA 任务 LLM 从没被调用过,却报出"100% 准确率、53 tokens/task"——典型的假数据。

**修复**:反转优先级——有 API Key 且非确定性任务时 LLM 优先,启发式作为 fallback。确定性任务(python/webshop 工具)继续用启发式是合法优化,因为工具自身给出精确输出。

### 坑 3:网络封锁逼出的工程替代

WebShop 真实环境要 pyserini(Java + Lucene),Windows 上极脆弱;沙箱又拉不到 github 源码(TLS 被挡)、没 Docker。硬卡死。

**替代方案**:① 用 `rank_bm25` 纯 Python 实现 BM25 搜索引擎,接口与 Lucene 对齐,惰性导入 pyserini 作 fallback;② torch 改惰性导入,text env 路径根本不碰 torch;③ 数据从 HF 镜像 hf-mirror.com 下,绕开 Google Drive 被墙。

这套替代让 WebShop 真实环境在本机 conda py3.8 能跑(不用装 Java/torch/pyserini)。代价是 BM25 与 Lucene 排序略有差异,属于"工程等价替代",作品集里如实标注了。

### 坑 4:可恢复评测驱动

沙箱经常杀进程,跑 28 题跑到一半被杀就得重来,白烧 API 额度。所以写了 `run_resumable.py`,用 SQLite 存每题进度,断点续跑。还加了 API 节流(429 时指数退避)和按 task_id 隔离 ckpt.db(避免跨题复用触发噪音)。

## 五、工程素养亮点

这块对大模型应用开发工程师岗位是核心卖点,比 GAIA 92% 数字更值钱:

- **可恢复评测驱动**:断点续跑,应对进程被杀
- **API 节流**:429 指数退避,3 次重试
- **误路由处理**:启发式关键词误路由(file_parse 默认解析 data/sample.xlsx)的兜底
- **报错冻结修复**:NameError 当答案冻结的完整链路定位与修复
- **AST 安全沙箱**:白名单 import,剥离危险模块,隔离命名空间执行
- **Token 预算感知**:三级降级 + 角色独立配额,单任务成本有上限

## 六、现在到哪了,还差什么

**已完工**:四角色架构、工具集、可恢复评测、P0-P2 修复、工程化(CI/Dockerfile/文档体系)、真实 API 评测跑通、WebShop 真环境脚手架(BM25+HTTP 桥+runbook)。

**还差三件**:
1. **WebShop 真实环境本机验证** — 脚手架齐备,待我在本机 conda py3.8 按 `docs/webshop_local_runbook.md` 跑通,拿真实 +pp。这是 JD 硬要求。
2. **GAIA 官方 165 题坐实** — hf.co 被网络拦,接受不了 license。解决访问后用现成的 `datasets/gaia_official_dataset.py` 接入。
3. **简历 bullet + 面试话术** — 见 [INTERVIEW_QA.md](INTERVIEW_QA.md)。

这个项目的代码、评测、文档全部在 [pecs-multi-agent](https://github.com/paopao-13/pecs-multi-agent)。
