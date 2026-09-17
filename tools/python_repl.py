"""
Python 代码执行工具

Executor 可以让 LLM 生成 Python 代码，然后在这里安全执行。
用于计算、数据处理、逻辑推理等需要精确计算的任务。

安全措施：
1. AST 预检查：拦截 import / exec / eval / __import__ 等危险调用
2. 白名单沙箱：只暴露安全的内置函数和预导入的模块
3. 不暴露 __import__：无法在运行时动态导入任意模块
"""
import io
import ast
import os
import sys
import json
import traceback
import math
import re
import datetime

# ========== 沙箱执行方式配置 ==========
# 是否用独立子进程执行（默认开启）。子进程是唯一能**真中断死循环**的方案：
# Python 无法强杀线程，`while True: pass` 会永久泄漏线程并吃空线程池。
# 若某环境不允许起子进程（沙箱限制/CI 策略），设 PEC_SANDBOX_PROCESS_ISOLATION=0 回退到
# 进程内执行（此时仍受 AST 检查与白名单保护，但失去"可中断"能力）。
_PROCESS_ISOLATION_ENABLED = os.getenv("PEC_SANDBOX_PROCESS_ISOLATION", "1") != "0"

# 单次沙箱执行墙钟上限（秒）。仅在子进程模式下生效——进程内模式没有中断手段，
# 设了也拦不住死循环，所以不假装有这个能力。
SANDBOX_TIMEOUT_SEC = float(os.getenv("PEC_SANDBOX_TIMEOUT", "15"))

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 允许在沙箱中安全使用的第三方数据处理库（已预导入，无需 import）
_SANDBOX_ALLOWED_MODULES = {}


def _load_sandbox_modules():
    """预导入评测需要的数据处理库到沙箱全局命名空间。"""
    global _SANDBOX_ALLOWED_MODULES
    if _SANDBOX_ALLOWED_MODULES:
        return _SANDBOX_ALLOWED_MODULES
    aliases = {
        "pandas": "pd",
        "numpy": "np",
        "openpyxl": None,
    }
    loaded = {}
    for mod_name, alias in aliases.items():
        try:
            mod = __import__(mod_name)
            if alias:
                loaded[alias] = mod
            else:
                loaded[mod_name] = mod
        except ImportError:
            continue
    _SANDBOX_ALLOWED_MODULES = loaded
    return loaded


# 仅允许这些顶层模块被 import（剥离语句，因为已预导入）
# 允许 import 的模块根名（这些模块已在沙箱内预导入，import 语句会被剥离）
#
# ⚠️ 2026-09-17 移除了 openpyxl：它在本项目里**只用于读写 Excel 文件**，属于纯 IO 库，
# 没有任何"计算"能力可留在沙箱里；文件解析已由 file_parse 工具完成（那道流程有路径守卫）。
# 保留它只会给沙箱多开一条文件读写通道。移除后 `import openpyxl` 会被明确拒绝，
# 而不是静默地让代码绕过沙箱去读文件。
_ALLOWED_IMPORT_ROOTS = {"pandas", "numpy", "math", "json", "re", "datetime"}


# ========== 安全检查：AST 级别拦截 ==========

# 禁止的 AST 节点类型
FORBIDDEN_AST_NODES = (
    ast.Import,          # import xxx
    ast.ImportFrom,      # from xxx import yyy
)

# 禁止调用的函数名（即使能访问到也拦截）
FORBIDDEN_CALLS = {
    "__import__",        # 动态导入
    "exec",              # 动态执行
    "eval",              # 动态求值
    "compile",           # 编译代码
    "globals",           # 访问全局命名空间
    "locals",            # 访问局部命名空间
    "vars",              # 访问对象属性
    "dir",               # 列出属性
    "getattr",           # 动态属性访问（可绕过沙箱）
    "setattr",           # 动态属性设置
    "delattr",           # 动态属性删除
    "open",              # 文件操作
    "input",             # 标准输入
    "breakpoint",        # 调试器
    "exit",              # 退出解释器
    "quit",              # 退出解释器
}

# ========== 库自带的文件 IO 拦截（2026-09-17 补充）==========
#
# 为什么必须有这一层：上面的 FORBIDDEN_CALLS 与白名单 builtins 只能管住 **builtins 面**
# （open / exec / __import__ …）。而沙箱预导入了 pandas / numpy，这两个库**自带完整的
# 文件读写能力**——实测（修复前）：
#     pd.read_csv("C:/Windows/win.ini")   → 成功读出系统文件内容
#     pd.DataFrame({...}).to_csv("x.csv") → 文件真的落地
#     而 open("x.csv","w") 同时被正确拦截（对照组）
# 即：AST 拦截被库能力**完全绕过**，可读写任意文件（含 .env / 密钥），
# 且 pd.read_pickle 是 pickle 反序列化，配合写能力可构成 RCE 链。
#
# 拦截策略（区分"写文件"与"内存转换"，避免误伤）：
#   - read_* 前缀：pandas 的 read_* 全部是 IO 读取，可安全按前缀拦；
#     注意只在**属性调用**（x.read_csv(...)）上拦，不拦裸函数名，
#     否则会误伤用户自定义的 def read_data()。
#   - to_* 必须逐一枚举：to_dict / to_list / to_string / to_numpy / to_records 等
#     是纯内存转换，不能按前缀拦（否则把正常计算也堵死）。
FORBIDDEN_IO_ATTRS = {
    # ---- pandas 写文件 ----
    "to_csv", "to_excel", "to_json", "to_parquet", "to_pickle", "to_sql",
    "to_hdf", "to_feather", "to_orc", "to_stata", "to_clipboard", "to_gbq",
    "to_markdown", "to_xml", "to_html",
    # ---- pandas 读文件（read_* 由前缀规则覆盖，这里补齐非 read_ 开头的）----
    "read_pickle",
    # ---- pandas 的 IO 类（构造即绑定文件路径）----
    "ExcelWriter", "ExcelFile", "HDFStore",
    # ---- numpy 读写 ----
    "load", "save", "savez", "savez_compressed", "savetxt", "loadtxt",
    "genfromtxt", "fromfile", "tofile", "memmap",
}

# 按前缀拦截的属性名（仅用于属性调用，见上方说明）
FORBIDDEN_IO_ATTR_PREFIXES = ("read_",)


def _is_forbidden_io_attr(attr_name: str) -> bool:
    """属性名是否属于被禁的库级文件 IO。

    抽成函数是为了让 visit_Call 与 visit_Attribute 共用**同一判据**——两处各写一份的话，
    将来加规则容易只改一处。本项目已有先例：`_ERROR_MARKERS` 漏加 "工具执行失败" 前缀，
    导致失败被静默判成成功（契约单侧失效）。这类"判据不一致"就是静默失败的温床。
    """
    return attr_name in FORBIDDEN_IO_ATTRS or attr_name.startswith(FORBIDDEN_IO_ATTR_PREFIXES)


class SecurityChecker(ast.NodeVisitor):
    """
    AST 遍历器：检查代码中是否包含危险操作

    在 exec() 之前先解析代码为 AST，遍历所有节点，
    如果发现 import 语句或危险函数调用，直接拒绝执行。
    """
    def __init__(self):
        self.violations = []

    def visit_Import(self, node):
        # 白名单内的安全模块（pandas/numpy/openpyxl 等）允许，稍后由 _strip_allowed_imports 剥离
        for n in node.names:
            if n.name.split(".")[0] not in _ALLOWED_IMPORT_ROOTS:
                self.violations.append(f"第{node.lineno}行: 禁止导入模块 '{n.name}'（沙箱仅允许 {sorted(_ALLOWED_IMPORT_ROOTS)}）")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        mod = node.module or ""
        if mod.split(".")[0] not in _ALLOWED_IMPORT_ROOTS:
            self.violations.append(f"第{node.lineno}行: 禁止从 '{mod}' 导入（沙箱仅允许 {sorted(_ALLOWED_IMPORT_ROOTS)}）")
        self.generic_visit(node)

    def visit_Call(self, node):
        # 检查函数调用：func 是 Name 节点时，检查函数名是否在黑名单中
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in FORBIDDEN_CALLS:
                self.violations.append(f"第{node.lineno}行: 禁止调用 '{func_name}()'")
            # hasattr 反射绕过防护：visit_Attribute 只拦 obj.__x__ 这种属性访问写法，
            # 拦不住 hasattr(obj, "__x__") 这种函数式反射。这里对 hasattr 的第二个参数
            # （属性名字符串）做 dunder 检查，防止通过反射摸到 __class__/__builtins__ 等。
            if func_name == "hasattr" and len(node.args) >= 2:
                second = node.args[1]
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    attr = second.value
                    if attr.startswith("__") and attr.endswith("__"):
                        self.violations.append(
                            f"第{node.lineno}行: 禁止通过 hasattr 探测双下划线属性 '{attr}'"
                        )
        # 检查属性调用：如 obj.__import__、obj.system、pd.read_csv 等
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name.startswith("__") or attr_name in FORBIDDEN_CALLS:
                self.violations.append(f"第{node.lineno}行: 禁止调用属性 '{attr_name}'")
            elif _is_forbidden_io_attr(attr_name):
                self.violations.append(
                    f"第{node.lineno}行: 禁止调用文件 IO 方法 '{attr_name}'"
                    "（沙箱不允许读写文件，如需数据请让 file_parse 工具先行解析）"
                )
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # 拦截 dunder 属性访问（如 __builtins__、__globals__ 等）
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.violations.append(f"第{node.lineno}行: 禁止访问 dunder 属性 '{node.attr}'")
        # 拦截文件 IO 方法的**属性取值**（如 f = pd.read_csv 后再调用，绕过 visit_Call）
        elif _is_forbidden_io_attr(node.attr):
            self.violations.append(
                f"第{node.lineno}行: 禁止访问文件 IO 方法 '{node.attr}'"
            )
        self.generic_visit(node)


def _strip_allowed_imports(code: str):
    """
    剥离白名单内的 import 语句（模块已预导入，无需 import）。

    返回 (清理后的代码, 拒绝原因 or None)
    - 若代码导入了白名单外的危险模块 -> 返回 (None, 拒绝原因)
    - 否则移除所有 import/import-from 语句后返回清理代码
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return None, f"语法错误: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                if n.name.split(".")[0] not in _ALLOWED_IMPORT_ROOTS:
                    return None, f"禁止导入模块 '{n.name}'（沙箱仅允许 {sorted(_ALLOWED_IMPORT_ROOTS)}）"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.split(".")[0] not in _ALLOWED_IMPORT_ROOTS:
                return None, f"禁止从 '{mod}' 导入（沙箱仅允许 {sorted(_ALLOWED_IMPORT_ROOTS)}）"

    # 移除顶层 import 语句（模块已在沙箱中预导入）
    new_body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    tree.body = new_body
    try:
        return ast.unparse(tree), None
    except Exception:
        # 某些边缘语法 ast.unparse 不支持，回退到逐行剥离
        import re as _re
        cleaned = [
            ln for ln in code.split("\n")
            if not _re.match(r"^\s*(import\s|from\s+\w+\s+import\b)", ln)
        ]
        return "\n".join(cleaned), None


def check_code_safety(code: str) -> list:
    """
    检查代码安全性

    返回违规列表，空列表表示安全
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"语法错误: {e}"]

    checker = SecurityChecker()
    checker.visit(tree)
    return checker.violations


# ========== 安全沙箱：预导入模块 + 白名单内置函数 ==========

def _make_restricted_import():
    """生成一个「只允许导入沙箱白名单模块」的 __import__。

    为什么沙箱需要 __import__（看起来与"不暴露 __import__"矛盾）：
        numpy / pandas 这类库内部会在**首次调用某些 API 时惰性导入自己的子模块**
        （如 `np.array([1,2]).sum()` 会触发 numpy 内部的模块加载）。沙箱的
        `__builtins__` 是白名单字典、不含 __import__，于是这些调用直接抛
        `KeyError: '__import__'` —— 表现为"基础 numpy 操作不可用"。
        实测（修复前）：`np.array([1,2,3]).sum()` 报 KeyError，而
        `pd.DataFrame(...).a.sum()` 正常 —— 差别只在后者不触发惰性导入。

    安全性如何保证：
        按**模块根名**白名单放行（numpy / pandas / math / json / re / datetime）。
        这些库没有能直接获取 shell 或文件句柄的子模块；而 `__import__("os")`
        这类请求会在 root 检查处被拒绝。同时 **AST 层仍然拦截用户显式调用
        `__import__`**（见 FORBIDDEN_CALLS），所以用户代码拿不到这个函数，
        只有库内部的合法惰性导入会用到它——两道防线各管一层。
    """
    def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = (name or "").split(".")[0]
        if root not in _ALLOWED_IMPORT_ROOTS:
            raise ImportError(f"沙箱禁止导入模块 '{name}'")
        return __import__(name, globals, locals, fromlist, level)
    return _restricted_import


def _make_buffer_print(buffer):
    """生成一个"只写往指定 buffer"的 print，用于替代进程级 sys.stdout 替换。

    为什么不用 print(..., file=buffer) 的闭包包装：语义要尽量贴近内置 print，
    但**忽略 file 参数**——否则沙箱代码可用 `print(x, file=某对象)` 把输出导向任意处。
    str() 转换与内置 print 一致。
    """
    def _print(*objects, sep=" ", end="\n", file=None, flush=False):
        buffer.write(sep.join(str(o) for o in objects) + end)
    return _print


def _build_safe_globals(out_buffer):
    """
    构建安全的执行环境

    核心思路：
    1. 预先在沙箱外 import 好需要的模块，把模块对象直接放进 globals
    2. 不暴露 __import__，代码无法在运行时动态导入其他模块
    3. 内置函数用白名单，只暴露安全的函数
    4. **print 改为写入本次调用的局部 buffer**（见下）

    参数:
        out_buffer: 本次调用专属的输出缓冲（io.StringIO）。
                    print 只往它写，不再替换进程级的 sys.stdout。

    为什么必须传 buffer 进来（2026-09-17 修复）：
        旧实现是「保存 sys.stdout → 替换成 buffer → exec → finally 恢复」。但 sys.stdout
        是**进程全局状态**，而 executor 用线程池并发跑任务，于是：
          - 并发竞态：实测拉长执行窗口后 8 并发有 4 个输出串味（A 拿到 B 的 print），
            错误数据被当作工具结果喂给 LLM；其他线程的裸输出还会漏进进程 stdout。
          - 更糟的是死循环兜不住：`while True: pass` 时 exec 永不返回 → finally 永不执行
            → sys.stdout **永久指向那个 StringIO**，整个进程从此失去输出能力（日志静默失效），
            实测确认（连主线程的 print 都被吞掉）。
        改为注入局部 print 后，输出捕获不再依赖任何全局状态：并发天然隔离，
        死循环也不会污染进程 stdout（最多泄漏一个线程，由外层超时兜底）。
    """
    g = {
        "__builtins__": {
            # 数学运算
            "abs": abs, "round": round, "min": min, "max": max,
            "sum": sum, "pow": pow, "divmod": divmod,
            # 类型转换
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "dict": dict, "tuple": tuple, "set": set,
            "frozenset": frozenset,
            # 常用函数
            "range": range, "len": len, "sorted": sorted, "reversed": reversed,
            "enumerate": enumerate, "zip": zip, "map": map, "filter": filter,
            # print 绑定到本次调用的局部 buffer（不是进程 stdout）
            "print": _make_buffer_print(out_buffer),
            "isinstance": isinstance, "type": type,
            "any": any, "all": all, "format": format,
            "repr": repr, "hash": hash, "bin": bin, "oct": oct, "hex": hex,
            "chr": chr, "ord": ord, "ascii": ascii,
            # 只读属性检查；但 hasattr(obj, "__dunder__") 的反射式探测已在
            # SecurityChecker.visit_Call 中拦截（getattr/setattr/delattr 本身在 FORBIDDEN_CALLS）
            "hasattr": hasattr,
            # 受限 __import__：仅供 numpy/pandas 等库内部惰性导入自己的子模块。
            # 用户代码显式调用 __import__ 仍被 AST 层拦截（FORBIDDEN_CALLS）。
            "__import__": _make_restricted_import(),
        },
        # 预导入的安全模块（直接放对象，不暴露 __import__）
        "math": math,
        "json": json,
        "re": re,
        "datetime": datetime,
    }
    # 预导入数据处理库（pandas/numpy），供文件解析后的**内存计算**使用。
    # 注意：这两个库自带文件 IO，已由 FORBIDDEN_IO_ATTRS 在 AST 层拦截其读写方法
    # （read_* / to_csv / load / save …）；这里保留它们是为了不误伤正常的数值计算。
    for mod_name, alias in (("pandas", "pd"), ("numpy", "np")):
        try:
            mod = __import__(mod_name)
            g[alias or mod_name] = mod
        except ImportError:
            continue
    return g


def python_repl(args: dict) -> str:
    """
    Python 代码执行工具（安全沙箱版）

    参数:
        args: {"code": "python代码字符串"}

    返回:
        代码执行结果或错误信息

    安全保障：
    1. AST 预检查：拦截 import、exec、eval、__import__、open 等危险操作
    2. 沙箱隔离：白名单内置函数，不暴露 __import__
    3. dunder 属性拦截：禁止访问 __builtins__、__globals__ 等
    """
    code = args.get("code", "")
    if not code:
        return "错误：缺少 code 参数"

    # ===== 第一步：AST 安全检查（拦截危险调用/属性） =====
    violations = check_code_safety(code)
    if violations:
        return "安全检查未通过，拒绝执行:\n" + "\n".join(f"  - {v}" for v in violations)

    # ===== 第二步：剥离白名单内的 import 语句（模块已预导入） =====
    # 注意：必须在安全检查之后，因为导入危险模块会在此被拒绝
    stripped, reject = _strip_allowed_imports(code)
    if reject:
        return "安全检查未通过，拒绝执行:\n  - " + reject
    code = stripped

    # ===== 第三步：执行 =====
    # 优先走独立子进程：它是唯一能**真正终止**死循环的方式（进程可 kill，
    # 线程不行）。子进程不可用时降级到进程内执行（安全策略不变，仅失去可中断）。
    if _PROCESS_ISOLATION_ENABLED:
        output, timed_out = _run_isolated(code)
        if timed_out:
            return (
                f"执行错误：\n沙箱执行超时（>{SANDBOX_TIMEOUT_SEC:.0f}s），已强制终止子进程。\n"
                "常见原因：代码中存在死循环（如 while True）。请检查循环条件后重试。"
            )
        if output is not None:
            return output

    return _run_inprocess(code)


def _run_inprocess(code: str) -> str:
    """在当前进程内执行（降级路径）。

    安全策略与子进程路径完全一致（共用 `_build_safe_globals`），差别只在隔离度：
    本路径**没有中断手段**，死循环会永久占用一个线程——所以它只是兜底，
    默认路径是 `_run_isolated`。

    输出捕获使用**本次调用的局部 buffer**：print 已在 globals 里绑定到它，
    不替换进程级 sys.stdout（那会导致并发串味与死循环下的 stdout 劫持）。
    """
    stdout_buffer = io.StringIO()
    # 每次调用都重新构建安全环境（防止上次执行污染命名空间）
    safe_globals = _build_safe_globals(stdout_buffer)
    try:
        exec(code, safe_globals)
        output = stdout_buffer.getvalue().strip()
        return f"输出:\n{output}" if output else "执行成功（无输出）"
    except Exception:
        # 异常时把已产生的部分输出一并返回——对定位"跑到哪一步炸的"很有用
        partial = stdout_buffer.getvalue().strip()
        error_trace = traceback.format_exc()
        if partial:
            return f"输出:\n{partial}\n执行错误:\n{error_trace}"
        return f"执行错误:\n{error_trace}"


def _run_isolated(code: str):
    """在独立子进程中执行代码，返回 (格式化输出 or None, 是否超时)。

    - 返回 `(文本, False)`：执行完成（成功或代码内部报错），文本已按统一格式包装
    - 返回 `(None, True)` ：超时，**子进程已被强制终止**（这是本方案存在的意义）
    - 返回 `(None, False)`：子进程不可用（环境不允许 / 启动失败），调用方应降级

    为什么格式要在这里就拼好：调用方不关心走的是哪条路径，两条路径必须产出**逐字一致**
    的输出契约，否则"换个执行方式结果就不同"会污染评测与缓存。
    """
    import subprocess

    payload = json.dumps({"code": code}).encode("utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tools._sandbox_runner"],
            input=payload,
            capture_output=True,
            timeout=SANDBOX_TIMEOUT_SEC,
            cwd=_PROJECT_ROOT,
        )
    except subprocess.TimeoutExpired:
        # subprocess 在超时时会自动 kill 子进程——死循环到此为止，不泄漏线程
        return None, True
    except Exception:
        # 起不了子进程（受限环境等）→ 交回调用方降级，不在这里假装成功
        return None, False

    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return None, False

    out = str(data.get("output", "")).strip()
    tb = str(data.get("traceback", "")).strip()
    if tb:
        return (f"输出:\n{out}\n执行错误:\n{tb}" if out else f"执行错误:\n{tb}"), False
    return (f"输出:\n{out}" if out else "执行成功（无输出）"), False
