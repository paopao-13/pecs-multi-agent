"""
Demo: AST 安全沙箱拦截演示

展示 PECS 框架的 Python 安全沙箱如何拦截 LLM 生成的恶意代码。
两层防护机制：
  1. AST 预检查：解析语法树，拦截 import / exec / eval 等危险调用
  2. 白名单沙箱：执行时只暴露 math/json/re/datetime 四个安全模块

运行方式：
    cd pecs-multi-agent
    python demos/security_sandbox_demo.py
"""
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.python_repl import python_repl, check_code_safety, SecurityChecker
import ast


def test_case(title: str, code: str, expect_blocked: bool = True):
    """运行一个安全测试用例"""
    print(f"\n{'─' * 60}")
    print(f"  测试: {title}")
    print(f"{'─' * 60}")
    print(f"  代码: {code.strip()}")
    print()

    # 第一层：AST 预检查
    checker = SecurityChecker()
    try:
        tree = ast.parse(code)
        checker.visit(tree)
    except SyntaxError as e:
        print(f"  [AST 检查] 语法错误: {e}")
        return

    if checker.violations:
        print(f"  [AST 预检查] 拦截到 {len(checker.violations)} 个违规:")
        for v in checker.violations:
            print(f"    -> {v}")
        if expect_blocked:
            print(f"  [结果] 预期拦截，AST 层成功拦截")
        else:
            print(f"  [结果] 非预期拦截，请检查")
        return

    print(f"  [AST 预检查] 通过，无违规节点")

    # 第二层：沙箱执行
    result = python_repl({"code": code})
    blocked = result.startswith("安全检查未通过") or "错误" in result[:10]

    if not blocked:
        print(f"  [沙箱执行] 成功")
        print(f"  [输出] {result[:200]}")
        if not expect_blocked:
            print(f"  [结果] 预期通过，执行成功")
    else:
        print(f"  [沙箱执行] 被拦截")
        print(f"  [错误] {result[:200]}")
        if expect_blocked:
            print(f"  [结果] 预期拦截，沙箱层成功拦截")

    return result


def main():
    print("=" * 60)
    print("  PECS 安全沙箱拦截演示")
    print("  两层防护：AST 预检查 + 白名单沙箱")
    print("=" * 60)
    print("""
  安全沙箱设计：
  第一层 - AST 预检查：
    用 ast.NodeVisitor 遍历语法树，拦截以下节点：
    - Import / ImportFrom（禁止导入任意模块）
    - __import__ / exec / eval / compile（禁止动态执行）
    - open / input / breakpoint / exit / quit（禁止系统操作）
    - getattr / setattr / delattr（禁止动态属性访问）
    - globals / locals / vars / dir（禁止命名空间访问）

  第二层 - 白名单沙箱：
    执行时只暴露 math / json / re / datetime 四个模块
    __builtins__ 被替换为白名单字典，不含 __import__
""")

    # ===== 合法代码（应通过） =====
    print("\n" + "=" * 60)
    print("  Part 1: 合法代码（应正常执行）")
    print("=" * 60)

    test_case(
        "数学计算 - 平方根",
        "print(math.sqrt(144))",
        expect_blocked=False,
    )

    test_case(
        "JSON 处理",
        'data = json.loads(\'{"name": "PECS", "score": 95}\')\nprint(data["name"])',
        expect_blocked=False,
    )

    test_case(
        "正则匹配",
        'result = re.findall(r"\\d+", "a1b2c3d4")\nprint(result)',
        expect_blocked=False,
    )

    test_case(
        "日期计算",
        "now = datetime.datetime.now()\nprint(now.year)",
        expect_blocked=False,
    )

    # ===== 恶意代码（应被拦截） =====
    print("\n" + "=" * 60)
    print("  Part 2: 恶意代码（应被拦截）")
    print("=" * 60)

    test_case(
        "尝试导入 os 模块",
        "import os\nos.system('echo hacked')",
        expect_blocked=True,
    )

    test_case(
        "尝试 from import 导入",
        "from os import system\nsystem('rm -rf /')",
        expect_blocked=True,
    )

    test_case(
        "尝试 __import__ 动态导入",
        "__import__('os').system('whoami')",
        expect_blocked=True,
    )

    test_case(
        "尝试 exec 执行任意代码",
        "exec(\"import os; os.system('id')\")",
        expect_blocked=True,
    )

    test_case(
        "尝试 eval 求值",
        "eval('__import__(\"os\").getcwd()')",
        expect_blocked=True,
    )

    test_case(
        "尝试 open 读写文件",
        "open('/etc/passwd').read()",
        expect_blocked=True,
    )

    test_case(
        "尝试 getattr 绕过沙箱",
        "getattr(__builtins__, '__import__')('os')",
        expect_blocked=True,
    )

    test_case(
        "尝试双下划线属性访问",
        "obj = object()\nobj.__class__.__bases__[0].__subclasses__()",
        expect_blocked=True,
    )

    print("\n" + "=" * 60)
    print("  Demo 完成！")
    print("=" * 60)
    print("""
  总结：
  1. AST 预检查在代码执行前拦截，零运行时风险
  2. 白名单沙箱作为第二道防线，即使 AST 漏过也无法访问危险模块
  3. 早期版本曾在白名单中包含 __import__ 导致安全漏洞，
     现已通过 AST 预检查 + 白名单双重防护彻底封堵
  4. 安全沙箱使框架可以安全执行 LLM 生成的 Python 代码
""")


if __name__ == "__main__":
    main()
