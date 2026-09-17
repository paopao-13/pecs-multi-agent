"""
工具集注册表

每个工具是一个函数，接受参数字典，返回字符串结果。
Executor 根据计划中的 action 名称，从这里找到对应工具并调用。
"""
from tools.web_search import web_search
from tools.web_browser import web_browser
from tools.python_repl import python_repl
from tools.file_reader import file_reader
from tools.file_parser import file_parser
from tools.api_caller import api_caller
from tools.webshop import webshop_select
from tools.multimodal import multimodal_process

from config import TOOL_WRAPPER_ENABLED, RUN_MODE
from tools.wrapper import invoke_tool

# 工具注册表：action名称 → 工具函数
TOOL_REGISTRY = {
    "search": web_search,
    "web_browse": web_browser,
    "python": python_repl,
    "file_read": file_reader,
    "file_parse": file_parser,
    "multimodal": multimodal_process,
    "api_call": api_caller,
    "webshop": webshop_select,
}

# 工具清单的两份"真相"及各自职责（2026-09-17 清理后明确）：
#   ① `TOOL_REGISTRY`（本文件）：决定"哪些工具能被**执行**"——execute_tool 按它分派。
#   ② `agents/planner.py` 的 `PLANNER_SYSTEM_PROMPT`：决定"哪些工具**被 LLM 看到**"。
# 两者需手工保持一致；`agents/planner.py` 的 `allowed_actions` 会在运行时过滤白名单外的
# action（LLM 幻觉出的工具被静默丢弃，这是刻意的安全设计）。
#
# ⚠️ 这里原本有一份 `TOOL_DESCRIPTIONS` 字典，已于 2026-09-17 删除，原因：
#   - 它**从未被任何代码消费**（planner.py 只 import 不用、react_baseline.py 亦然），
#     属纯死代码；
#   - 它的措辞与 prompt 里的工具说明**并不一致**（例：这里是"输入查询关键词，返回搜索
#     结果摘要"，prompt 里是"查找实时信息"），留着反而形成"改它就能影响 Planner"的
#     维护陷阱——实际改了毫无作用；
#   - 唯一可行的替代（让 Planner prompt 从它渲染）会**改变 prompt 内容**，进而改变
#     LLM 规划行为，破坏 GAIA/WebShop 跑分的可比性。收益不抵风险，故直接删除。

# 【约束 K4】内容生成 Pipeline 工具仅在 RUN_MODE=business 时注册，
# 保证 eval 模式（GAIA / WebShop 评测）的可用工具集与改造前完全一致。
if RUN_MODE == "business":
    from tools.content_pipeline import CONTENT_PIPELINE_TOOLS  # noqa: E402 - 需在注册表定义后就地合并
    TOOL_REGISTRY.update(CONTENT_PIPELINE_TOOLS)


# 工具执行结果中的错误标记前缀（用于判定执行成功/失败）
#
# ⚠️ 这是一条**双向契约**（tools/wrapper.py 的约束 K1 也依赖它）：
#   凡是工具失败文案，都必须以本元组中的某个前缀开头；反过来，本元组
#   也必须覆盖所有失败文案。任一侧漏掉，失败就会被静默判成"成功"。
#
# "工具执行失败" 是 2026-09-12 补入的：execute_tool 的**原路径**（wrapper
# 关闭时）在工具抛异常时返回该文案，但它此前不在本元组里 → is_tool_success
# 返回 True，进而 executor 记 success=True、步骤置 done、Critic 走"合格"
# 路径、Planner 不重试 —— **一次工具崩溃被完整地伪装成成功**，最终答案
# 建立在失败步骤上却全程无人察觉。这类"静默失败"比直接崩溃更危险，因为
# 崩溃至少会被发现。wrapper 路径的错误文案以 "执行错误：" 开头（已覆盖），
# 原路径此前是唯一的漏网分支。
_ERROR_MARKERS = ("错误", "执行错误", "安全检查未通过", "工具执行失败")


def is_tool_success(result: str) -> bool:
    """判定工具返回结果是否表示执行成功（不含错误标记）。

    仅依据显式错误前缀判定，避免把含"失败"字样的正常值
    （如统计结果"失败率 = 0.05"）误判为执行失败。
    """
    if not result:
        return False
    return not result.startswith(_ERROR_MARKERS)


def execute_tool(action: str, args: dict, context: dict = None) -> str:
    """
    执行工具调用

    参数:
        action: 工具名称（search / python / file_read / api_call）
        args: 工具参数字典
        context: 可选的调用上下文 {thread_id, node_name, iteration}，
                 仅包装器开启时用于结构化日志；不传不影响功能

    返回:
        工具执行结果字符串（失败时以 _ERROR_MARKERS 前缀开头）

    说明:
        TOOL_WRAPPER_ENABLED=false（默认）时走改造前的原路径，行为逐字一致；
        开启后走 tools/wrapper.py，获得超时、异常分类、结构化日志。
        熔断 / 幂等 / 权限白名单是包装器内部能力，各自有独立子开关
        （TOOL_BREAKER_ENABLED / TOOL_IDEMPOTENT_ENABLED / TOOL_PERMISSION_ENABLED），
        仅在总开关 TOOL_WRAPPER_ENABLED=true 时生效，且同样默认关闭。
    """
    tool_fn = TOOL_REGISTRY.get(action)
    if tool_fn is None:
        return f"错误：未知工具 '{action}'，可用工具：{list(TOOL_REGISTRY.keys())}"

    if not TOOL_WRAPPER_ENABLED:
        # ---- 原路径：与改造前逐字一致，保证 eval 模式行为不变 ----
        try:
            return tool_fn(args)
        except Exception as e:
            return f"工具执行失败 [{action}]: {type(e).__name__}: {str(e)}"

    # ---- 包装路径：超时 + 异常分类 + 结构化日志 ----
    #
    # 本函数对调用方的契约是「**永不抛异常**，失败以 _ERROR_MARKERS 前缀返回」。
    # 原路径用 try/except 满足了；包装路径此前只依赖 invoke_tool 的内部实现来
    # 保证——而 invoke_tool 除"真正执行"外还有若干**辅助动作**（熔断计数
    # breaker_record_*、结构化日志 log_tool_call、幂等落库 _idempotent_store），
    # 这些都可能在存储故障时抛异常（如 PEC_SHARED_STATE_DB 指向的 SQLite 被
    # 锁住/损坏）。一旦如此，异常会直接冒到 executor_node，把"一次工具调用失败"
    # 升级为"整个任务崩溃"——辅助功能不该有拖垮主流程的能力。
    #
    # 这里补上兜底，让契约在两条路径上一致成立（对称性修复）。
    try:
        result, _error_type, _duration = invoke_tool(tool_fn, action, args, context=context)
        return result
    except Exception as e:
        return f"工具执行失败 [{action}]: {type(e).__name__}: {str(e)}"
