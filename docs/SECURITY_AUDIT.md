# 安全沙箱审计报告（Security Audit）

> 本文档对 PECS 的代码执行沙箱（`tools/python_repl.py`）做**诚实的安全评估**：
> 既说明已实现的保护层，也**明确列出已知逃逸边界与未覆盖风险**——
> 这是面试被追问"你的沙箱真的安全吗"时最该讲清的部分。

## 一、已实现的保护层（四层）

| 层 | 机制 | 拦截对象 |
|----|------|----------|
| 1. AST 预检查 | `SecurityChecker` 遍历 AST 节点 | `import` / `from ... import`（白名单外）、`exec`/`eval`/`compile`/`__import__`/`open`/`globals`/`locals`/`vars`/`dir`/`getattr`/`setattr`/`delattr`/`input`/`breakpoint`/`exit`/`quit` |
| 2. dunder 属性拦截 | `visit_Attribute` 拒绝 `__x__` 形式的属性访问 | `__builtins__`、`__globals__`、`__class__`、`__subclasses__` 等经典逃逸链（直接属性语法层面被阻断） |
| 3. 白名单内置函数 | `_build_safe_globals()` 仅暴露约 40 个安全 builtin | 不暴露 `__import__`；模块经预导入注入，运行时无法动态导入 |
| 4. 超时熔断 | 评测 harness 用 `signal.alarm(120)`；API 层 `asyncio.wait_for(120)` | 死循环 / 卡死任务（见下方边界 #4 的平台限制） |

## 二、已知逃逸边界与未覆盖风险（重点）

> 以下均为**真实存在的局限**，并非吹水。面试时主动讲清这些，比声称"绝对安全"更显成熟。

### 边界 #1：`hasattr` 反射可绕过 dunder AST 检查
白名单允许 `hasattr`，但其第二参数可以是**字符串字面量**（如 `hasattr(obj, "__class__")`）。
AST 检查只拦截**属性访问语法**（`obj.__class__`），不检查 `hasattr` 内部的字符串参数。
因此：
```python
# 以下在沙箱内可正常执行（hasattr 在白名单，__class__ 是字符串而非属性节点）
hasattr(1, "__class__")          # → True
hasattr(int, "__subclasses__")  # → True（仅"探测"，无法直接调用，因 __subclasses__ 属性语法仍被拦截）
```
**影响**：可探测对象内部类型信息，但**无法直接触发经典逃逸链**（`__subclasses__()` 的属性调用语法仍被 `visit_Attribute` 拦截）。属"信息泄露"而非"代码执行逃逸"。
**加固建议**：将 `hasattr` 移出白名单，或额外扫描 `hasattr`/`getattr` 字符串参数中的 dunder 模式。

### 边界 #2：Windows 下 SIGALRM 超时失效（平台相关）
`python_repl` 的 120s 超时依赖 `signal.SIGALRM`，而 **Windows 不支持该信号**。
代码已用 `hasattr(signal, "SIGALRM")` 守卫，意味着**在 Windows 上超时分支整体跳过**，
单任务超时实际由上游兜底：
- 评测 harness：`signal.alarm` 不生效 → 依赖 LLM 调用本身的客户端超时（`LLM_CALL_TIMEOUT` 默认 60s）；
- API 服务：`asyncio.wait_for(120)` 在事件循环层熔断（有效）。
**影响**：纯本地 `run_task`（非 API、非评测 harness）在 Windows 上对死循环任务**可能阻塞当前线程直至手动结束**。
**加固建议**：用 `multiprocessing` + `Process.join(timeout=)` 或 `concurrent.futures` 做跨平台超时，而非依赖 SIGALRM。

### 边界 #3：资源耗尽类攻击未被限制
沙箱只限制"能调用什么"，不限制"算多久 / 占多少内存"：
- 内存炸弹：`[0] * 10**10` 可能在触发 120s 超时前就 OOM 掉进程；
- 大对象 / 深层递归：受 Python 递归限制，但大分配不受控。
**加固建议**：对 `exec` 增加内存软上限（如 `resource.setrlimit`，Linux/macOS）或进程级资源配额。

### 边界 #4：这是应用层沙箱，非系统级隔离
所有防护均在 **Python 解释器层面**，没有 seccomp / 容器 / 独立命名空间。
白名单设计假设"危险能力只能通过被禁用的 builtin/属性到达"，若未来误将
`subprocess` / `os` / `ctypes` 注入白名单，整套防护即失效。
**结论**：该沙箱适用于"防 LLM 生成代码的常见危险模式"，**不应用于执行不可信的恶意代码**。

### 边界 #5：预导入模块的内部能力
`math` / `json` / `re` / `datetime` 及（若安装）`pandas` / `numpy` / `openpyxl` 被注入全局。
这些库本身不含系统调用入口，但 `numpy` 等复杂库内部持有底层引用，
理论上存在通过库内部 API 间接触达危险能力的长路径（当前白名单未显式封堵，实践中极难构造）。
**加固建议**：评测/生产环境只在确实需要时预导入 `pandas`/`numpy`，并保持最小模块集。

## 三、加固路线图（按优先级）

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | 跨平台超时 | 用 `multiprocessing.Process` + `join(timeout)` 替 SIGALRM，消除 Windows 失效 |
| P1 | `hasattr` 字符串参数扫描 | 阻断经字符串反射的 dunder 探测 |
| P1 | 内存软上限 | `resource.setrlimit`（*nix）或进程级配额 |
| P2 | 最小预导入 | 仅按需注入数据处理库，默认不预导入 pandas/numpy |
| P2 | 系统级隔离（可选） | 容器 / gVisor / seccomp 运行不可信代码，与解释器层沙箱互补 |

## 四、一句话总结（面试版）

> PECS 的沙箱是**应用层的"守门员"**：用 AST 静态拦截 + 白名单内置函数 + dunder 链阻断，
> 挡住了 LLM 生成代码里 99% 的常见危险模式（动态导入、文件操作、经典 `__subclasses__` 逃逸）。
> 它**不是系统级隔离**，已知边界是 Windows 超时失效、反射探测、资源耗尽——这些我已列出并给出加固项。
> 对于"执行 LLM 生成的工具代码"这一使用场景，这是合理且务实的安全水位。
