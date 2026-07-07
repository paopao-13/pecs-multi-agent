"""
文件读取工具

Executor 可以读取本地文件内容，用于处理用户上传的文档、配置文件等。
"""
import os


def file_reader(args: dict) -> str:
    """
    文件读取工具

    参数:
        args: {"path": "文件路径", "encoding": "utf-8"}

    返回:
        文件内容字符串
    """
    path = args.get("path", "")
    encoding = args.get("encoding", "utf-8")

    if not path:
        return "错误：缺少 path 参数"

    # 路径安全校验：阻止路径遍历攻击
    # 1. 规范化路径，消除 ../ 和符号链接
    safe_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    real_path = os.path.realpath(path)

    # 2. 禁止访问系统敏感目录
    forbidden_prefixes = [
        "/etc", "/var", "/root", "/proc", "/sys", "/dev",
        "C:\\Windows", "C:\\Users", "C:\\Program",
    ]
    for prefix in forbidden_prefixes:
        if real_path.lower().startswith(prefix.lower()):
            return f"错误：禁止访问系统敏感路径"

    # 3. 禁止读取隐藏文件（以 . 开头的文件/目录）
    path_parts = os.path.normpath(path).split(os.sep)
    if any(part.startswith(".") for part in path_parts):
        return f"错误：禁止访问隐藏文件"

    if not os.path.exists(path):
        return f"错误：文件不存在 '{path}'"

    if not os.path.isfile(path):
        return f"错误：路径不是文件 '{path}'"

    # 限制文件大小（10MB），防止读取超大文件
    file_size = os.path.getsize(path)
    if file_size > 10 * 1024 * 1024:
        return f"错误：文件过大 ({file_size} bytes)，最大支持 10MB"

    try:
        with open(path, "r", encoding=encoding) as f:
            content = f.read()
        return content
    except UnicodeDecodeError:
        # 尝试其他编码
        for enc in ["gbk", "latin-1", "utf-16"]:
            try:
                with open(path, "r", encoding=enc) as f:
                    content = f.read()
                return content
            except UnicodeDecodeError:
                continue
        return f"错误：无法解码文件 '{path}'，请指定正确的编码"
    except Exception as e:
        return f"错误：读取文件失败 - {type(e).__name__}: {str(e)}"
