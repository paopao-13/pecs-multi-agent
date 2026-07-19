# PECS 多智能体协作框架 — 架构文档

> 基于 LangGraph 的 Plan-Execute-Reflect 闭环多智能体系统
> 四大固定角色：Planner / Executor / Critic / Synthesizer

---

## 目录

1. [四大角色精确定义](#1-四大角色精确定义)
2. [AgentState 全字段定义](#2-agentstate-全字段定义)
3. [三段式条件路由完整规则表](#3-三段式条件路由完整规则表)
4. [Token 预算三级降级机制](#4-token-预算三级降级机制)
5. [三重循环终止条件](#5-三重循环终止条件)
6. [安全沙箱机制](#6-安全沙箱机制)
7. [统一配置加载规则](#7-统一配置加载规则)
8. [项目核心创新点总结](#8-项目核心创新点总结)
9. [消融实验设计说明](#9-消融实验设计说明)
10. [数据分层抽象设计](#10-数据分层抽象设计)
11. [模块扩展接口](#11-模块扩展接口)

---

## 1. 四大角色精确定义

PECS 架构由四个固定角色组成，每个角色有明确的输入/输出契约、独立的温度参数和清晰的职责边界。温度参数定义于 `agents/llm_utils.py` 的 `ROLE_TEMPERATURES` 字典中，每个角色拥有独立的 LLM 实例。

| 角色 | 输入（读取的 AgentState 字段） | 输出（写入的 AgentState 字段） | 温度参数 | 职责边界 |
|------|------|------|------|------|
| **Planner**（规划者） | `query`、`reflection`、`iteration`、`token_used`、`token_budget`、`use_heuristics` | `plan`、`complexity`、`current_step_idx`（重置为0）、`token_used`、`role_token_used`、`budget_events`、`scheduler_decisions`、`iteration`、`logs` | **0.3** | 接收用户原始问题，判断任务复杂度（simple/medium/complex），将任务分解为有序的子任务步骤列表。根据上一轮反思调整计划；预算紧张（>85%）时合并剩余步骤。不负责执行和评审。 |
| **Executor**（执行者） | `plan`、`current_step_idx`、`results`、`retry_feedback`、`query`、`use_heuristics` | `plan`（更新步骤状态）、`results`、`current_step_idx`（+1）、`token_used`、`role_token_used`、`budget_events`、`retry_feedback`（清除）、`logs` | **0.0** | 按 Planner 制定的计划逐步调用工具执行任务。参数不完整时调用 LLM 生成工具参数；接收 Critic 重试反馈后修正参数重做。不负责规划、评审和综合。 |
| **Critic**（评审者） | `results`、`critic_scores`、`token_used`、`token_budget` | `critic_scores`、`retry_feedback`、`token_used`、`role_token_used`、`budget_events`、`logs` | **0.1** | 评估 Executor 每步执行结果的质量，三维打分（准确性/一致性/完整性，各1-5分）。综合分≥4为合格；<4且重试<3次则生成重试反馈；预算>70%时对低风险步骤启用快速验证模式。不负责执行和综合。 |
| **Synthesizer**（综合者） | `query`、`results`、`token_used`、`token_budget`、`iteration`、`complexity`、`use_heuristics` | `final_answer`、`reflection`、`token_used`、`role_token_used`、`budget_events`、`scheduler_decisions`、`iteration`（+1）、`logs` | **0.5** | 整合所有子任务执行结果，生成最终答案。判断是否触发反思循环回 Planner 重新规划。预算>95%时进入紧急模式直接拼接结果；simple单步任务走快速提取路径。不负责规划和执行。 |

**温度参数设计依据**（源自 `agents/llm_utils.py` 文档注释）：

| 角色 | 温度 | 设计理由 |
|------|------|------|
| Planner | 0.3 | 规划需要一点创造性来拆分任务 |
| Executor | 0.0 | 生成代码/参数要求精确无随机性 |
| Critic | 0.1 | 评分要稳定一致 |
| Synthesizer | 0.5 | 表达需要灵活性 |

---

## 2. AgentState 全字段定义

`AgentState` 定义于 `graph/state.py`，继承自 `TypedDict`，是 LangGraph 状态图中所有节点共享的数据载体。以下为全部字段及其读写权限映射（R=读，W=写）。

### 2.1 输入字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 | 初始化 |
|------|------|------|------|------|------|------|------|------|
| `query` | `str` | 用户的原始问题 | R | R | - | R | - | W |

### 2.2 Planner 输出字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `plan` | `List[StepPlan]` | 分解后的执行计划（步骤列表） | W | R/W | R | R | R |
| `current_step_idx` | `int` | 当前执行到第几步 | W（重置0） | W（+1） | - | - | R |
| `complexity` | `str` | 任务复杂度：simple / medium / complex | W | - | - | R | R |

### 2.3 Executor 输出字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `results` | `List[Dict[str, Any]]` | 每步执行结果 `[{step_id, action, description, result, success}]` | - | W | R | R | R |

### 2.4 Critic 输出字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `critic_scores` | `List[CriticScore]` | 每步的评审打分（三维评分+综合分+反馈） | - | - | W | - | R |
| `retry_feedback` | `str` | 给 Executor 的重试建议 | - | W（清除） | W | - | R |

### 2.5 Synthesizer 输出字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `final_answer` | `str` | 最终答案 | - | - | - | W | - |
| `answer_format` | `str` | 输出格式：text / json / table / code | - | - | - | - | - |

### 2.6 Token 预算管理字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `token_used` | `int` | 已消耗的 Token 数 | R/W | R/W | R/W | R/W | R |
| `token_budget` | `int` | Token 预算上限 | R | R | R | R | R |
| `budget_degraded` | `bool` | 是否已触发降级 | R | R | R | R | R |
| `role_token_used` | `Dict[str, int]` | 各角色 Token 消耗明细 | W | W | W | W | - |
| `budget_events` | `List[Dict[str, Any]]` | Token 消耗与降级事件记录 | W | W | W | W | - |
| `scheduler_decisions` | `List[Dict[str, Any]]` | 预算感知调度决策记录 | W | - | - | W | - |

### 2.7 反思循环字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `reflection` | `str` | 上一轮的反思总结（供下一轮 Planner 参考） | R | - | - | W | R |
| `iteration` | `int` | 当前循环轮次 | R/W | - | - | W（+1） | R |

### 2.8 执行日志与模式控制字段

| 字段名 | 类型 | 用途说明 | Planner | Executor | Critic | Synthesizer | 路由函数 |
|------|------|------|------|------|------|------|------|
| `logs` | `List[str]` | 执行过程日志（供 Web 界面展示） | R/W | R/W | R/W | R/W | - |
| `use_heuristics` | `bool` | 是否启用启发式规划/综合（成本消融时设为 False） | R | R | - | R | - |

### 2.9 辅助 TypedDict 结构

**StepPlan**（单个执行步骤结构）：

| 字段名 | 类型 | 用途说明 |
|------|------|------|
| `id` | `int` | 步骤编号 |
| `action` | `str` | 动作类型：search / python / file_read / api_call / llm_reasoning |
| `description` | `str` | 步骤描述（给 Executor 看的自然语言指令） |
| `args` | `Dict[str, Any]` | 动作参数 |
| `status` | `str` | pending / running / done / failed |
| `result` | `Optional[str]` | 执行结果 |
| `retry_count` | `int` | 已重试次数 |
| `risk` | `str` | low / medium / high，用于预算调度 |
| `depends_on` | `List[int]` | 依赖的步骤 ID 列表 |

**CriticScore**（评审结果结构）：

| 字段名 | 类型 | 用途说明 |
|------|------|------|
| `accuracy` | `int` | 事实准确性 1-5 |
| `consistency` | `int` | 逻辑一致性 1-5 |
| `completeness` | `int` | 信息完整性 1-5 |
| `overall` | `float` | 综合评分（三维均值，保留一位小数） |
| `feedback` | `str` | 修改建议 |
| `step_id` | `int` | 评估的步骤 ID |

---

## 3. 三段式条件路由完整规则表

三个路由函数定义于 `graph/builder.py`，通过 `graph.add_conditional_edges()` 注册为 LangGraph 条件边，根据当前状态动态决定下一步跳转的节点。

### 3.1 route_after_executor — Executor 之后的路由

该函数在 Executor 执行完一个步骤后触发，决定是进入 Critic 评审、继续执行下一步、还是直接综合。

| 序号 | 判断条件 | 跳转去向 | 说明 |
|------|------|------|------|
| 1 | `policy["force_synthesize"]` 为 True（预算>95%） | `synthesizer` | 预算耗尽，强制进入综合阶段 |
| 2 | `complexity == "simple"` 且 `current_idx >= len(plan)`（所有步骤已执行完） | `synthesizer` | 简单任务跳过 Critic，直接综合 |
| 3 | `complexity == "simple"` 且 `current_idx < len(plan)`（还有步骤） | `executor` | 简单任务继续执行下一步，不经过 Critic |
| 4 | `policy["skip_low_risk_critic"]` 为 True（预算>70%且步骤为低风险）且 `latest_result.success` 为 True 且 `current_idx < len(plan)` | `executor` | 预算紧张时跳过低风险步骤的 Critic 验证，继续执行 |
| 5 | `policy["skip_low_risk_critic"]` 为 True（预算>70%且步骤为低风险）且 `latest_result.success` 为 True 且 `current_idx >= len(plan)` | `synthesizer` | 低风险步骤全部跳过 Critic 后直接综合 |
| 6 | 以上条件均不满足（medium/complex 任务正常流程） | `critic` | 进入 Critic 做质量评审 |

### 3.2 route_after_critic — Critic 之后的路由

该函数在 Critic 评审完成后触发，按优先级从高到低判断下一步去向。

| 序号 | 判断条件 | 跳转去向 | 说明 |
|------|------|------|------|
| 1 | `get_budget_policy(state)["force_synthesize"]` 为 True（预算>95%） | `synthesizer` | 预算耗尽，强制综合 |
| 2 | `critic_scores` 为空 | `synthesizer` | 无评分数据，直接综合 |
| 3 | `overall >= 4.0`（质量达标）且 `current_idx < len(plan)`（还有未执行步骤） | `executor` | 执行下一步 |
| 4 | `overall >= 4.0`（质量达标）且 `current_idx >= len(plan)`（所有步骤完成） | `synthesizer` | 所有步骤完成，去综合 |
| 5 | `overall < 4.0`（质量不达标）且该步骤 `retry_count >= 3`（重试上限）且 `current_idx < len(plan)` | `executor` | 重试上限，强制通过，执行下一步 |
| 6 | `overall < 4.0`（质量不达标）且该步骤 `retry_count >= 3`（重试上限）且 `current_idx >= len(plan)` | `synthesizer` | 重试上限，强制通过，去综合 |
| 7 | `overall < 4.0`（质量不达标）且 `retry_feedback` 非空（可重试） | `executor_retry` | 回到 Executor 重试当前步骤 |
| 8 | 以上条件均不满足（默认兜底） | `current_idx < len(plan)` → `executor`；否则 → `synthesizer` | 默认执行下一步或综合 |

### 3.3 route_after_synthesizer — Synthesizer 之后的路由

该函数在 Synthesizer 生成最终答案后触发，决定是否触发反思循环或结束任务。

| 序号 | 判断条件 | 跳转去向 | 说明 |
|------|------|------|------|
| 1 | `reflection` 非空（需要反思）且 `iteration < MAX_ITERATIONS`（未达最大迭代次数5） | `planner` | 回到 Planner 重新规划，进入新一轮 Plan-Execute-Reflect 循环 |
| 2 | `reflection` 为空，或 `iteration >= MAX_ITERATIONS`（已达最大迭代次数5） | `END` | 输出最终答案，结束任务 |

### 3.4 图节点与边拓扑

```
入口 → planner → executor → [route_after_executor]
                                    ├─ synthesizer ──→ [route_after_synthesizer]
                                    │                        ├─ planner（反思循环）
                                    │                        └─ END
                                    ├─ executor（继续下一步）
                                    └─ critic → [route_after_critic]
                                                    ├─ executor（下一步）
                                                    ├─ executor_retry → executor（重试）
                                                    └─ synthesizer
```

**节点清单**（`build_graph()` 中注册）：
- `planner` → `planner_node`
- `executor` → `executor_node`
- `executor_retry` → `executor_retry_node`
- `critic` → `critic_node`
- `synthesizer` → `synthesizer_node`

**固定边**：
- `planner` → `executor`（规划完即执行）
- `executor_retry` → `executor`（重试时重新执行）

**条件边**：
- `executor` → `route_after_executor` → `{synthesizer, executor, critic}`
- `critic` → `route_after_critic` → `{executor, executor_retry, synthesizer}`
- `synthesizer` → `route_after_synthesizer` → `{planner, END}`

**入口点**：`planner`

---

## 4. Token 预算三级降级机制

Token 预算管理实现于 `graph/token_budget.py`，阈值参数定义于 `config.py`。系统在运行过程中实时追踪已消耗 Token，当消耗占预算比例达到不同阈值时自动触发降级策略，在保精度前提下显著降本。

### 4.1 三级降级规则表

| 降级级别 | 触发阈值 | 降级动作 | 代码位置 |
|------|------|------|------|
| **Level 0**（正常） | usage_ratio ≤ 0.70 | 无降级，全流程正常运行 | `token_budget.py` `get_degrade_level()` 返回 0 |
| **Level 1**（轻度降级） | 0.70 < usage_ratio ≤ 0.85 | Critic 跳过低风险步骤的详细验证，改用快速验证模式（`_fast_evaluate`，不调 LLM）；`skip_low_risk_critic=True`，`fast_critic=True` | `config.py` `DEGRADE_THRESHOLD_1 = 0.70`；`token_budget.py` `get_budget_policy()` 返回 `skip_low_risk_critic` 和 `fast_critic`；`critic.py` `critic_node()` 中 `fast_mode` 分支 |
| **Level 2**（中度降级） | 0.85 < usage_ratio ≤ 0.95 | Planner 将剩余步骤合并为1-2个大步骤，减少 LLM 调用次数；`merge_steps=True` | `config.py` `DEGRADE_THRESHOLD_2 = 0.85`；`token_budget.py` `get_budget_policy()` 返回 `merge_steps`；`planner.py` `planner_node()` 中提示词追加"预算紧张，请将剩余步骤合并" |
| **Level 3**（重度降级） | usage_ratio > 0.95 | Synthesizer 直接用已有结果生成答案，不再执行更多步骤；`force_synthesize=True`；路由函数强制跳转至 `synthesizer`；Synthesizer 进入紧急模式（`_emergency_synthesize`） | `config.py` `DEGRADE_THRESHOLD_3 = 0.95`；`token_budget.py` `get_budget_policy()` 返回 `force_synthesize`；`builder.py` `route_after_executor()` 和 `route_after_critic()` 中 `force_synthesize` 检查；`synthesizer.py` `synthesizer_node()` 中 `budget_ratio > 0.95` 分支 |

### 4.2 预算分配比例

各角色预算配额定义于 `config.py` 的 `BUDGET_ALLOCATION`，总和为 1.0：

| 角色 | 预算占比 | 说明 |
|------|------|------|
| Planner | 15% | 规划消耗较少 |
| Executor | 50% | 执行最耗 Token（需调用工具和生成参数） |
| Critic | 20% | 评审需要调用 LLM 评分 |
| Synthesizer | 15% | 综合需要调用 LLM 生成答案 |

### 4.3 核心函数说明

| 函数 | 位置 | 作用 |
|------|------|------|
| `estimate_tokens(text)` | `token_budget.py` | 粗略估算文本 Token 数（中英文混合用 3 字符/token 经验值） |
| `get_usage_ratio(token_used, token_budget)` | `token_budget.py` | 返回当前消耗占预算的比例（0.0~1.0+） |
| `get_degrade_level(token_used, token_budget)` | `token_budget.py` | 返回降级级别（0/1/2/3） |
| `get_budget_policy(state, risk)` | `token_budget.py` | 根据当前状态生成调度策略字典，供节点和路由函数共用 |
| `record_token_usage(state, role, tokens)` | `token_budget.py` | 记录 Token 消耗，返回更新后的三元组 |
| `append_scheduler_decision(state, actor, decision, reason)` | `token_budget.py` | 追加结构化调度决策记录 |
| `check_role_quota(role)` | `token_budget.py` `TokenBudgetManager` | 检查单个角色是否超出独立配额 |
| `get_role_degrade_action(role)` | `token_budget.py` `TokenBudgetManager` | 返回角色超配额时的降级动作 |
| `check_role_budget(state, role)` | `token_budget.py` | 模块级函数，从状态字典检查角色预算状态 |

### 4.4 角色独立配额机制（新增）

在全局三级降级之外，新增四大角色独立 Token 配额，解决角色资源争抢问题。配置于 `experiments/config.yaml` 的 `token_budget.role_quotas`：

| 角色 | 独立配额 | 超限降级动作 | 说明 |
|------|------|------|------|
| Planner | 7,500 (15%) | `skip_llm_use_heuristic` — 用启发式替代 LLM 调用 | 规划消耗少，超限说明任务过于复杂 |
| Executor | 25,000 (50%) | `merge_remaining_steps` — 合并剩余步骤 | 执行最耗 Token，超限需减少调用 |
| Critic | 10,000 (20%) | `skip_critic` — 跳过评审 | 评审超限说明重试过多 |
| Synthesizer | 7,500 (15%) | `fast_synthesize` — 快速综合 | 综合超限走确定性路径 |

**分级超限降级流程**：
1. 每次角色消耗 Token 后，`consume()` 方法检查该角色独立配额
2. 超限时记录到 `budget_events`，并返回对应降级动作
3. 节点函数根据降级动作调整行为（如 Critic 超限→跳过评审、Executor 超限→合并步骤）
4. 无需等待全局预算耗尽即可触发轻量化降级，实现更精细的成本控制

---

## 5. 三重循环终止条件

系统设计了三重终止保护机制，防止 Agent 陷入无限循环或成本失控。

| 终止保护 | 参数名 | 参数值 | 定义位置 | 触发逻辑 | 代码位置 |
|------|------|------|------|------|------|
| **最大迭代次数** | `MAX_ITERATIONS` | 5 | `config.py` 第45行 | Synthesizer 之后的路由函数 `route_after_synthesizer` 检查 `iteration < MAX_ITERATIONS`，若已达上限则跳转 `END`，不再回 Planner 重新规划；Synthesizer 内部 `_should_reflect()` 也会检查 `iteration >= MAX_ITERATIONS - 1` 时不再触发反思 | `builder.py` `route_after_synthesizer()`；`synthesizer.py` `_should_reflect()` |
| **最大重试次数** | `MAX_RETRIES` | 3 | `config.py` 第44行 | Critic 之后的路由函数 `route_after_critic` 检查步骤的 `retry_count >= 3` 时，即使质量不达标也强制通过（跳转执行下一步或综合），不再重试 | `builder.py` `route_after_critic()` 中 `step.get("retry_count", 0) >= 3` 判断 |
| **预算耗尽强制终止** | `DEGRADE_THRESHOLD_3` | 0.95（即预算的95%） | `config.py` 第41行 | 当 `usage_ratio > 0.95` 时，`get_budget_policy()` 返回 `force_synthesize=True`，所有路由函数（`route_after_executor`、`route_after_critic`）检测到此标志后强制跳转 `synthesizer`；Synthesizer 进入紧急模式直接拼接结果输出 | `token_budget.py` `get_budget_policy()`；`builder.py` 两个路由函数；`synthesizer.py` `synthesizer_node()` 中 `budget_ratio > 0.95` 分支 |

**三重保护的协作关系**：

```
任务开始
  │
  ├─ 迭代1: Planner → Executor → Critic → ... → Synthesizer
  │    ├─ 每步最多重试3次（MAX_RETRIES保护）
  │    ├─ 预算>95%时强制综合（预算保护）
  │    └─ 触发反思？→ 回Planner（iteration+1）
  │
  ├─ 迭代2: ... (iteration=1)
  ├─ 迭代3: ... (iteration=2)
  ├─ 迭代4: ... (iteration=3)
  ├─ 迭代5: ... (iteration=4)
  │
  └─ iteration >= 5（MAX_ITERATIONS保护）→ END，输出最终答案
```

---

## 6. 安全沙箱机制

Python 代码执行工具实现于 `tools/python_repl.py`，采用 AST 预检查 + 白名单沙箱双重安全机制，防止 LLM 生成的恶意代码破坏宿主环境。

### 6.1 AST 拦截黑名单（FORBIDDEN_AST_NODES）

在代码执行前，先用 `ast.parse()` 将代码解析为抽象语法树，通过 `SecurityChecker`（`ast.NodeVisitor` 子类）遍历所有节点，拦截以下 AST 节点类型：

| AST 节点类型 | 对应代码语法 | 拦截原因 |
|------|------|------|
| `ast.Import` | `import xxx` | 禁止运行时动态导入任意模块 |
| `ast.ImportFrom` | `from xxx import yyy` | 禁止运行时动态导入任意模块 |

### 6.2 禁止调用函数列表（FORBIDDEN_CALLS）

`SecurityChecker.visit_Call()` 检查函数调用节点，以下函数名即使能访问到也会被拦截：

| 禁止函数 | 拦截原因 |
|------|------|
| `__import__` | 动态导入，可绕过沙箱限制 |
| `exec` | 动态执行任意代码 |
| `eval` | 动态求值任意表达式 |
| `compile` | 编译代码字符串，可绕过 AST 检查 |
| `globals` | 访问全局命名空间 |
| `locals` | 访问局部命名空间 |
| `vars` | 访问对象属性字典 |
| `dir` | 列出对象所有属性 |
| `getattr` | 动态属性访问，可绕过沙箱 |
| `setattr` | 动态属性设置 |
| `delattr` | 动态属性删除 |
| `open` | 文件读写操作 |
| `input` | 标准输入 |
| `breakpoint` | 调试器 |
| `exit` | 退出解释器 |
| `quit` | 退出解释器 |

此外，`SecurityChecker.visit_Attribute()` 拦截所有 dunder 属性访问（如 `__builtins__`、`__globals__`、`__class__` 等），防止通过属性链逃逸沙箱。

### 6.3 白名单内置函数

沙箱通过自定义 `__builtins__` 字典暴露安全的内置函数，未列出的内置函数均不可访问：

| 分类 | 白名单函数 |
|------|------|
| 数学运算 | `abs`、`round`、`min`、`max`、`sum`、`pow`、`divmod` |
| 类型转换 | `int`、`float`、`str`、`bool`、`list`、`dict`、`tuple`、`set`、`frozenset` |
| 常用函数 | `range`、`len`、`sorted`、`reversed`、`enumerate`、`zip`、`map`、`filter`、`print`、`isinstance`、`type`、`any`、`all`、`format`、`repr`、`hash`、`bin`、`oct`、`hex`、`chr`、`ord`、`ascii`、`hasattr` |

> 注意：`hasattr` 被允许（只读属性检查），但 `setattr` / `delattr` / `getattr` 被禁止。

### 6.4 预导入模块

沙箱在宿主环境预先 import 好安全模块，将模块对象直接放入 `globals` 字典，代码可直接使用但无法导入新模块：

| 模块名 | 用途 |
|------|------|
| `math` | 数学运算（sqrt、factorial、pi 等） |
| `json` | JSON 序列化/反序列化 |
| `re` | 正则表达式 |
| `datetime` | 日期时间处理 |

### 6.5 安全执行流程

```
LLM 生成 Python 代码
  │
  ▼
第一步：AST 安全检查（check_code_safety）
  ├─ ast.parse() 解析为语法树
  ├─ SecurityChecker 遍历所有节点
  ├─ 检查 Import / ImportFrom 节点 → 拒绝
  ├─ 检查 Call 节点的函数名是否在 FORBIDDEN_CALLS → 拒绝
  ├─ 检查 Attribute 节点是否为 dunder 属性 → 拒绝
  └─ 有违规 → 返回违规列表，拒绝执行
  │
  ▼
第二步：沙箱执行（exec）
  ├─ 重新构建 safe_globals（每次调用都重建，防止命名空间污染）
  ├─ 替换 __builtins__ 为白名单字典
  ├─ 注入预导入模块对象（不暴露 __import__）
  ├─ 重定向 stdout / stderr 到 StringIO 缓冲区
  └─ exec(code, safe_globals)
  │
  ▼
返回执行结果或错误信息
```

---

## 7. 统一配置加载规则

### 7.1 当前配置架构

项目当前的所有超参数和配置项集中定义于 `config.py`，作为全局默认值统一生效。关键配置项包括：

| 配置分类 | 配置项 | 默认值 | 说明 |
|------|------|------|------|
| API 配置 | `DEEPSEEK_API_KEY` | 环境变量读取 | 从 `.env` 或平台环境变量注入，不硬编码 |
| API 配置 | `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek API 基础地址 |
| API 配置 | `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名称 |
| LLM 参数 | `LLM_TEMPERATURE` | 0.1 | 全局默认温度（被角色级温度覆盖） |
| LLM 参数 | `LLM_MAX_TOKENS` | 2048 | 单次调用最大输出 Token 数 |
| Token 预算 | `DEFAULT_TOKEN_BUDGET` | 50000 | 每个任务的默认 Token 预算上限 |
| Token 预算 | `BUDGET_ALLOCATION` | planner:0.15 / executor:0.50 / critic:0.20 / synthesizer:0.15 | 各角色预算分配比例 |
| 降级阈值 | `DEGRADE_THRESHOLD_1` | 0.70 | Level 1 降级阈值 |
| 降级阈值 | `DEGRADE_THRESHOLD_2` | 0.85 | Level 2 降级阈值 |
| 降级阈值 | `DEGRADE_THRESHOLD_3` | 0.95 | Level 3 降级阈值 |
| 执行参数 | `MAX_RETRIES` | 3 | Executor 单步最大重试次数 |
| 执行参数 | `MAX_ITERATIONS` | 5 | Plan-Execute-Reflect 最大循环次数 |
| Flask 配置 | `FLASK_HOST` | 127.0.0.1 | Flask 服务地址 |
| Flask 配置 | `FLASK_PORT` | 5000 | Flask 服务端口 |
| Flask 配置 | `FLASK_DEBUG` | True | Flask 调试模式 |

角色级温度参数定义于 `agents/llm_utils.py` 的 `ROLE_TEMPERATURES` 字典：

| 配置项 | 值 |
|------|------|
| `planner` | 0.3 |
| `executor` | 0.0 |
| `critic` | 0.1 |
| `synthesizer` | 0.5 |
| `default` | 0.1 |

### 7.2 实验配置覆盖机制（规划中）

未来将引入 `experiments/config.yaml`（即将创建），实现配置的分层管理：

```
配置优先级（从高到低）：
  experiments/config.yaml（实验覆盖配置）
      ↓ 覆盖
  config.py（全局默认值）
      ↓ 覆盖
  环境变量（API Key 等敏感信息）
```

**设计原则**：

1. **统一入口**：所有超参数（温度、预算、阈值、迭代次数等）和实验配置（benchmark 选择、任务集、消融开关等）统一从 `experiments/config.yaml` 读取，全局统一生效。
2. **默认值兜底**：`config.py` 中的参数作为默认值，`experiments/config.yaml` 中定义的参数可覆盖同名默认值，未定义的参数继续使用默认值。
3. **无硬编码**：所有可调参数通过配置文件管理，代码中不硬编码任何超参数，确保实验可复现、参数可追溯。
4. **消融实验支持**：`use_heuristics` 等消融开关通过配置文件控制，方便进行成本消融实验。

---

## 8. 项目核心创新点总结

### 创新1：固定 PECS 四角色 + Plan-Execute-Reflect 标准闭环

**痛点**：LangGraph 原生框架允许用户自由定义任意节点和边，导致多 Agent 系统的拓扑结构混乱、角色职责不清、难以复现和对比。

**方案**：固定四个标准角色（Planner-Executor-Critic-Synthesizer），形成 Plan → Execute → Critic → Synthesize 的标准闭环。每个角色有明确的输入/输出契约和职责边界，通过 `AgentState` 共享状态传递数据。区别于 LangGraph 原生的自定义混乱节点，PECS 提供了可复现、可对比、可解释的标准化多智能体协作范式。

**技术体现**：
- `graph/builder.py` 中固定注册 5 个节点（4 角色 + 1 重试节点），3 个条件路由函数
- `graph/state.py` 中 `AgentState` 明确定义每个字段的读写权限
- 每个角色的系统提示词（System Prompt）严格限定职责边界

### 创新2：业界少见的三级 Token 动态降级调度

**痛点**：传统 Agent 系统不管成本，反复调用 LLM 直到任务完成，导致简单任务花 10 元、复杂任务花 100 元，成本完全不可控。

**方案**：为每个任务设定 Token 预算上限，运行过程中实时追踪消耗，在三个阈值点自动触发降级：
- **70% 预算**：Critic 跳过低风险步骤的详细验证（快速模式，不调 LLM）
- **85% 预算**：Planner 合并剩余步骤（减少 LLM 调用次数）
- **95% 预算**：Synthesizer 直接用已有结果输出（紧急模式，停止执行）

**技术体现**：
- `graph/token_budget.py` 中 `get_budget_policy()` 统一生成调度策略，避免各角色重复硬编码阈值
- `config.py` 中三个降级阈值参数化，可配置
- 调度决策记录到 `scheduler_decisions`，可追溯分析
- 在保精度前提下显著降本（目标：单任务成本降低 30%）

### 创新3：Critic 纠错 + Synthesizer 全局修正双层反思机制

**痛点**：单 Agent 执行长链任务时容易出现"执行漂移"问题——中间步骤的小错误逐步累积，最终答案严重偏离用户意图，且无法自我纠正。

**方案**：设计双层反思机制：
- **第一层（Critic 纠错）**：Critic 对每步执行结果进行三维评分（准确性/一致性/完整性），不达标时生成重试反馈，Executor 根据反馈修正参数重新执行。解决单步级别的错误。
- **第二层（Synthesizer 全局修正）**：Synthesizer 综合所有步骤结果后，判断整体答案是否完整、是否存在矛盾、是否缺少明确结论。若需要反思，生成反思总结回传 Planner 重新规划，进入新一轮 Plan-Execute-Reflect 循环。解决全局级别的漂移。

**技术体现**：
- `agents/critic.py` 中 `critic_node()` 实现三维评分 + 重试反馈生成
- `agents/synthesizer.py` 中 `_should_reflect()` 多条件判断触发反思（答案过短、步骤失败、缺少结论等）
- `graph/builder.py` 中 `route_after_synthesizer` 实现反思循环路由
- 三重终止保护（MAX_ITERATIONS=5、MAX_RETRIES=3、预算95%强制终止）防止反思循环失控

---

## 9. 消融实验设计说明

### 9.1 两种消融模式

本项目设计了两类消融实验，确保实验单一变量严谨性：

**完全移除型消融**（验证角色存在必要性）：
- `full_pecs`：完整四角色（对照组）
- `no_critic`：移除 Critic 节点，Executor 直连 Synthesizer
- `no_synthesizer`：移除 Synthesizer，Executor 直接输出结果
- `single_agent`：退化为纯 ReAct 单智能体

**单变量功能关闭型消融**（保留节点，仅关闭核心功能）：
- `critic_no_reflect`：保留 Critic 评分功能，但阻断反思文本向 Synthesizer/Planner 传递，无修正闭环。验证反思机制本身的价值（vs 仅评分但不修正）
- `synthesizer_no_replan`：保留 Synthesizer 结果拼接功能，关闭计划重规划更新逻辑。验证动态重规划的价值（vs 静态计划执行到底）

### 9.2 消融对比逻辑

通过对比两种模式可以分离两个变量：
- `no_critic` vs `critic_no_reflect`：前者移除整个 Critic 节点，后者保留评分但不反馈。差异 = 反馈修正机制的价值
- `no_synthesizer` vs `synthesizer_no_replan`：前者移除整个 Synthesizer，后者保留综合但不重规划。差异 = 动态重规划的价值

---

## 10. 数据分层抽象设计

为解耦 Mock 数据集与官方数据集切换逻辑，新增 `datasets/` 抽象层：

```
datasets/
├── base_dataset.py           # 抽象基类 BaseDataset
├── gaia_mock_dataset.py      # GAIA Mock 数据集（28题内置样例）
├── gaia_official_dataset.py  # GAIA 官方数据集（HuggingFace 加载）
└── webshop_mock_dataset.py   # WebShop Mock 数据集（6题内置样例）
```

**设计原则**：
- `BaseDataset` 定义统一接口：`load_samples()`, `get_sample_by_id()`, `get_dataset_info()`, `evaluate_answer()`
- 所有评测脚本通过 `config.dataset_type` 切换数据集，无需修改业务代码
- `gaia_official_dataset.py` 支持从 HuggingFace 加载官方 GAIA 数据集，需配置 `HF_TOKEN`
- Mock 数据集直接复用 `benchmarks/gaia_eval.py` 中的 `GAIA_L1_SAMPLES`

---

## 11. 模块扩展接口

### 11.1 自定义 Critic

原生 `critic_node` 是函数式节点，通过包装模式实现自定义：

```python
from agents.critic import critic_node

class CustomCritic:
    def __init__(self, efficiency_weight=0.2):
        self.efficiency_weight = efficiency_weight

    def __call__(self, state: dict) -> dict:
        # 先调用原生 Critic 获取三维评分
        new_state = critic_node(state)
        # 追加自定义维度评分
        efficiency_score = self._evaluate_efficiency(state)
        # 重新计算综合分
        ...
        return new_state
```

完整示例见 `demos/custom_critic_override_demo.py`，展示：
- 继承原生评分逻辑 + 新增效率维度
- 用 `graph.add_node("critic", custom_critic)` 替换原生节点
- 构建自定义图并运行任务

### 11.2 批量任务执行

`src/batch_runner.py` 提供 `BatchRunner` 类：
- `run_tasks(queries)`：批量执行字符串列表
- `run_dataset(dataset, num_samples)`：从 `BaseDataset` 加载并执行
- 自动统计成功率、平均 Token、导出报告

### 11.3 全链路日志

`logger/graph_trace_logger.py` 提供：
- `GraphTraceLogger.log_node()`：实时打印节点信息
- `export_task_trace(state)`：导出单任务完整 Plan-Execute-Reflect 流程到 `results/traces/`

---

*本文档所有内容均从项目源代码中提取，未做任何虚构。代码版本对应仓库当前状态。*
