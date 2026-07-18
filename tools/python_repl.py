"""
Python 代码执行工具

Executor 可以让 LLM 生成 Python 代码，然后在这里安全执行。
用于计算、数据处理、逻辑推理等需要精确计算的任务。

安全措施：
1. AST 预检查：拦截 import / exec / eval / __import__ 等危险调用
2. 白名单沙箱：只暴露安全的内置函数和预导入的模块
3. 不暴露 __import__：无法在运行时动态导入任意模块
"""
import sys
import io
import ast
import traceback
import math
import json
import re
import datetime

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
_ALLOWED_IMPORT_ROOTS = {"pandas", "numpy", "openpyxl", "math", "json", "re", "datetime"}


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
        # 检查属性调用：如 obj.__import__、obj.system 等
        if isinstance(node.func, ast.Attribute):
            attr_name = node.func.attr
            if attr_name.startswith("__") or attr_name in FORBIDDEN_CALLS:
                self.violations.append(f"第{node.lineno}行: 禁止调用属性 '{attr_name}'")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # 拦截 dunder 属性访问（如 __builtins__、__globals__ 等）
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.violations.append(f"第{node.lineno}行: 禁止访问 dunder 属性 '{node.attr}'")
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

def _build_safe_globals():
    """
    构建安全的执行环境

    核心思路：
    1. 预先在沙箱外 import 好需要的模块，把模块对象直接放进 globals
    2. 不暴露 __import__，代码无法在运行时动态导入其他模块
    3. 内置函数用白名单，只暴露安全的函数
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
            "print": print, "isinstance": isinstance, "type": type,
            "any": any, "all": all, "format": format,
            "repr": repr, "hash": hash, "bin": bin, "oct": oct, "hex": hex,
            "chr": chr, "ord": ord, "ascii": ascii,
            "hasattr": hasattr,  # 只读属性检查，不允许 setattr/delattr
        },
        # 预导入的安全模块（直接放对象，不暴露 __import__）
        "math": math,
        "json": json,
        "re": re,
        "datetime": datetime,
    }
    # 预导入数据处理库（pandas/numpy/openpyxl），供文件解析后的计算使用
    for mod_name, alias in (("pandas", "pd"), ("numpy", "np"), ("openpyxl", None)):
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

    # ===== 第二步：在沙箱中执行 =====
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    # 每次调用都重新构建安全环境（防止上次执行污染命名空间）
    safe_globals = _build_safe_globals()

    try:
        sys.stdout = stdout_buffer
        sys.stderr = stderr_buffer

        # 执行代码
        exec(code, safe_globals)

        output = stdout_buffer.getvalue().strip()
        error = stderr_buffer.getvalue().strip()

        if error:
            return f"输出:\n{output}\n警告:\n{error}" if output else f"警告:\n{error}"
        return f"输出:\n{output}" if output else "执行成功（无输出）"

    except Exception as e:
        error_trace = traceback.format_exc()
        return f"执行错误:\n{error_trace}"
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
