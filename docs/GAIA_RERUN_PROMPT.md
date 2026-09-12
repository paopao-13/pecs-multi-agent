# 提示词：在用户本机重跑 GAIA 官方 53 题并把真实增益写入 README

> **注**：`results/gaia_official_run.json` 是重跑后生成的汇总快照（`benchmarks/gaia_official.py` 的 `run_official_comparison()` 写出），不在仓库中静态保留——旧快照已因与 `gaia_official_react.json` 数字矛盾而移除。执行 `python run_gaia_official.py` 即会自动再生。当前权威数据源是 `results/gaia_official_multi_agent.json` 与 `results/gaia_official_react.json`。

> 把下面整段（含说明）直接复制，发给另一个 AI（它会在**用户的本机**上执行，沙箱代不了——因为需要真实 API Key、外网、HuggingFace 数据集）。这个 AI 要做的不是改代码，而是**配置 → 评测 → 如实记录 → 单独 commit**。

---

## 你是谁 / 任务目标

你是一个执行助手。任务是：**在用户本机重跑 PECS 多智能体框架的 GAIA 官方 Level 1（53 题）评测，并把真实结果按诚实口径更新到 README，最后单独 commit（不要 push）。**

代码已经写好（四杠杆提分能力已落地：多模态后端可插拔、文本附件接线、Tavily 真实搜索、放弃型答案重试）。你只需要跑起来拿真实数字，并把数字如实写进文档。

**最重要的一条铁律：绝不编造数字。所有写进 README 的数字必须来自本次实际跑出的 `results/gaia_official_run.json`。跑不出来的就标 N/A 或保留原声明，不要猜测、不要估算、不要"美化"。**

---

## 项目背景（给执行 AI 的速览）

- 仓库：`pecs-multi-agent`，一个四角色（Planner/Executor/Critic/Synthesizer）多智能体框架，基于 LangGraph。
- 评测入口：`run_gaia_official.py`，跑 HuggingFace `gaia-benchmark/GAIA` Level 1 validation set（53 题），同时跑 PECS 和 ReAct 基线，并做 McNemar 显著性检验。
- 之前已实跑过一次（升级前），README 里写着：PECS 26.4% (14/53) vs ReAct 24.5% (13/53)，McNemar p=1.0 不显著。其中 4 道多模态题（2 png + 2 mp3）因未接多模态后端被 skip（0%）。
- 本次重跑目的：把刚落地的四杠杆（多模态 + 附件 + 搜索 + 重试）跑出真实增益。

---

## 前置条件（先检查，缺什么就停下来问用户，别硬跑）

1. **Python 环境**：项目用 Python ≥ 3.10。确认能 `python --version`。依赖已装（`pip install -r requirements.txt`）；若没装，先装。
2. **`.env` 已存在**且含有效的 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL`（默认 DeepSeek-chat，仓库已验证可跑）。如果 `.env` 不存在，先 `cp .env.example .env` 并让**用户**填入 LLM Key（Key 是秘密，你不要替用户编造）。
3. **网络与外网（2026-09 实测踩坑，务必先做连通性自检）**：评测要调真实 LLM API + 拉数据集 + **真实网页检索**。前两项通不代表第三项通——实测遇到过"LLM 网关正常、但外网检索全废"的情况，照跑会得出**因网络失败而系统性偏低的无效数字**。

   先跑这条自检（零成本）：
   ```bash
   python -c "
   import requests
   for name, url in [('LLM 网关', '<你的 LLM_BASE_URL>'), ('DuckDuckGo', 'https://duckduckgo.com'),
                     ('Wikipedia', 'https://en.wikipedia.org'), ('Tavily', 'https://api.tavily.com')]:
       try:
           print(f'{name:12} OK {requests.get(url, timeout=10).status_code}')
       except Exception as e:
           print(f'{name:12} FAIL {type(e).__name__}')
   "
   ```
   **判定标准**：`DuckDuckGo` 或 `Wikipedia` 任一项 FAIL，就**不要跑全量**——先解决检索可达性（配 `SEARCH_PROVIDER=tavily` + `SEARCH_API_KEY`，或换网络环境），否则白跑 3 小时拿一个无效数字。

   若 HuggingFace 被墙，需用户配 `HF_ENDPOINT` 或代理；若已配 `PEC_GAIA_LOCAL_DIR` 指向本地镜像（本仓库已就绪），则完全不触碰 HF。这是用户环境问题，你给指引即可，不代执行敏感操作。
4. **成本预期（务必先告知用户）**：53 题 × 每题多角色多次 LLM 调用，加上若启用多模态/搜索后端都要烧真实 API 额度。标定实测约 **195 秒/题**，53 题全量（仅 PECS）约 **2.9 小时**，加 ReAct 对比约 **5.8 小时**（网络不通时会大量卡满 300s 超时，更慢）。先和用户确认再开跑。

---

## 第零步：备份权威结果（**跳过这步会不可逆地丢数字**）

```bash
mkdir -p results/backup/pre-$(date +%Y%m%d-%H%M%S)
cp results/gaia_official_{multi_agent,react,run}.json results/backup/pre-*/
```

**为什么必须做**：`--num N` 试跑会把 N 题结果**直接写进与权威文件同名的文件**，覆盖 53 题的跑分。实测已踩过一次（3 题跑分把 `gaia_official_multi_agent.json` 覆盖成 3 题版本，靠备份恢复）。备份目录已在 `.gitignore` 中，不会入库。

---

## 第一步：配置多模态 + 搜索后端（需用户给 Key，你不要编造）

读取 `.env`，检查以下变量。它们**都是可选**的——不配也能跑（会优雅降级），但配了才有真实增益。

```
# 多模态后端（图片/音频/视频附件题用，OpenAI 兼容协议）
PEC_VISION_BASE_URL=https://api.openai.com/v1
PEC_VISION_MODEL=gpt-4o-mini
PEC_VISION_API_KEY=<用户提供的视觉 Key>
PEC_TRANSCRIBE_MODEL=gpt-4o-mini   # 音频转写模型，默认同 PEC_VISION_MODEL，可省略

# 真实搜索 API（提升 Web 接地质量）
PEC_SEARCH_PROVIDER=tavily
PEC_SEARCH_API_KEY=<用户提供的 Tavily Key>
```

- 如果用户**提供了**这些 Key：把它们写进 `.env`（用 Edit 工具改对应行，去掉前面的 `#` 注释并填值）。
- 如果用户**不提供**视觉/搜索 Key：照样跑，但在最终 README 里明确标注「本次多模态后端**未启用**，4 道多模态题仍按 skip 处理」，绝不把"未启用"说成"已生效"。
- **绝对不要**在 commit 里把真实 Key 写进任何文件（`.env` 已被 gitignore，确认一下 `.gitignore` 含 `.env`）。

---

## 第二步：先小批量冒烟（3 题），确认链路通

```bash
python run_gaia_official.py --num 3 --out results/_calib
```

> **`--out` 不可省**：不加它，3 题结果会覆写 `results/gaia_official_multi_agent.json`（权威跑分）。加 `--out` 后结果写到临时目录，权威文件零风险。

预期：能正常初始化、调 LLM、出结果，不报错退出。**重点看单题耗时与是否出现"搜索无结果/抓取超时"**：
- 若每题都卡到 300s 超时且答案为空 → 检索链路不通，回到前置条件第 3 条，**别继续全量**。
- `ModuleNotFoundError` → 让用户 `pip install -r requirements.txt`。
- 数据集拉不下来 → 确认 `PEC_GAIA_LOCAL_DIR` 指向本地镜像（`data/gaia/`），或配 `HF_ENDPOINT`。
- LLM 401/403 → 让用户检查 `LLM_API_KEY`。

冒烟通过（且**检索有真实结果**）后再跑全量。

---

## 第三步：全量重跑（拿真实数字）

```bash
python run_gaia_official.py --dump-failures
```

这会跑 PECS + ReAct 完整对比（默认 `--only all`），结果写入：
- `results/gaia_official_run.json`（聚合 + McNemar，你要读这个）
- `results/gaia_official_multi_agent.json`
- `results/gaia_official_react.json`
- `results/gaia_failures.json`（`--dump-failures`，PECS 答错的逐题详情）

**只想更新 PECS 数字、省一半时间**：加 `--only multi_agent`（但不产出 McNemar 对比）。

**当前状态（2026-09-12）**：本仓库的权威数字仍是**升级前的 26.4% / 24.5%**——升级后已尝试重跑，但实测本机外网检索不可达（`duckduckgo.com` 与 `en/zh.wikipedia.org` 均超时），此状态下重跑会得到因网络失败而系统性偏低的**无效数字**，故主动中止并保留原数字。README 已如实披露该边界，**不要用无效数字去"更新"它**。

**耐心等它跑完**，别中途杀进程（标定实测约 195s/题）。

---

## 第四步：读取真实结果（严格按字段取值，别估）

读 `results/gaia_official_run.json`，按下面映射取值（字段名以文件实际为准，读取时 `print` 出来核对）：

| README 表单元格 | JSON 字段 |
|---|---|
| PECS 多智能体 准确率 | `multi_agent.accuracy` → ×100，配 `(correct_count/total_samples)` |
| ReAct 基线 准确率 | `react_baseline.accuracy` → ×100，配 `(correct_count/total_samples)` |
| 无附件准确率（两列） | `multi_agent.no_attachment_acc` / `react_baseline.no_attachment_acc` |
| 有附件准确率（两列） | `multi_agent.with_attachment_acc` / `react_baseline.with_attachment_acc` |
| 平均 Token/题（两列） | `multi_agent.avg_tokens_per_task` / `react_baseline.avg_tokens_per_task` |
| 差值(pp) | `diff.accuracy_pp` |
| 统计检验 | `mcnemar_test.p_value`（标注是否 `< 0.05` 显著） |

**诚实要点**：README 表里 PECS 列和 ReAct 列**必须来自同一次重跑的同一对**（`gaia_official_run.json` 里的 `multi_agent` vs `react_baseline`），**禁止**拿"新 PECS"配"旧 24.5% ReAct"混搭——那会造假。本次重跑里多模态预处理和 Tavily 搜索对 PECS 和 ReAct **同时生效**（都在同一 harness 里），只有"放弃型答案重试"是 PECS 的 Synthesizer 独有，这点要在 README 用一句话说清，保证对比公平透明。

---

## 第五步：更新 README（诚实口径，单独成块）

用 Edit 工具改 `README.md`。定位「## 评测结果」一节下的「**GAIA 官方数据集验证**」表格和「**能力升级（待重跑确认**」块（靠标题文字定位，不要依赖行号）。

### 5.1 更新 GAIA 官方数据集验证表
把表里这几行换成第四步读到的真实值：

| 指标 | 旧值（升级前，仅供参考） | 要改成 |
|------|------|------|
| 准确率（总体） | 24.5%(13/53) / 26.4%(14/53) / +1.9pp / p=1.0 | 本次重跑真实值 |
| 准确率（无附件） | 28.6%(12/42) / 33.3%(14/42) | 本次重跑真实值 |
| 准确率（有附件） | 9.1%(1/11) / 0%(0/11) / "文本附件已接 file_parse；图音视频待多模态后端" | 本次重跑真实值（若多模态已启用，应能看到提升；若未启用，写明"本次仍未启用"） |
| 平均 Token/题 | 5,076 / 20,966 | 本次重跑真实值 |

保留「McNemar p=」这一列，用真实 `p_value`；若 p<0.05 标注"显著"，否则保留"不显著"。

### 5.2 处理「能力升级（待重跑确认）」块
这个块本来是"升级前写的预期，待重跑确认"。重跑后：
- 若增益坐实：把块标题改为「**能力升级（重跑已确认）**」，并把"预期回收/推向可解"改为"实测回收 X 道 / 实测提升至 Y%"，引用本次 `gaia_official_run.json` 数字。
- 若增益不明显（比如多模态没配、或题太难）：把块改为「**能力升级（重跑结果）**」，如实写"代码已落地但本次重跑未观测到明显增益，原因：…"，**不要删掉局限、不要粉饰**。

### 5.3 同步更新数据来源注释
README 里有一句「4 道（2 png + 2 mp3）多模态附件题因需多模态模型标记 skipped」——若本次启用了多模态后端，改成「4 道多模态题已接入后端预处理」或保留 skip 说明（取决于是否真配了 Key）。

### 5.4 总原则
- 数字只增不减地"写真"，不"写好看的"。
- 任何你不确定或文件里没有的字段，标 `N/A` 或保留原诚实声明，**不要编**。
- 不要动 GAIA L1 100%（33 题自建子集）和 WebShop +25pp 那几行——它们和本次任务无关，保持原样。

---

## 第六步：单独 commit（**不要 push**）

```bash
git add README.md
git commit -m "docs(gaia): 重跑官方53题更新真实评测数据（四杠杆增益确认）"
```

- **只 commit，不要 `git push`**。这个仓库历史曾因 `git filter-repo` 被改写过，push 是破坏性操作，必须由用户本人在确认后手动执行。你在回复里明确告诉用户"已 commit 未 push，等你确认再 push"。
- 如果本次还改了 `.env`——**不要**把 `.env` 加进 commit（确认 `.gitignore` 已忽略它）。只提交 README。
- 如果还顺手改了别的文件，单独评估是否要一起提交；本任务核心交付物是 README 更新。

---

## 成功标准

- [ ] 全量 53 题跑完，无崩溃
- [ ] `results/gaia_official_run.json` 存在且字段可读
- [ ] README 表里 PECS/ReAct 两列数字严格来自同一次重跑
- [ ] McNemar p 值如实填写
- [ ] 「能力升级」块已据实改写（确认 or 如实说明未增益）
- [ ] 已 commit，未 push，且回复中明确告知用户"未 push"

## 失败处理

- 跑一半 LLM 超时/限流：让用户检查 Key 额度，可加 `--timeout 180` 重跑；不要伪造部分结果。
- HF 数据集拉取失败：让用户配镜像/代理，给指引，不代执行敏感操作。
- 用户坚决不给任何 Key：照样能跑（全降级），但 README 必须写明"本次仅验证文本附件 + 搜索/重试增益，多模态未启用"，把"未启用"作为诚实结论而非缺陷。

## 硬约束（违反即失败）

1. **不造假**：任何数字必须有 JSON 出处。
2. **不 push**：commit 后停手，等用户确认。
3. **不泄漏 Key**：真实 Key 绝不写入 git 跟踪的文件。
4. **不改动无关结论**：GAIA 33 题 100%、WebShop +25pp 保持原样。
