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

关于「数据目录白名单」（PEC_DATA_ALLOW_DIR）：
  黑名单里含 `C:\\Users`，而 Windows 上**用户数据默认就住在 C:\\Users** ——
  HuggingFace 默认缓存 `C:\\Users\\<u>\\.cache\\huggingface\\...` 会同时命中
  「敏感路径」和「隐藏文件」（`.cache`）两条规则。实测后果：GAIA 官方 53 题里
  11 道附件题**全部**被拒解析，预测文本原话是「禁止访问系统敏感路径」，
  附件子集准确率被静默打成 0%（见 results/gaia_official_multi_agent.json）。

  这是一个**静默失败**：工具不报错、图不红，只是附件子集永远 0 分。
  因此提供一个**显式、可审计**的豁免口：把评测数据目录放进
  `PEC_DATA_ALLOW_DIR`（多个用 `;` 或 `:` 分隔），该目录下的文件不再被黑名单
  与隐藏文件规则拦截。安全性依赖「显式配置」而非「默认放行」——
  不配这个变量时行为与之前完全一致。
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


def allowed_roots() -> list:
    """显式放行的数据目录（PEC_DATA_ALLOW_DIR），多个用 `;` 或 `:` 分隔。

    用于让「评测数据目录」从黑名单下豁免 —— Windows 上用户数据默认就在
    `C:\\Users` 下（含 HF 默认缓存），不豁免会导致附件**静默**解析失败。
    """
    raw = os.getenv("PEC_DATA_ALLOW_DIR", "")
    sep = ";" if ";" in raw else os.pathsep
    roots = []
    for p in raw.split(sep):
        p = p.strip().strip('"').strip("'")
        if p:
            try:
                roots.append(os.path.realpath(p))
            except Exception:
                continue
    return roots


def _under(root: str, target: str) -> bool:
    """target 是否位于 root 之内（或就是 root 本身）"""
    r, t = norm_sep(root), norm_sep(target)
    return t == r or t.startswith(r.rstrip("/") + "/")


def is_forbidden_path(path: str, real_path: str = None) -> str:
    """检查路径是否命中安全禁区。

    判定顺序：
      1. 命中 PEC_DATA_ALLOW_DIR 白名单 → 直接放行（返回 ""）
      2. 命中系统敏感目录黑名单 → "敏感路径"
      3. 路径中含以 '.' 开头的片段 → "隐藏文件"（含 ".." 路径遍历）
      4. 否则返回 ""

    参数:
        path: 用户传入的原始路径
        real_path: 可选，调用方已算好的 os.path.realpath(path)（避免重复计算）

    返回:
        命中时返回拒绝原因（"敏感路径" / "隐藏文件"），未命中返回空字符串 ""
    """
    if real_path is None:
        real_path = os.path.realpath(path)

    # 1) 显式白名单优先（评测数据目录，如 GAIA 本地镜像）
    for root in allowed_roots():
        if _under(root, real_path):
            return ""

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
