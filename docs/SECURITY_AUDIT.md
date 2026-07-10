# 安全审计报告

> pecs-multi-agent AST 安全沙箱渗透测试报告，验证沙箱对常见攻击向量的防护能力。

## 1. 审计范围

| 项 | 说明 |
|----|------|
| 审计对象 | `tools/python_repl.py` — AST 安全沙箱 |
| 审计目标 | 验证沙箱对 LLM 生成恶意代码的防护能力 |
| 审计方法 | 黑盒渗透测试 + 白盒代码审查 |
| 测试用例数 | 12 种攻击向量 + 4 种合法代码 |

## 2. 防护机制概述

### 第一层：AST 预检查

在代码执行前，用 `ast.parse()` 解析为语法树，通过 `SecurityChecker(ast.NodeVisitor)` 遍历所有节点，拦截：

| 拦截类型 | AST 节点 | 说明 |
|----------|----------|------|
| 导入语句 | `Import`, `ImportFrom` | 禁止 `import os` / `from os import system` |
| 危险函数调用 | `Call` (Name) | 禁止 `__import__` / `exec` / `eval` / `compile` / `open` / `globals` / `locals` / `vars` / `dir` / `getattr` / `setattr` / `delattr` / `input` / `breakpoint` / `exit` / `quit` |
| 危险属性调用 | `Call` (Attribute) | 禁止调用 `__` 开头的属性 |
| dunder 属性访问 | `Attribute` | 禁止访问 `__builtins__` / `__globals__` 等 |

### 第二层：白名单沙箱

执行时通过受限的 `exec()` 运行，`__builtins__` 被替换为白名单字典：

- **允许的内置函数**：`abs`, `round`, `min`, `max`, `sum`, `pow`, `int`, `float`, `str`, `bool`, `list`, `dict`, `tuple`, `set`, `range`, `len`, `sorted`, `reversed`, `enumerate`, `zip`, `map`, `filter`, `print`, `isinstance`, `type`, `any`, `all`, `format`, `repr`, `hash`, `bin`, `oct`, `hex`, `chr`, `ord`, `ascii`, `hasattr`
- **允许的模块**：`math`, `json`, `re`, `datetime`
- **禁止的**：`__import__`（不在 `__builtins__` 中）

## 3. 渗透测试结果

### 3.1 攻击向量测试

| # | 攻击向量 | 攻击代码 | 拦截层 | 结果 |
|---|----------|----------|:------:|:----:|
| 1 | 导入 os 执行系统命令 | `import os; os.system('whoami')` | AST | ✅ 拦截 |
| 2 | from import 导入 | `from os import system; system('rm -rf /')` | AST | ✅ 拦截 |
| 3 | __import__ 动态导入 | `__import__('os').system('id')` | AST | ✅ 拦截 |
| 4 | exec 执行任意代码 | `exec("import os; os.system('id')")` | AST | ✅ 拦截 |
| 5 | eval 求值 | `eval('__import__("os").getcwd()')` | AST | ✅ 拦截 |
| 6 | open 读写文件 | `open('/etc/passwd').read()` | AST | ✅ 拦截 |
| 7 | getattr 绕过沙箱 | `getattr(__builtins__, '__import__')('os')` | AST | ✅ 拦截 |
| 8 | dunder 属性访问 | `object().__class__.__bases__[0].__subclasses__()` | AST | ✅ 拦截 |
| 9 | compile 编译代码 | `compile('import os', '', 'exec')` | AST | ✅ 拦截 |
| 10 | globals 访问命名空间 | `globals()['__builtins__']['__import__']('os')` | AST | ✅ 拦截 |
| 11 | setattr 修改属性 | `setattr(obj, '__class__', type('X',(),{}))` | AST | ✅ 拦截 |
| 12 | 通过 print 写入文件 | `print('data', file=open('/tmp/x','w'))` | AST | ✅ 拦截 |

**拦截率：12/12 = 100%**

### 3.2 合法代码测试

| # | 测试代码 | 预期 | 结果 |
|---|----------|------|:----:|
| 1 | `print(math.sqrt(144))` | 12.0 | ✅ 通过 |
| 2 | `data = json.loads('{"a":1}'); print(data["a"])` | 1 | ✅ 通过 |
| 3 | `print(re.findall(r'\d+', 'a1b2'))` | ['1','2'] | ✅ 通过 |
| 4 | `print(datetime.datetime.now().year)` | 2026 | ✅ 通过 |

**通过率：4/4 = 100%**

### 3.3 边界情况测试

| # | 测试场景 | 代码 | 结果 |
|---|----------|------|:----:|
| 1 | 空代码 | `""` | ✅ 返回"缺少 code 参数" |
| 2 | 语法错误 | `print(` | ✅ AST 解析阶段拦截 |
| 3 | 无限循环 | `while True: pass` | ⚠️ 未设超时，需手动中断 |
| 4 | 内存耗尽 | `[0] * 10**9` | ⚠️ 未设内存限制 |
| 5 | 递归溢出 | `def f(): f()` | ✅ Python 自带 RecursionError |
| 6 | 命名空间污染 | `x = 1`（连续两次调用） | ✅ 每次调用重建 safe_globals |

## 4. 已知风险

| 风险 | 等级 | 说明 | 缓解措施 |
|------|:---:|------|----------|
| 无执行超时 | 中 | `while True` 等死循环代码会阻塞 | 添加 `signal.alarm()` 超时机制（未来优化） |
| 无内存限制 | 中 | 大列表/大字典可能耗尽内存 | 添加 `resource.setrlimit()` 限制（未来优化） |
| print 输出捕获 | 低 | 通过 print 向 stdout 写入大量数据 | 已用 StringIO 捕获，限制输出长度（未来优化） |
| AST 绕过新攻击 | 低 | 未来可能发现新的 AST 绕过方式 | 持续更新 FORBIDDEN_CALLS 黑名单 |

## 5. 历史漏洞修复记录

### 漏洞 #1：白名单包含 `__import__`（已修复）

**发现时间：** 项目早期版本

**漏洞描述：** 早期版本的白名单 `__builtins__` 中包含了 `__import__` 函数，导致攻击者可以通过 `__import__('os').system('rm -rf /')` 绕过沙箱执行任意系统命令。

**修复方案：**
1. 从 `__builtins__` 白名单中移除 `__import__`
2. 添加 AST 预检查作为第一层防护，拦截 `Import` / `ImportFrom` 节点
3. 添加 `FORBIDDEN_CALLS` 黑名单，拦截 `__import__` 函数调用

**修复验证：** 渗透测试用例 #3 验证通过。

### 漏洞 #2：未拦截 dunder 属性访问（已修复）

**发现时间：** 代码审查阶段

**漏洞描述：** 早期版本未拦截 `__class__` / `__bases__` / `__subclasses__` 等 dunder 属性访问，攻击者可通过 Python 对象继承链访问到危险类。

**修复方案：** 在 `SecurityChecker.visit_Attribute` 中添加 dunder 属性拦截规则。

**修复验证：** 渗透测试用例 #8 验证通过。

## 6. 安全建议

1. **添加执行超时**：使用 `signal.alarm()` 或 `multiprocessing.Process.terminate()` 限制单次执行时间
2. **添加内存限制**：使用 `resource.setrlimit(RLIMIT_AS, ...)` 限制进程内存
3. **定期更新黑名单**：关注 Python 安全社区，及时更新 `FORBIDDEN_CALLS`
4. **日志审计**：记录所有被拦截的代码，用于安全分析和攻击溯源
5. **生产环境隔离**：在 Docker 容器中执行用户代码，增加一层 OS 级隔离

## 7. 审计结论

PECS 安全沙箱在当前测试范围内（12 种攻击向量）实现了 100% 拦截率，合法代码 100% 通过率。两层防护机制（AST 预检查 + 白名单沙箱）有效覆盖了常见攻击路径。

已知风险（无超时、无内存限制）属于资源类问题，不影响安全防护有效性，建议在后续版本中修复。

**复现方法：** 运行 `python demos/security_sandbox_demo.py` 查看完整渗透测试演示。
