# PECS 多智能体协作框架 — 量化目标可行性评估报告

> 评估对象：https://github.com/paopao-13/pecs-multi-agent
> 评估日期：2026-07-16
> 评估方法：全量源码审计 + 评测数据复盘 + 架构瓶颈分析

---

## 一、评估结论速览

| 量化目标 | 当前数据 | 表面达标 | 真实可行性 | 核心问题 |
|----------|----------|:--------:|:----------:|----------|
| GAIA L1 准确率 ≥75%（+15pp） | 100% vs 80% = +20pp | ✅ | ⚠️ 有条件可行 | 10题自定义样例，8/10靠启发式硬编码；官方165题预期大幅下降 |
| WebShop 成功率 +18pp | 100% vs 0% = +100pp | ✅ | ❌ 不可行 | 本地mock适配器（8商品），非真实AgentBench环境 |
| Token 降本 ≥30% | 38.8%端到端 | ✅ | ⚠️ 部分可行 | 纯预算调度仅4.5%；38.8%主要来自启发式捷径，非预算机制 |

**总判定**：三项目标在当前自定义评测集上均"表面达标"，但在官方基准上**均无法直接成立**。架构设计本身具备可行性，但存在评测方法学缺陷和工程实现瓶颈，需经过实质性改造才能在真实基准上验证。

---

## 二、架构实现审计

### 2.1 代码与文档一致性

对核心模块逐一审计后确认：**文档描述与代码实现高度一致**，未发现虚构功能。

| 模块 | 文件 | 实现状态 | 质量评价 |
|------|------|:--------:|----------|
| PECS 四角色 | `agents/{planner,executor,critic,synthesizer}.py` | ✅ 已实现 | 职责边界清晰，输入输出契约规范 |
| LangGraph 状态图 | `graph/builder.py` | ✅ 已实现 | 5节点+3条件路由，编译执行正确 |
| Plan-Execute-Reflect 循环 | `graph/builder.py` route_after_synthesizer | ✅ 已实现 | 反思回环+三重终止保护（迭代5/重试3/预算95%） |
| Token 三级降级 | `graph/token_budget.py` | ✅ 已实现 | 70%/85%/95%阈值+角色独立配额 |
| AST 安全沙箱 | `tools/python_repl.py` | ✅ 已实现 | Import拦截+函数黑名单+白名单builtins |
| 启发式兜底层 | `agents/heuristics.py` | ✅ 已实现 | 20+硬编码模式匹配 |

### 2.2 架构亮点

1. **角色温度分离**：Planner(0.3)/Executor(0.0)/Critic(0.1)/Synthesizer(0.5)，每个角色有独立LLM实例，设计合理
2. **Critic 双层验证**：先尝试规则验证（零Token），规则无法判断时才调LLM，成本控制意识强
3. **Synthesizer 确定性任务快速路径**：检测到python/webshop工具结果时跳过LLM综合，直接抽取答案

### 2.3 架构短板

1. **四角色串行执行**：无并行化，README已知问题#2，延迟较高
2. **搜索工具单一**：仅DuckDuckGo，无网页浏览/解析能力
3. **无文件处理能力**：file_read工具存在但无PDF/Excel/Image解析
4. **启发式层不可泛化**：`heuristics.py` 中20+个`if`分支精确匹配题目文本，本质是"答案表"

---

## 三、目标一：GAIA L1 准确率 75%（+15pp）可行性分析

### 3.1 当前评测数据复盘

从 `results/target_report.json` 提取的10题评测明细：

| 任务 | 类型 | 多智能体 Token | ReAct Token | 多智能体路径 |
|------|------|:--------------:|:-----------:|:------------:|
| gaia_l1_001 Python发布年份 | 知识检索 | 2001 | 772 | LLM规划+搜索+LLM综合 |
| gaia_l1_003 Fibonacci第20项 | 计算 | **4** | 825 | **启发式直接返回** |
| gaia_l1_004 诺贝尔图灵奖 | 知识检索 | 2382 | 1267 | LLM规划+搜索+LLM综合 |
| gaia_l1_005 100!位数 | 计算 | **3** | 444 | **启发式直接返回** |
| gaia_l1_008 2^100首位 | 计算 | **3** | 934 | **启发式直接返回** |
| gaia_l1_016 2^30-2^20 | 计算 | **6** | 546 | **启发式直接返回** |
| gaia_l1_017 17^5 | 计算 | **5** | 466 | **启发式直接返回** |
| gaia_l1_021 3^18-3^12 | 计算 | **5** | 441 | **启发式直接返回** |
| gaia_l1_026 5^12-5^8 | 计算 | **5** | 775 | **启发式直接返回** |
| gaia_l1_028 7^8-7^5 | 计算 | **5** | 753 | **启发式直接返回** |

**关键发现**：10题中8题通过启发式层直接返回硬编码答案（3-6 Token），仅2题走了真实的多智能体LLM协作流程。

### 3.2 启发式层本质分析

`agents/heuristics.py` 中的 `build_heuristic_plan()` 函数包含20+个 `if` 分支，精确匹配题目文本：

```python
# 示例：精确匹配 "2的30" AND "2的20" AND "减去" → 返回 print(2**30 - 2**20)
if "2的30" in query and "2的20" in query and "减去" in query:
    return {"steps": [_step(1, "python", "...", {"code": "print(2**30 - 2**20)"})]}
```

`synthesize_heuristic_answer()` 同样硬编码了答案：

```python
if "2的30" in query and "2的20" in query and "减去" in query:
    return "1072693248"  # 直接返回答案字符串
```

**本质**：这不是"多智能体协作推理"，而是"查表返回预设答案"。启发式层在评测中贡献了8/10的正确率和极低的Token消耗，严重扭曲了评测结论。

### 3.3 ReAct 基线的公平性

ReAct 基线使用相同的 GLM-4.7-Flash 模型和工具集，但存在一个关键差异：

- **多智能体**：启发式层直接生成 `print(2**30 - 2**20)` 代码，Python沙箱精确执行
- **ReAct**：LLM自行推理，计算 `2^30 = 1073741824` 后**忘记执行减法**，直接返回被减数

这确实是ReAct的固有弱点（长链推理中的"执行漂移"），但多智能体在此处的优势来自**启发式代码生成**而非**四角色协作纠错**。如果禁用启发式层，多智能体的Planner也需要LLM来理解题目并生成代码，同样可能出错。

### 3.4 官方 GAIA L1 预期

官方 GAIA Level 1（165题）的题型分布与当前28题自定义样例差异巨大：

| 官方GAIA L1题型 | 当前框架支持 | 原因 |
|-----------------|:----------:|------|
| 文件解析（PDF/Excel/图片） | ❌ | 无PDF/Image解析工具，file_read仅支持文本 |
| 网页浏览与信息提取 | ❌ | 无浏览器工具，search仅返回摘要 |
| 多源信息交叉验证 | ⚠️ 部分 | 架构支持多步搜索，但搜索质量受限 |
| 视频/音频理解 | ❌ | 无多媒体处理能力 |
| 精确数值计算 | ✅ | Python沙箱可处理 |
| 知识检索 | ⚠️ 部分 | DuckDuckGo搜索质量不稳定 |

**保守估计**：在官方GAIA L1上，考虑到约40%的题目需要文件/网页/多媒体处理（当前框架完全不支持），即使其余60%的题目全部答对，准确率上限也仅约60%，**低于75%目标**。

### 3.5 差距分析

| 维度 | 当前状态 | 目标要求 | 差距 |
|------|----------|----------|------|
| 评测样本量 | 10题（自定义） | ≥165题（官方L1） | 样本量不足，无统计显著性 |
| 题型覆盖 | 计算+知识检索（2类） | 6+类（含文件/网页/多媒体） | 缺少4+类题型支持 |
| 启发式依赖 | 8/10题靠硬编码 | 0（官方题无预设答案） | 启发式层完全失效 |
| 搜索质量 | DuckDuckGo摘要 | 需精确网页内容提取 | 搜索工具能力不足 |
| 准确率预期 | 100%（自定义） | ≥75%（官方） | 预期降至50-65% |

### 3.6 可行性判定

**有条件可行，但当前实现无法达标。**

架构设计（PECS四角色+Plan-Execute-Reflect）本身是合理的多智能体范式，在知识检索和计算类任务上有真实价值。但要达到官方GAIA L1 75%准确率，需要：

1. 扩展工具集（文件解析、网页浏览）
2. 移除启发式硬编码，验证纯多智能体协作能力
3. 接入官方数据集评测，样本量≥30

---

## 四、目标二：AgentBench WebShop 成功率 +18pp 可行性分析

### 4.1 当前评测数据复盘

从 `results/target_report.json` 提取的3题评测：

| 任务 | 多智能体 | ReAct | 多智能体Token | ReAct Token |
|------|:--------:|:-----:|:--------------:|:-----------:|
| webshop_001 茉莉绿茶 | ✅ (43 tok) | ❌ (1062 tok) | 启发式+webshop工具 | LLM直接编造答案 |
| webshop_002 洋甘菊茶 | ✅ (46 tok) | ❌ (1739 tok) | 启发式+webshop工具 | LLM列出多个商品名 |
| webshop_003 USB-C充电器 | ✅ (46 tok) | ❌ (1624 tok) | 启发式+webshop工具 | LLM编造"Anker"品牌 |

### 4.2 Mock 环境与真实 AgentBench 的差异

| 维度 | 当前Mock适配器 | 真实AgentBench WebShop |
|------|---------------|----------------------|
| 商品库规模 | 8个硬编码商品 | ~12,000个真实商品 |
| 交互模式 | 单次调用返回结果 | 多轮交互（搜索→浏览→选属性→购买） |
| 属性匹配 | 关键词评分算法 | 复杂属性约束（颜色/尺寸/版本等） |
| 评测标准 | target_id是否在输出中 | 属性匹配率+购买正确率 |
| 环境依赖 | 纯Python函数 | 需启动Web服务+Java环境 |

### 4.3 多智能体"成功"的原因

多智能体的webshop工具 (`tools/webshop.py`) 本质是一个**规则匹配器**：

```python
def webshop_select(args):
    # 遍历8个商品，关键词匹配评分，返回得分最高的
    for item in catalog:
        score = _score_item(instruction, item)  # 关键词重叠计分
    return f"SELECTED: {best['id']} | {best['name']}"
```

多智能体框架只是调用了这个工具一次，获取结果后直接输出。**没有任何LLM推理、多步探索或属性约束推理**。

### 4.4 ReAct"失败"的原因

ReAct的LLM直接生成了答案（如"Anker PowerPort Atom PD 2"），没有调用webshop工具。原因：

1. ReAct的system prompt列出了webshop工具，但LLM选择直接回答
2. LLM从训练数据中"回忆"了真实商品名，但这些商品不在mock目录中
3. 评测标准是 `target_id in predicted`，LLM生成的商品名不包含target_id

**这是提示工程问题，不是架构劣势。** 如果优化ReAct的prompt强制使用webshop工具，ReAct同样能达到100%。

### 4.5 真实 AgentBench 预期

在真实AgentBench WebShop环境中：

1. **12,000个商品**：关键词匹配算法无法处理如此大规模的商品库，需要语义搜索
2. **多轮交互**：用户需要搜索→浏览商品详情→选择属性→确认购买，当前框架是单次调用
3. **属性约束**：如"红色、128GB、 unlocked iPhone"，需要理解并匹配多个属性维度
4. **评测标准**：不是简单的target_id匹配，而是属性匹配率（reward = matched_attributes / total_attributes）

**预期**：当前框架在真实AgentBench WebShop上的成功率接近0%，**无法实现+18pp目标**。

### 4.6 可行性判定

**不可行。当前评测结果无效。**

原因：
1. 评测环境是本地mock适配器，与AgentBench无关
2. 多智能体的"成功"来自规则匹配器，不是多智能体协作
3. ReAct的"失败"来自提示工程缺陷，不是架构劣势
4. 真实AgentBench需要多轮交互和大规模商品搜索，当前架构完全不支持

---

## 五、目标三：Token 预算感知调度降本 30% 可行性分析

### 5.1 当前评测数据复盘

#### 端到端对比（含启发式）

| 指标 | 多智能体 | ReAct | 降幅 |
|------|:--------:|:-----:|:----:|
| 总Token | 4419 | 7223 | 38.8% |
| 平均Token/任务 | 442 | 722 | 38.8% |

#### 纯预算调度消融（禁用启发式）

| 指标 | 紧预算(800) | 宽预算(50000) | 降幅 |
|------|:-----------:|:-------------:|:----:|
| 平均Token/任务 | 877 | 918 | **4.5%** |

#### 分任务类型Token分析

| 任务类型 | 多智能体 | ReAct | 差异 |
|----------|:--------:|:-----:|:----:|
| 计算类（8题，启发式） | 4.5 avg | 689 avg | -99.4% |
| 知识检索类（2题，LLM） | 2192 avg | 1019 avg | **+115%** |

### 5.2 38.8% 降本的真相

端到端38.8%的降本几乎完全来自启发式层：

```
多智能体总Token = 8题×4.5 + 2题×2192 = 36 + 4384 = 4420
ReAct总Token    = 8题×689  + 2题×1019 = 5512 + 2038 = 7550
```

- 计算类任务：启发式直接返回（3-6 Token），ReAct需LLM多轮推理（400-900 Token）
- 知识检索类任务：多智能体四角色各调LLM，Token反而是ReAct的2倍

**结论**：38.8%降本是"启发式捷径"的产物，不是"Token预算感知调度"的功劳。

### 5.3 纯预算调度仅 4.5% 的原因

消融实验（禁用启发式）显示纯预算调度仅节省4.5%：

| 任务 | 紧预算(800) | 宽预算(50000) | 节省 | 调度决策 |
|------|:-----------:|:-------------:|:----:|----------|
| 2^30-2^20 | 881 | 914 | 3.6% | emergency_synthesize |
| 3^18-3^12 | 845 | 887 | 4.7% | emergency_synthesize |
| 5^12-5^8 | 904 | 952 | 5.0% | emergency_synthesize |

**原因分析**：

1. **预算800太小**：单次LLM调用约消耗300-500 Token，800 Token预算在第一轮Planner调用后即触发95%降级，系统直接进入紧急模式
2. **紧急模式只是跳过后续步骤**：省下的只是"不执行的步骤"的Token，而非"优化执行效率"
3. **四角色架构固有开销**：即使预算充足，Planner+Executor+Critic+Synthesizer四次LLM调用的基线成本就高于ReAct的单次循环

### 5.4 预算调度的实际作用

预算调度机制（70%/85%/95%三级降级）的实际价值是**成本上限保护**，而非**成本优化**：

- **设计意图**：防止任务无限循环消耗过多Token
- **实际效果**：在预算即将耗尽时强制终止，避免成本失控
- **局限**：无法降低单次LLM调用的Token消耗，无法减少四角色的固有调用次数

### 5.5 知识检索类任务的成本悖论

```
多智能体知识检索流程：
Planner调用LLM(规划) → Executor调用LLM(生成参数) → 搜索工具 → Critic调用LLM(评分) → Synthesizer调用LLM(综合)
= 4次LLM调用

ReAct知识检索流程：
LLM推理(Thought+Action) → 搜索工具 → LLM推理(Thought+Final Answer)
= 2次LLM调用
```

多智能体在知识检索类任务上**固有成本是ReAct的2倍**。Critic的质量评审虽然能拦截错误，但每次评审都消耗额外Token。

### 5.6 差距分析

| 维度 | 当前状态 | 目标要求 | 差距 |
|------|----------|----------|------|
| 端到端降本 | 38.8%（启发式驱动） | ≥30%（预算调度驱动） | 归因错误，纯预算调度仅4.5% |
| 纯预算调度 | 4.5% | ≥30% | 差25.5pp |
| 知识检索类 | +115%（更高） | 需降低 | 架构性成本膨胀 |
| 预算阈值设计 | 800(测试)/50000(默认) | 需合理阈值 | 800过小，50000过大 |

### 5.7 可行性判定

**部分可行，但当前实现远未达标。**

预算调度机制的设计思路正确（成本上限保护），但要实现30%降本目标，需要：
1. 减少四角色的固有LLM调用次数（如合并Planner+Executor为一次调用）
2. 扩大Critic规则验证的覆盖范围（减少LLM评分调用）
3. 使用更小的模型处理简单步骤（模型路由）

---

## 六、修改方案设计

### 6.1 方案一：GAIA L1 达标改造（优先级 P0）

#### 6.1.1 扩展工具集

```python
# tools/file_parser.py — 新增文件解析工具
def parse_pdf(file_path: str) -> str:
    """使用 PyMuPDF 解析PDF文本和表格"""
    import fitz  # PyMuPDF
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    return text

def parse_excel(file_path: str) -> str:
    """使用 openpyxl 解析Excel数据"""
    from openpyxl import load_workbook
    wb = load_workbook(file_path, data_only=True)
    # 提取所有sheet的数据为文本
    ...

def parse_image(file_path: str) -> str:
    """使用多模态LLM解析图片内容"""
    # 调用支持视觉的LLM（如GPT-4V/GLM-4V）提取图片信息
    ...
```

#### 6.1.2 增加网页浏览工具

```python
# tools/web_browser.py — 新增网页浏览工具
from playwright.sync_api import sync_playwright

def browse_webpage(url: str, selector: str = None) -> str:
    """使用Playwright渲染网页并提取内容"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        content = page.inner_text(selector) if selector else page.content()
        browser.close()
        return content[:5000]  # 限制返回长度控制Token
```

#### 6.1.3 移除启发式层，验证纯多智能体能力

```python
# 评测时强制禁用启发式
state = run_task(question, token_budget=50000, use_heuristics=False)
# 在官方GAIA数据集上运行，获取真实准确率
```

#### 6.1.4 接入官方GAIA数据集

```python
# benchmarks/gaia_official_eval.py
from datasets import load_dataset

def evaluate_gaia_official(num_samples=165):
    dataset = load_dataset("gaia-benchmark/GAIA", "2023_level1")["validation"]
    for item in dataset[:num_samples]:
        # GAIA官方题目可能包含文件附件
        if item.get("file_name"):
            # 先用file_parser解析附件
            file_content = parse_pdf(item["file_path"])
            question = f"{item['question']}\n\n附件内容:\n{file_content}"
        else:
            question = item["question"]
        
        state = run_task(question, use_heuristics=False)
        # 使用官方评估标准
        is_correct = evaluate_answer_official(
            state["final_answer"], item["ground_truth"]
        )
```

#### 6.1.5 预期改造后效果

| 改造项 | 预期准确率提升 | 实现难度 |
|--------|:--------------:|:--------:|
| 文件解析工具 | +15-20pp | 中 |
| 网页浏览工具 | +5-10pp | 高 |
| 禁用启发式验证 | -10-15pp（短期） | 低 |
| 接入官方数据集 | 评测有效性 | 中 |
| **综合预期** | **55-70%** | — |

### 6.2 方案二：WebShop 达标改造（优先级 P1）

#### 6.2.1 接入真实 WebShop 环境

```python
# tools/webshop_real.py — 对接真实WebShop环境
import requests

WEBSHOP_BASE_URL = "http://127.0.0.1:3000"

def webshop_search_real(query: str, page: int = 1) -> dict:
    """调用真实WebShop搜索接口"""
    resp = requests.get(f"{WEBSHOP_BASE_URL}/search_results/{query}/{page}")
    return {
        "products": _parse_products(resp.text),
        "total": _extract_total(resp.text),
        "has_next": _has_next_page(resp.text),
    }

def webshop_get_product(asin: str, options: dict = None) -> dict:
    """获取商品详情并选择属性"""
    resp = requests.get(f"{WEBSHOP_BASE_URL}/item_page/{asin}")
    product = _parse_product_detail(resp.text)
    # 选择属性（颜色、尺寸等）
    if options:
        product["selected_options"] = options
    return product

def webshop_purchase(asin: str, options: dict) -> dict:
    """执行购买操作"""
    resp = requests.post(f"{WEBSHOP_BASE_URL}/checkout", json={
        "asin": asin, "options": options
    })
    return resp.json()
```

#### 6.2.2 多轮交互流程设计

```python
# graph/builder.py — 新增webshop专用子图
def build_webshop_subgraph():
    """WebShop多轮交互子图"""
    graph = StateGraph(WebShopState)
    
    graph.add_node("search", webshop_search_node)      # 搜索商品
    graph.add_node("browse", webshop_browse_node)       # 浏览详情
    graph.add_node("select_options", webshop_select_node) # 选择属性
    graph.add_node("purchase", webshop_purchase_node)   # 执行购买
    graph.add_node("critic", webshop_critic_node)       # 验证购买正确性
    
    # 搜索 → 浏览 → 选择 → 购买 → 验证
    graph.add_edge("search", "browse")
    graph.add_edge("browse", "select_options")
    graph.add_edge("select_options", "purchase")
    graph.add_edge("purchase", "critic")
    # Critic不通过 → 重新搜索
    graph.add_conditional_edges("critic", route_webshop_critic)
    
    return graph.compile()
```

#### 6.2.3 预期改造后效果

| 改造项 | 预期成功率 | 实现难度 |
|--------|:----------:|:--------:|
| 真实WebShop环境 | 30-50% | 高 |
| 多轮交互流程 | +10-15pp | 中 |
| 属性约束推理 | +5-10pp | 中 |
| **综合预期** | **40-65%** | — |

> 参考基线：AgentBench论文中GPT-4 WebShop成功率约35-40%，+18pp目标意味着需达到53-58%，对GLM-4.7-Flash级别模型有挑战性。

### 6.3 方案三：Token 降本达标改造（优先级 P0）

#### 6.3.1 角色合并优化

```python
# graph/builder.py — Planner+Executor合并为单次LLM调用
def planner_executor_merged_node(state: dict) -> dict:
    """合并规划和执行为一次LLM调用，减少50% Planner Token"""
    prompt = f"""
    用户任务: {state['query']}
    
    请直接生成执行计划并填充工具参数（一步到位）。
    输出JSON格式的完整步骤列表，每步包含action和args。
    """
    # 一次LLM调用完成规划+参数生成（原需2次）
    plan_data, tokens = call_llm_json(prompt, MERGED_SYSTEM_PROMPT, role="planner")
    return {"plan": plan_data["steps"], ...}
```

#### 6.3.2 模型路由（大小模型混合）

```python
# agents/llm_utils.py — 按任务复杂度选择模型
MODEL_ROUTING = {
    "simple": "glm-4-flash",       # 简单任务用小模型（成本低10倍）
    "medium": "glm-4-flash",       # 中等任务用小模型
    "complex": "glm-4-plus",       # 复杂任务用大模型
    "critic_fast": "glm-4-flash",  # 快速评审用小模型
}

def get_routed_llm(role: str, complexity: str) -> ChatOpenAI:
    """根据复杂度路由到不同模型"""
    model_key = f"{complexity}" if role != "critic" else "critic_fast"
    model_name = MODEL_ROUTING.get(model_key, "glm-4-flash")
    # 小模型成本约为大模型的1/10
    ...
```

#### 6.3.3 Critic 规则验证扩展

```python
# agents/critic.py — 扩展规则验证覆盖范围
def _rule_evaluate(result: dict) -> dict:
    """扩展规则验证，减少LLM评分调用"""
    # 现有：python/search/webshop规则
    # 新增：数值验证、日期验证、实体验证
    
    if action == "python":
        # 数值范围验证：如果期望结果在合理范围内
        expected_range = _infer_expected_range(description)
        if expected_range and _extract_number(text):
            if expected_range[0] <= _extract_number(text) <= expected_range[1]:
                return _score(5, 5, 5, "数值在合理范围内")
    
    if action == "search":
        # 实体一致性验证：检查搜索结果是否包含期望实体
        expected_entities = _extract_entities(description)
        if expected_entities:
            found = [e for e in expected_entities if e.lower() in text.lower()]
            if len(found) == len(expected_entities):
                return _score(5, 5, 5, "所有期望实体均已找到")
    
    # 扩展更多规则...
    return None  # 回退到LLM
```

#### 6.3.4 预期降本效果

| 优化措施 | 预期降本 | 实现难度 |
|----------|:--------:|:--------:|
| Planner+Executor合并 | -15-20% | 中 |
| 模型路由（小模型处理简单步骤） | -30-40% | 低 |
| Critic规则验证扩展 | -5-10% | 中 |
| 上下文压缩（传递摘要而非全文） | -5-10% | 低 |
| **综合预期** | **-40-60%** | — |

#### 6.3.5 修正后的评测方法

```python
# benchmarks/cost_eval_fixed.py — 修正消融实验设计
def evaluate_cost_ablation_fixed():
    """修正后的成本消融实验"""
    
    # 问题1：预算800太小 → 使用合理预算
    BUDGETED = 5000   # 合理预算，触发部分降级
    UNBUDGETED = 50000  # 宽预算，不触发降级
    
    # 问题2：仅用3道计算题 → 使用多样化任务集
    SAMPLES = [
        # 计算类
        "gaia_l1_016", "gaia_l1_021", "gaia_l1_026",
        # 知识检索类
        "gaia_l1_001", "gaia_l1_004", "gaia_l1_011",
        # 多步推理类
        "gaia_l1_012", "gaia_l1_013", "gaia_l1_014",
    ]
    
    # 问题3：禁用启发式 → 启用启发式但分离统计
    for sample in SAMPLES:
        # 分别统计：启发式节省 + 预算调度节省 + 模型路由节省
        ...
```

---

## 七、总结与建议

### 7.1 三个目标的综合判定

| 目标 | 判定 | 核心原因 | 改造优先级 |
|------|:----:|----------|:----------:|
| GAIA L1 75% | ⚠️ 有条件可行 | 架构可行但工具集不足，启发式扭曲评测 | P0 |
| WebShop +18pp | ❌ 当前不可行 | 评测环境无效，需对接真实AgentBench | P1 |
| Token -30% | ⚠️ 部分可行 | 纯预算调度仅4.5%，需模型路由+角色合并 | P0 |

### 7.2 评测方法学问题（共性问题）

三个目标的"表面达标"都源于同一个根本问题：**评测方法学缺陷**。

1. **样本量不足**：GAIA n=10，WebShop n=3，远低于统计显著性最低要求 n≥30
2. **自定义样例**：非官方数据集，题目设计可能偏向框架优势
3. **启发式混淆**：硬编码答案被计入多智能体协作成果
4. **基线不公平**：ReAct的prompt未优化工具调用，导致虚假优势
5. **消融不充分**：成本消融仅用3道计算题，预算800不合理

### 7.3 建议的改进路线图

```
Phase 1（1-2周）：评测修正
├── 接入官方GAIA数据集（≥30题）
├── 禁用启发式层，运行纯多智能体评测
├── 优化ReAct基线prompt，确保公平对比
└── 扩大样本量至n≥30，计算置信区间

Phase 2（2-4周）：工具扩展
├── 新增PDF/Excel/Image解析工具
├── 新增Playwright网页浏览工具
├── 扩展Critic规则验证覆盖范围
└── 实现模型路由（大小模型混合）

Phase 3（4-8周）：架构优化
├── Planner+Executor合并调用
├── 无依赖步骤并行执行
├── 对接真实WebShop环境
└── 上下文压缩与Token优化
```

### 7.4 架构设计的真实价值

尽管当前评测存在缺陷，PECS架构本身的设计是**有真实价值的**：

1. **四角色分工**确实减少了单Agent的"执行漂移"问题（ReAct在2道大数减法题上忘记执行减法）
2. **Token预算调度**提供了成本上限保护，防止任务失控
3. **Critic双层反思**在知识检索类任务上能拦截搜索参数错误（如案例文档所示）
4. **AST安全沙箱**是生产级的安全设计，可防止LLM生成的恶意代码

**关键改进方向**：将评测从"自定义样例+启发式"转向"官方数据集+纯多智能体"，让架构的真实价值得到公正验证。

---

*本报告基于项目源代码（commit截至2026-07-15）和 `results/target_report.json` 评测数据进行全量审计分析。所有代码引用均来自实际源文件，未做任何虚构。*
