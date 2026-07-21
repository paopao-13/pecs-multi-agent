# 代码缺陷修复方案（FIX_PLAN）

> 面向本项目的代码缺陷体检与修复方案。
> 原则：**只修真缺陷，不篡改已跑出的评测数字；每一次改动都可独立 revert、可验证。**
> 沙箱无 LLM Key，验证仅覆盖不依赖 API 的纯逻辑（沙箱检查、成功判定、状态字段）；依赖 LLM 的端到端路径靠单测在用户本机验证。

---

## 0. 范围澄清（先排除误报）

| 审查原结论 | 复核结论 | 处理 |
|---|---|---|
| `TokenBudgetManager` 疑似死代码（P1#4） | **误报**：`scripts/app.py:18`、`demos/token_budget_demo.py` 均导入并使用 | **不修** |
| README 确定性需显式开启（P1#3） | **已正确**：README 第 147 行已写明「基准评测开启 `PEC_DETERMINISTIC=1` 固定全 0」 | **不修** |
| `state.py` lambda 默认工厂两处来源（P1#2） | 非缺陷，Pydantic 合法用法；属可维护性 smell | **不修**（避免引入回归） |

**本次实际修复 4 个缺陷 + 补 2 类测试。** 严重程度：1 个 P0（安全）、3 个 P1/P3（正确性/一致性）。

---

## 缺陷 1（P0 安全）：沙箱 `hasattr` 反射绕过

### 1.1 问题分析
- **位置**：`tools/python_repl.py`
  - 第 206 行白名单 `__builtins__` 含 `"hasattr": hasattr`
  - `SecurityChecker.visit_Call`（105-116 行）只拦截两类调用：① `func` 是 `Name` 且函数名在 `FORBIDDEN_CALLS`（无 `hasattr`）；② `func` 是 `Attribute` 且属性名是 dunder（如 `obj.__class__`）。
  - `SecurityChecker.visit_Attribute`（118-122 行）只拦 `obj.__class__` 这种**属性访问节点**。
- **根本原因**：`hasattr(obj, "__class__")` 是**函数调用式反射**，第二个参数是字符串字面量 `"__class__"`，既不在 `FORBIDDEN_CALLS`、也不是 `Attribute` 节点，因此 AST 检查完全放行。
- **后果**：攻击者在沙箱内可用 `hasattr(math, "__class__")` 等拿到 class 对象引用，进而绕开 dunder 拦截摸到 `__builtins__` / `__globals__` 链路，使「安全沙箱」名存实亡。这正是 `docs/SECURITY_AUDIT.md` 里写的「已知逃逸边界」中真正可被打穿的一条。

### 1.2 修复步骤
在 `SecurityChecker.visit_Call` 内、处理 `func` 为 `Name` 的分支中，新增对 `hasattr` 第二参数（属性名）的 dunder 检查：
- 若 `func_name == "hasattr"` 且 `node.args[1]` 是字符串常量且首尾均为 `__`，记录违规。
- 保留 `hasattr` 在白名单中（正常 `hasattr(math, "sqrt")` 不受影响），仅堵反射式探测。

### 1.3 修改范围
- `tools/python_repl.py`：`visit_Call` 新增 8 行；同步更新第 206 行注释说明 dunder 反射已被 AST 拦截。

### 1.4 验证方法
- 沙箱内可直接跑（仅依赖标准库）：
  ```python
  from tools.python_repl import check_code_safety
  assert check_code_safety("hasattr(math, '__class__')")   # 应返回非空（拦截）
  assert check_code_safety("hasattr(math, 'sqrt')") == []   # 正常用法放行
  assert check_code_safety("getattr(math, '__class__')")    # 仍被 FORBIDDEN_CALLS 拦截
  ```
- 新增单测 `tests/test_security_fix.py::test_hasattr_dunder_reflection_blocked`。

### 1.5 风险评估
- **风险**：极低。仅对「属性名是 dunder 字符串」的 `hasattr` 调用加限制；正常 `hasattr(x, "普通属性")` 行为不变。
- **副作用**：若某 benchmark 代码用 `hasattr` 探测双下划线属性（极罕见），会被拒绝——这属于应被拒绝的危险用法，符合预期。
- **应对**：白名单注释同步更新，明确「dunder 反射已由 AST 拦截」。

---

## 缺陷 2（P1 正确性）：Executor 成功判定误判

### 2.1 问题分析
- **位置**：`agents/executor.py:160`
  ```python
  "success": not result.startswith("错误") and not result.startswith("执行错误") and "失败" not in result,
  ```
- **根本原因**：用 `"失败" not in result` 作为「非错误」判定。统计/计算类正常结果常含「失败」二字（如 `"失败率 = 0.05"`、`"失败次数 = 3"`），会被误判为 `success=False`，导致 Critic 无谓重试、白烧 Token，甚至把正确结果当错误处理。
- **后果**：正确性 bug + 隐性成本浪费；且与 Critic 的判定逻辑（`critic.py:166` 同样含 `"失败" in text`）重复且不一致。

### 2.2 修复步骤
1. 在 `tools/__init__.py` 新增共用函数 `is_tool_success(result)`，以**显式错误前缀**（`错误`/`执行错误`/`安全检查未通过`）作为失败判据，不再用「失败」字样。
2. `agents/executor.py` 改用 `is_tool_success(result)`。
3. `agents/critic.py:_fast_evaluate` 把 `"失败" in text` 改为 `not is_tool_success(text)`，消除同一误判。

### 2.3 修改范围
- `tools/__init__.py`：新增 `_ERROR_MARKERS` 常量 + `is_tool_success()`。
- `agents/executor.py`：import 调整（加 `is_tool_success`），第 160 行替换。
- `agents/critic.py`：import `is_tool_success`，第 166 行修正。

### 2.4 验证方法
- 沙箱内逻辑验证（`is_tool_success` 为纯函数，但 `tools` 包在沙箱可能缺 `openai`，故用等价内联验证 + 用户本机跑单测）：
  ```python
  assert is_tool_success("失败率 = 0.05") is True     # 关键：不再误判
  assert is_tool_success("错误：缺少 code 参数") is False
  assert is_tool_success("执行错误:\nTraceback...") is False
  assert is_tool_success("安全检查未通过...") is False
  assert is_tool_success("2 的 100 次方 = ...") is True
  ```
- 新增单测 `tests/test_tool_success.py`。

### 2.5 风险评估
- **风险**：低。判定规则收紧为「显式错误前缀」，对现有错误返回格式（均以 `错误`/`执行错误`/`安全检查未通过` 开头）100% 覆盖。
- **副作用**：若未来新增工具返回以非前缀形式嵌入错误（不符合现有约定），需同步更新 `_ERROR_MARKERS`；已在函数 docstring 注明约定。
- **应对**：`is_tool_success` 成为唯一判据来源，删除 executor/critic 中重复的内联判断，降低漂移。

---

## 缺陷 3（P3 一致性）：`step_count` 恒为 0

### 3.1 问题分析
- **位置**：`scripts/api.py:365` 读 `final_state.get("step_count", 0)`；`:464` 用 `result["step_count"]` 上报。
- **根本原因**：`graph/state.py` 的 `AgentState` **没有 `step_count` 字段**，Pydantic 模型不会凭空产生该键，故 API 永远拿到默认值 `0`。
- **后果**：API 上报的「执行步数」永远是 0，是一个静默的数据错误（不报错但恒错），代码评审被问到「你 API 上报的步数怎么一直是 0」会显得准备不足。

### 3.2 修复步骤
1. `graph/state.py`：`AgentState` 新增字段 `step_count: int = 0`（默认值 0，向后兼容）。
2. `agents/executor.py`：每步执行完 `results.append(...)` 后，在返回值中写 `"step_count": len(results)`，使该字段反映真实已执行步数。

### 3.3 修改范围
- `graph/state.py`：`AgentState` 新增一行字段。
- `agents/executor.py`：`executor_node` 返回值新增 `"step_count": len(results)`。

### 3.4 验证方法
- 用户本机：跑 `python scripts/api.py` 或单测，确认返回 `step_count == len(results)` 而非恒 0。
- 沙箱内静态校验：`graph/state.py` 可被 import（仅依赖 pydantic + typing），确认 `AgentState().step_count == 0`；且 `model_fields` 含 `step_count`。
- 新增单测 `tests/test_state_step_count.py`：构造初始 state，模拟 executor 写回后 `step_count` 更新。

### 3.5 风险评估
- **风险**：极低。新增字段带默认值，不破坏现有 state 序列化/节点返回值合并。
- **副作用**：无。LangGraph 按 channel 合并部分状态，新增键被正常合并。
- **应对**：字段默认值 0 保证旧有未写回路径不报错。

---

## 缺陷 4（P1 文档诚实度）：`web_search` mock 注释误导

### 4.1 问题分析
- **位置**：`tools/web_search.py:36-37` 注释
  ```python
  # 优先匹配 mock 数据（benchmark 评测可复现性保证）
  # mock 数据覆盖了所有 GAIA/WebShop 样例查询，确保评测结果一致
  ```
- **根本原因**：注释称 mock 是为「评测可复现性」确保「结果一致」，但**未说明 mock 实为预置标准答案键（开卷）**。这与 README 第 138 行已声明的诚实口径（内置 33 题知识检索子集走 mock 答案键、更测编排而非真实检索）不一致——代码注释与文档打架，读源码会觉得自相矛盾。

### 4.2 修复步骤
将注释改为明确「mock 为内置样例预置答案键（开卷），仅保证内置 33 题可复现；真实检索以官方 53 题走真实 API 为准」。

### 4.3 修改范围
- `tools/web_search.py`：第 36-37 行注释改写（纯注释，零行为风险）。

### 4.4 验证方法
- 人工 review；`py_compile` 确认无语法影响（注释改动天然安全）。

### 4.5 风险评估
- **风险**：无。纯注释。

---

## 总结：修改文件清单

| 文件 | 改动 | 严重度 |
|---|---|---|
| `tools/python_repl.py` | `visit_Call` 加 hasattr dunder 反射拦截 + 注释 | P0 |
| `tools/__init__.py` | 新增 `_ERROR_MARKERS` + `is_tool_success()` | P1 |
| `agents/executor.py` | 用 `is_tool_success`；返回 `step_count` | P1/P3 |
| `agents/critic.py` | `_fast_evaluate` 用 `is_tool_success` 去误判 | P1 |
| `graph/state.py` | `AgentState` 加 `step_count` 字段 | P3 |
| `tools/web_search.py` | mock 注释诚实化 | P1 |
| `tests/test_security_fix.py` | 新增：hasattr 反射拦截单测 | — |
| `tests/test_tool_success.py` | 新增：`is_tool_success` 单测 | — |
| `tests/test_state_step_count.py` | 新增：step_count 字段单测 | — |

> 全部改动 **不 push**（filter-repo 教训，push 须用户确认）。
