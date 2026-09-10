"""
文件工具路径安全性单元测试

覆盖 tools/file_reader.py 与 tools/file_parser.py，以及两者共用的守卫
tools/path_guard.py：
- 空路径 / 不存在文件返回错误
- 隐藏文件 / 路径遍历 / 系统敏感路径被拒绝（**跨平台**：POSIX 与 Windows 两类写法都要拦住）
- 项目内正常文件可读取

接口说明（已通过阅读源码确认）：
    from tools.file_reader import file_reader
    result = file_reader({"path": "some_file"})
    # result 为字符串：
    #   - 成功: 文件内容
    #   - 失败: "错误：..." 开头的提示

跨平台相关的用例必须**不加平台 skip** —— 历史上正因 Linux runner 上
Windows 路径规则失效，导致远端 CI 的 unit-test job 变红。
"""
import os
from tools.file_reader import file_reader


def test_read_empty_path():
    """空路径返回错误"""
    result = file_reader({"path": ""})
    assert "错误" in result
    assert "path" in result or "缺少" in result


def test_read_nonexistent():
    """不存在的文件返回错误"""
    result = file_reader({"path": "this_file_does_not_exist_xyz_12345.txt"})
    assert "错误" in result
    assert "不存在" in result


def test_read_hidden_file():
    """.env 隐藏文件被拒绝"""
    result = file_reader({"path": ".env"})
    assert "错误" in result
    assert "隐藏" in result


def test_read_path_traversal():
    """路径遍历 ../../../etc/passwd 被拒绝。

    2026-09-10 复核说明：该输入实际是被「隐藏文件」规则拦下的
    （'..' 也满足 startswith('.')），而非敏感目录规则 —— 遍历后的真实路径
    在不同平台上会落到不同位置（Linux 落 /home/.../etc/passwd，Windows 落
    X:\\etc\\passwd），无法用固定前缀匹配。两条规则都能兜住遍历，
    因此这里只断言「必须被拒绝」，不绑定具体是哪条规则。
    """
    result = file_reader({"path": "../../../etc/passwd"})
    assert "错误" in result
    assert "禁止" in result


def test_read_system_path_windows_cross_platform():
    """Windows 系统敏感路径被拒绝（且必须跨平台生效）。

    回归背景（CI 曾因此变红）：黑名单原本只比 os.path.realpath(...).startswith(prefix)，
    而 realpath 是平台相关的 —— Linux 上 "C:\\Windows\\..." 会被解析成当前工作目录下的
    相对路径，前缀匹配失败 → 返回「文件不存在」→ 本用例在 ubuntu runner 上必挂。
    修复后（tools/path_guard.py 的 norm_sep 归一化后同时比对原始输入与 realpath），
    该规则在 Windows / Linux / macOS 上均生效，故此处**不加平台 skip**。
    """
    result = file_reader({"path": r"C:\Windows\System32\config\SAM"})
    assert "错误" in result
    assert "禁止" in result or "敏感" in result


def test_read_system_path_posix_cross_platform():
    """POSIX 系统敏感路径被拒绝（跨平台验证同一套规则的另一半）。

    对称回归：修复前在 Windows 上 "/etc/passwd" 会被 realpath 解析成
    "X:\\etc\\passwd"，同样逃过前缀匹配 → 返回「文件不存在」。
    """
    result = file_reader({"path": "/etc/passwd"})
    assert "错误" in result
    assert "禁止" in result or "敏感" in result


def test_forbidden_prefix_normalization_helper():
    """_norm_sep 归一化：反斜杠/正斜杠与大小写差异应被消除。"""
    from tools.path_guard import norm_sep

    assert norm_sep(r"C:\Windows\System32") == "c:/windows/system32"
    assert norm_sep("/etc/passwd") == "/etc/passwd"
    assert norm_sep("C:/Windows") == norm_sep(r"c:\windows")


def test_path_guard_rejects_both_families():
    """共用守卫：POSIX 与 Windows 两类敏感路径都必须被拒（与运行平台无关）。"""
    from tools.path_guard import is_forbidden_path

    assert is_forbidden_path(r"C:\Windows\System32\config\SAM") == "敏感路径"
    assert is_forbidden_path("/etc/passwd") == "敏感路径"
    assert is_forbidden_path("/proc/self/environ") == "敏感路径"
    assert is_forbidden_path(".env") == "隐藏文件"
    assert is_forbidden_path("config.py") == ""


def test_file_parser_shares_the_guard():
    """file_parser 复用同一守卫：Windows 敏感路径在 Linux 上同样被拒。"""
    from tools.file_parser import file_parser

    result = file_parser({"path": r"C:\Windows\System32\config\SAM"})
    assert "错误" in result
    assert "禁止" in result


# ============================================================
# CI 回归锁定：把「平台相关的 realpath 语义」固定下来
# ============================================================

def test_windows_path_blocked_under_linux_realpath_semantics():
    """模拟 Linux 的 realpath：Windows 路径被解析成 <cwd>/C:\\Windows\\... 时仍须拦下。

    这就是远端 CI 变红的精确场景。修复前只比 realpath → 前缀匹配失败 → 放行到
    「文件不存在」；修复后由「原始输入」分支兜底，与平台无关。
    """
    from tools.path_guard import is_forbidden_path

    linux_resolved = (
        "/home/runner/work/pecs-multi-agent/pecs-multi-agent/C:\\Windows\\System32\\config\\SAM"
    )
    assert is_forbidden_path(
        r"C:\Windows\System32\config\SAM", real_path=linux_resolved
    ) == "敏感路径"


def test_posix_path_blocked_under_windows_realpath_semantics():
    """对称场景：Windows 上 "/etc/passwd" 被解析成 "D:\\etc\\passwd" 时仍须拦下。"""
    from tools.path_guard import is_forbidden_path

    assert is_forbidden_path("/etc/passwd", real_path="D:\\etc\\passwd") == "敏感路径"


def test_read_normal_file():
    """读取项目内的正常文件（config.py）成功"""
    # 使用绝对路径定位 config.py，避免相对路径引入 ".." 被误判为隐藏文件
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config.py")

    result = file_reader({"path": config_path})
    # 不应返回错误
    assert "错误" not in result
    # config.py 源码中包含的关键标识符
    assert "DEEPSEEK_API_KEY" in result
