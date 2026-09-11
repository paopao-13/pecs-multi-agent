"""Prompt 版本注册表 —— 灰度发布与回滚的最小实现。

为什么是"覆盖式"而不是"搬家式"：
  四角色的 SYSTEM_PROMPT 目前是 agents/*.py 里的模块级常量（代码内基线）。
  把它们全部搬进 prompts/v1/*.txt 需要同时改动 4 个 agent 文件与 token
  估算逻辑，风险大；而覆盖式只需每个 agent 改一行——resolve(role, baseline)
  在无覆盖文件时**逐字返回基线**（与改造前一致），有 prompts/v{N}/<role>.txt
  时才用文件内容。当前版本 = 代码内基线（v0），新版本 = 建目录放文件。

版本解析规则（resolve 的全部逻辑）：
  1. 读当前生效版本号（运行时可变，见下）；
  2. 版本目录 prompts/v{N}/ 存在且含 <role>.txt → 用文件内容；
  3. 否则 → 返回 baseline（代码内常量）。
  因此：删掉所有版本目录 = 全部回滚到基线，不需要额外开关。

运行时切换（灰度/回滚的机制）：
  - 生效版本存在进程内变量 _active_version，默认取环境变量
    PEC_PROMPT_VERSION（未设置 = "v0" 基线）。
  - set_active_version() 由 /admin/prompt/rollback 端点调用——改的是
    进程内状态：立即生效、无需重启；进程重启后回落到环境变量的值，
    这正是"临时回滚"的语义（永久回滚应改 .env 并重启）。

灰度（部分流量用新版本）怎么算支持：
  多 worker 部署下可对部分 worker 注入不同的 PEC_PROMPT_VERSION 启动
  （例如 4 个 worker 里 1 个用 v1），即按进程粒度的流量切分；单进程内
  不做按请求的概率灰度——状态一致性难验证，且本项目 QPS 由 LLM 时延
  主导，进程级切分已足够。诚实边界：这比生产级的按请求灰度粗。
"""
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger("pecs.tools.prompt_registry")

# 支持版本化的角色（与四角色一一对应）
ROLES = ("planner", "executor", "critic", "synthesizer")

_DEFAULT_VERSION = os.getenv("PEC_PROMPT_VERSION", "") or "v0"
_active_version: str = _DEFAULT_VERSION

# 缓存已读的覆盖文件：版本号 -> {role: 内容}
# key 含版本号，切换版本后旧版本缓存自然失配（不主动清，量小无碍）
_override_cache: Dict[str, Dict[str, str]] = {}


def get_active_version() -> str:
    """当前生效的 Prompt 版本号（v0 = 代码内基线）。"""
    return _active_version


def set_active_version(version: str) -> str:
    """切换生效版本（/admin/prompt/rollback 用）。返回切换后的版本号。

    只接受 "v<数字>" 形式；不存在的版本目录不会在这里拒绝——resolve
    时自然回退基线（宽松处理：回滚到 v99 也只是等价于回基线）。
    """
    global _active_version
    version = (version or "").strip()
    if not (version.startswith("v") and version[1:].isdigit()):
        raise ValueError(f"非法版本号: {version!r}（应为 v<数字>，如 v1）")
    _active_version = version
    logger.info(json_event("prompt_version_switch", version=version))
    return _active_version


def override_path(role: str, version: Optional[str] = None) -> str:
    """某角色某版本的覆盖文件路径（不检查存在性）。"""
    v = version if version is not None else _active_version
    return os.path.join("prompts", v, f"{role}.txt")


def has_override(role: str, version: Optional[str] = None) -> bool:
    """该角色在该版本下是否有覆盖文件。"""
    path = override_path(role, version)
    if not os.path.isfile(path):
        return False
    return os.path.getsize(path) > 0  # 空文件视为无效覆盖，防误放空 prompt


def resolve(role: str, baseline: str, version: Optional[str] = None) -> str:
    """解析某角色当前应使用的 SYSTEM_PROMPT。

    有覆盖文件用文件内容，否则逐字返回 baseline。文件读取失败时
    回退基线并告警——Prompt 加载绝不能让任务直接崩掉。
    """
    v = version if version is not None else _active_version
    if v == "v0" or not has_override(role, v):
        return baseline
    try:
        if v not in _override_cache:
            _override_cache[v] = {}
        cache = _override_cache[v]
        if role not in cache:
            with open(override_path(role, v), encoding="utf-8") as f:
                cache[role] = f.read()
        return cache[role]
    except OSError as exc:
        logger.warning(json_event("prompt_override_read_failed", role=role, error=str(exc)))
        return baseline


def status() -> Dict[str, object]:
    """各角色的 Prompt 来源概况（/admin/prompt/status 与 /health 用）。"""
    sources: Dict[str, str] = {}
    for role in ROLES:
        sources[role] = (
            f"override:{override_path(role)}" if has_override(role) else "baseline"
        )
    return {"version": _active_version, "sources": sources}


def json_event(event: str, **fields) -> str:
    """结构化日志行（与 wrapper.log_tool_call 同风格）。"""
    import json

    return json.dumps({"event": event, **fields}, ensure_ascii=False)
