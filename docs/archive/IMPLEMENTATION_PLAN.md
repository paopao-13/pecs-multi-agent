# PECS 多智能体框架 — 三大量化目标达标实施方案

> 基于可行性分析报告的差距诊断，为三大目标提供代码级、可执行的改造方案。
> 每个方案包含：问题根因 → 改造策略 → 具体代码变更 → 预期效果 → 验证方法。

---

## 目录

- [目标一：GAIA L1 准确率 ≥75%（+15pp vs ReAct）](#目标一gaia-l1-准确率-75pp-vs-react)
- [目标二：AgentBench WebShop 成功率 +18pp](#目标二agentbench-webshop-成功率-18pp)
- [目标三：Token 预算感知调度降本 30%](#目标三token-预算感知调度降本-30)
- [评测方法学修正（三目标共性）](#评测方法学修正三目标共性)
- [实施路线图](#实施路线图)

---

## 目标一：GAIA L1 准确率 ≥75%（+15pp vs ReAct）

### 1.1 问题根因回顾

| 问题 | 当前状态 | 影响 |
|------|----------|------|
| 启发式层硬编码答案 | `heuristics.py` 中 20+ 个 `if` 分支精确匹配题目文本，直接返回预设答案 | 8/10 题靠查表，评测结果无效 |
| 工具集不足以覆盖官方题型 | 无 PDF/Excel/Image 解析、无网页浏览 | 官方 L1 约 40% 题型无法处理 |
| 评测样本仅 10 题自定义 | 非官方数据集，统计无显著性 | 无法验证真实泛化能力 |
| ReAct 基线不公平 | ReAct 的 LLM 不调用 webshop 工具，直接编造答案 | 虚假的 +20pp 优势 |

### 1.2 改造策略总览

```
策略矩阵：
┌─────────────────────────────┬──────────┬──────────┬──────────┐
│ 改造项                       │ 预期提升  │ 实现难度  │ 优先级    │
├─────────────────────────────┼──────────┼──────────┼──────────┤
│ A. 启发式层重构为代码模式提取器  │ 评测有效性 │ 中        │ P0       │
│ B. 新增 PDF/Excel/Image 工具  │ +15-20pp │ 中        │ P0       │
│ C. 新增 Playwright 网页浏览    │ +5-10pp  │ 高        │ P1       │
│ D. 接入官方 GAIA 数据集        │ 评测有效性 │ 中        │ P0       │
│ E. 修复 ReAct 基线公平性       │ 基线准确  │ 低        │ P0       │
│ F. Planner few-shot 增强      │ +3-5pp   │ 低        │ P1       │
└─────────────────────────────┴──────────┴──────────┴──────────┘
```

### 1.3 改造 A：启发式层重构为"代码模式提取器"（P0）

#### 1.3.1 核心思路

当前 `heuristics.py` 的本质是"答案表"——对每道题硬编码了 `print(2**30 - 2**20)` 和返回 `"1072693248"`。

**改造方向**：将"精确匹配题目文本 → 返回硬编码答案"替换为"解析数学表达式 → 动态生成 Python 代码"。这样启发式层不再依赖题目原文，而是靠模式识别提取计算意图。

#### 1.3.2 具体代码变更

**文件：`agents/heuristics.py`**

删除所有精确匹配 `if` 分支，替换为以下结构：

```python
import re

def build_heuristic_plan(query: str, merge_steps: bool = False) -> Optional[Dict[str, Any]]:
    """Return a deterministic plan for known benchmark/task patterns.
    
    改造后：仅处理可泛化的模式（数学表达式提取、WebShop 关键词），
    不再硬编码任何题目的完整文本匹配或预设答案。
    """
    q = query.lower()

    # === WebShop 任务：保留，因为 instruction 本身是动态参数 ===
    if "webshop" in q or "购买" in query or "商品" in query or "shop" in q:
        return {
            "complexity": "medium",
            "steps": [
                _step(1, "webshop", "根据购物需求检索并选择最匹配的商品",
                      {"instruction": query}, risk="high")
            ],
        }

    # === 数学表达式提取（新增，替代 20+ 硬编码 if 分支）===
    math_code = _extract_math_expression(query)
    if math_code:
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", "执行数学计算", {"code": math_code}, risk="low")
            ],
        }

    # === Fibonacci 数列（保留，因为模式可泛化）===
    if "fibonacci" in q or "斐波那契" in query:
        n = _first_int(query, default=20)
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算Fibonacci数列的第{n}项",
                      {"code": _fib_code(n)}, risk="low")
            ],
        }

    # === 阶乘计算（新增泛化模式）===
    factorial_match = re.search(r'(\d+)\s*[的]?\s*阶乘', query)
    if factorial_match:
        n = int(factorial_match.group(1))
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算{n}的阶乘",
                      {"code": f"print(math.factorial({n}))"}, risk="low")
            ],
        }

    # === 平方和（新增泛化模式）===
    sq_sum_match = re.search(r'前\s*(\d+)\s*[个]?\s*[正整自然数]*\s*的?平方和', query)
    if sq_sum_match:
        n = int(sq_sum_match.group(1))
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算前{n}个正整数的平方和",
                      {"code": f"print(sum(i**2 for i in range(1, {n+1})))"}, risk="low")
            ],
        }

    # === 偶数/奇数求和（新增泛化模式）===
    even_sum_match = re.search(r'(\d+)\s*[以到内]*\s*偶数.*和', query)
    if even_sum_match:
        n = int(even_sum_match.group(1))
        return {
            "complexity": "simple",
            "steps": [
                _step(1, "python", f"计算2到{n}所有偶数的和",
                      {"code": f"print(sum(i for i in range(2, {n+1}, 2)))"}, risk="low")
            ],
        }

    return None  # 不再硬编码任何题目特定答案


def _extract_math_expression(query: str) -> Optional[str]:
    """
    从自然语言中提取数学表达式并生成 Python 代码。
    
    支持的模式：
    - "X的Y次方减去X的Z次方" → print(X**Y - X**Z)
    - "X的Y次方" → print(X**Y)
    - "X的Y次方的首位" → print(str(X**Y)[0])
    - "X的阶乘的位数" → print(len(str(math.factorial(X))))
    """
    # 幂次差：X的Y次方减去X的Z次方
    power_diff = re.findall(r'(\d+)\s*的\s*(\d+)\s*次方', query)
    if len(power_diff) >= 2 and ('减去' in query or '减' in query or '差' in query):
        bases = [int(m[0]) for m in power_diff]
        exps = [int(m[1]) for m in power_diff]
        if bases[0] == bases[1]:
            return f"print({bases[0]}**{exps[0]} - {bases[0]}**{exps[1]})"
        else:
            return f"print({bases[0]}**{exps[0]} - {bases[1]}**{exps[1]})"

    # 单个幂次：X的Y次方
    if power_diff and ('首位' not in query and '第一位' not in query):
        b, e = int(power_diff[0][0]), int(power_diff[0][1])
        return f"print({b}**{e})"

    # 幂次首位：X的Y次方的首位
    if power_diff and ('首位' in query or '第一位' in query):
        b, e = int(power_diff[0][0]), int(power_diff[0][1])
        return f"print(str({b}**{e})[0])"

    # 阶乘位数：X的阶乘的位数 / X!的位数
    factorial_digits = re.search(r'(\d+)\s*[!！]\s*[的]?\s*(位数|多少位)', query)
    if factorial_digits:
        n = int(factorial_digits.group(1))
        return f"print(len(str(math.factorial({n}))))"
    factorial_digits2 = re.search(r'(\d+)\s*的阶乘.*位数', query)
    if factorial_digits2:
        n = int(factorial_digits2.group(1))
        return f"print(len(str(math.factorial({n}))))"

    return None


def synthesize_heuristic_answer(query: str, results: List[Dict[str, Any]]) -> str:
    """Extract a direct answer from deterministic tool outputs.
    
    改造后：不再返回任何硬编码答案字符串，全部从工具执行结果中提取。
    """
    if not results:
        return ""

    combined = "\n".join(str(r.get("result", "")) for r in results)
    q = query.lower()

    # WebShop：从 SELECTED 行提取
    if "webshop" in q or "购买" in query or "商品" in query or "shop" in q:
        selected = _match_line(combined, r"SELECTED:\s*(.+)")
        return selected or combined.strip()

    # 所有其他类型：从 Python 输出中提取最终结果
    # 按执行顺序取最后一个 Python 步骤的输出
    python_outputs = [
        _clean_python_output(str(r.get("result", "")))
        for r in results
        if r.get("action") == "python"
    ]
    python_outputs = [o for o in python_outputs if o]
    if python_outputs:
        return python_outputs[-1]

    # 搜索结果：返回原始内容
    if len(results) == 1:
        return str(results[0].get("result", "")).strip()

    return ""
```

**关键变更说明**：

| 变更项 | 旧行为 | 新行为 |
|--------|--------|--------|
| `build_heuristic_plan` | 20+ 个精确文本匹配 `if` 分支 | 6 个正则模式提取器（幂次、阶乘、平方和、偶数和、Fibonacci） |
| `synthesize_heuristic_answer` | 硬编码返回 `"1072693248"` 等答案 | 从 Python 工具输出中动态提取 |
| 覆盖范围 | 仅匹配 28 道自定义样例 | 可泛化到任意幂次/阶乘/平方和计算题 |

#### 1.3.3 验证方法

```python
# tests/test_heuristics_generalization.py
from agents.heuristics import build_heuristic_plan, synthesize_heuristic_answer

def test_power_diff_generalization():
    """测试幂次差模式是否泛化到未见过的新题目"""
    # 原始题目
    plan = build_heuristic_plan("计算2的30次方减去2的20次方")
    assert plan is not None
    assert "print(2**30 - 2**20)" in plan["steps"][0]["args"]["code"]
    
    # 新题目（未硬编码）
    plan = build_heuristic_plan("计算13的7次方减去13的3次方")
    assert plan is not None
    assert "print(13**7 - 13**3)" in plan["steps"][0]["args"]["code"]

def test_no_hardcoded_answers():
    """确保 synthesize_heuristic_answer 不返回任何硬编码答案"""
    results = [{"action": "python", "result": "输出: 42"}]
    answer = synthesize_heuristic_answer("计算7的8次方减去7的5次方", results)
    assert answer == "42"  # 从工具输出提取，不是硬编码
```

### 1.4 改造 B：新增文件解析工具（P0）

#### 1.4.1 新建文件 `tools/file_parser.py`

```python
"""
文件解析工具集

支持 GAIA 官方 L1 中常见的文件附件类型：
- PDF 文档解析（PyMuPDF）
- Excel 表格解析（openpyxl）
- 图片内容解析（多模态 LLM）
- CSV 文件解析（标准库 csv）
"""
from __future__ import annotations

import csv
import os
from typing import Any, Dict


def parse_pdf(args: dict) -> str:
    """
    解析 PDF 文件，提取文本和表格内容。
    
    Args:
        {"file_path": "/path/to/file.pdf", "max_pages": 50}
    
    Returns:
        提取的文本内容（截断到 8000 字符以控制 Token）
    """
    import fitz  # PyMuPDF
    
    file_path = args.get("file_path", "")
    max_pages = args.get("max_pages", 50)
    
    if not file_path or not os.path.exists(file_path):
        return f"错误：文件不存在 {file_path}"
    
    doc = fitz.open(file_path)
    text_parts = []
    
    for i, page in enumerate(doc):
        if i >= max_pages:
            text_parts.append(f"\n[已截断：仅显示前{max_pages}页]")
            break
        page_text = page.get_text()
        text_parts.append(f"--- 第{i+1}页 ---\n{page_text}")
    
    doc.close()
    full_text = "\n".join(text_parts)
    
    # 控制 Token：截断到 8000 字符
    if len(full_text) > 8000:
        full_text = full_text[:8000] + "\n[内容已截断]"
    
    return full_text


def parse_excel(args: dict) -> str:
    """
    解析 Excel 文件，提取所有 sheet 的数据。
    
    Args:
        {"file_path": "/path/to/file.xlsx", "max_rows": 100}
    
    Returns:
        表格内容的文本表示
    """
    from openpyxl import load_workbook
    
    file_path = args.get("file_path", "")
    max_rows = args.get("max_rows", 100)
    
    if not file_path or not os.path.exists(file_path):
        return f"错误：文件不存在 {file_path}"
    
    wb = load_workbook(file_path, data_only=True)
    text_parts = []
    
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        text_parts.append(f"=== Sheet: {sheet_name} ===")
        
        for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
            if row_idx >= max_rows:
                text_parts.append(f"[已截断：仅显示前{max_rows}行]")
                break
            # 跳过全空行
            if all(cell is None for cell in row):
                continue
            row_text = " | ".join(str(cell) if cell is not None else "" for cell in row)
            text_parts.append(row_text)
    
    wb.close()
    full_text = "\n".join(text_parts)
    
    if len(full_text) > 8000:
        full_text = full_text[:8000] + "\n[内容已截断]"
    
    return full_text


def parse_image(args: dict) -> str:
    """
    使用多模态 LLM 解析图片内容。
    
    Args:
        {"file_path": "/path/to/image.png", "question": "这张图片中有多少个人？"}
    
    Returns:
        LLM 对图片内容的描述
    """
    import base64
    
    file_path = args.get("file_path", "")
    question = args.get("question", "请描述这张图片的内容")
    
    if not file_path or not os.path.exists(file_path):
        return f"错误：文件不存在 {file_path}"
    
    # 读取图片并编码为 base64
    with open(file_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")
    
    # 获取文件扩展名确定 MIME 类型
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp"}
    mime = mime_map.get(ext, "image/png")
    
    # 调用多模态 LLM
    from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    from langchain_openai import ChatOpenAI
    
    if not LLM_API_KEY:
        return f"[无API Key] 图片文件: {file_path}，无法进行多模态分析"
    
    llm = ChatOpenAI(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,  # 需要使用支持视觉的模型如 glm-4v
        temperature=0.1,
        max_tokens=1024,
    )
    
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_data}"}},
        ],
    }
    
    response = llm.invoke([message])
    return response.content


def parse_csv(args: dict) -> str:
    """解析 CSV 文件"""
    file_path = args.get("file_path", "")
    max_rows = args.get("max_rows", 100)
    
    if not file_path or not os.path.exists(file_path):
        return f"错误：文件不存在 {file_path}"
    
    text_parts = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row_idx, row in enumerate(reader):
            if row_idx >= max_rows:
                text_parts.append(f"[已截断：仅显示前{max_rows}行]")
                break
            text_parts.append(" | ".join(row))
    
    return "\n".join(text_parts)
```

#### 1.4.2 注册新工具

**文件：`tools/__init__.py`**

```python
# 新增导入
from tools.file_parser import parse_pdf, parse_excel, parse_image, parse_csv

# 扩展工具注册表
TOOL_REGISTRY = {
    "search": web_search,
    "python": python_repl,
    "file_read": file_reader,
    "api_call": api_caller,
    "webshop": webshop_select,
    # 新增文件解析工具
    "parse_pdf": parse_pdf,
    "parse_excel": parse_excel,
    "parse_image": parse_image,
    "parse_csv": parse_csv,
}

# 扩展工具描述
TOOL_DESCRIPTIONS = {
    ...  # 保留原有描述
    "parse_pdf": "PDF文件解析工具。输入文件路径，提取PDF文本和表格内容。适用于需要阅读PDF附件的任务。",
    "parse_excel": "Excel文件解析工具。输入文件路径，提取所有sheet的数据。适用于需要处理Excel表格的任务。",
    "parse_image": "图片解析工具。输入文件路径和分析问题，使用多模态LLM理解图片内容。适用于需要分析图片的任务。",
    "parse_csv": "CSV文件解析工具。输入文件路径，提取表格数据。适用于需要处理CSV文件的任务。",
}
```

#### 1.4.3 更新 Planner 系统提示词

**文件：`agents/planner.py`**

在 `PLANNER_SYSTEM_PROMPT` 中追加可用工具说明：

```python
PLANNER_SYSTEM_PROMPT = """你是一个任务规划专家（Planner），负责将用户的复杂任务分解为可执行的步骤列表。

...

可用工具：
- search: Web搜索工具，查找实时信息
- python: Python代码执行，计算和数据处理
- file_read: 读取本地文件
- api_call: 调用外部API
- webshop: WebShop商品选择工具
- parse_pdf: PDF文件解析，提取文本和表格内容
- parse_excel: Excel文件解析，提取表格数据
- parse_image: 图片内容解析（使用多模态LLM理解图片）
- parse_csv: CSV文件解析

文件处理规则：
- 如果用户问题包含文件附件路径，先用对应的解析工具提取内容
- 解析结果作为后续步骤的输入（通过 description 传递关键信息）
- 对于图片，可以在 parse_image 的 args 中指定具体的分析问题

输出格式（严格JSON）：
...
"""
```

### 1.5 改造 C：新增 Playwright 网页浏览工具（P1）

#### 1.5.1 新建文件 `tools/web_browser.py`

```python
"""
网页浏览工具

使用 Playwright 渲染网页并提取内容，支持：
- 页面全文提取
- CSS 选择器定位提取
- 搜索结果页面链接提取
"""
from __future__ import annotations
from typing import Any, Dict


def browse_webpage(args: dict) -> str:
    """
    渲染网页并提取文本内容。
    
    Args:
        {
            "url": "https://example.com",
            "selector": "div.content",  # 可选，CSS选择器
            "max_chars": 5000           # 可选，最大返回字符数
        }
    
    Returns:
        网页文本内容
    """
    url = args.get("url", "")
    selector = args.get("selector")
    max_chars = args.get("max_chars", 5000)
    
    if not url:
        return "错误：缺少 url 参数"
    
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            # 等待页面基本加载完成
            page.wait_for_timeout(2000)
            
            if selector:
                content = page.inner_text(selector)
            else:
                # 提取页面主要内容（优先 article, main, body）
                for tag in ["article", "main", "body"]:
                    try:
                        element = page.query_selector(tag)
                        if element:
                            content = element.inner_text()
                            break
                    except Exception:
                        continue
                else:
                    content = page.content()
            
            browser.close()
            
            # 清理和截断
            content = content.strip()
            if len(content) > max_chars:
                content = content[:max_chars] + "\n[内容已截断]"
            
            return content
            
    except ImportError:
        return "错误：Playwright 未安装。请运行: pip install playwright && playwright install chromium"
    except Exception as e:
        return f"网页浏览失败: {type(e).__name__}: {str(e)}"


def extract_links(args: dict) -> str:
    """
    从网页中提取链接列表。
    
    Args:
        {"url": "https://example.com", "filter": "python"}  # filter 可选
    
    Returns:
        链接列表文本
    """
    url = args.get("url", "")
    link_filter = args.get("filter", "")
    
    if not url:
        return "错误：缺少 url 参数"
    
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            
            links = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(el => ({text: el.innerText.trim(), href: el.href}))"
            )
            browser.close()
            
            # 过滤和格式化
            result_lines = []
            for link in links:
                text = link.get("text", "").strip()
                href = link.get("href", "")
                if not href or href.startswith("javascript:"):
                    continue
                if link_filter and link_filter.lower() not in text.lower():
                    continue
                result_lines.append(f"{text}: {href}")
            
            return "\n".join(result_lines[:30])  # 最多返回30个链接
            
    except Exception as e:
        return f"链接提取失败: {type(e).__name__}: {str(e)}"
```

#### 1.5.2 注册到工具表

```python
# tools/__init__.py 追加
from tools.web_browser import browse_webpage, extract_links

TOOL_REGISTRY["browse_webpage"] = browse_webpage
TOOL_REGISTRY["extract_links"] = extract_links

TOOL_DESCRIPTIONS["browse_webpage"] = "网页浏览工具。输入URL，渲染网页并提取文本内容。适用于需要阅读网页内容的任务。"
TOOL_DESCRIPTIONS["extract_links"] = "链接提取工具。从网页中提取链接列表。适用于需要找到相关页面URL的任务。"
```

#### 1.5.3 依赖安装

```bash
pip install playwright pymupdf openpyxl
playwright install chromium
```

### 1.6 改造 D：接入官方 GAIA 数据集（P0）

#### 1.6.1 新建文件 `benchmarks/gaia_official_eval.py`

```python
"""
官方 GAIA Level 1 评测

从 HuggingFace 加载官方 GAIA 数据集（165 题），
使用多智能体框架评测真实准确率。
"""
from __future__ import annotations

import os
import json
from typing import Optional

from graph.builder import run_task
from benchmarks.gaia_eval import evaluate_answer, save_results


def load_gaia_official(num_samples: Optional[int] = None):
    """
    加载官方 GAIA Level 1 数据集。
    
    数据集地址: gaia-benchmark/GAIA (2023_level1, validation split)
    需要先运行: huggingface-cli login (GAIA 需要接受协议)
    
    Returns:
        list of {task_id, question, answer, file_name, file_path}
    """
    from datasets import load_dataset
    
    dataset = load_dataset("gaia-benchmark/GAIA", "2023_level1", split="validation")
    
    samples = []
    for i, item in enumerate(dataset):
        if num_samples and i >= num_samples:
            break
        
        sample = {
            "task_id": f"gaia_official_{i+1:03d}",
            "question": item["question"],
            "answer": item["ground_truth"],
            "level": item.get("Level", 1),
            "file_name": item.get("file_name", ""),
            "file_path": item.get("file_path", ""),
            "anonymized_apx": item.get("AnonymizedAppx", ""),
        }
        samples.append(sample)
    
    return samples


def evaluate_gaia_official(
    num_samples: int = 30,
    token_budget: int = 50000,
    use_heuristics: bool = False,
    output_file: str = "gaia_official_results.json",
) -> dict:
    """
    在官方 GAIA Level 1 上评测多智能体框架。
    
    参数:
        num_samples: 评测样本数（建议 ≥30 以获得统计显著性）
        token_budget: 每个任务的 Token 预算
        use_heuristics: 是否启用启发式（官方评测建议设为 False）
    """
    samples = load_gaia_official(num_samples)
    
    results = []
    correct_count = 0
    total_tokens = 0
    
    # 确保文件附件目录存在
    attachment_dir = os.path.join(os.path.dirname(__file__), "..", "gaia_attachments")
    os.makedirs(attachment_dir, exist_ok=True)
    
    for i, sample in enumerate(samples):
        task_id = sample["task_id"]
        question = sample["question"]
        ground_truth = sample["answer"]
        
        # 如果有文件附件，在问题中附带附件路径
        if sample["file_name"] and sample["file_path"]:
            file_ext = os.path.splitext(sample["file_name"])[1].lower()
            question = f"{question}\n\n附件文件: {sample['file_path']}"
        
        print(f"[{i+1}/{len(samples)}] 评测 {task_id}: {question[:80]}...", flush=True)
        
        try:
            state = run_task(question, token_budget=token_budget, use_heuristics=use_heuristics)
            predicted = state.get("final_answer", "")
            tokens_used = state.get("token_used", 0)
        except Exception as e:
            predicted = f"[执行失败] {type(e).__name__}: {str(e)}"
            tokens_used = 0
        
        total_tokens += tokens_used
        is_correct = evaluate_answer(predicted, ground_truth)
        if is_correct:
            correct_count += 1
        
        results.append({
            "task_id": task_id,
            "question": question[:200],
            "ground_truth": ground_truth,
            "predicted": predicted[:200],
            "correct": is_correct,
            "tokens_used": tokens_used,
            "has_file": bool(sample["file_name"]),
        })
        
        print(f"  → 预测: {predicted[:80]}... | 正确: {is_correct}", flush=True)
    
    accuracy = correct_count / len(samples) if samples else 0
    avg_tokens = total_tokens / len(samples) if samples else 0
    
    eval_result = {
        "benchmark": "gaia_official_l1",
        "agent_type": "multi_agent",
        "total_samples": len(samples),
        "correct_count": correct_count,
        "accuracy": round(accuracy, 4),
        "total_tokens": total_tokens,
        "avg_tokens_per_task": round(avg_tokens),
        "use_heuristics": use_heuristics,
        "details": results,
    }
    
    save_results(eval_result, output_file)
    return eval_result
```

### 1.7 改造 E：修复 ReAct 基线公平性（P0）

#### 1.7.1 问题

当前 ReAct 基线（`benchmarks/react_baseline.py`）存在两个公平性问题：

1. **WebShop 任务中 LLM 不调用 webshop 工具**：LLM 直接从训练数据中编造商品名，导致 `target_id not in predicted`
2. **计算任务中 LLM 可能跳过 Python 执行**：LLM 自己计算大数减法，容易出错

#### 1.7.2 具体修复

**文件：`benchmarks/react_baseline.py`**

```python
# 修改 REACT_SYSTEM_PROMPT，强化工具使用约束

REACT_SYSTEM_PROMPT = """你是一个 ReAct 智能体，需要通过推理和行动来完成任务。

⚠️ 重要规则：
1. 你必须使用工具来完成任务，不要直接从记忆中给出答案
2. 对于计算问题，必须使用 python 工具执行计算，不要心算
3. 对于购物问题，必须使用 webshop 工具选择商品，不要编造商品名
4. 对于信息查询，必须使用 search 工具搜索，不要凭记忆回答
5. 只有在获得足够的 Observation 后，才能给出 Final Answer

请按以下格式工作（重复直到得出答案）：

Thought: 思考当前应该做什么
Action: 工具名称（search/python/file_read/api_call/webshop/parse_pdf/parse_excel/parse_image/browse_webpage）
Action Input: 工具参数（JSON格式）
Observation: [系统返回工具执行结果]

... (重复 Thought/Action/Observation)

Thought: 我现在知道答案了
Final Answer: 最终答案

可用工具及参数格式：
- search: {"query": "搜索关键词", "num_results": 3}
- python: {"code": "print(2**10)"}  （math/json/re/datetime已预导入，禁止写import语句）
- file_read: {"path": "文件路径"}
- api_call: {"url": "API地址", "method": "GET"}
- webshop: {"instruction": "购物需求描述"}
- parse_pdf: {"file_path": "PDF文件路径"}
- parse_excel: {"file_path": "Excel文件路径"}
- parse_image: {"file_path": "图片路径", "question": "分析问题"}
- browse_webpage: {"url": "网页URL", "selector": "CSS选择器（可选）"}

注意：
- Action 必须是上述工具名之一，Action Input 必须是合法 JSON
- 每次只能执行一个工具
- 不要在 Thought 中给出最终答案，必须通过工具验证
"""
```

#### 1.7.3 增加工具调用强制检查

```python
# 在 run_react_task 中增加：如果 LLM 连续 2 步不调用工具，强制要求调用

def run_react_task(query: str, token_budget: int = DEFAULT_TOKEN_BUDGET, max_steps: int = 5) -> dict:
    ...
    no_tool_count = 0  # 连续不调用工具的次数
    
    for step in range(max_steps):
        prompt = f"{conversation}\n\n请继续（Thought/Action/Final Answer）。"
        
        # 如果连续2步没调用工具，在 prompt 中强制要求
        if no_tool_count >= 2:
            prompt += "\n⚠️ 你已经连续多步没有使用工具。请使用工具来完成任务。"
        
        response, tokens = call_llm(prompt, REACT_SYSTEM_PROMPT, role="default")
        token_used += tokens
        
        if "Final Answer:" in response:
            idx = response.index("Final Answer:")
            final_answer = response[idx + len("Final Answer:"):].strip()
            break
        
        action, action_input, observation = _parse_react_response(response)
        
        if action is None:
            no_tool_count += 1
            conversation += f"\n{response}\n"
            continue
        else:
            no_tool_count = 0  # 重置计数
        
        result = execute_tool(action, action_input)
        ...
```

### 1.8 改造 F：Planner Few-Shot 增强（P1）

在 Planner 的系统提示词中添加 few-shot 示例，帮助 LLM 更好地分解复杂任务：

```python
# 在 PLANNER_SYSTEM_PROMPT 中追加 few-shot 示例

PLANNER_SYSTEM_PROMPT = """...

示例1（文件解析任务）：
用户任务: 阅读附件PDF中关于公司财报的数据，计算利润增长率
{
    "complexity": "medium",
    "steps": [
        {"id": 1, "action": "parse_pdf", "description": "解析PDF附件提取财报数据", "args": {"file_path": "附件路径"}},
        {"id": 2, "action": "python", "description": "根据提取的财报数据计算利润增长率", "args": {"code": "print('根据PDF内容计算')"}}
    ]
}

示例2（多步搜索任务）：
用户任务: 找出2024年诺贝尔物理学奖得主中谁也获得过图灵奖
{
    "complexity": "complex",
    "steps": [
        {"id": 1, "action": "search", "description": "搜索2024年诺贝尔物理学奖得主", "args": {"query": "2024 诺贝尔 物理学奖 得主"}},
        {"id": 2, "action": "search", "description": "搜索图灵奖得主列表", "args": {"query": "图灵奖 历届 得主 列表"}, "depends_on": [1]},
        {"id": 3, "action": "python", "description": "交叉比对两个列表找出重叠人物", "args": {"code": "print('交叉比对结果')"}, "depends_on": [1, 2]}
    ]
}

示例3（网页浏览任务）：
用户任务: 访问某个网页并提取其中的表格数据
{
    "complexity": "medium",
    "steps": [
        {"id": 1, "action": "browse_webpage", "description": "浏览目标网页提取内容", "args": {"url": "目标URL", "selector": "table"}},
        {"id": 2, "action": "python", "description": "处理提取的表格数据", "args": {"code": "print('处理表格')"}}
    ]
}
"""
```

### 1.9 预期效果与验证

| 改造项 | 预期官方 L1 准确率 | 验证方法 |
|--------|:------------------:|----------|
| 启发式重构 | 评测有效性恢复 | 禁用启发式后自定义10题准确率应从100%降至60-70% |
| 文件解析工具 | +15-20pp | 官方L1中有文件附件的题目准确率提升 |
| 网页浏览 | +5-10pp | 官方L1中需要浏览网页的题目准确率提升 |
| 官方数据集 | 统计显著性 | ≥30题评测，计算95%置信区间 |
| ReAct修复 | 基线准确 | ReAct在WebShop题目上准确率应从0%提升到30-50% |
| **综合预期** | **55-75%** | 在官方L1上达到75%有挑战性，但+15pp（vs修复后的ReAct）可行 |

---

## 目标二：AgentBench WebShop 成功率 +18pp

### 2.1 问题根因回顾

| 问题 | 当前状态 | 影响 |
|------|----------|------|
| Mock 适配器仅 8 商品 | `webshop.py` 中硬编码 8 个商品 | 与真实 AgentBench（~12000 商品）完全不可比 |
| 单次调用交互 | `webshop_select()` 一次调用返回结果 | 真实 WebShop 需多轮搜索→浏览→选属性→购买 |
| 关键词评分匹配 | `_score_item()` 基于关键词重叠 | 无法处理语义匹配和复杂属性约束 |
| ReAct 不调 webshop | LLM 直接编造商品名 | 基线为 0%，比较无意义 |

### 2.2 改造策略选择

根据实现条件和目标可达性，提供两种方案：

```
方案对比：
┌──────────────────┬───────────────────────────┬───────────────────────────┐
│                   │ 方案 A：真实 WebShop 环境   │ 方案 B：增强 Mock 环境      │
├──────────────────┼───────────────────────────┼───────────────────────────┤
│ 商品库规模         │ ~12,000（真实）             │ 200+（扩充）                │
│ 交互模式           │ 多轮 API 交互               │ 多轮函数调用                │
│ 属性匹配           │ 真实属性约束                 │ 模拟属性约束                │
│ 评测标准           │ AgentBench 官方 reward       │ 属性匹配率                  │
│ 实现难度           │ 高（需 Java 环境+Web服务）   │ 中（纯 Python）             │
│ 可信度             │ 高                          │ 中                          │
│ 预期成功率         │ 35-55%                      │ 40-60%                     │
│ 推荐度             │ ★★★★★（如条件允许）         │ ★★★★☆（备选）              │
└──────────────────┴───────────────────────────┴───────────────────────────┘
```

### 2.3 方案 A：接入真实 WebShop 环境

#### 2.3.1 环境准备

```bash
# WebShop 环境基于 Java + Python，需要先搭建
git clone https://github.com/princeton-nlp/WebShop.git
cd WebShop

# 安装依赖
pip install -r requirements.txt

# 启动 WebShop 服务
# WebShop 使用 Flask 提供 API 接口
python run_env_server.py --port 3000
```

#### 2.3.2 新建文件 `tools/webshop_real.py`

```python
"""
真实 WebShop 环境适配器

对接 Princeton NLP 的 WebShop 环境，支持多轮交互：
  search → browse → select_options → purchase

WebShop API 接口：
  GET  /search_results/<query>/<page>  — 搜索商品
  GET  /item_page/<asin>/<query>/<page> — 浏览商品详情
  POST /checkout                       — 完成购买
"""
from __future__ import annotations

import re
import json
from typing import Any, Dict, List, Optional
import requests

WEBSHOP_BASE_URL = "http://127.0.0.1:3000"
WEBSHOP_SESSION_URL = "http://127.0.0.1:3000"


def webshop_search_real(args: dict) -> str:
    """
    在真实 WebShop 中搜索商品。
    
    Args:
        {"query": "USB-C charger", "page": 1}
    
    Returns:
        搜索结果列表（商品名称、ASIN、价格、评分）
    """
    query = args.get("query", "")
    page = args.get("page", 1)
    
    if not query:
        return "错误：缺少 query 参数"
    
    try:
        # WebShop 搜索接口
        url = f"{WEBSHOP_BASE_URL}/search_results/{query}/{page}"
        resp = requests.get(url, timeout=30)
        
        # 解析搜索结果页面（WebShop 返回 HTML）
        products = _parse_search_results(resp.text)
        
        if not products:
            return "搜索未返回结果，建议更换关键词"
        
        # 格式化输出
        lines = [f"搜索 '{query}' 返回 {len(products)} 个结果:"]
        for i, p in enumerate(products[:10]):  # 最多展示10个
            lines.append(
                f"{i+1}. [{p['asin']}] {p['title'][:60]}... "
                f"price=${p.get('price', 'N/A')} "
                f"rating={p.get('rating', 'N/A')}"
            )
        
        return "\n".join(lines)
        
    except requests.ConnectionError:
        return "错误：无法连接 WebShop 服务，请确保服务已启动 (python run_env_server.py)"
    except Exception as e:
        return f"搜索失败: {type(e).__name__}: {str(e)}"


def webshop_browse_real(args: dict) -> str:
    """
    浏览商品详情页，获取完整信息和可用属性选项。
    
    Args:
        {"asin": "B08XX...", "query": "USB-C charger", "page": 1}
    
    Returns:
        商品详情（描述、属性选项、库存状态）
    """
    asin = args.get("asin", "")
    query = args.get("query", "")
    page = args.get("page", 1)
    
    if not asin:
        return "错误：缺少 asin 参数"
    
    try:
        url = f"{WEBSHOP_BASE_URL}/item_page/{asin}/{query}/{page}"
        resp = requests.get(url, timeout=30)
        
        detail = _parse_product_detail(resp.text)
        
        lines = [
            f"商品: {detail.get('title', 'N/A')}",
            f"ASIN: {asin}",
            f"价格: ${detail.get('price', 'N/A')}",
            f"评分: {detail.get('rating', 'N/A')}",
            f"描述: {detail.get('description', 'N/A')[:200]}...",
            f"可用属性选项:",
        ]
        
        options = detail.get("options", {})
        for attr, values in options.items():
            lines.append(f"  {attr}: {', '.join(values[:5])}")
        
        return "\n".join(lines)
        
    except Exception as e:
        return f"浏览失败: {type(e).__name__}: {str(e)}"


def webshop_purchase_real(args: dict) -> str:
    """
    执行购买操作，选择属性并提交订单。
    
    Args:
        {"asin": "B08XX...", "options": {"color": "black", "size": "64GB"}}
    
    Returns:
        购买结果（成功/失败、匹配的属性数）
    """
    asin = args.get("asin", "")
    options = args.get("options", {})
    
    if not asin:
        return "错误：缺少 asin 参数"
    
    try:
        url = f"{WEBSHOP_BASE_URL}/checkout"
        data = {"asin": asin, "options": options}
        resp = requests.post(url, json=data, timeout=30)
        result = resp.json()
        
        if result.get("success"):
            reward = result.get("reward", 0)
            matched = result.get("matched_attributes", 0)
            total = result.get("total_attributes", 0)
            return (
                f"PURCHASED: {asin}\n"
                f"reward={reward:.2f}\n"
                f"matched_attributes={matched}/{total}\n"
                f"selected_options={json.dumps(options)}"
            )
        else:
            return f"购买失败: {result.get('error', '未知错误')}"
            
    except Exception as e:
        return f"购买失败: {type(e).__name__}: {str(e)}"


def _parse_search_results(html: str) -> List[dict]:
    """从 WebShop 搜索结果 HTML 中解析商品列表"""
    # WebShop 返回的 HTML 包含商品卡片
    # 使用正则或 BeautifulSoup 解析
    products = []
    
    # 简单解析（实际可能需要 BeautifulSoup）
    # 匹配 ASIN
    asin_pattern = re.findall(r'data-asin="([^"]+)"', html)
    title_pattern = re.findall(r'class="product-title"[^>]*>([^<]+)<', html)
    price_pattern = re.findall(r'class="product-price"[^>]*>\$?([\d.]+)<', html)
    rating_pattern = re.findall(r'class="product-rating"[^>]*>([\d.]+)<', html)
    
    for i in range(len(asin_pattern)):
        products.append({
            "asin": asin_pattern[i],
            "title": title_pattern[i].strip() if i < len(title_pattern) else "N/A",
            "price": float(price_pattern[i]) if i < len(price_pattern) else None,
            "rating": float(rating_pattern[i]) if i < len(rating_pattern) else None,
        })
    
    return products


def _parse_product_detail(html: str) -> dict:
    """从商品详情页 HTML 中解析详细信息"""
    # 实际实现可能需要 BeautifulSoup
    detail = {
        "title": "",
        "price": None,
        "rating": None,
        "description": "",
        "options": {},
    }
    
    title_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if title_match:
        detail["title"] = title_match.group(1).strip()
    
    price_match = re.search(r'\$([\d.]+)', html)
    if price_match:
        detail["price"] = float(price_match.group(1))
    
    # 解析属性选项
    options_match = re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', html, re.DOTALL)
    for name, options_html in options_match:
        values = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', options_html)
        detail["options"][name] = [v[1].strip() for v in values if v[0]]
    
    return detail
```

#### 2.3.3 设计 WebShop 多轮交互子图

**文件：`graph/webshop_subgraph.py`**

```python
"""
WebShop 多轮交互子图

将 WebShop 任务的执行分解为多轮交互：
  Planner 分解购物需求
  → Executor 搜索商品
  → Executor 浏览候选商品详情
  → Executor 选择属性
  → Executor 执行购买
  → Critic 验证购买正确性
  → Synthesizer 输出结果
"""
from langgraph.graph import StateGraph, END
from typing import TypedDict, Any, Dict, List


class WebShopState(TypedDict):
    instruction: str          # 用户购物需求
    search_query: str         # 搜索关键词
    search_results: List[dict] # 搜索结果
    selected_asin: str        # 选中的商品 ASIN
    product_detail: dict      # 商品详情
    selected_options: dict    # 选中的属性选项
    purchase_result: dict     # 购买结果
    reward: float             # 匹配奖励
    logs: List[str]


def webshop_search_node(state: dict) -> dict:
    """搜索商品节点：根据用户需求生成搜索关键词并搜索"""
    instruction = state["instruction"]
    
    # 使用 LLM 从购物需求中提取搜索关键词
    from agents.llm_utils import call_llm
    prompt = f"""
    用户购物需求: {instruction}
    
    请提取适合电商搜索的关键词（英文，空格分隔）。
    只返回关键词，不要其他内容。
    """
    search_query, tokens = call_llm(prompt, "你是搜索关键词提取专家", role="executor")
    search_query = search_query.strip()
    
    # 调用真实 WebShop 搜索
    from tools.webshop_real import webshop_search_real
    result = webshop_search_real({"query": search_query, "page": 1})
    
    logs = state.get("logs", [])
    logs.append(f"[WebShop-Search] 搜索 '{search_query}': {result[:100]}...")
    
    return {
        "search_query": search_query,
        "search_results": result,
        "logs": logs,
    }


def webshop_browse_node(state: dict) -> dict:
    """浏览商品详情节点：选择搜索结果中最相关的商品并浏览详情"""
    from agents.llm_utils import call_llm
    import re
    
    search_results = state.get("search_results", "")
    instruction = state["instruction"]
    
    # 使用 LLM 从搜索结果中选择最相关的商品 ASIN
    prompt = f"""
    用户购物需求: {instruction}
    
    搜索结果:
    {search_results}
    
    请选择最符合用户需求的商品。只返回该商品的 ASIN（方括号中的字母数字代码）。
    """
    response, tokens = call_llm(prompt, "你是商品选择专家", role="executor")
    
    # 提取 ASIN
    asin_match = re.search(r'[A-Z0-9]{10}', response)
    selected_asin = asin_match.group(0) if asin_match else ""
    
    if not selected_asin:
        logs = state.get("logs", [])
        logs.append(f"[WebShop-Browse] 无法从LLM响应中提取ASIN: {response[:50]}")
        return {"selected_asin": "", "logs": logs}
    
    # 浏览商品详情
    from tools.webshop_real import webshop_browse_real
    detail = webshop_browse_real({
        "asin": selected_asin,
        "query": state.get("search_query", ""),
    })
    
    logs = state.get("logs", [])
    logs.append(f"[WebShop-Browse] 浏览商品 {selected_asin}: {detail[:100]}...")
    
    return {
        "selected_asin": selected_asin,
        "product_detail": detail,
        "logs": logs,
    }


def webshop_select_options_node(state: dict) -> dict:
    """选择属性节点：根据用户需求和商品详情选择最合适的属性"""
    from agents.llm_utils import call_llm
    import json
    
    instruction = state["instruction"]
    product_detail = state.get("product_detail", "")
    
    prompt = f"""
    用户购物需求: {instruction}
    
    商品详情:
    {product_detail}
    
    请根据用户需求选择最合适的商品属性。返回 JSON 格式，key 是属性名，value 是选择的值。
    例如: {{"color": "black", "size": "64GB"}}
    """
    response, tokens = call_llm(prompt, "你是商品属性选择专家", role="executor")
    
    try:
        options = json.loads(response)
    except json.JSONDecodeError:
        # 尝试从代码块中提取
        if "```json" in response:
            start = response.index("```json") + 7
            end = response.index("```", start)
            options = json.loads(response[start:end].strip())
        else:
            options = {}
    
    logs = state.get("logs", [])
    logs.append(f"[WebShop-SelectOptions] 选择属性: {json.dumps(options)}")
    
    return {"selected_options": options, "logs": logs}


def webshop_purchase_node(state: dict) -> dict:
    """执行购买节点"""
    from tools.webshop_real import webshop_purchase_real
    
    asin = state.get("selected_asin", "")
    options = state.get("selected_options", {})
    
    result = webshop_purchase_real({"asin": asin, "options": options})
    
    # 解析 reward
    import re
    reward_match = re.search(r'reward=([\d.]+)', result)
    reward = float(reward_match.group(1)) if reward_match else 0.0
    
    logs = state.get("logs", [])
    logs.append(f"[WebShop-Purchase] 购买结果: reward={reward:.2f}")
    
    return {"purchase_result": result, "reward": reward, "logs": logs}


def build_webshop_subgraph():
    """构建 WebShop 多轮交互子图"""
    graph = StateGraph(WebShopState)
    
    graph.add_node("search", webshop_search_node)
    graph.add_node("browse", webshop_browse_node)
    graph.add_node("select_options", webshop_select_options_node)
    graph.add_node("purchase", webshop_purchase_node)
    
    graph.add_edge("search", "browse")
    graph.add_edge("browse", "select_options")
    graph.add_edge("select_options", "purchase")
    graph.add_edge("purchase", END)
    
    graph.set_entry_point("search")
    return graph.compile()
```

#### 2.3.4 新建 WebShop 官方评测

**文件：`benchmarks/webshop_official_eval.py`**

```python
"""
AgentBench WebShop 官方评测

使用 WebShop 环境的标准评测协议：
- 评测 reward = matched_attributes / total_attributes
- 成功标准: reward ≥ 0.5（至少匹配一半属性）
- 多智能体 vs ReAct 对比
"""
from __future__ import annotations

from graph.webshop_subgraph import build_webshop_subgraph
from benchmarks.react_baseline import run_react_task


# WebShop 标准测试集（从 WebShop 仓库加载）
WEBSHOP_TEST_INSTRUCTIONS = [
    # AgentBench WebShop 使用的标准指令
    # 实际使用时从 WebShop/data/test.json 加载
    "I need a USB-C charger with at least 65W power output, preferably with GaN technology",
    "I'm looking for a green tea, preferably organic and caffeinated, in tea bags",
    "I want a wireless mouse, silent clicks, ergonomic design, under $30",
    # ... 更多指令从 WebShop 仓库加载
]


def evaluate_webshop_multi_agent(instructions: list = None, num_samples: int = 30) -> dict:
    """评测多智能体框架在 WebShop 上的表现"""
    if instructions is None:
        instructions = WEBSHOP_TEST_INSTRUCTIONS[:num_samples]
    
    results = []
    total_reward = 0
    success_count = 0  # reward >= 0.5
    
    for i, instruction in enumerate(instructions):
        print(f"[{i+1}/{len(instructions)}] WebShop 多智能体: {instruction[:60]}...", flush=True)
        
        try:
            subgraph = build_webshop_subgraph()
            state = subgraph.invoke({"instruction": instruction, "logs": []})
            reward = state.get("reward", 0.0)
        except Exception as e:
            reward = 0.0
            print(f"  → 执行失败: {e}")
        
        total_reward += reward
        if reward >= 0.5:
            success_count += 1
        
        results.append({
            "instruction": instruction[:100],
            "reward": round(reward, 4),
            "success": reward >= 0.5,
        })
        
        print(f"  → reward={reward:.2f}, success={reward >= 0.5}", flush=True)
    
    avg_reward = total_reward / len(instructions) if instructions else 0
    success_rate = success_count / len(instructions) if instructions else 0
    
    return {
        "benchmark": "webshop_official",
        "agent_type": "multi_agent",
        "total_samples": len(instructions),
        "avg_reward": round(avg_reward, 4),
        "success_rate": round(success_rate, 4),
        "details": results,
    }


def evaluate_webshop_react(instructions: list = None, num_samples: int = 30) -> dict:
    """评测 ReAct 基线在 WebShop 上的表现"""
    if instructions is None:
        instructions = WEBSHOP_TEST_INSTRUCTIONS[:num_samples]
    
    results = []
    total_reward = 0
    success_count = 0
    
    for i, instruction in enumerate(instructions):
        print(f"[{i+1}/{len(instructions)}] WebShop ReAct: {instruction[:60]}...", flush=True)
        
        # ReAct 使用增强后的 prompt（强制使用 webshop 工具）
        state = run_react_task(instruction, max_steps=5)
        final_answer = state.get("final_answer", "")
        
        # 解析 ReAct 输出中的购买信息
        # 需要 ReAct 调用 webshop_real 工具并返回 reward
        reward = _parse_react_reward(final_answer)
        
        total_reward += reward
        if reward >= 0.5:
            success_count += 1
        
        results.append({
            "instruction": instruction[:100],
            "reward": round(reward, 4),
            "success": reward >= 0.5,
        })
    
    avg_reward = total_reward / len(instructions) if instructions else 0
    success_rate = success_count / len(instructions) if instructions else 0
    
    return {
        "benchmark": "webshop_official",
        "agent_type": "react_baseline",
        "total_samples": len(instructions),
        "avg_reward": round(avg_reward, 4),
        "success_rate": round(success_rate, 4),
        "details": results,
    }


def _parse_react_reward(answer: str) -> float:
    """从 ReAct 输出中解析 reward"""
    import re
    match = re.search(r'reward=([\d.]+)', answer)
    return float(match.group(1)) if match else 0.0
```

### 2.4 方案 B：增强 Mock 环境（备选）

如果无法搭建真实 WebShop 环境，可以构建一个更逼真的 Mock：

#### 2.4.1 扩充商品库到 200+

```python
# tools/webshop_enhanced.py

import json
import random

def _generate_catalog(size: int = 200) -> list:
    """生成扩充的商品目录"""
    categories = {
        "tea": ["green", "black", "oolong", "white", "herbal", "matcha"],
        "electronics": ["charger", "mouse", "keyboard", "headphone", "speaker"],
        "kitchen": ["bottle", "thermos", "knife", "cutting_board"],
        "sports": ["bottle", "mat", "dumbbell", "rope"],
    }
    
    brands = ["Anker", "Logitech", "Razer", "Twinings", "Lipton", "YETI", "HydroFlask"]
    attributes_pool = {
        "color": ["black", "white", "blue", "red", "green", "silver"],
        "size": ["small", "medium", "large"],
        "material": ["plastic", "metal", "stainless steel", "glass"],
    }
    
    catalog = []
    for i in range(size):
        cat = random.choice(list(categories.keys()))
        sub = random.choice(categories[cat])
        brand = random.choice(brands)
        
        attrs = {
            "color": random.choice(attributes_pool["color"]),
            "size": random.choice(attributes_pool["size"]),
            "material": random.choice(attributes_pool["material"]),
        }
        
        catalog.append({
            "id": f"ws_{cat}_{i:04d}",
            "name": f"{brand} {sub.title()} {cat.title()}",
            "category": cat,
            "price": round(random.uniform(10, 80), 2),
            "rating": round(random.uniform(3.5, 5.0), 1),
            "attributes": attrs,
        })
    
    return catalog
```

#### 2.4.2 实现多轮交互

```python
def webshop_search_multi(args: dict) -> str:
    """多轮搜索：支持分页、属性过滤"""
    query = args.get("query", "")
    page = args.get("page", 1)
    filters = args.get("filters", {})
    catalog = _generate_catalog()
    
    # 语义匹配（使用简单的关键词+属性过滤）
    results = []
    for item in catalog:
        # 关键词匹配
        if query.lower() not in item["name"].lower():
            continue
        # 属性过滤
        if filters:
            match = all(
                item["attributes"].get(k) == v
                for k, v in filters.items()
            )
            if not match:
                continue
        results.append(item)
    
    # 分页
    start = (page - 1) * 10
    page_results = results[start:start + 10]
    
    return json.dumps({
        "total": len(results),
        "page": page,
        "products": page_results,
    }, ensure_ascii=False)
```

### 2.5 预期效果

| 改造项 | 预期 WebShop 成功率 | vs ReAct 优势 |
|--------|:-------------------:|:-------------:|
| 真实 WebShop + 多轮交互 | 35-55% | +10-20pp |
| 增强 Mock + 多轮交互 | 40-60% | +15-25pp |
| ReAct 基线（修复后） | 25-40% | — |
| **+18pp 目标可达性** | ✅ 可达 | 需多智能体多轮交互优势 |

---

## 目标三：Token 预算感知调度降本 30%

### 3.1 问题根因回顾

| 问题 | 当前状态 | 影响 |
|------|----------|------|
| 纯预算调度仅 4.5% | 消融实验预算 800 太小，单次 LLM 调用就耗尽 | 预算调度机制无法展示真实价值 |
| 38.8% 降本来自启发式 | 8/10 题用 3-6 Token，非预算调度功劳 | 降本归因错误 |
| 知识检索类 +115% | 四角色各调 LLM = 4 次调用，ReAct 仅 2 次 | 架构性成本膨胀 |
| 消融实验设计缺陷 | 仅 3 道计算题、预算 800 不合理 | 无法得出有效结论 |

### 3.2 改造策略总览

```
策略矩阵（降本贡献预估）：
┌───────────────────────────────┬────────────┬──────────┬──────────┐
│ 改造项                         │ 预期降本    │ 实现难度  │ 优先级    │
├───────────────────────────────┼────────────┼──────────┼──────────┤
│ A. 模型路由（大小模型混合）      │ -20-30%    │ 低        │ P0       │
│ B. Planner+Executor 合并调用    │ -10-15%    │ 中        │ P0       │
│ C. Critic 规则验证扩展          │ -5-10%     │ 中        │ P1       │
│ D. 上下文压缩                   │ -5-8%      │ 低        │ P1       │
│ E. 修正消融实验设计             │ 评测有效性  │ 低        │ P0       │
│ F. Prompt 精简优化              │ -5-10%     │ 低        │ P2       │
└───────────────────────────────┴────────────┴──────────┴──────────┘
综合预期：-35-55%（叠加效果有递减，保守估计 -30%+ 可达）
```

### 3.3 改造 A：模型路由 — 大小模型混合调度（P0）

#### 3.3.1 核心思路

当前所有角色都使用同一个模型（如 `glm-4.7-flash`）。不同角色的任务复杂度差异很大：
- Planner 规划：需要推理能力 → 可以用大模型
- Executor 参数生成：简单任务只需小模型
- Critic 规则验证后 fallback 的 LLM 评分：可以用小模型
- Synthesizer 简单综合：可以用小模型

通过模型路由，简单步骤使用低成本模型，可在不损失质量的前提下降低 20-30% 成本。

#### 3.3.2 具体代码变更

**文件：`config.py`**

```python
# ============ 模型路由配置 ============
# 不同复杂度任务使用的模型
# 小模型成本约为大模型的 1/5 ~ 1/10
MODEL_ROUTING = {
    "large": {
        "model_name": _yaml_get("model", "routing", "large", "model_name", 
                                default=os.getenv("LLM_MODEL_LARGE", "glm-4-plus")),
        "max_tokens": _yaml_get("model", "routing", "large", "max_tokens", default=2048),
    },
    "small": {
        "model_name": _yaml_get("model", "routing", "small", "model_name",
                                default=os.getenv("LLM_MODEL_SMALL", "glm-4-flash")),
        "max_tokens": _yaml_get("model", "routing", "small", "max_tokens", default=1024),
    },
}

# 角色到模型的映射策略
# "auto" 表示根据任务复杂度自动选择
ROLE_MODEL_STRATEGY = {
    "planner": "auto",       # 规划：根据复杂度选择
    "executor": "small",     # 执行：默认用小模型
    "critic": "small",       # 评审：默认用小模型
    "synthesizer": "auto",   # 综合：根据复杂度选择
}
```

**文件：`agents/llm_utils.py`**

```python
# 修改 get_llm 和 call_llm 支持模型路由

from config import (
    LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_MAX_TOKENS,
    MODEL_ROUTING, ROLE_MODEL_STRATEGY,
)

# 按角色+模型大小缓存 LLM 实例
_llm_instances: dict = {}


def _get_model_name(role: str, complexity: str = "medium") -> str:
    """根据角色和复杂度选择模型"""
    strategy = ROLE_MODEL_STRATEGY.get(role, "auto")
    
    if strategy == "auto":
        # simple → small, medium/complex → large
        if complexity == "simple":
            return MODEL_ROUTING["small"]["model_name"]
        else:
            return MODEL_ROUTING["large"]["model_name"]
    elif strategy == "small":
        return MODEL_ROUTING["small"]["model_name"]
    elif strategy == "large":
        return MODEL_ROUTING["large"]["model_name"]
    else:
        return LLM_MODEL  # 默认模型


def get_llm(role: str = "default", complexity: str = "medium") -> ChatOpenAI:
    """获取指定角色的 LLM 实例（支持模型路由）"""
    temp = ROLE_TEMPERATURES.get(role, ROLE_TEMPERATURES["default"])
    model_name = _get_model_name(role, complexity)
    max_tokens = (MODEL_ROUTING["small"]["max_tokens"] 
                  if model_name == MODEL_ROUTING["small"]["model_name"]
                  else MODEL_ROUTING["large"]["max_tokens"])
    
    cache_key = f"{role}_{model_name}"
    if cache_key not in _llm_instances:
        _llm_instances[cache_key] = ChatOpenAI(
            api_key=LLM_API_KEY,
            base_url=LLM_BASE_URL,
            model=model_name,
            temperature=temp,
            max_tokens=max_tokens,
        )
    return _llm_instances[cache_key]


def call_llm(prompt: str, system_prompt: str = "", role: str = "default", 
             complexity: str = "medium") -> tuple:
    """调用 LLM（支持模型路由）"""
    ...
    llm = get_llm(role, complexity)
    ...
```

**文件：`agents/planner.py`**

```python
# 在调用 LLM 时传入 complexity

def planner_node(state: dict) -> dict:
    ...
    # 从启发式或上一轮获取复杂度
    complexity = state.get("complexity", "medium")
    
    # LLM 规划时传入复杂度
    plan_data, token_consumed = call_llm_json(
        prompt, PLANNER_SYSTEM_PROMPT, 
        role="planner", 
        complexity=complexity
    )
    
    # 从返回的 plan 中获取复杂度
    complexity = plan_data.get("complexity", "medium")
    ...
```

**文件：`agents/executor.py`、`agents/critic.py`、`agents/synthesizer.py`**

在所有 `call_llm` / `call_llm_json` 调用中追加 `complexity` 参数：

```python
# executor.py
response, tokens = call_llm(prompt, system_prompt, role="executor", 
                           complexity=state.get("complexity", "medium"))

# critic.py
score, token_consumed = call_llm_json(prompt, CRITIC_SYSTEM_PROMPT, role="critic",
                                      complexity=state.get("complexity", "medium"))

# synthesizer.py
response, tokens = call_llm(prompt, system_prompt, role="synthesizer",
                           complexity=state.get("complexity", "medium"))
```

### 3.4 改造 B：Planner+Executor 合并调用（P0）

#### 3.4.1 核心思路

对于 `simple` 复杂度任务，当前流程是：
```
Planner 调 LLM 生成计划 → Executor 调 LLM 生成参数 → 执行工具
= 2 次 LLM 调用
```

合并后：
```
Planner-Executor 合并调 LLM 一步生成计划+参数 → 执行工具
= 1 次 LLM 调用
```

减少 50% 的 Planner Token 消耗。

#### 3.4.2 具体代码变更

**文件：`graph/builder.py`**

```python
from agents.planner import planner_node, planner_executor_merged_node

def build_graph(token_budget: int = DEFAULT_TOKEN_BUDGET):
    graph = StateGraph(AgentState)
    
    # 新增合并节点
    graph.add_node("planner", planner_node)
    graph.add_node("planner_executor_merged", planner_executor_merged_node)  # 新增
    graph.add_node("executor", executor_node)
    ...
    
    # 入口路由：根据是否有 API Key 决定走合并节点还是分开
    graph.add_conditional_edges(
        "__start__",
        route_entry,
        {
            "merged": "planner_executor_merged",
            "split": "planner",
        }
    )
    
    # 合并节点直接到 Executor 执行（跳过 Planner→Executor 的 LLM 调用）
    graph.add_edge("planner_executor_merged", "executor")
    ...
    return graph.compile()


def route_entry(state: dict) -> str:
    """入口路由：决定是否合并 Planner+Executor"""
    # 无 API Key 时走启发式（原逻辑）
    if not LLM_API_KEY:
        return "split"
    
    # 有 API Key 时，对 simple 任务走合并路径
    # 注意：首次调用时还不知道复杂度，可以先用合并路径尝试
    # 如果合并路径发现任务是 medium/complex，可以回退到分离路径
    return "merged"
```

**文件：`agents/planner.py`**

```python
PLANNER_EXECUTOR_MERGED_PROMPT = """你是一个任务规划和执行专家，需要一步完成规划+参数生成。

用户任务: {query}

请直接生成完整的执行计划（包含步骤和工具参数），不需要分步规划。

输出格式（严格JSON）：
```json
{
    "complexity": "simple|medium|complex",
    "steps": [
        {
            "id": 1,
            "action": "search|python|file_read|api_call|webshop|parse_pdf|parse_excel|parse_image|browse_webpage",
            "description": "步骤描述",
            "args": {"参数名": "参数值"},  // 必须填写完整的工具参数
            "status": "pending",
            "result": null,
            "retry_count": 0,
            "risk": "low|medium|high",
            "depends_on": []
        }
    ]
}
```

规则：
- simple 任务只需 1 步，medium 2-3 步，complex 4-5 步
- args 必须包含该工具需要的完整参数
- 如果是计算任务，直接生成可执行的 Python 代码
"""


def planner_executor_merged_node(state: dict) -> dict:
    """
    合并 Planner+Executor 节点
    
    一次 LLM 调用同时完成规划+参数生成，减少一次 LLM 调用。
    适用于 simple/medium 任务。
    """
    query = state["query"]
    reflection = state.get("reflection", "")
    
    prompt = PLANNER_EXECUTOR_MERGED_PROMPT.format(query=query)
    if reflection:
        prompt += f"\n\n上一轮反思:\n{reflection}"
    
    plan_data, token_consumed = call_llm_json(
        prompt, "你是任务规划和执行专家", 
        role="planner",
        complexity="medium"  # 合并调用用 medium 级别模型
    )
    
    steps = plan_data.get("steps", [])
    complexity = plan_data.get("complexity", "medium")
    
    # 如果 LLM 判断为 complex，回退到分离路径（标记需要重新规划）
    if complexity == "complex" and len(steps) > 3:
        # 存入 plan 但标记需要 Critic 评审
        logs = state.get("logs", [])
        logs.append("[Planner-Executor-Merged] complex 任务，已生成完整计划+参数")
    else:
        logs = state.get("logs", [])
        logs.append(f"[Planner-Executor-Merged] 合并调用完成，{len(steps)} 步 (节省 1 次 LLM 调用)")
    
    # 规范化步骤
    normalized_steps = []
    allowed_actions = {"search", "python", "file_read", "api_call", "webshop",
                       "parse_pdf", "parse_excel", "parse_image", "browse_webpage"}
    for idx, step in enumerate(steps, start=1):
        if step.get("action") not in allowed_actions:
            continue
        step.setdefault("id", idx)
        step.setdefault("status", "pending")
        step.setdefault("result", None)
        step.setdefault("retry_count", 0)
        step.setdefault("risk", "medium")
        step.setdefault("depends_on", [])
        step.setdefault("args", {})
        normalized_steps.append(step)
    
    token_used, role_token_used, budget_events = record_token_usage(state, "planner", token_consumed)
    
    # 合并节点的 Executor 部分消耗 0 token（参数已生成）
    # 标记 executor 角色也消耗了 0（因为跳过了 Executor 的 LLM 调用）
    saved = estimate_tokens(prompt)
    
    return {
        "plan": normalized_steps,
        "complexity": complexity,
        "current_step_idx": 0,
        "token_used": token_used,
        "role_token_used": role_token_used,
        "budget_events": budget_events,
        "logs": logs,
        "iteration": state.get("iteration", 0),
    }
```

### 3.5 改造 C：Critic 规则验证扩展（P1）

#### 3.5.1 核心思路

当前 Critic 的 `_rule_evaluate()` 仅覆盖 python/search/webshop 三种 action 的基本检查。扩展规则验证覆盖范围可以减少 LLM 评分调用，每次 LLM 评分约消耗 300-500 Token。

#### 3.5.2 具体代码变更

**文件：`agents/critic.py`**

```python
def _rule_evaluate(result: dict) -> dict:
    """扩展后的规则验证（不消耗 Token）"""
    text = result.get("result", "")
    action = result.get("action", "")
    success = result.get("success", False)
    description = result.get("description", "")
    
    # 执行失败
    if not success or "错误" in text[:20] or "执行错误" in text[:20]:
        return _score(2, 2, 2, "执行失败", result.get("step_id", 0))
    
    # Python 结果验证（保持原有逻辑，新增数值范围验证）
    if action == "python":
        if not text.strip() or text.strip() == "输出:" or text.strip() == "无输出":
            return _score(2, 2, 2, "无输出", result.get("step_id", 0))
        if "Traceback" in text or "SyntaxError" in text or "NameError" in text:
            return _score(2, 2, 2, "代码执行报错", result.get("step_id", 0))
        
        # 新增：数值合理性验证
        nums = re.findall(r'-?\d+\.?\d*', text)
        if nums:
            for num_str in nums:
                num = float(num_str)
                # 极大/极小值异常检测
                if abs(num) > 1e15:
                    return _score(3, 2, 3, f"结果数值异常大: {num}", result.get("step_id", 0))
                if num == 0 and "计算" in description:
                    return _score(3, 3, 3, "计算结果为0，可能需要验证", result.get("step_id", 0))
        
        return _score(5, 5, 5, "Python执行成功", result.get("step_id", 0))
    
    # 搜索结果验证（保持原有，新增相关性检查）
    if action == "search":
        if "[模拟搜索] 未找到" in text:
            return _score(2, 3, 2, "搜索无结果", result.get("step_id", 0))
        if len(text) < 30:
            return _score(3, 3, 2, "搜索结果过少", result.get("step_id", 0))
        
        # 新增：检查搜索结果是否与问题描述相关
        desc_keywords = [w for w in re.split(r'[\s,，。]+', description) if len(w) > 2]
        if desc_keywords:
            matched = sum(1 for kw in desc_keywords if kw.lower() in text.lower())
            relevance = matched / len(desc_keywords)
            if relevance < 0.3:
                return _score(3, 3, 3, f"搜索结果与问题描述相关性低 ({relevance:.0%})", result.get("step_id", 0))
        
        return _score(4, 4, 4, "搜索结果有效", result.get("step_id", 0))
    
    # WebShop 结果验证（保持原有）
    if action == "webshop":
        if "NO_MATCH" in text or "错误" in text:
            return _score(2, 2, 2, "未找到匹配商品", result.get("step_id", 0))
        if "SELECTED:" in text:
            return _score(5, 5, 5, "商品选择成功", result.get("step_id", 0))
        return _score(3, 3, 2, "结果格式异常", result.get("step_id", 0))
    
    # 新增：文件解析结果验证
    if action in ("parse_pdf", "parse_excel", "parse_csv"):
        if not text.strip():
            return _score(2, 2, 2, "文件解析结果为空", result.get("step_id", 0))
        if "错误" in text[:20]:
            return _score(2, 2, 2, "文件解析失败", result.get("step_id", 0))
        if len(text) < 50:
            return _score(3, 3, 2, "文件解析结果过少", result.get("step_id", 0))
        return _score(4, 4, 4, "文件解析成功", result.get("step_id", 0))
    
    # 新增：图片解析结果验证
    if action == "parse_image":
        if not text.strip():
            return _score(2, 2, 2, "图片解析结果为空", result.get("step_id", 0))
        if "错误" in text[:20] or "无API" in text:
            return _score(2, 2, 2, "图片解析失败", result.get("step_id", 0))
        return _score(4, 4, 4, "图片解析成功", result.get("step_id", 0))
    
    # 新增：网页浏览结果验证
    if action == "browse_webpage":
        if not text.strip():
            return _score(2, 2, 2, "网页内容为空", result.get("step_id", 0))
        if "错误" in text[:20] or "失败" in text[:20]:
            return _score(2, 2, 2, "网页浏览失败", result.get("step_id", 0))
        if len(text) < 100:
            return _score(3, 3, 2, "网页内容过少", result.get("step_id", 0))
        return _score(4, 4, 4, "网页浏览成功", result.get("step_id", 0))
    
    return None  # 回退到 LLM


def _score(acc, cons, comp, feedback, step_id):
    """辅助函数：生成评分字典"""
    return {
        "accuracy": acc, "consistency": cons, "completeness": comp,
        "overall": round((acc + cons + comp) / 3, 1),
        "feedback": feedback, "step_id": step_id,
    }
```

### 3.6 改造 D：上下文压缩（P1）

#### 3.6.1 核心思路

当前各角色调用 LLM 时传递完整的上下文（问题+所有历史结果），随着步骤增多，prompt 越来越长。通过传递摘要而非全文，可减少 5-8% Token。

#### 3.6.2 具体代码变更

**文件：`agents/llm_utils.py`**

```python
def compress_context(text: str, max_chars: int = 2000) -> str:
    """
    压缩上下文文本，保留关键信息。
    
    策略：
    1. 如果文本未超限，直接返回
    2. 截取前 max_chars 字符 + 末尾摘要
    3. 移除重复行
    """
    if len(text) <= max_chars:
        return text
    
    # 去重
    lines = text.split("\n")
    seen = set()
    unique_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            unique_lines.append(line)
    
    # 截取
    result = "\n".join(unique_lines)
    if len(result) <= max_chars:
        return result
    
    # 前 max_chars*0.7 + 后 max_chars*0.3
    front_size = int(max_chars * 0.7)
    back_size = max_chars - front_size - 20
    return result[:front_size] + "\n...[已压缩]...\n" + result[-back_size:]
```

**在各角色中使用压缩**

```python
# agents/critic.py — 传递给 LLM 的 prompt 使用压缩
prompt = f"""
步骤描述: {compress_context(latest_result.get('description', ''), 500)}
执行结果: {compress_context(latest_result.get('result', ''), 1500)}
步骤ID: {step_id}

请评估以上执行结果的质量。
"""

# agents/synthesizer.py — 综合时压缩历史结果
def synthesizer_node(state: dict) -> dict:
    ...
    results = state.get("results", [])
    # 压缩每个结果到 500 字符
    compressed_results = []
    for r in results:
        compressed_results.append({
            "step_id": r.get("step_id"),
            "action": r.get("action"),
            "result": compress_context(str(r.get("result", "")), 500),
        })
    ...
```

### 3.7 改造 E：修正消融实验设计（P0）

#### 3.7.1 当前问题

```python
# 当前 benchmarks/cost_eval.py 的问题：
BUDGETED_TOKEN_BUDGET = 800      # 问题1：800太小，单次LLM调用就300-500
COST_ABLATION_INDICES = [15, 20, 25]  # 问题2：仅3道计算题，无多样性
# 禁用启发式后全走LLM，但预算太小导致一开始就触发紧急模式
```

#### 3.7.2 修正后的消融实验

**文件：`benchmarks/cost_eval.py`（重写）**

```python
"""
Token budget scheduling cost ablation (修正版)

修正点：
1. 紧预算从 800 改为 3000（合理范围，能触发 70%/85% 降级但不至于一开始就紧急）
2. 样本从 3 道计算题扩展到 9 道（计算+知识检索+多步推理）
3. 启发式策略：分三组评测（启发式开/关 × 紧/宽预算）
4. 新增模型路由对比（大小模型 vs 单一模型）
"""
from __future__ import annotations
from typing import Optional
from benchmarks.gaia_eval import GAIA_L1_SAMPLES, save_results
from graph.builder import run_task

# 修正1：合理预算
BUDGETED_TOKEN_BUDGET = 3000   # 能触发70%/85%降级，不至于一开始就95%
UNBUDGETED_TOKEN_BUDGET = 50000

# 修正2：多样化任务集
COST_ABLATION_INDICES = [
    # 计算类（3题）
    15, 20, 25,  # 2^30-2^20, 3^18-3^12, 5^12-5^8
    # 知识检索类（3题）
    0, 3, 11,    # Python发布年份, 诺贝尔图灵奖, Hinton出生年份
    # 多步推理类（3题）
    7, 8, 9,     # 巴黎vs东京奥运, 赤道周长, 诺贝尔化学奖
]


def evaluate_cost_ablation(
    num_samples: Optional[int] = 9,
    token_budget: int = 50000,
) -> dict:
    """
    修正后的成本消融评测
    
    四组对比：
    A. 启发式开 + 紧预算（当前默认配置）
    B. 启发式关 + 紧预算（纯预算调度效果）
    C. 启发式关 + 宽预算（无降级基线）
    D. 启发式开 + 宽预算（启发式单独效果）
    
    降本计算：
    - 预算调度降本 = (C - B) / C
    - 启发式降本 = (C - D) / C
    - 综合降本 = (C - A) / C
    - 模型路由降本 = 单独对比（在 B 组内用大小模型 vs 单一模型）
    """
    if num_samples and num_samples <= len(COST_ABLATION_INDICES):
        indices = COST_ABLATION_INDICES[:num_samples]
        samples = [GAIA_L1_SAMPLES[i] for i in indices]
    else:
        samples = GAIA_L1_SAMPLES[:num_samples] if num_samples else GAIA_L1_SAMPLES
    
    groups = {
        "A_heuristic_on_budgeted": {"heuristics": True, "budget": BUDGETED_TOKEN_BUDGET},
        "B_heuristic_off_budgeted": {"heuristics": False, "budget": BUDGETED_TOKEN_BUDGET},
        "C_heuristic_off_unbudgeted": {"heuristics": False, "budget": UNBUDGETED_TOKEN_BUDGET},
        "D_heuristic_on_unbudgeted": {"heuristics": True, "budget": UNBUDGETED_TOKEN_BUDGET},
    }
    
    group_results = {}
    
    for group_name, config in groups.items():
        print(f"\n=== 评测组: {group_name} ===", flush=True)
        details = []
        total_tokens = 0
        
        for sample in samples:
            task_id = sample["task_id"]
            question = sample["question"]
            
            state = run_task(
                question,
                token_budget=config["budget"],
                use_heuristics=config["heuristics"],
            )
            tokens = state.get("token_used", 0)
            total_tokens += tokens
            decisions = state.get("scheduler_decisions", [])
            
            details.append({
                "task_id": task_id,
                "tokens": tokens,
                "scheduler_decisions": [
                    f"{d.get('actor','?')}:{d.get('decision','?')}" for d in decisions
                ],
            })
            
            print(f"  {task_id}: {tokens} tokens", flush=True)
        
        avg_tokens = total_tokens / len(samples) if samples else 0
        group_results[group_name] = {
            "avg_tokens": round(avg_tokens),
            "total_tokens": total_tokens,
            "details": details,
        }
    
    # 计算各维度降本
    base = group_results["C_heuristic_off_unbudgeted"]["avg_tokens"]
    
    result = {
        "benchmark": "token_budget_ablation_fixed",
        "mode": "4-group comparison",
        "groups": group_results,
        "savings_analysis": {
            "budget_scheduling_only": round((base - group_results["B_heuristic_off_budgeted"]["avg_tokens"]) / base * 100, 1) if base else 0,
            "heuristic_only": round((base - group_results["D_heuristic_on_unbudgeted"]["avg_tokens"]) / base * 100, 1) if base else 0,
            "combined": round((base - group_results["A_heuristic_on_budgeted"]["avg_tokens"]) / base * 100, 1) if base else 0,
        },
        "budget_config": {
            "budgeted": BUDGETED_TOKEN_BUDGET,
            "unbudgeted": UNBUDGETED_TOKEN_BUDGET,
        },
        "sample_indices": COST_ABLATION_INDICES[:num_samples] if num_samples else "all",
    }
    
    save_results(result, "cost_ablation_fixed.json")
    return result
```

### 3.8 改造 F：Prompt 精简优化（P2）

当前系统提示词较为冗长（Planner 约 800 字符，Critic 约 500 字符）。精简 prompt 可以直接减少每次 LLM 调用的 Token 消耗。

**优化原则**：
1. 移除示例中的冗余注释
2. 将规则描述压缩为要点
3. 使用英文而非中文描述技术规则（英文 Token 效率更高）

```python
# 精简后的 Planner system prompt（约 400 字符，减少 50%）
PLANNER_SYSTEM_PROMPT = """Task Planner. Decompose user task into executable steps.

Complexity: simple(1 step) / medium(2-3 steps) / complex(4-5 steps).
Tools: search, python(math/json/re pre-imported, no import), file_read, api_call, webshop, parse_pdf, parse_excel, parse_image, browse_webpage.

Output JSON: {"complexity":"simple","steps":[{"id":1,"action":"python","description":"desc","args":{"code":"print(42)"},"status":"pending","result":null,"retry_count":0,"risk":"low","depends_on":[]}]}
"""
```

### 3.9 预期效果与验证

| 改造项 | 预期降本 | 验证方法 |
|--------|:--------:|----------|
| 模型路由 | -20-30% | 对比单一模型 vs 大小模型混合的 Token 消耗 |
| Planner+Executor 合并 | -10-15% | simple 任务从 2 次 LLM 调用降为 1 次 |
| Critic 规则扩展 | -5-10% | 统计规则验证命中率（目标 ≥60%） |
| 上下文压缩 | -5-8% | 对比压缩前后 prompt 长度 |
| 消融实验修正 | 评测有效 | 4 组对比清晰分离各降本因素 |
| Prompt 精简 | -5-10% | 对比精简前后 system_prompt Token 数 |
| **综合预期** | **-30-45%** | 修正后消融实验验证 |

---

## 评测方法学修正（三目标共性）

### 4.1 当前评测方法学问题

| 问题 | 影响 | 涉及目标 |
|------|------|----------|
| 样本量不足（GAIA n=10, WebShop n=3） | 无统计显著性 | 三目标均受影响 |
| 自定义样例偏向框架优势 | 评测不客观 | GAIA, Token |
| 启发式混淆评测结果 | 归因错误 | GAIA, Token |
| ReAct 基线 prompt 未优化 | 基线不公平 | GAIA, WebShop |
| 消融实验设计缺陷 | 结论无效 | Token |

### 4.2 修正方案

#### 4.2.1 统一评测框架

**新建文件：`benchmarks/fair_eval.py`**

```python
"""
公平评测框架

确保三个目标的评测满足以下条件：
1. 样本量 ≥ 30（统计显著性最低要求）
2. 使用官方数据集（GAIA 官方 L1、WebShop 官方测试集）
3. 多智能体和 ReAct 使用相同的 LLM 模型和工具集
4. 启发式层作为独立变量控制（开/关对比）
5. 计算置信区间
"""
import math
from typing import Optional


def compute_confidence_interval(successes: int, total: int, confidence: float = 0.95) -> tuple:
    """计算二项分布的置信区间（Wilson 区间）"""
    if total == 0:
        return (0, 0)
    
    z = 1.96 if confidence == 0.95 else 2.576  # 95% or 99%
    p = successes / total
    
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    
    return (max(0, center - margin), min(1, center + margin))


def run_fair_evaluation(
    benchmark: str = "gaia",  # "gaia" or "webshop"
    num_samples: int = 30,
    use_heuristics: bool = False,
) -> dict:
    """
    运行公平评测
    
    对多智能体和 ReAct 使用完全相同的条件：
    - 相同的 LLM 模型
    - 相同的工具集
    - 相同的评测样本
    - 相同的 Token 预算上限
    """
    results = {"multi_agent": {}, "react": {}}
    
    if benchmark == "gaia":
        from benchmarks.gaia_official_eval import load_gaia_official
        from benchmarks.gaia_eval import evaluate_answer
        from graph.builder import run_task
        from benchmarks.react_baseline import run_react_task
        
        samples = load_gaia_official(num_samples)
        
        # 多智能体评测
        ma_correct = 0
        ma_tokens = 0
        for i, sample in enumerate(samples):
            question = sample["question"]
            answer = sample["answer"]
            
            state = run_task(question, use_heuristics=use_heuristics)
            predicted = state.get("final_answer", "")
            tokens = state.get("token_used", 0)
            
            if evaluate_answer(predicted, answer):
                ma_correct += 1
            ma_tokens += tokens
            
            print(f"[MA {i+1}/{num_samples}] correct={evaluate_answer(predicted, answer)}", flush=True)
        
        # ReAct 评测（使用相同的样本和模型）
        react_correct = 0
        react_tokens = 0
        for i, sample in enumerate(samples):
            question = sample["question"]
            answer = sample["answer"]
            
            state = run_react_task(question)
            predicted = state.get("final_answer", "")
            tokens = state.get("token_used", 0)
            
            if evaluate_answer(predicted, answer):
                react_correct += 1
            react_tokens += tokens
            
            print(f"[ReAct {i+1}/{num_samples}] correct={evaluate_answer(predicted, answer)}", flush=True)
        
        ma_acc = ma_correct / num_samples
        react_acc = react_correct / num_samples
        ma_ci = compute_confidence_interval(ma_correct, num_samples)
        react_ci = compute_confidence_interval(react_correct, num_samples)
        
        results = {
            "benchmark": "gaia_official_l1",
            "num_samples": num_samples,
            "use_heuristics": use_heuristics,
            "multi_agent": {
                "accuracy": round(ma_acc, 4),
                "correct": ma_correct,
                "ci_95": [round(ma_ci[0], 4), round(ma_ci[1], 4)],
                "avg_tokens": round(ma_tokens / num_samples),
            },
            "react_baseline": {
                "accuracy": round(react_acc, 4),
                "correct": react_correct,
                "ci_95": [round(react_ci[0], 4), round(react_ci[1], 4)],
                "avg_tokens": round(react_tokens / num_samples),
            },
            "accuracy_diff": round(ma_acc - react_acc, 4),
            "token_savings_pct": round((react_tokens - ma_tokens) / react_tokens * 100, 1) if react_tokens else 0,
        }
    
    elif benchmark == "webshop":
        # WebShop 评测逻辑类似
        from benchmarks.webshop_official_eval import (
            evaluate_webshop_multi_agent, evaluate_webshop_react
        )
        
        ma_result = evaluate_webshop_multi_agent(num_samples=num_samples)
        react_result = evaluate_webshop_react(num_samples=num_samples)
        
        results = {
            "benchmark": "webshop_official",
            "num_samples": num_samples,
            "multi_agent": {
                "success_rate": ma_result["success_rate"],
                "avg_reward": ma_result["avg_reward"],
            },
            "react_baseline": {
                "success_rate": react_result["success_rate"],
                "avg_reward": react_result["avg_reward"],
            },
            "success_rate_diff": round(ma_result["success_rate"] - react_result["success_rate"], 4),
        }
    
    return results
```

#### 4.2.2 评测报告生成

**修改文件：`benchmarks/report.py`**

```python
def build_report() -> dict:
    """生成包含置信区间的完整评测报告"""
    
    # 公平评测结果
    gaia_fair = run_fair_evaluation("gaia", num_samples=30, use_heuristics=False)
    webshop_fair = run_fair_evaluation("webshop", num_samples=30)
    
    # 成本消融（修正版）
    cost_ablation = evaluate_cost_ablation(num_samples=9)
    
    report = {
        "targets": {
            "gaia_l1_accuracy": "≥75% (+15pp vs ReAct)",
            "webshop_success_rate": "+18pp vs ReAct",
            "token_cost_reduction": "≥30%",
        },
        "gaia_results": gaia_fair,
        "webshop_results": webshop_fair,
        "cost_ablation": cost_ablation,
        "evaluation_config": {
            "gaia_samples": 30,
            "webshop_samples": 30,
            "use_heuristics": False,
            "model": LLM_MODEL,
            "fair_baseline": True,
        },
    }
    
    # 判定是否达标
    gaia_diff = gaia_fair.get("accuracy_diff", 0)
    webshop_diff = webshop_fair.get("success_rate_diff", 0)
    token_savings = cost_ablation.get("savings_analysis", {}).get("combined", 0)
    
    report["target_achievement"] = {
        "gaia_l1": {
            "target": 0.75,
            "actual": gaia_fair.get("multi_agent", {}).get("accuracy", 0),
            "diff_target": 0.15,
            "actual_diff": gaia_diff,
            "achieved": gaia_fair.get("multi_agent", {}).get("accuracy", 0) >= 0.75 and gaia_diff >= 0.15,
        },
        "webshop": {
            "target_diff": 0.18,
            "actual_diff": webshop_diff,
            "achieved": webshop_diff >= 0.18,
        },
        "token": {
            "target": 0.30,
            "actual": token_savings / 100,
            "achieved": token_savings >= 30,
        },
    }
    
    return report
```

---

## 实施路线图

### Phase 1：评测修正（第 1-2 周）

```
优先级：P0 | 目标：恢复评测有效性

Week 1:
├── [任务1] 重构 heuristics.py：删除硬编码答案，改为正则模式提取器
├── [任务2] 修复 react_baseline.py：强化工具使用约束
├── [任务3] 接入官方 GAIA 数据集（HuggingFace）
└── [任务4] 编写 fair_eval.py 公平评测框架

Week 2:
├── [任务5] 运行官方 GAIA L1 评测（n≥30，启发式开/关对比）
├── [任务6] 修正 cost_eval.py：4 组消融对比
├── [任务7] 运行修正后的成本消融实验
└── [任务8] 生成包含置信区间的评测报告
```

**Phase 1 交付物**：
- `agents/heuristics.py`（重构版）
- `benchmarks/gaia_official_eval.py`
- `benchmarks/fair_eval.py`
- `benchmarks/cost_eval.py`（修正版）
- 官方 GAIA L1 评测结果（n≥30）

### Phase 2：工具扩展（第 3-4 周）

```
优先级：P0-P1 | 目标：扩展工具集覆盖官方题型

Week 3:
├── [任务9] 实现 tools/file_parser.py（PDF/Excel/Image/CSV 解析）
├── [任务10] 实现 tools/web_browser.py（Playwright 网页浏览）
├── [任务11] 注册新工具到 tools/__init__.py
└── [任务12] 更新 Planner 系统提示词（新增工具+few-shot 示例）

Week 4:
├── [任务13] 在官方 GAIA L1 上测试新工具效果
├── [任务14] 实现 tools/webshop_real.py（真实 WebShop 适配器）
├── [任务15] 实现 graph/webshop_subgraph.py（多轮交互子图）
└── [任务16] 运行 WebShop 官方评测
```

**Phase 2 交付物**：
- `tools/file_parser.py`、`tools/web_browser.py`
- `tools/webshop_real.py`
- `graph/webshop_subgraph.py`
- `benchmarks/webshop_official_eval.py`
- 带新工具的 GAIA L1 评测结果

### Phase 3：架构优化（第 5-6 周）

```
优先级：P0-P1 | 目标：Token 降本优化

Week 5:
├── [任务17] 实现模型路由（config.py + llm_utils.py 修改）
├── [任务18] 实现 Planner+Executor 合并节点
├── [任务19] 扩展 Critic 规则验证覆盖范围
└── [任务20] 实现上下文压缩

Week 6:
├── [任务21] 运行修正后的成本消融（含模型路由对比）
├── [任务22] 精简系统提示词
├── [任务23] 运行最终三目标综合评测
└── [任务24] 生成最终评测报告
```

**Phase 3 交付物**：
- 模型路由配置和实现
- `agents/planner.py`（含合并节点）
- `agents/critic.py`（扩展规则验证）
- 最终三目标评测报告

### Phase 4：验证与文档（第 7-8 周）

```
优先级：P1-P2 | 目标：验证达标、更新文档

Week 7:
├── [任务25] 完整三目标评测（n≥30，官方数据集）
├── [任务26] 计算 95% 置信区间，验证统计显著性
├── [任务27] 对比改造前后的评测数据
└── [任务28] 编写消融分析报告

Week 8:
├── [任务29] 更新 README.md 评测结果
├── [任务30] 更新 ARCHITECTURE.md 架构文档
├── [任务31] 更新 EXPERIMENT.md 实验复现指南
└── [任务32] 编写改造总结报告
```

---

## 附：各目标达标条件检查清单

### 目标一：GAIA L1 ≥75%（+15pp vs ReAct）

- [ ] 启发式层无硬编码答案（`synthesize_heuristic_answer` 不返回任何预设字符串）
- [ ] 新增 PDF/Excel/Image/CSV 解析工具并注册
- [ ] 新增 Playwright 网页浏览工具并注册
- [ ] 接入官方 GAIA L1 数据集（≥165 题可用）
- [ ] 评测样本量 ≥ 30
- [ ] ReAct 基线 prompt 已修复（强制使用工具）
- [ ] 多智能体和 ReAct 使用相同 LLM 模型
- [ ] 多智能体准确率 ≥ 75%
- [ ] 准确率差值 ≥ 15pp
- [ ] 95% 置信区间不包含 0（统计显著）

### 目标二：WebShop +18pp

- [ ] 真实 WebShop 环境已搭建（或增强 Mock ≥ 200 商品）
- [ ] 多轮交互子图已实现（search → browse → select → purchase）
- [ ] WebShop 官方测试集已加载（≥ 30 条指令）
- [ ] ReAct 基线能正确调用 webshop 工具
- [ ] 多智能体成功率 - ReAct 成功率 ≥ 18pp
- [ ] 95% 置信区间验证

### 目标三：Token -30%

- [ ] 模型路由已实现（大小模型混合）
- [ ] Planner+Executor 合并节点已实现
- [ ] Critic 规则验证覆盖率 ≥ 60%（减少 LLM 调用）
- [ ] 上下文压缩已实现
- [ ] 消融实验修正为 4 组对比
- [ ] 紧预算设为 3000（合理范围）
- [ ] 样本集包含计算+知识检索+多步推理（≥ 9 题）
- [ ] 纯预算调度降本 ≥ 15%（修正后）
- [ ] 综合降本（预算+模型路由+合并）≥ 30%

---

*本方案基于项目源代码（commit 截至 2026-07-15）全量审计后设计，所有代码变更建议均基于实际源文件结构，可直接实施。*
