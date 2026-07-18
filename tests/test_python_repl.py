"""
Python REPL 沙箱安全性单元测试

测试 tools/python_repl.py 的安全沙箱：
- 正常数学计算 / 预导入模块可用
- 危险操作（import / exec / eval / dunder / open / __import__）被 AST 预检查拦截

接口说明（已通过阅读源码确认）：
    from tools.python_repl import python_repl
    result = python_repl({"code": "print(2+3)"})
    # result 为字符串：
    #   - 正常执行: "输出:\\n{stdout}" 或 "执行成功（无输出）"
    #   - 安全拦截: "安全检查未通过，拒绝执行:\\n  - ..."
    #   - 运行错误: "执行错误:\\n{traceback}"
"""
from tools.python_repl import python_repl


# ========== 正常执行 ==========

def test_normal_math():
    """正常数学计算：print(2 + 3) 输出 5"""
    result = python_repl({"code": "print(2 + 3)"})
    assert "5" in result
    # 不应出现安全拦截或错误
    assert "安全检查未通过" not in result
    assert "执行错误" not in result


def test_normal_fibonacci():
    """用预导入的 math 模块计算，并验证正常逻辑（斐波那契）可执行"""
    # 1. 验证预导入的 math 模块可直接使用（无需 import）
    result_math = python_repl({"code": "print(math.factorial(5))"})
    assert "120" in result_math
    assert "安全检查未通过" not in result_math

    # 2. 验证函数定义 + 循环等正常逻辑可执行
    fib_code = (
        "def fib(n):\n"
        "    a, b = 0, 1\n"
        "    for _ in range(n):\n"
        "        a, b = b, a + b\n"
        "    return a\n"
        "print(fib(10))"
    )
    result_fib = python_repl({"code": fib_code})
    assert "55" in result_fib
    assert "安全检查未通过" not in result_fib


# ========== 安全拦截 ==========

def test_block_import():
    """import os 被拦截"""
    result = python_repl({"code": "import os"})
    assert "安全检查未通过" in result
    assert "os" in result  # 报错中文化为"禁止导入模块 'os'"，含被拦截模块名


def test_block_import_from():
    """from os import system 被拦截"""
    result = python_repl({"code": "from os import system"})
    assert "安全检查未通过" in result
    assert "os" in result  # 报错中文化为"禁止从 'os' 导入"，含被拦截模块名


def test_block_exec():
    """exec(...) 被拦截"""
    result = python_repl({"code": 'exec("print(1)")'})
    assert "安全检查未通过" in result
    assert "exec" in result


def test_block_eval():
    """eval(...) 被拦截"""
    result = python_repl({"code": 'eval("1+1")'})
    assert "安全检查未通过" in result
    assert "eval" in result


def test_block_dunder():
    """dunder 属性访问 ()。__class__ 被拦截"""
    # 代码 ()。__class__ 访问空元组的 __class__ 属性，
    # 属于 dunder 属性访问，会被 AST 预检查拦截。
    # （注意：不能写成带外层引号的 "().__class__"，否则会被当成普通字符串字面量而不触发拦截）
    result = python_repl({"code": "().__class__"})
    assert "安全检查未通过" in result
    assert "dunder" in result or "__class__" in result


def test_block_open():
    """open(...) 被拦截"""
    result = python_repl({"code": 'open("/etc/passwd")'})
    assert "安全检查未通过" in result
    assert "open" in result


def test_block_double_underscore_import():
    """__import__(...) 被拦截"""
    result = python_repl({"code": '__import__("os")'})
    assert "安全检查未通过" in result
    assert "import" in result
