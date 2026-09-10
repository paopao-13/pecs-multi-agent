"""
工具统一包装器（P0 生产化增强）

Day1 范围：超时控制 + 异常分类 + 结构化日志 + 功能开关。
熔断 / 幂等 / 权限白名单在 Day2 追加。

============================ 设计约束（勿违反） ============================

K1 对外仍返回 str
   tools/__init__.py 的 execute_tool(action, args) -> str，且 is_tool_success()
   按「错误标记前缀」判定成功失败。包装器绝不能把返回值改成 dict，
   所有包装器产生的错误都必须以 _ERROR_MARKERS 内的前缀开头。

K2 项目是同步的
   agents/executor.py 的 executor_node 是同步函数，scripts/api.py 用线程池跑
   同步图。因此超时不能用 asyncio.wait_for，Windows 也没有 signal.alarm。
   本模块用 ThreadPoolExecutor + future.result(timeout=) 实现。

K3 开关默认关闭
   所有能力默认关闭，保证 eval 模式（跑 GAIA / WebShop 评测）行为与改造前
   逐字一致。
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

from config import TOOL_TIMEOUT_SEC

# 模块级 logger：调用方可按需配置 handler；测试用 caplog 捕获
logger = logging.getLogger("pecs.tools.wrapper")


class ToolErrorType(str, Enum):
    """工具执行失败的结构化分类。

    str 混入是为了让 error_type 能直接进 JSON / Prometheus label。
    """
    TIMEOUT = "TIMEOUT"                    # 超过 TOOL_TIMEOUT_SEC 未返回
    JSON_PARSE_ERR = "JSON_PARSE_ERR"      # 返回内容不是合法 JSON
    INVALID_ARGS = "INVALID_ARGS"          # 参数缺失 / 类型错误
    SANDBOX_ERR = "SANDBOX_ERR"            # 被 AST 安全沙箱拦截
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 角色越权（Day2 启用）
    UNKNOWN = "UNKNOWN"                    # 未归类异常


# 错误类型 -> 返回给上层的错误文案前缀。
# 【约束 K1】这些前缀必须落在 tools/__init__.py 的 _ERROR_MARKERS
# （"错误" / "执行错误" / "安全检查未通过"）之内，否则 is_tool_success()
# 会把失败误判成成功。
_ERROR_TEXT = {
    ToolErrorType.TIMEOUT: "执行错误：工具 {name} 超时（超过 {sec}s 未返回）",
    ToolErrorType.JSON_PARSE_ERR: "执行错误：工具 {name} 返回内容不是合法 JSON",
    ToolErrorType.INVALID_ARGS: "执行错误：工具 {name} 参数无效",
    ToolErrorType.SANDBOX_ERR: "安全检查未通过：工具 {name} 被沙箱拦截",
    ToolErrorType.PERMISSION_DENIED: "执行错误：工具 {name} 越权，当前节点无权调用",
    ToolErrorType.UNKNOWN: "执行错误：工具 {name} 执行异常",
}


def classify_error(exc: BaseException) -> ToolErrorType:
    """把异常映射为结构化错误类型。

    注意判定顺序：json.JSONDecodeError 是 ValueError 的子类，
    必须先判 JSON 再判 INVALID_ARGS，否则 JSON 错误会被误归成参数错误。
    """
    if isinstance(exc, json.JSONDecodeError):
        return ToolErrorType.JSON_PARSE_ERR
    if isinstance(exc, (TimeoutError, FuturesTimeoutError)):
        return ToolErrorType.TIMEOUT
    if isinstance(exc, (KeyError, TypeError)):
        return ToolErrorType.INVALID_ARGS
    if isinstance(exc, ValueError):
        return ToolErrorType.INVALID_ARGS

    msg = f"{type(exc).__name__}: {exc}".lower()
    sandbox_keys = ("安全检查", "沙箱", "sandbox", "forbidden", "不允许", "禁止")
    if any(k in msg for k in sandbox_keys):
        return ToolErrorType.SANDBOX_ERR
    return ToolErrorType.UNKNOWN


def run_with_timeout(
    tool_fn: Callable[[dict], str],
    args: dict,
    timeout: float,
) -> Tuple[Optional[str], Optional[BaseException], float]:
    """在独立线程里执行工具并限时等待。

    【已知局限 —— 主动披露，不要包装】
    这是「超时后放弃等待」，不是「真正中断线程」。原因：
      1. 项目图与工具都是同步函数，无法用 asyncio.wait_for；
      2. Windows 没有 signal.alarm；
      3. CPython 没有安全的跨线程强杀 API。
    后果：若工具内部死循环或永久阻塞，那个工作线程仍会继续占用 CPU/IO，
    且 concurrent.futures 在解释器退出时会 join 工作线程，极端情况下会拖慢
    进程退出。真正强杀需要子进程或进程池，本期不做。

    返回: (结果字符串 | None, 异常 | None, 耗时秒)
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pecs-tool")
    started = time.perf_counter()
    try:
        future = executor.submit(tool_fn, args)
        try:
            result = future.result(timeout=timeout)
            return result, None, time.perf_counter() - started
        except FuturesTimeoutError:
            return None, TimeoutError(f"tool exceeded {timeout}s"), time.perf_counter() - started
        except Exception as exc:  # noqa: BLE001 - 工具异常需统一收敛为结构化结果
            return None, exc, time.perf_counter() - started
    finally:
        # wait=False：超时后不阻塞当前调用线程。
        # 代价是超时线程无法回收（见上面局限说明）。
        executor.shutdown(wait=False)


def _digest(args: Any, max_len: int = 200) -> str:
    """参数摘要：进日志前截断，避免超长参数/大段文本把日志打爆，也顺带减少敏感信息落盘。"""
    if args is None:
        return ""
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= max_len else text[:max_len] + "...(truncated)"


def log_tool_call(
    action: str,
    context: Optional[Dict[str, Any]],
    duration: float,
    ok: bool,
    error_type: Optional[ToolErrorType] = None,
    args: Any = None,
) -> None:
    """输出一条结构化工具调用日志。

    字段固定：thread_id / node / tool / duration_ms / ok / error_type / args_digest，
    便于事后按 thread_id 串起一条完整链路。
    """
    ctx = context or {}
    logger.info(
        json.dumps(
            {
                "event": "tool_call",
                "tool": action,
                "thread_id": ctx.get("thread_id", "-"),
                "node": ctx.get("node_name", "-"),
                "iteration": ctx.get("iteration"),
                "duration_ms": round(duration * 1000, 1),
                "ok": ok,
                "error_type": error_type.value if error_type else None,
                "args_digest": _digest(args),
            },
            ensure_ascii=False,
        )
    )


def format_error(action: str, error_type: ToolErrorType, **fmt) -> str:
    """生成对外错误文案。必须带 _ERROR_MARKERS 前缀（约束 K1）。"""
    template = _ERROR_TEXT.get(error_type, _ERROR_TEXT[ToolErrorType.UNKNOWN])
    return template.format(name=action, **fmt)


def invoke_tool(
    tool_fn: Callable[[dict], str],
    action: str,
    args: dict,
    context: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> Tuple[str, Optional[ToolErrorType], float]:
    """包装一次工具调用。

    返回: (结果字符串, 错误类型 | None, 耗时秒)
    结果字符串始终以 _ERROR_MARKERS 前缀表达失败，保证与 is_tool_success 一致。
    """
    timeout = TOOL_TIMEOUT_SEC if timeout is None else timeout
    started = time.perf_counter()
    result, exc, duration = run_with_timeout(tool_fn, args, timeout)

    if exc is not None:
        error_type = classify_error(exc)
        sec = timeout
        message = format_error(action, error_type, sec=sec)
        log_tool_call(action, context, duration, ok=False, error_type=error_type, args=args)
        return message, error_type, duration

    # 工具返回了内容，但要防住「返回空串」这类静默失败
    if result is None or (isinstance(result, str) and not result.strip()):
        error_type = ToolErrorType.UNKNOWN
        message = format_error(action, error_type) + "（返回空结果）"
        log_tool_call(action, context, duration, ok=False, error_type=error_type, args=args)
        return message, error_type, duration

    log_tool_call(action, context, duration, ok=True, error_type=None, args=args)
    return result, None, duration
