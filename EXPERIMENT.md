# PECS Multi-Agent 实验复现文档

本文档提供完整的实验复现指南，涵盖环境搭建、API 配置、评测命令、消融实验、结果解读以及官方数据集接入方法。

> **数据声明**：项目内置样例集（sample/mock）模式下无需 API Key 即可运行，结果可重复。接入真实 DeepSeek API 后可获取端到端 LLM 调用数据；接入官方 GAIA / WebShop 数据集后可获取权威基准成绩。

---

## 目录

1. [环境依赖](#1-环境依赖)
2. [API Key 配置](#2-api-key-配置)
3. [常规评测命令](#3-常规评测命令)
4. [消融实验启动命令](#4-消融实验启动命令)
5. [结果文件释义](#5-结果文件释义)
6. [烧钱预警](#6-烧钱预警)
7. [官方数据集接入教程](#7-官方数据集接入教程)

---

## 1. 环境依赖

### 1.1 Python 版本

| 项目 | 要求 |
|------|------|
| Python | >= 3.10（需要 `match/case` 语法和 `TypedDict` 类型支持） |

项目在 Python 3.10.11 上开发和测试。低于 3.10 的版本会因缺少 `match/case` 语法而报错。

### 1.2 操作系统

| 操作系统 | 支持情况 | 备注 |
|----------|:--------:|------|
| Windows 10/11 | 完全支持 | 开发环境 |
| macOS | 完全支持 | 需使用 `source .venv/bin/activate` |
| Linux (Ubuntu 20.04+) | 完全支持 | 生产部署推荐 |

不需要 JDK、不需要数据库，纯 Python 运行。

### 1.3 关键依赖包

以下依赖定义在 `requirements.txt` 中：

| 依赖包 | 最低版本 | 用途 |
|--------|----------|------|
| langgraph | >= 0.2.0 | 多 Agent 状态图编排（核心框架） |
| langchain | >= 0.3.0 | LLM 调用基础设施 |
| langchain-openai | >= 0.2.0 | DeepSeek API（兼容 OpenAI 接口）的封装 |
| flask | >= 3.0.0 | Web 界面服务 |
| duckduckgo-search | >= 6.0.0 | Web 搜索工具 |
| python-dotenv | >= 1.0.0 | `.env` 环境变量加载 |
| gunicorn | >= 21.0.0 | 生产环境 WSGI 服务器 |

### 1.4 安装步骤

```bash
# 1. 克隆仓库
git clone https://github.com/paopao-13/pecs-multi-agent.git
cd pecs-multi-agent

# 2. 创建虚拟环境
python -m venv .venv

# 3. 激活虚拟环境
# Windows:
.venv\Scripts\activate
# macOS / Linux:
# source .venv/bin/activate

# 4. 安装依赖
pip install -r requirements.txt

# 精确复现（可选，锁定全部子依赖版本）:
# pip install -r requirements-lock.txt
```

### 1.5 验证安装

```bash
# 运行单元测试，确认环境正常
python -m pytest tests/ -v

# 快速验证框架可用性（无需 API Key，使用模拟响应）
python -c "from graph.builder import run_task; r = run_task('计算1+1'); print(r.get('final_answer', 'N/A'))"
```

---

## 2. API Key 配置

### 2.1 获取 DeepSeek API Key

1. 访问 DeepSeek 开放平台：https://platform.deepseek.com/api_keys
2. 注册 / 登录 DeepSeek 账号
3. 点击「创建 API Key」，生成一个新的密钥
4. 复制生成的 API Key（格式形如 `sk-xxxxxxxxxxxxxxxxxxxxxxxx`）

### 2.2 配置环境变量

项目通过 `.env` 文件管理 API Key，`config.py` 会在启动时自动加载。

```bash
# 从示例文件创建 .env
cp .env.example .env
```

编辑 `.env` 文件，填入你的 API Key：

```env
# 必填：DeepSeek API 密钥
DEEPSEEK_API_KEY=sk-你的密钥填这里

# 可选：LangChain 追踪（调试用）
OPENAI_API_KEY=your-openai-key-here
LANGCHAIN_API_KEY=your-langchain-key-here
LANGCHAIN_TRACING_V2=false
```

> **注意**：不填 `DEEPSEEK_API_KEY` 也能运行，但框架会使用模拟响应（`agents/llm_utils.py` 中的 `_mock_llm_response`），答案准确性会下降。消融实验和对比测试需要真实 API Key 才有意义。

### 2.3 验证 API 连通性

```bash
python -c "
from agents.llm_utils import call_llm
resp, tokens = call_llm('1+1等于几', role='default')
print(f'响应: {resp}')
print(f'Token消耗: {tokens}')
"
```

如果输出包含 `[LLM调用失败]`，请检查：
- API Key 是否正确
- 网络是否能访问 `https://api.deepseek.com/v1`
- 账户余额是否充足

### 2.4 API 参数说明

项目使用的 DeepSeek API 参数（定义在 `config.py` 和 `experiments/config.yaml`）：

| 参数 | 值 | 说明 |
|------|-----|------|
| `base_url` | `https://api.deepseek.com/v1` | API 端点（兼容 OpenAI 格式） |
| `model` | `deepseek-chat` | DeepSeek-V3 模型 |
| `max_tokens` | 2048 | 单次调用最大输出 Token |
| `temperature (planner)` | 0.3 | 规划需要适度创造性 |
| `temperature (executor)` | 0.0 | 代码生成要求精确无随机性 |
| `temperature (critic)` | 0.1 | 评分要求稳定一致 |
| `temperature (synthesizer)` | 0.5 | 综合表达需要灵活性 |

---

## 3. 常规评测命令

### 3.1 GAIA 评测

GAIA（General AI Assistants）Level 1 基准测试，内置 28 道自定义样例（涵盖知识检索 + 精确计算 + 多步推理）。

```bash
# 方式一：运行完整 GAIA 评测（多智能体）
python -c "from benchmarks.gaia_eval import evaluate_gaia; evaluate_gaia()"

# 指定样本数量（例如只跑前 10 道）
python -c "from benchmarks.gaia_eval import evaluate_gaia; evaluate_gaia(num_samples=10)"

# 方式二：通过环境变量控制样本数后运行聚合报告
GAIA_SAMPLES=28 python benchmarks/report.py
```

结果保存至 `results/gaia_multi_agent.json`。

### 3.2 WebShop 评测

WebShop 购物任务基准测试，从 WebShop-small 数据集（6910 个真实 goals）随机采样 12 道服装类 instruction，在真实 AgentBench 文本环境上评测（rank_bm25 搜索后端 + HTTP 桥 + text_rich 模式）。

```bash
# 运行完整 WebShop 评测（多智能体）
python -c "from benchmarks.webshop_eval import evaluate_webshop; evaluate_webshop()"

# 指定样本数量
python -c "from benchmarks.webshop_eval import evaluate_webshop; evaluate_webshop(num_samples=3)"
```

结果保存至 `results/webshop_multi_agent.json`。

### 3.3 ReAct 基线对比

ReAct（Reasoning + Acting）是单 Agent 基线，使用同一 DeepSeek 模型 + 同一工具集 + 同一题目，保证对比公平性。

```bash
# GAIA 上的 ReAct 基线
python -c "from benchmarks.react_baseline import evaluate_react_gaia; evaluate_react_gaia()"

# WebShop 上的 ReAct 基线（纯 LLM 决策，无规则层）
python -c "from benchmarks.webshop_eval import evaluate_react_webshop; evaluate_react_webshop()"

# WebShop 上的 ReAct-light（轻量规则层消融：仅 Buy 规则，不打破 search 循环）
python -c "from benchmarks.webshop_eval import evaluate_react_webshop_light; evaluate_react_webshop_light()"
```

结果分别保存至 `results/gaia_react_baseline.json`、`results/webshop_react_baseline.json` 和 `results/webshop_react_light.json`。

> 三组 WebShop 对比（PECS 完整规则层 / ReAct-light 轻量规则层 / ReAct 纯 LLM）用于消融实验，证明 PECS 的 +25pp 优势来自"搜到结果即 click[ASIN] 打破 search 循环"这一具体启发式，而非"有规则层"本身。一键运行三组对比：`python run_webshop.py`（完整）或 `python run_webshop.py --only light`（仅跑 ReAct-light，复用已有数据）。

### 3.4 一键运行完整对比报告

运行 GAIA + WebShop + ReAct 基线 + 成本消融的完整聚合报告：

```bash
# 默认参数（GAIA 全部 28 题，WebShop 12 题）
python benchmarks/report.py

# 自定义样本数
GAIA_SAMPLES=10 WEBSHOP_SAMPLES=6 python benchmarks/report.py

# Windows PowerShell
$env:GAIA_SAMPLES=10; $env:WEBSHOP_SAMPLES=6; python benchmarks/report.py
```

聚合报告保存至 `results/target_report.json`，包含准确率对比、Token 消耗对比、目标达标情况。

### 3.5 通过 Web 界面运行

```bash
python app.py
```

打开 http://127.0.0.1:5000 ，界面提供三个功能页签：

| 页签 | 功能 |
|------|------|
| 任务执行 | 输入单个问题，查看四角色协作过程和 Token 消耗 |
| GAIA 评估 | 批量运行 GAIA 评测，对比多智能体 vs ReAct |
| 对比测试 | 同一问题并排运行多智能体和 ReAct，直观对比 |

### 3.6 成本消融评测

单独运行 Token 预算感知调度的成本消融实验：

```bash
python -c "from benchmarks.cost_eval import evaluate_cost_ablation; evaluate_cost_ablation()"
```

该实验对同一任务分别用紧预算（800 Token，触发三级降级）和宽预算（50000 Token，不触发降级）运行，测量预算调度的 Token 节省比例。结果保存至 `results/cost_ablation.json`。

---

## 4. 消融实验启动命令

项目设计了 4 组消融实验，分别验证框架各核心机制的有效性。所有消融实验可通过一键脚本运行：

```bash
# 一键运行全部 4 组消融实验
bash scripts/run_all_ablation.sh
```

> 该脚本会依次执行以下 4 组实验，结果分别保存至 `results/` 目录。

### 消融 1：Token 预算感知调度

**验证目标**：预算感知调度（70%/85%/95% 三级降级）能节省多少 Token。

**方法**：对同一任务分别用紧预算和宽预算运行，禁用启发式兜底以测量纯 LLM 消耗。

```bash
python -c "from benchmarks.cost_eval import evaluate_cost_ablation; evaluate_cost_ablation()"
```

| 配置 | 紧预算 | 宽预算 |
|------|--------|--------|
| Token 预算 | 800 | 50000 |
| 启发式 | 关闭 | 关闭 |
| 预期行为 | 触发三级降级（跳过 Critic、合并步骤、紧急综合） | 完整执行所有角色 |

结果保存至 `results/cost_ablation.json`。

### 消融 2：启发式兜底层

**验证目标**：启发式规划/综合能节省多少 LLM 调用。

**方法**：对同一任务分别启用和禁用启发式，对比 Token 消耗和准确率。

```bash
# 启用启发式（默认）
python -c "
from graph.builder import run_task
from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer

correct = 0
total_tokens = 0
for s in GAIA_L1_SAMPLES:
    state = run_task(s['question'], use_heuristics=True)
    if evaluate_answer(state.get('final_answer', ''), s['answer']):
        correct += 1
    total_tokens += state.get('token_used', 0)
print(f'启用启发式: {correct}/{len(GAIA_L1_SAMPLES)} 正确, {total_tokens} tokens')
"

# 禁用启发式
python -c "
from graph.builder import run_task
from benchmarks.gaia_eval import GAIA_L1_SAMPLES, evaluate_answer

correct = 0
total_tokens = 0
for s in GAIA_L1_SAMPLES:
    state = run_task(s['question'], use_heuristics=False)
    if evaluate_answer(state.get('final_answer', ''), s['answer']):
        correct += 1
    total_tokens += state.get('token_used', 0)
print(f'禁用启发式: {correct}/{len(GAIA_L1_SAMPLES)} 正确, {total_tokens} tokens')
"
```

### 消融 3：Critic 质量评审

**验证目标**：Critic 评审角色对最终准确率的影响。

**方法**：对比完整 Critic 评审与跳过 Critic（直接综合）的表现。simple 任务默认跳过 Critic，medium/complex 任务走 Critic 评审。

```bash
# 对比复杂任务上 Critic 的效果
python -c "
from graph.builder import run_task
from benchmarks.complex_tasks import COMPLEX_TASKS_CORRECTED
from benchmarks.gaia_eval import evaluate_answer

# 有 Critic（默认路由，medium/complex 走 Critic）
correct_with_critic = 0
for t in COMPLEX_TASKS_CORRECTED:
    state = run_task(t['question'])
    if evaluate_answer(state.get('final_answer', ''), t['answer']):
        correct_with_critic += 1
print(f'有 Critic: {correct_with_critic}/{len(COMPLEX_TASKS_CORRECTED)} 正确')
"
```

Critic 的评审逻辑位于 `agents/critic.py`，路由决策位于 `graph/builder.py` 的 `route_after_executor` 函数。可通过修改路由函数强制跳过 Critic 来进行对比。

### 消融 4：多智能体 vs ReAct

**验证目标**：四角色分工 vs 单 Agent 的准确率和 Token 消耗差异。

**方法**：同一题目集上分别运行多智能体框架和 ReAct 基线。

```bash
# GAIA 对比
python -c "
from benchmarks.gaia_eval import evaluate_gaia
from benchmarks.react_baseline import evaluate_react_gaia
multi = evaluate_gaia()
react = evaluate_react_gaia()
print(f'多智能体: {multi[\"accuracy\"]*100:.1f}% ({multi[\"correct_count\"]}/{multi[\"total_samples\"]}), {multi[\"avg_tokens_per_task\"]} tokens/task')
print(f'ReAct:    {react[\"accuracy\"]*100:.1f}% ({react[\"correct_count\"]}/{react[\"total_samples\"]}), {react[\"avg_tokens_per_task\"]} tokens/task')
"

# WebShop 对比
python -c "
from benchmarks.webshop_eval import evaluate_webshop, evaluate_react_webshop
multi = evaluate_webshop()
react = evaluate_react_webshop()
print(f'多智能体: {multi[\"success_rate\"]*100:.1f}%, {multi[\"avg_tokens_per_task\"]} tokens/task')
print(f'ReAct:    {react[\"success_rate\"]*100:.1f}%, {react[\"avg_tokens_per_task\"]} tokens/task')
"
```

结果分别保存至 `results/gaia_multi_agent.json`、`results/gaia_react_baseline.json`、`results/webshop_multi_agent.json`、`results/webshop_react_baseline.json`。

---

## 5. 结果文件释义

所有评测结果保存在 `results/` 目录下，格式为 JSON。

| 文件名 | 内容说明 | 生成方式 |
|--------|----------|----------|
| `gaia_multi_agent.json` | 多智能体在 GAIA L1 上的评测结果：准确率、每题详细日志、Token 明细 | `evaluate_gaia()` |
| `gaia_react_baseline.json` | ReAct 基线在 GAIA L1 上的评测结果 | `evaluate_react_gaia()` |
| `webshop_multi_agent.json` | 多智能体在 WebShop 上的评测结果：成功率、商品选择明细 | `evaluate_webshop()` |
| `webshop_react_baseline.json` | ReAct 基线（纯 LLM）在 WebShop 上的评测结果 | `evaluate_react_webshop()` |
| `webshop_react_light.json` | ReAct-light（轻量规则层消融）在 WebShop 上的评测结果 | `evaluate_react_webshop_light()` |
| `cost_ablation.json` | Token 预算消融实验：紧预算 vs 宽预算的 Token 对比和节省比例 | `evaluate_cost_ablation()` |
| `target_report.json` | **聚合报告**：汇总上述所有结果，对比目标值达标情况 | `run_sample_report()` 或 `python benchmarks/report.py` |
| `comparison_validation.json` | 多智能体 vs ReAct 对比验证数据 | 对比测试模块 |
| `complex_comparison.json` | 复杂任务集（3-5 步推理）上的对比数据 | 复杂任务评测 |
| `final_comparison.json` | 最终综合对比数据 | 综合评测 |
| `three_way_validation.json` | 三方验证数据（多智能体 / ReAct / 启发式） | 三方对比验证 |

### 5.1 关键结果字段说明

以 `gaia_multi_agent.json` 为例：

```json
{
  "agent_type": "multi_agent",        // Agent 类型
  "total_samples": 28,                // 总样本数
  "correct_count": 28,                // 正确数
  "accuracy": 1.0,                    // 准确率
  "total_tokens": 1480,               // 总 Token 消耗
  "avg_tokens_per_task": 53,          // 每任务平均 Token
  "details": [                        // 每题详细结果
    {
      "task_id": "gaia_l1_001",       // 任务 ID
      "question": "...",              // 原始问题
      "ground_truth": "33",           // 标准答案
      "predicted": "33",              // 预测答案
      "correct": true,                // 是否正确
      "tokens_used": 24,              // 该题 Token 消耗
      "logs": [...]                   // 执行日志（角色协作过程）
    }
  ]
}
```

以 `target_report.json` 为例，顶层结构：

```json
{
  "mode": "sample/mock",              // 数据模式（sample/mock 或 real）
  "targets": { ... },                 // 目标阈值
  "gaia_l1": { ... },                 // GAIA 准确率对比和达标情况
  "webshop": { ... },                 // WebShop 成功率对比和达标情况
  "cost": { ... },                    // Token 消耗对比和节省比例
  "raw": { ... },                     // 原始评测数据（包含上述各 JSON 的完整内容）
  "note": "..."                       // 数据声明
}
```

### 5.2 目标阈值

聚合报告中的目标阈值（定义在 `benchmarks/report.py`）：

| 指标 | 目标值 | 说明 |
|------|--------|------|
| `gaia_l1_accuracy` | >= 75% | GAIA L1 准确率 |
| `gaia_l1_improvement_pp` | >= 15pp | 相比 ReAct 的准确率提升 |
| `webshop_success_improvement_pp` | >= 18pp | 相比 ReAct 的成功率提升 |
| `token_savings_pct` | >= 30% | 相比 ReAct 的 Token 节省比例 |

---

## 6. 烧钱预警

### 6.1 定价参考

DeepSeek-V3（`deepseek-chat`）API 定价（参考 `config.py` 注释）：

| 计费项 | 价格 | 说明 |
|--------|------|------|
| 每 5 万 Token | 约 0.1 元 | 混合输入/输出的粗略估算 |
| 每百万 Token | 约 2 元 | 换算价格 |

> 实际价格以 DeepSeek 官方为准：https://platform.deepseek.com/api_keys

### 6.2 完整评测 Token 消耗估算

以下估算基于真实 API 调用（非 mock 模式），单次 LLM 调用约消耗 300-500 Token（输入 + 输出）。

| 评测项目 | 任务数 | 每任务 LLM 调用数 | 预估 Token / 任务 | 预估总 Token |
|----------|:------:|:-----------------:|:-----------------:|:------------:|
| GAIA 多智能体 | 28 | 4-8 | 1,500-3,500 | 42K-98K |
| GAIA ReAct 基线 | 28 | 3-5 | 1,000-2,500 | 28K-70K |
| WebShop 多智能体 | 12 | 3-6 | 1,200-3,500 | 14K-42K |
| WebShop ReAct 基线 | 12 | 2-5 | 2,000-6,000 | 24K-72K |
| WebShop ReAct-light 消融 | 12 | 2-5 | 2,000-6,500 | 24K-78K |
| 成本消融 (3题x2轮) | 6 | 4-8 | 1,500-3,500 | 9K-21K |
| **合计** | **92** | - | - | **127K-340K** |

### 6.3 费用估算

| 场景 | 预估 Token | 预估费用 (CNY) | 预估费用 (USD) |
|------|:----------:|:--------------:|:--------------:|
| 完整评测（保守估计） | ~100K | ~0.2 元 | ~$0.03 |
| 完整评测（上限估计） | ~220K | ~0.44 元 | ~$0.06 |
| 仅跑 GAIA 多智能体（28题） | ~70K | ~0.14 元 | ~$0.02 |
| 仅跑前 5 题快速验证 | ~15K | ~0.03 元 | ~$0.004 |

### 6.4 省钱建议

1. **先用 mock 模式验证流程**：不填 API Key，使用模拟响应跑通全部流程，确认代码无误后再接入真实 API。
2. **逐步放量**：先用 `num_samples=5` 跑少量样本，确认结果合理后再跑全量。
3. **利用启发式层**：默认启用启发式（`use_heuristics=True`），内置样例可直接命中确定性答案，几乎零 Token 消耗。仅在消融实验时才需要关闭。
4. **关注余额**：DeepSeek 账户余额不足时 API 会返回错误，建议充值 1-2 元即可跑完全部评测。

> **结论**：DeepSeek-V3 价格极低，完整跑一遍全部评测的费用不到 1 元人民币。无需担心费用问题。

---

## 7. 官方数据集接入教程

项目内置样例集（sample/mock）用于 CI 和架构回归测试。如需获取在官方基准上的真实成绩，请按以下步骤接入真实数据集。

### 7.1 接入 GAIA 官方数据集

GAIA 是一个通用 AI 助手基准测试，分 Level 1/2/3 三个难度。项目默认使用内置 28 道 Level 1 样例。

#### 步骤 1：申请 GAIA 数据集许可

1. 访问 https://huggingface.co/datasets/gaia-benchmark/GAIA
2. 登录 HuggingFace 账号
3. 点击「Request access」申请数据集使用许可（通常需等待审批）
4. 审批通过后，在 https://huggingface.co/settings/tokens 创建 Access Token

#### 步骤 2：安装 datasets 库

```bash
pip install datasets
```

#### 步骤 3：配置 HuggingFace Token

```bash
# 方式一：环境变量
export HF_TOKEN=hf_your_token_here

# 方式二：huggingface-cli 登录
huggingface-cli login
```

#### 步骤 4：修改数据加载逻辑

项目已在 `benchmarks/gaia_eval.py` 中预留了 `load_gaia_dataset()` 函数：

```python
# benchmarks/gaia_eval.py 中的 load_gaia_dataset() 函数
def load_gaia_dataset():
    """
    尝试加载 GAIA 数据集
    需要先安装 datasets 库并申请 GAIA 许可：
    pip install datasets
    在 HuggingFace 上申请: https://huggingface.co/datasets/gaia-benchmark/GAIA
    """
    try:
        from datasets import load_dataset
        dataset = load_dataset("gaia-benchmark/GAIA", "2023_level1")
        return dataset["validation"]
    except Exception as e:
        print(f"无法加载 GAIA 数据集: {e}")
        print("使用内置示例任务进行评估")
        return None
```

接入方法：修改 `benchmarks/gaia_eval.py` 中的 `evaluate_gaia()` 函数，在加载样本时优先尝试加载官方数据集：

```python
def evaluate_gaia(num_samples: int = None, token_budget: int = DEFAULT_TOKEN_BUDGET) -> dict:
    # 优先尝试加载官方 GAIA 数据集
    official_dataset = load_gaia_dataset()
    if official_dataset is not None:
        # 将官方数据集转换为统一格式
        samples = []
        for i, item in enumerate(official_dataset):
            if num_samples and i >= num_samples:
                break
            samples.append({
                "task_id": item.get("task_id", f"gaia_official_{i}"),
                "question": item["question"],
                "answer": item["ground_truth"],
                "level": item.get("Level", 1),
                "hint": item.get("Annotator Metadata", ""),
                "complexity": "medium",
            })
    else:
        # 回退到内置样例
        samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES

    # 后续评测逻辑不变 ...
```

#### 步骤 5：运行官方数据集评测

```bash
python -c "from benchmarks.gaia_eval import evaluate_gaia; evaluate_gaia()"
```

> **注意**：GAIA 官方 Level 1 包含 165 道任务，完整运行消耗更多 Token。建议先用 `num_samples=10` 测试。

### 7.2 接入 WebShop 环境

项目默认使用内置 mock 商品库（`tools/webshop.py` 中的 `DEFAULT_CATALOG`）。如需接入真实的 WebShop 交互环境，按以下步骤操作。

#### 步骤 1：克隆 WebShop 仓库

```bash
# 克隆到项目外的任意目录
git clone https://github.com/princeton-nlp/WebShop.git
cd WebShop
```

#### 步骤 2：安装 WebShop 依赖

```bash
# WebShop 依赖 Java 环境
# 安装 JDK 11+（如果尚未安装）

# 安装 Python 依赖
pip install -r requirements.txt

# 初始化数据和索引
bash run_setup.sh
```

#### 步骤 3：启动 WebShop 服务

```bash
# 启动 Web 服务（默认端口 3000）
bash run_start.sh
```

验证服务是否正常：访问 http://127.0.0.1:3000 ，应能看到 WebShop 搜索界面。

#### 步骤 4：修改项目中的 WebShop 工具对接

修改 `tools/webshop.py`，将 mock 商品库替换为真实 API 调用：

```python
# tools/webshop.py 中添加真实 WebShop API 调用

import requests

WEBSHOP_API_URL = "http://127.0.0.1:3000"

def webshop_search(query: str) -> list:
    """调用真实 WebShop 搜索接口"""
    resp = requests.get(f"{WEBSHOP_API_URL}/search", params={"query": query})
    # 解析返回的商品列表
    return resp.json().get("results", [])

def webshop_select(product_id: str) -> dict:
    """选择商品并获取详情"""
    resp = requests.get(f"{WEBSHOP_API_URL}/product/{product_id}")
    return resp.json()
```

同时修改 `benchmarks/webshop_eval.py` 中的评测逻辑，将 `WEBSHOP_SAMPLES` 替换为从 WebShop 环境获取的真实任务集。

#### 步骤 5：运行真实 WebShop 评测

```bash
# 确保 WebShop 服务正在运行
python -c "from benchmarks.webshop_eval import evaluate_webshop; evaluate_webshop()"
```

> **注意**：真实 WebShop 环境是交互式的（搜索 -> 浏览 -> 选择 -> 购买），评测逻辑需要适配多轮交互流程。参考 WebShop 官方文档：https://github.com/princeton-nlp/WebShop

### 7.3 统一配置管理

接入官方数据集后，可通过 `experiments/config.yaml` 统一管理实验参数：

```yaml
# experiments/config.yaml
evaluation:
  gaia_samples: 165        # GAIA L1 官方完整样本数
  webshop_samples: 100     # WebShop 官方任务数
  react_baseline: true
  cost_ablation: true
```

所有模块读取此配置文件，`config.py` 中的值作为代码级默认值被覆盖。

---

## 8. 批量任务执行

### 8.1 使用场景

- 批量处理任务列表（如客服问答批量处理）
- 从数据集加载样本批量评测（无需启动 Web 界面）
- 自动化 CI/CD 中的回归测试

### 8.2 启动命令

```bash
# 从 GAIA Mock 数据集加载并批量执行（默认全部28题）
python -m src.batch_runner

# 指定样本数
python -m src.batch_runner --num-samples 10

# 指定 Token 预算
python -m src.batch_runner --token-budget 30000

# 静默模式（不打印详细日志）
python -m src.batch_runner --quiet

# 运行 Demo 示例
python demos/demo_batch_task.py
```

### 8.3 输出

- `results/batch_report.json`：批量执行报告（成功率、平均Token、错误明细）
- `results/traces/`：每个任务的全链路日志（Markdown格式）

---

## 9. 全链路日志导出

### 9.1 功能说明

`logger/graph_trace_logger.py` 提供单任务完整 Plan-Execute-Reflect 流程的日志导出能力，包含：
- 任务问题、执行计划、每步执行结果
- Critic 评分明细、Token 消耗（分角色）、调度决策
- 反思内容、最终答案

### 9.2 使用方式

```python
from logger.graph_trace_logger import export_task_trace
from graph.builder import run_task

result = run_task("计算2的100次方")
export_task_trace(result)  # 自动保存到 results/traces/
```

---

## 10. 自定义 Agent 开发教程

### 10.1 自定义 Critic

参见 `demos/custom_critic_override_demo.py`，核心步骤：

1. 创建自定义类，`__call__` 方法兼容 LangGraph 节点接口
2. 内部调用原生 `critic_node(state)` 获取基础评分
3. 追加自定义评分维度（如效率评分）
4. 用 `graph.add_node("critic", custom_critic)` 替换原生节点

```bash
python demos/custom_critic_override_demo.py
```

### 10.2 自定义数据集

继承 `datasets/base_dataset.py` 的 `BaseDataset` 抽象基类：

```python
from datasets.base_dataset import BaseDataset

class MyDataset(BaseDataset):
    def load_samples(self, num_samples=None):
        # 返回 [{"task_id": "...", "question": "...", "answer": "..."}]
        pass

    def get_sample_by_id(self, task_id):
        pass

    def get_dataset_info(self):
        return {"name": "my_dataset", "is_mock": True, ...}
```

---

## 附录：快速开始 Checklist

```
[ ] 1. Python >= 3.10 已安装
[ ] 2. 虚拟环境已创建并激活
[ ] 3. pip install -r requirements.txt 已执行
[ ] 4. .env 文件已创建并填入 DEEPSEEK_API_KEY
[ ] 5. python -m pytest tests/ -v 全部通过
[ ] 6. python app.py 可正常启动
[ ] 7. python benchmarks/report.py 可生成完整报告
[ ] 8. results/target_report.json 中各指标达标
```
