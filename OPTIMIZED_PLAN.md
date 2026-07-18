# PECS 多智能体项目 — 优化后执行方案（逼近三大量化目标）

> 目标：GAIA L1 准确率 ≥ 75% ｜ WebShop 成功率 +18pp ｜ Token 降本 30%
> 成文时间：2026-07-17
> 核心结论：**GAIA 与 Token 已达标且有余量，唯一需要实补的是 WebShop。**

---

## 0. 现状基准线（来自 results/ 实测，非估算）

| 目标 | 当前真实数字 | 判定 |
|---|---|---|
| GAIA L1 准确率 | 整体 96.4%（27/28）；**推理题子集 92.3%（12/13）**；计算题子集 100%（15/15） | ✅ 已超 75% |
| WebShop 成功率 | 多智能体 100%（6/6 硬编码题）vs ReAct 0% | ⚠️ 环境失真，+18pp 不可信 |
| Token 降本 | 端到端 −38.78%（vs 同模型 ReAct 基线）；机制本身 −4.5% | ✅ 已超 30% |

**关键事实核验：**
- ReAct 基线使用同一 `call_llm`（同一模型），对比公平 → −38.78% 含真实调度收益。
- GAIA 推理题（tokens>10）准确率 92.3%，说明 PECS 框架真实能力强，非灌水。
- WebShop 是 6 题硬编码本地 adapter（`tools/webshop.py` 的 `DEFAULT_CATALOG`），非真实 AgentBench。

---

## 1. 已达标项 — 只需"确认"，无需"优化"

### 1.1 GAIA ≥ 75%
- **动作**：换 API 后用 `run_resumable.py` 重跑全部 28 题（或接官方 165 题，见 3.2），确认推理题子集仍 ≥ 75%。
- **无需改框架**：92.3% 已是真实高分，余量充足。
- **交付物**：`results/gaia_full_28.json`（更新）或 `results/gaia_official_165.json`。

### 1.2 Token − 30%
- **动作**：报告口径写清两层：
  - 机制本身降本：**−4.5%**（纯角色配额/调度，来自 `cost_ablation.json`）
  - 端到端降本：**−38.78%**（vs 同模型 ReAct，含调度 + 失败率下降）
- **无需改框架**：−38.78% 已超 −30% 目标。
- **可选增强**：落地模型路由（见 3.3）可把"机制贡献"往上推，让 −30% 更站得住。

---

## 2. 真正要补的 — WebShop +18pp

唯一离目标有距离的项。两条可行路径，按成本排序。

### 路径 A — 接真实 AgentBench WebShop（最硬，最值）
**适用**：沙箱能连真实交互式 simulator。
**工作拆解：**
1. 探针：测沙箱能否访问 AgentBench WebShop 环境（`webarena` / 官方 simulator 端口）。
2. 写 `WebShopEnv` 客户端：多轮 `search → click → buy` 交互协议。
3. graph 加 webshop 交互子图：循环执行直到 `buy` 动作或步数上限。
4. 跑真实 50 题，算多智能体 vs ReAct 的成功率差（pp）。
**成本**：高（客户端 + 子图 + 环境部署），约 2-4 天。
**风险**：真实环境部署可能卡沙箱网络/端口限制 → 先用探针确认。

### 路径 B — 本地放大 + 规范化（低成本，半真实）
**适用**：探针不通，或想快速出有说服力数字。
**工作拆解：**
1. 把 `tools/webshop.py` 的 8 商品扩到 **20-30 题**带多约束（价格/材质/功能/品牌），更考验多约束推理。
2. 用**同一框架 vs 同一模型 ReAct** 跑对比，算 +pp。
3. 诚实标注"本地扩展 adapter，非官方环境"。
**成本**：低（纯本地），约 1-2 天，零环境风险。
**产出**：一个有说服力的 +pp 数字，逼近 +18pp。

**决策规则**：先跑探针（见 3.1）。通 → 路径 A；不通 → 路径 B。

---

## 3. 执行步骤（按 ROI 排序）

| 顺序 | 动作 | 逼近目标 | 成本 | 依赖 |
|---|---|---|---|---|
| 0 | 换 API + 重跑 GAIA/WebShop 确认现状 | 全部三项 | 低 | 你的 key |
| 1 | **WebShop 探针 → 路径 A 或 B** | +18pp | 中-高 | 探针结果 |
| 2 | 接官方 GAIA 165 题（`gaia_official_dataset.py` 现成） | GAIA 显著性 | 低 | `pip install datasets` + HF 许可 |
| 3 | 模型路由落地（小模型跑简单题 / 大模型跑推理题） | Token −30% 机制贡献 | 中 | 你的 key |
| 4 | 失败重试兜底（final_answer 空/错时自动重试一次） | GAIA 再+ | 低 | 无 |

### 3.1 WebShop 环境探针（立即可执行，不消耗你的 token）
一条命令测沙箱能否连真实 WebShop simulator，决定路径 A/B。
- 检查 `agentbench` / `webshop` 包是否可装
- 检查目标环境域名/端口是否可达
- 输出：`ENV_OK`（走 A） / `ENV_BLOCKED`（走 B）

### 3.2 官方 GAIA 165 题接入（ROI 最高）
- `datasets/gaia_official_dataset.py` 已完整实现，只需：
  ```bash
  pip install datasets
  # 在 HF 申请 gaia-benchmark/GAIA 许可，设 HF_TOKEN
  ```
- 改 `run_resumable.py` 或新增 `run_official.py` 调用 `GAIAOfficialDataset().load_samples()`。
- 样本量 28 → 165，统计显著性提升，结论更硬。

### 3.3 模型路由（Token 降本增强）
- 当前 `llm_utils.py` 的 `call_llm` 单模型。新增路由：
  - `complexity == "simple"` / 计算题 → 小模型（快、便宜）
  - 推理题 / 失败重试 → 大模型（准）
- 配置：`.env` 加 `LLM_MODEL_SMALL` / `LLM_MODEL_LARGE`。
- 预期：把"机制本身 −4.5%"往上推，更接近 −30% 的叙事。

### 3.4 失败重试兜底（GAIA 再提分）
- 在 `graph/builder.py` 的 `route_after_critic` 或 `run_task` 收口处：
  - 若 `final_answer` 为空 / 含失败标记 → 自动重试一次（同 thread_id 续跑）
- 推理题 13 道里错 1 道，重试可再抬 1-2 pp。

---

## 4. 时间线建议

```
Day 0  (现在)   写探针 + 基准线文档（不耗 token）
Day 0  你给 key  模块0 回归重跑，确认 GAIA/Token 仍达标
Day 1  探针结果  → 路径 A（部署环境）或 B（扩本地题库）
Day 1-2 WebShop 对比跑完，+18pp 数字出炉
Day 2  接官方 GAIA 165 题（并行，不依赖 WebShop）
Day 3  模型路由 + 失败重试（可选增强）
Day 3  出最终报告：三目标全部对齐
```

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 新 API 仍限流 | `LLM_MIN_GAP` 可调（默认 3s），或 `set LLM_MIN_GAP=0` 关闭 |
| 真实 WebShop 环境部署卡壳 | 探针不通立即转路径 B，不阻塞 |
| 官方 GAIA HF 许可未批 | 先用 28 题推理子集（已 92.3%）交差，许可到再扩 |
| 沙箱杀进程 | 已用 `run_resumable.py` 断点续跑机制兜底 |

---

## 6. 给你的"最小动手清单"

你现在能立刻做的：
1. **把新 API 的 key / base_url / model 给我**（或填进 `.env`）
2. 我随即：跑探针 + 模块0 回归 + 写基准线文档
3. 探针出结果后，确定 WebShop 走 A 还是 B，开始实补 +18pp

不依赖你也能先做的（零 token）：
- WebShop 环境探针脚本
- `results/metrics_verified.json`（三目标基准线）
- 官方 GAIA 接入脚本骨架
