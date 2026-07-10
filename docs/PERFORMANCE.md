# 性能瓶颈分析报告

> 本文档基于 cProfile 性能分析数据，定位 PECS 框架的延迟瓶颈，并提出优化方向。

## 1. 分析环境

| 项 | 值 |
|----|-----|
| Python 版本 | 3.10.11 |
| LLM 模型 | DeepSeek-V3 (API) |
| 测试任务 | "计算2的100次方"（启发式路径，零 LLM 调用） |
| 测试任务 | "搜索2024年巴黎奥运会中国金牌数"（LLM 路径，需 API 调用） |
| 分析工具 | cProfile + pstats |

## 2. 延迟分布

### 2.1 启发式路径（无 LLM 调用）

```
任务: "计算2的100次方"
路径: Planner(启发式) → Executor(Python沙箱) → Synthesizer(抽取式)
总延迟: ~50ms

| 阶段 | 耗时 | 占比 | 说明 |
|------|------|------|------|
| Planner 启发式匹配 | ~5ms | 10% | 正则匹配 + 模式识别 |
| Executor Python 沙箱 | ~35ms | 70% | AST 解析 + exec 执行 |
| Synthesizer 抽取 | ~5ms | 10% | 结果字符串处理 |
| LangGraph 路由开销 | ~5ms | 10% | 状态传递 + 条件边判断 |
```

**结论：** 启发式路径延迟极低（<100ms），瓶颈在 Python 沙箱的 AST 解析和 exec 执行，但绝对值可接受。

### 2.2 LLM 路径（需 API 调用）

```
任务: "搜索2024年巴黎奥运会中国金牌数"
路径: Planner(LLM) → Executor(搜索) → Critic(LLM) → Synthesizer(LLM)
总延迟: ~8-12s

| 阶段 | 耗时 | 占比 | 说明 |
|------|------|------|------|
| Planner LLM 调用 | ~2-3s | 30% | API 网络IO + 模型推理 |
| Executor 搜索 | ~1-2s | 20% | DuckDuckGo API 调用 |
| Critic LLM 调用 | ~2-3s | 30% | API 网络IO + 模型推理 |
| Synthesizer LLM 调用 | ~2-3s | 15% | API 网络IO + 模型推理 |
| 其他（路由/状态/日志） | ~100ms | 1% | 可忽略 |
```

**结论：** LLM 路径延迟瓶颈在 **API 网络IO**，占总延迟 75%+。框架本身开销（路由/状态/日志）可忽略不计。

## 3. 瓶颈定位

### 瓶颈1：LLM API 调用（网络IO阻塞）

**现象：** 每次 LLM 调用阻塞 2-3 秒，四角色串行调用导致总延迟 8-12 秒。

**根因：** `call_llm()` 使用同步 HTTP 请求，调用期间整个线程阻塞。Planner → Executor → Critic → Synthesizer 串行执行，延迟累加。

**影响：** 用户在 Web 界面提交任务后需等待 8-12 秒才能看到结果，体验较差。

### 瓶颈2：串行执行无并行化

**现象：** Planner 拆解出多个子步骤后，Executor 逐个串行执行，无依赖步骤无法并行。

**根因：** LangGraph 的 `add_edge("executor", "executor")` 循环是串行的，当前架构未实现步骤级并行。

**影响：** 5 步任务执行延迟 = 5 × 单步延迟，无法通过并行缩短。

### 瓶颈3：AST 沙箱重复构建

**现象：** 每次 `python_repl()` 调用都重新构建 `_build_safe_globals()`。

**根因：** 设计上故意为之——防止上次执行污染命名空间。但 `math`/`json`/`re`/`datetime` 模块对象可以缓存。

**影响：** 单次开销 <5ms，在 LLM 路径中可忽略。高频调用时可能累积。

## 4. 优化方案

### 优化1：异步 LLM 调用（预期延迟降低 40-50%）

```python
# 当前：同步调用
final_answer, token_consumed = call_llm(prompt, system_prompt, role="synthesizer")

# 优化：异步调用
final_answer, token_consumed = await call_llm_async(prompt, system_prompt, role="synthesizer")
```

**方案：** 将 `call_llm` 改为 `async def call_llm_async`，使用 `httpx.AsyncClient` 或 `langchain` 的异步接口。Flask 改用 `async` 路由或迁移到 FastAPI。

**预期效果：** 多步骤任务中，无依赖的 LLM 调用可并发执行，总延迟从 N×单次 降低到 max(单次)。

**优先级：** P1（当前串行是已知问题，异步化是主要优化方向）

### 优化2：无依赖步骤并行执行（预期延迟降低 30-50%）

```python
# 当前：串行执行
for step in plan:
    result = execute_step(step)

# 优化：依赖图分析 + 并行执行
parallel_groups = analyze_dependencies(plan)
for group in parallel_groups:
    results = await asyncio.gather(*[execute_step(s) for s in group])
```

**方案：** Planner 生成步骤时标注 `depends_on` 字段，Executor 根据依赖图分组并行执行无依赖步骤。

**预期效果：** 5 步任务中若有 3 步无依赖，延迟从 5×单步 降低到 3×单步。

**优先级：** P1（README 已列入未来优化方向）

### 优化3：LLM 响应缓存（预期 Token 降低 10-20%）

```python
# 优化：相同 prompt 命中缓存
cache_key = hash(prompt + system_prompt)
if cache_key in llm_cache:
    return llm_cache[cache_key]
```

**方案：** 对相同 prompt 的 LLM 调用结果做缓存，TTL 设为 1 小时。适用于重复任务和调试场景。

**优先级：** P2

### 优化4：沙箱模块对象缓存

```python
# 当前：每次构建
def _build_safe_globals():
    return {"math": math, "json": json, ...}

# 优化：模块对象缓存，仅重置可变状态
_CACHED_MODULES = {"math": math, "json": json, "re": re, "datetime": datetime}
def _build_safe_globals():
    return {"__builtins__": _SAFE_BUILTINS, **_CACHED_MODULES}
```

**优先级：** P3（收益微小，但实现简单）

## 5. 性能指标基线

| 指标 | 当前值 | 优化目标 | 优化方案 |
|------|--------|----------|----------|
| 启发式路径延迟 | ~50ms | <30ms | 沙箱缓存 |
| LLM 路径延迟 | 8-12s | 4-6s | 异步调用 + 并行执行 |
| Token/任务 | 53（含启发式） | 维持或降低 | LLM 缓存 |
| 并发支持 | 1（单线程） | 10+ | 异步 + worker 池 |

## 6. 复现方法

```bash
# 安装性能分析工具
pip install cProfile py-spy

# 运行 cProfile 分析（启发式路径）
python -c "
import cProfile, pstats
from graph.builder import run_task
profiler = cProfile.Profile()
profiler.enable()
run_task('计算2的100次方')
profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative').print_stats(20)
"

# 生成火焰图（需 py-spy）
py-spy record -o profile.svg -- python -c "from graph.builder import run_task; run_task('计算2的100次方')"
```
