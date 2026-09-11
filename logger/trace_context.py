"""trace_id 全链路贯穿（Phase 4 可观测性）。

为什么用 contextvars 而不是参数透传：
  四角色图与工具的调用链很深（api → graph → node → tools → llm），
  逐层加 trace_id 参数要改十几个签名。contextvars 是标准库提供的
  "隐式上下文"，读一次即可全链路可见。

必须知道的两个坑（本项目都踩得到）：
  1. **线程池不继承 context**：scripts/api.py 用 run_in_executor 跑同步图，
     contextvars **不会**自动传播到工作线程（asyncio.to_thread 才会）。
     所以必须在工作线程入口显式 set——见 bind_trace_id()。
  2. **ContextVar 必须在线程内赋值**：主线程 set 的值子线程读不到，
     这不是 bug 而是设计（避免线程间串味）。

取值策略：未设置时返回占位符 "-" 而非 None，让日志字段类型稳定
（下游聚合不用处理 null）。

trace_id 形态：UUID4 字符串。没用 W3C traceparent 是因为当前没有
跨服务调用（工具里的 HTTP 是一个个独立请求，不形成调用链），
32 字符的 UUID 已足够定位；若将来接入 OpenTelemetry 再对齐。
"""
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

_TRACE_ID: ContextVar[Optional[str]] = ContextVar("pecs_trace_id", default=None)

PLACEHOLDER = "-"  # 未绑定时的日志占位符（保持字段类型稳定）


def new_trace_id() -> str:
    """生成一个新的 trace_id（UUID4）。"""
    return str(uuid.uuid4())


def get_trace_id() -> str:
    """当前上下文的 trace_id；未绑定返回占位符 "-"。"""
    return _TRACE_ID.get() or PLACEHOLDER


def set_trace_id(trace_id: str) -> Token:
    """绑定 trace_id 到当前上下文，返回可用于复位（reset）的 Token。"""
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: Token) -> None:
    """用 set 返回的 Token 复位，避免测试间相互污染。"""
    _TRACE_ID.reset(token)


@contextmanager
def bind_trace_id(trace_id: Optional[str] = None) -> Iterator[str]:
    """在当前上下文（含当前线程）绑定 trace_id，退出时自动复位。

    用于工作线程入口——run_in_executor 不会传播 contextvars，必须在
    线程函数开头进入本上下文管理器。
    """
    tid = trace_id or new_trace_id()
    token = set_trace_id(tid)
    try:
        yield tid
    finally:
        reset_trace_id(token)


def trace_fields() -> dict:
    """供结构化日志直接展开的字段字典。"""
    return {"trace_id": get_trace_id()}
