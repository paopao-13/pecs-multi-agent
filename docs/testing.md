# 测试实践：TDD 补齐与 bug 发现记录

> 本文档记录了用 TDD（测试驱动开发）为 PECS 项目补齐测试覆盖的过程，以及在此过程中发现并修复的 bug。
> 目的不是炫耀 bug 数量，而是记录 TDD 方法论的实际价值：**先写测试让我发现了用代码 review 看不出来的边界 case**。

## 测试规模

| 维度 | 数量 |
|---|---|
| 现有测试 | 29 |
| 新增测试 | 98 |
| **总测试数** | **127** |
| 修复的 bug | 7（5 个代码 bug + 2 个基础设施 bug） |
| 影响评测数据的 bug | 2（bug #2、bug #7） |

## 测试覆盖模块

| 模块 | 测试文件 | 测试数 | 重点 |
|---|---|:---:|---|
| conftest 配置 | test_conftest.py | 3 | API key 跳过逻辑 |
| GAIA 答案判定 | test_gaia_official.py | 39 | 归一化/数字/列表/字符串/泄露检查/McNemar/LLM mock |
| 启发式路由 | test_heuristics.py | 25 | 计划生成/数学提取/参数生成/答案合成 |
| Agent 核心逻辑 | test_agents.py | 31 | 任务判定/代码净化/参数检查/评分/反思触发 |
| 原有工具与图 | (已有) | 29 | tools/ 和 graph/ 模块 |

## TDD 发现的 7 个 bug

### bug #1：执行结果 success 判定截断

**位置**：`agents/executor.py`

**问题**：`"失败" not in result[:20]` 只检查前 20 个字符，如果"失败"出现在第 21 个字符之后，会被误判为成功。

**TDD 发现过程**：
1. 先写测试 `test_executor_failure_at_position_25`：构造 `result = "a" * 20 + "执行失败"`，期望 `success=False`
2. 运行测试，**看失败**（success 被判为 True）
3. 修复：`result[:20]` → `result`（全文检查）
4. 运行测试，**看通过**

**为什么 code review 看不出来**：`[:20]` 看起来像是"优化"（避免扫描长字符串），实际是逻辑错误。只有构造特定的边界 case 才能发现。

### bug #2：LLM 兜底判定误匹配（影响评测数据）

**位置**：`benchmarks/gaia_official.py`

**问题**：`"是" in result or "yes" in result.lower()` 会误匹配"不是"和"yesterday"。

**TDD 发现过程**：
1. 先写测试 `test_llm_fallback_no`：mock LLM 返回"不是"，期望 False
2. 运行测试，**看失败**（被误判为 True）
3. 再写测试 `test_llm_fallback_yesterday`：mock LLM 返回"yesterday"，期望 False
4. 运行测试，**也失败**（"yes" in "yesterday" 为 True）
5. 修复：改为精确匹配 `result == "是" or result.lower() == "yes"`
6. 运行测试，**看通过**

**影响**：这个 bug 导致 GAIA 评测中 4 道题的错误答案被误判为正确。修复后重新验证，PECS 准确率数据得到修正。

### bug #3（伪 bug）：大写 E 科学计数

**位置**：`benchmarks/gaia_official.py` 的 `_parse_number`

**原以为的问题**：正则 `-?\d+\.?\d*e[+-]?\d+` 不支持大写 E（如 "1.5E6"）。

**TDD 验证结果**：写测试后发现**通过**了——因为函数在正则匹配前先做了 `.lower()`，大写 E 已被转成小写 e。

**价值**：这个"伪 bug"的验证过程证明了 TDD 的另一个价值——**不只是发现真 bug，也能证伪怀疑的 bug**，避免不必要的修改。

### bug #4：Windows 路径含空格被截断

**位置**：`agents/heuristics.py`

**问题**：正则 `[\w\-./\\]+` 不匹配空格，路径含空格时（如 `C:\Users\My Name\file.pdf`）会被截断为 `Name\file.pdf`。

**TDD 发现过程**：
1. 先写测试 `test_plan_file_parse_with_space_path`
2. 运行测试，**看失败**（路径丢失前半段）
3. 修复：改为 `[a-zA-Z0-9\-./\\ ]+`（注意用 ASCII 字符类避免中文混入）
4. 运行测试，**看通过**

### bug #5：规则评分 text[:20] 截断

**位置**：`agents/critic.py`

**问题**：`"错误" in text[:20]` 只看前 20 个字符，与 bug #1 同类。

**TDD 发现过程**：与 bug #1 相同的模式，构造 `"a" * 20 + "错误信息"` 触发。

### bug #6：子模块 import 漏网

**位置**：`agents/executor.py` 的 `_sanitize_python_code`

**问题**：正则 `from\s+\w+\s+import` 的 `\w+` 不匹配点号，`from os.path import system` 不会被净化。

**TDD 发现过程**：
1. 先写测试 `test_sanitize_from_submodule_import`
2. 运行测试，**看失败**（子模块 import 未被移除）
3. 修复：`\w+` → `[\w.]+`
4. 运行测试，**看通过**

**安全影响**：这是 AST 安全沙箱的漏洞，用户代码可以通过 `from os.path import system` 绕过 import 净化。

### bug #7：数据泄露检查数字子串误匹配（影响评测数据）

**位置**：`benchmarks/gaia_official.py` 的 `check_data_leakage`

**问题**：归一化包含检查 `norm_truth in norm_q` 不使用 word boundary，导致答案"17"误匹配题目中的"2017"。

**TDD 发现过程**：
1. 先写测试 `test_leakage_number_boundary_safe`：question="2017年发生了什么"，truth="17"，期望不泄露
2. 运行测试，**看失败**（被判为泄露）
3. 修复：数字答案优先走 word boundary 检查，跳过归一化包含检查
4. 运行测试，**看通过**

**影响**：这个 bug 导致 GAIA 评测中 5 道题被误判为泄露而跳过（实际不是泄露）。修复后补评这 5 道题，ReAct 准确率从 15.1% 升至 24.5%。

## bug 修复对评测数据的影响

| bug | 影响 | PECS 准确率 | ReAct 准确率 | 差值 |
|---|---|:---:|:---:|:---:|
| 原始数据 | - | 26.4% | 15.1% | +11.3pp |
| bug #2 修复 | 4 道 True → False | 26.4% → 22.6% | 不变 | +7.5pp |
| bug #7 修复 | 5 道补评 | +4 道 → 26.4% | +5 道 → 24.5% | +1.9pp |
| **最终数据** | - | **26.4%** | **24.5%** | **+1.9pp** |

**结论**：原始 +11.3pp 的优势有很大部分来自 bug 导致的 ReAct 题目被错误跳过。修复后 PECS 在 GAIA 上的优势缩至 +1.9pp（McNemar p=1.0，完全不显著）。诚实更新数据比掩盖更有价值。

## 两个"故事性测试"

这两个测试适合作为工程严谨性和科研诚信的案例展示。

### 故事 1：数据泄露防护（学术诚信）

`test_leakage_truth_in_question`：验证当 ground_truth 出现在 question 中时，`check_data_leakage` 能检测到。

> GAIA validation set 的答案是公开的。如果答案出现在 LLM 的 prompt 里，LLM 可能直接复述答案而非真正推理，导致评测结果无效。`check_data_leakage` 在评测前拦截这类题目，这是学术诚信的红线。

### 故事 2：TDD 发现 bug（工程能力）

`test_executor_failure_at_position_25` / `test_rule_evaluate_error_at_position_25` / `test_llm_fallback_no`：通过 TDD 的 RED 阶段发现 `[:20]` 截断和子串误匹配 bug。

> 这些 bug 用 code review 看不出来——`[:20]` 看起来像是优化，`"是" in result` 看起来没问题。只有先写测试构造边界 case，才能发现它们。这就是 TDD 的价值：让测试来证明代码正确，而不是靠人眼。

## 运行测试

```bash
# 全量测试
pytest tests/ -q

# 只跑 TDD 新增测试
pytest tests/test_conftest.py tests/test_gaia_official.py tests/test_heuristics.py tests/test_agents.py -q

# 跳过需要 API Key 的测试
pytest tests/ -q -m "not requires_api_key"
```

## 已知局限

- **Windows SIGALRM**：`gaia_official.py` 的单题超时用 `signal.SIGALRM`，Windows 上不存在（有 `hasattr` 保护不会崩，但超时机制静默失效）
- **LLM mock 覆盖有限**：只 mock 了 `evaluate_answer_official` 的 LLM 兜底分支，未 mock `run_task` / `run_react_task` 内部的 LLM 调用
- **覆盖率未量化**：未配置 pytest-cov，没有具体的覆盖率百分比
