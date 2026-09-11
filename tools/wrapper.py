"""
工具统一包装器（P0 生产化增强）

Day1：超时控制 + 异常分类 + 结构化日志 + 功能开关。
Day2：熔断 + 幂等 + 权限白名单（全部受子开关控制，默认关闭）。

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

============================ 已知局限（主动披露） ============================

L1 熔断状态仅单进程有效（可选解法已就绪）
   默认计数存于本进程内存字典，多 worker 不共享。设置 PEC_SHARED_STATE_DB
   后由 scripts/api.py 注入 SharedStateStore（SQLite 跨进程共享），
   阈值在全部 worker 间生效。

L2 幂等缓存有上限且默认是进程内 LRU（可选解法已就绪）
   最多缓存 _IDEMPOTENT_MAX 条，超出按插入顺序淘汰最旧项。共享模式下
   落 SQLite，带 TTL（PEC_IDEM_TTL_SEC，默认 600s），跨进程可见。

L3 超时是「放弃等待」而非「真正中断」
   见 run_with_timeout 的说明。
"""
import hashlib
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

from config import (
    TOOL_BREAKER_ENABLED,
    TOOL_BREAKER_RESET_SEC,
    TOOL_BREAKER_THRESHOLD,
    TOOL_IDEMPOTENT_ENABLED,
    TOOL_PERMISSION_ENABLED,
    TOOL_PERMISSION_MAP,
    TOOL_TIMEOUT_SEC,
)

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
    CIRCUIT_OPEN = "CIRCUIT_OPEN"          # 熔断开启，调用被短路（Day2 启用）
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
    ToolErrorType.CIRCUIT_OPEN: "执行错误：工具 {name} 处于熔断状态（连续失败达阈值，约 {sec}s 后自动恢复）",
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


# ============================================================
# Day2-1 只读工具白名单（幂等只对这些工具生效）
# ============================================================
# 只读 = 重复调用不产生副作用，可安全缓存返回。
# 反例：python（可写文件 / 改变状态）、api_call（可能是 POST）、
# webshop（会产生选择动作）——缓存会掩盖副作用，故一律不缓存。
READ_ONLY_TOOLS = frozenset({"search", "web_browse", "file_read", "file_parse", "multimodal"})


# ============================================================
# Day2-2 熔断器
# ============================================================
# 【局限 L1 → 已提供可选解法】仅单进程有效：状态是本进程内存字典，多
# worker 不共享。设置 PEC_SHARED_STATE_DB 后熔断/幂等自动改走
# tools/wrapper_state.SharedStateStore（SQLite 跨进程共享），见
# configure_shared_state()。未配置时行为与历史版本逐字一致。
_breaker_state: Dict[str, Dict[str, Any]] = {}
_breaker_lock = threading.Lock()

# 跨进程共享存储（可选）：默认 None = 进程内模式
_shared_store: Optional[Any] = None


def configure_shared_state(store: Optional[Any]) -> None:
    """注入跨进程共享存储（SharedStateStore）。

    由 scripts/api.py 启动时按 PEC_SHARED_STATE_DB 调用；测试可直接注入
    临时实例。传 None 恢复进程内模式。
    """
    global _shared_store
    _shared_store = store


def breaker_record_failure(action: str) -> None:
    """记录一次失败；连续失败达阈值即打开熔断（记录 opened_at）。"""
    if _shared_store is not None:
        _shared_store.breaker_record_failure(action, TOOL_BREAKER_THRESHOLD)
        return
    with _breaker_lock:
        state = _breaker_state.setdefault(action, {"failures": 0, "opened_at": None})
        state["failures"] += 1
        if state["failures"] >= TOOL_BREAKER_THRESHOLD:
            state["opened_at"] = time.monotonic()


def breaker_record_success(action: str) -> None:
    """一次成功即清零该工具的连续失败计数（半开成功 → 完全恢复）。"""
    if _shared_store is not None:
        _shared_store.breaker_record_success(action)
        return
    with _breaker_lock:
        _breaker_state.pop(action, None)


def breaker_is_open(action: str, now: Optional[float] = None) -> bool:
    """判断某工具是否处于熔断中。

    RESET_SEC 到期后自动「半开」：清掉计数并放行本次调用，由随后的
    成功/失败重新决定是否再次熔断。

    注意 now 参数仅在进程内模式下生效（monotonic 不可跨进程传递）；
    共享模式用存储侧墙钟自行判断冷却。
    """
    if not TOOL_BREAKER_ENABLED:
        return False
    if _shared_store is not None:
        failures, opened_ts = _shared_store.breaker_snapshot(action)
        if failures < TOOL_BREAKER_THRESHOLD or opened_ts is None:
            return False
        if time.time() - opened_ts >= TOOL_BREAKER_RESET_SEC:
            _shared_store.breaker_record_success(action)  # 半开：放行探测
            return False
        return True
    now = time.monotonic() if now is None else now
    with _breaker_lock:
        state = _breaker_state.get(action)
        if not state or state.get("failures", 0) < TOOL_BREAKER_THRESHOLD:
            return False
        opened_at = state.get("opened_at")
        if opened_at is None:
            return False
        if now - opened_at >= TOOL_BREAKER_RESET_SEC:
            _breaker_state.pop(action, None)  # 半开：放行探测
            return False
        return True


def reset_breakers() -> None:
    """清空全部熔断状态（测试 / 运维手动复位用）。"""
    if _shared_store is not None:
        _shared_store.breaker_reset_all()
        return
    with _breaker_lock:
        _breaker_state.clear()


# ============================================================
# Day2-3 幂等缓存
# ============================================================
# 【局限 L2 → 已提供可选解法】进程内 LRU，最多 _IDEMPOTENT_MAX 条。
# 共享模式（configure_shared_state 注入后）落 SQLite，带 TTL
# （PEC_IDEM_TTL_SEC，默认 600s），跨进程可见。
_IDEMPOTENT_MAX = 512
_IDEMPOTENT_TTL_SEC = float(os.getenv("PEC_IDEM_TTL_SEC", "600"))
_idempotent_cache: "OrderedDict[str, str]" = OrderedDict()
_idempotent_lock = threading.Lock()


def idempotency_key(action: str, args: Any, context: Optional[Dict[str, Any]]) -> str:
    """幂等键 = thread_id + 工具名 + 参数哈希。

    thread_id 取自 context（缺省 "-"）：同一任务内重复调用同工具同参数
    才会命中缓存；不同 thread_id 之间不串味。

    租户隔离说明：thread_id 遵循鉴权层的命名约定 "<tenant>-<后缀>"
    （scripts/auth.assert_thread_owner 强制校验归属），因此键里天然带租户
    边界，无需显式 tenant 前缀。不传 thread_id 的匿名请求共享 "-" 键空间，
    语义上仍正确——同 thread_id + 同工具 + 同参数的缓存结果一致。
    """
    thread_id = (context or {}).get("thread_id", "-")
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(args)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{thread_id}|{action}|{digest}"


def _idempotent_lookup(key: str) -> Optional[str]:
    if _shared_store is not None:
        # DB 读失败按未命中处理（重新执行，语义安全），见 wrapper_state 模块说明
        return _shared_store.idem_lookup(key, _IDEMPOTENT_TTL_SEC)
    with _idempotent_lock:
        if key in _idempotent_cache:
            _idempotent_cache.move_to_end(key)  # LRU：命中即刷新
            return _idempotent_cache[key]
        return None


def _idempotent_store(key: str, result: str) -> None:
    if _shared_store is not None:
        _shared_store.idem_store(key, result)
        return
    with _idempotent_lock:
        _idempotent_cache[key] = result
        _idempotent_cache.move_to_end(key)
        while len(_idempotent_cache) > _IDEMPOTENT_MAX:
            _idempotent_cache.popitem(last=False)


def clear_idempotent_cache() -> None:
    """清空幂等缓存（测试 / 运维用）。"""
    if _shared_store is not None:
        _shared_store.idem_clear_all()
        return
    with _idempotent_lock:
        _idempotent_cache.clear()


# ============================================================
# Day2-4 权限白名单
# ============================================================
# 节点名 -> 允许的工具列表（或 "*" 表示全允许）。
# 未配置的节点默认全允许（宽松兜底），保持对旧调用方兼容。
PERMISSION_MAP: Dict[str, Any] = dict(TOOL_PERMISSION_MAP or {})


def check_permission(action: str, context: Optional[Dict[str, Any]]) -> bool:
    """判断当前调用上下文是否有权执行该工具。

    返回 False 表示越权，调用方必须【不执行】工具。
    无节点信息（context 缺 node_name）时不拦截——权限机制依赖调用方
    显式传 node_name，未接线前等价于不启用。
    """
    if not TOOL_PERMISSION_ENABLED:
        return True
    node = (context or {}).get("node_name")
    if not node:
        return True
    allowed = PERMISSION_MAP.get(node)
    if allowed is None or allowed == "*":
        return True
    try:
        return action in set(allowed)
    except TypeError:  # 配置写错类型时不误伤：按放行处理
        return True


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
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """输出一条结构化工具调用日志。

    字段固定：thread_id / node / tool / duration_ms / ok / error_type / args_digest，
    便于事后按 thread_id 串起一条完整链路。extra 用于附加旁路信息
    （如 cached / throttled），不改变固定字段。
    """
    ctx = context or {}
    record: Dict[str, Any] = {
        "event": "tool_call",
        "tool": action,
        "thread_id": ctx.get("thread_id", "-"),
        "node": ctx.get("node_name", "-"),
        "iteration": ctx.get("iteration"),
        "duration_ms": round(duration * 1000, 1),
        "ok": ok,
        "error_type": error_type.value if error_type else None,
        "args_digest": _digest(args),
    }
    if extra:
        record.update(extra)
    logger.info(json.dumps(record, ensure_ascii=False))


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

    检查顺序（短路即返回，且【不执行】工具）：
      1. 权限白名单：越权 → PERMISSION_DENIED
      2. 熔断器    ：熔断中 → CIRCUIT_OPEN
      3. 幂等缓存  ：只读工具命中 → 直接返回缓存
      4. 真正执行  ：run_with_timeout

    返回: (结果字符串, 错误类型 | None, 耗时秒)
    结果字符串始终以 _ERROR_MARKERS 前缀表达失败，保证与 is_tool_success 一致。
    """
    # ---- 1. 权限白名单：越权不执行 ----
    if not check_permission(action, context):
        duration = 0.0
        message = format_error(action, ToolErrorType.PERMISSION_DENIED)
        log_tool_call(
            action, context, duration, ok=False,
            error_type=ToolErrorType.PERMISSION_DENIED, args=args,
            extra={"denied": True},
        )
        return message, ToolErrorType.PERMISSION_DENIED, duration

    # ---- 2. 熔断器：熔断中不执行 ----
    if breaker_is_open(action):
        duration = 0.0
        message = format_error(action, ToolErrorType.CIRCUIT_OPEN, sec=TOOL_BREAKER_RESET_SEC)
        log_tool_call(
            action, context, duration, ok=False,
            error_type=ToolErrorType.CIRCUIT_OPEN, args=args,
            extra={"breaker": "open"},
        )
        return message, ToolErrorType.CIRCUIT_OPEN, duration

    # ---- 3. 幂等缓存：仅只读工具 ----
    use_cache = TOOL_IDEMPOTENT_ENABLED and action in READ_ONLY_TOOLS
    cache_key = idempotency_key(action, args, context) if use_cache else None
    if use_cache:
        cached = _idempotent_lookup(cache_key)
        if cached is not None:
            log_tool_call(
                action, context, 0.0, ok=True, error_type=None, args=args,
                extra={"cached": True},
            )
            return cached, None, 0.0

    # ---- 4. 真正执行 ----
    timeout = TOOL_TIMEOUT_SEC if timeout is None else timeout
    result, exc, duration = run_with_timeout(tool_fn, args, timeout)

    if exc is not None:
        error_type = classify_error(exc)
        message = format_error(action, error_type, sec=timeout)
        breaker_record_failure(action)
        log_tool_call(action, context, duration, ok=False, error_type=error_type, args=args)
        return message, error_type, duration

    # 工具返回了内容，但要防住「返回空串」这类静默失败
    if result is None or (isinstance(result, str) and not result.strip()):
        error_type = ToolErrorType.UNKNOWN
        message = format_error(action, error_type) + "（返回空结果）"
        breaker_record_failure(action)
        log_tool_call(action, context, duration, ok=False, error_type=error_type, args=args)
        return message, error_type, duration

    breaker_record_success(action)
    if use_cache:
        _idempotent_store(cache_key, result)
    log_tool_call(action, context, duration, ok=True, error_type=None, args=args)
    return result, None, duration
