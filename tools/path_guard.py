"""
路径安全守卫（file_reader / file_parser 共用）

抽出来的原因：
  file_reader 与 file_parser 原先各自内联了一份相同的路径校验逻辑，且两份都
  存在同一个缺陷 —— 只用 `os.path.realpath(path).startswith(prefix)` 判定敏感
  目录，而 realpath 是**平台相关**的：

    - Linux 上 "C:\\Windows\\x" 没有盘符概念，会被解析成 <cwd>/C:\\Windows\\x，
      Windows 侧前缀规则永远匹配不上（形同死代码）；
    - Windows 上 "/etc/passwd" 会被解析成 "X:\\etc\\passwd"，POSIX 侧前缀规则
      同样失效。

  结果是两套规则各自只在自身平台生效，另一平台可绕过。此处在比对前对两侧做
  分隔符归一化，并**同时比对「原始输入」与「realpath 结果」**（只查 realpath 会
  随平台失效，只查原始输入则可被 symlink / 相对路径绕过），使两条规则在两个
  平台都生效。
"""
import os

# 禁止访问的系统敏感目录前缀（同时覆盖 POSIX 与 Windows 写法）
FORBIDDEN_PREFIXES = (
    "/etc", "/var", "/root", "/proc", "/sys", "/dev",
    "C:\\Windows", "C:\\Users", "C:\\Program",
)


def norm_sep(p: str) -> str:
    """把路径分隔符统一为 '/' 并小写，用于跨平台前缀比对。"""
    return p.replace("\\", "/").lower()


def is_forbidden_path(path: str, real_path: str = None) -> str:
    """检查路径是否命中安全禁区。

    参数:
        path: 用户传入的原始路径
        real_path: 可选，调用方已算好的 os.path.realpath(path)（避免重复计算）

    返回:
        命中时返回拒绝原因（"敏感路径" / "隐藏文件"），未命中返回空字符串 ""
    """
    if real_path is None:
        real_path = os.path.realpath(path)

    norm_real = norm_sep(real_path)
    norm_raw = norm_sep(path)
    for prefix in FORBIDDEN_PREFIXES:
        norm_prefix = norm_sep(prefix)
        if norm_real.startswith(norm_prefix) or norm_raw.startswith(norm_prefix):
            return "敏感路径"

    # 隐藏文件（以 . 开头）。注：'..' 也满足 startswith('.')，
    # 因此路径遍历通常会在这一条被拦下。
    path_parts = os.path.normpath(path).split(os.sep)
    if any(part.startswith(".") for part in path_parts):
        return "隐藏文件"

    return ""
