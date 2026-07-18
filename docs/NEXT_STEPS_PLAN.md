# PECS 项目后续推进方案

> 定位：求职作品集（非论文、非内部 KPI）
> 目标岗位：大模型应用开发工程师（LLM Application Engineer）
> 关键约束：JD 普遍要求 AgentBench 经验 → WebShop +18pp 是硬要求
> 最后更新：2026-07-17

---

## 一、当前进展总结

### 已完成（代码层，已验证）
- PECS 四角色（Planner/Executor/Critic/Synthesizer）+ Plan-Execute-Reflect 架构完整
- 可恢复驱动（LangGraph `SqliteSaver`，按 task 隔离 checkpoint）
- P0~P2 bug 修复（报错冻结、误路由拦截、节流、simple 任务 Critic 跳过修正）已验证
- lingshucode 网关 + GLM 5.2 接入端到端跑通（`.env` 三行切换）
- WebShop 真实环境全套脚手架：HTTP 桥 + 客户端 + QUICKSTART + 体检脚本

### 三目标真实状态
| 目标 | 状态 | 真相 |
|---|---|---|
| GAIA ≥75% | 推理子集 92% 真达标，但仅 28 道偏计算样本 | 官方 L1 有 165 题、分布不同，**未坐实** |
| Token −30% | 端到端 −38.78% 已超 | 机制本身仅 −4.5%，大头来自失败重算节省 |
| WebShop +18pp | 本地 8 商品 mock 100%（不可信） | 真实环境沙箱拉不到源码(NO-GO)，本机可试但从未真跑 |

### 卡住的根因（只有一个）
**沙箱网络/资源限制**：无 Docker、对 GitHub 建不起 TLS、无 Java/WebShop 数据。
→ 凡需「真实环境 / 外部源码 / 计算资源」的事，都得你回本机或租云服务器做。
→ 凡只需「LLM API + 写代码」的事，我能在沙箱直接做。

---

## 二、关键认知（避免再乱）

1. **部署环境 ≠ 达成指标**：装好 WebShop 容器 = "能测"，不等于 "+18pp 达标"。
2. **GAIA 官方 165 题预判仅 40–55%**（非 75%）：内置 33 题是精心挑过的偏计算题才 96%。
   "坐实"= 拿到真实可信数字（求职够用），不是"冲到 75%"。
3. **WebShop +18pp 才是 JD 硬要求**（AgentBench）—— 差异点全压在这项。
4. **两项都需你先行动**：GAIA 要 hf.co token；WebShop 要你本机/云服务器。

---

## 三、推进路线图（4 阶段）

```
Phase 0 解锁 ──▶ Phase 1 GAIA 坐实 ──▶ Phase 2 WebShop 真环境 ──▶ Phase 3 作品集
  (你)              (我+token)             (你)                     (我)
```

### Phase 0 — 解锁（需你操作，约 10 分钟）
1. **GAIA token**：登录 `huggingface.co` → 打开 `huggingface.co/datasets/gaia-benchmark/GAIA` → 点 **Accept License** → `Settings → Access Tokens` 生成 token → 发我（仅运行时用，不落文件）。
2. **WebShop 路径决策**：二选一
   - A. 本机 Windows 起 conda py3.8 + WebShop（推荐，省内存、最稳，不用 Docker）
   - B. 租云服务器 + Docker（资源足，但需花钱、配置成本高）
   - 决定后我据此给你对应的执行手册。

### Phase 1 — GAIA 坐实（我来做，需你的 token）
> 目标：把 `evaluate_gaia()` 从内置 33 题切到官方 165 题，拿到真实数字。

1. **修工程坑（否则官方题跑不起来）**
   - `tools/file_parser.py`：路径黑名单会挡掉 HF 缓存附件（`.cache` / `C:\Users`）→ 改白名单，允许 GAIA 附件。
   - `tools/web_search.py`：当前是 mock 优先（只覆盖 33 内置题关键词）→ 保证官方题走真实搜索（DDG 或接 Tavily/SerpAPI）。
   - 图片/图表题：GLM/DeepSeek 是文本模型无解 → 如实标记为"无法处理"，计入分母但单独分类。
2. **接线**：`benchmarks/gaia_eval.py` 的 `evaluate_gaia()` 改为优先调 `datasets/gaia_official_dataset.py` 的 `GAIAOfficialDataset`，保留内置样本作 fallback。
3. **跑**：用 lingshucode GLM 5.2 跑官方 L1 165 题，记录真实准确率 + 按题型（计算/文件/搜索/图片）拆解。
4. **交付**：`results/gaia_official_*.json` + 诚实报告「官方 GAIA L1：X%（N=165），其中搜索类 Y%、文件类 Z%…」。

⚠️ 预期管理：**真实准确率大概率 40–55%**。这是诚实数字，求职上比"96%"经得起追问。除非你明确要求冲 75%，否则不做额外的搜索 API + 多模态增强（那是另一大工程）。

### Phase 2 — WebShop 真实环境（需你操作）
> 目标：在真实 WebShop 上跑 PECS vs 同模型 ReAct 基线，算 +pp。

1. 本机照 `docs/QUICKSTART_webshop_routeA.md` 起环境（conda 路线），起 HTTP 桥 `:8000`。
2. `set WEBSHOP_SERVER_URL=http://localhost:8000` → PECS 自动切真实模式。
3. 跑 PECS 子集（建议 50 题），再跑 ReAct 基线同一子集。
4. 算 `+(PECS − ReAct)` 的 pp。若 <18pp，回头调 `webshop_interact` 的多轮决策。
5. 卡住时把报错贴我，我对照真实输出调代码。

### Phase 3 — 作品集交付（我来做，零/低成本，ROI 最高）
1. **README / 技术报告**：架构图 + 三目标真实状态 + 诚实局限 + 踩坑记录 → 直接可贴 GitHub。
2. **简历 bullet**（按 LLM 应用岗口径，Token + 稳定性放前面）：
   - 端到端 token 较同模型 ReAct 基线低 39%（调度机制 −4.5% + 失败重算节省，已做消融）
   - 生产级稳定性：SqliteSaver 断点续跑、限流节流、报错冻结、误路由拦截
   - GAIA 评测管线 + 成本归因脚本（数据驱动验证收益与局限）
3. **3 分钟面试话术**：为什么 92%/真实 X% 可信 / 为什么 −39% 是端到端 / WebShop 为何诚实降级。

---

## 四、你现在就要做的 2 件事

1. 去 `hf.co` 接受 GAIA license + 生成 token 发我（解锁 Phase 1）。
2. 定 WebShop 路径（本机 conda / 租云服务器）—— 不定也能先跑 Phase 1。

---

## 五、风险与备选

- **GAIA 真实分 < 75%**：不阻塞求职。诚实呈现 + 强调工程素养/Token 降本，反而加分。
- **WebShop 本机起不来**（Java/索引坑）：改用云服务器路线，或退回"诚实降级 + 可复用脚手架"叙事（本身已是 senior 信号）。
- **lingshucode 额度/限流**：可切回 DeepSeek 直连或换 `deepseek-v4-flash`（更省）。
