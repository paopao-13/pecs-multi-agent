"""沙箱子进程执行入口（内部模块，**不**通过 TOOL_REGISTRY 注册）。

为什么需要独立进程
------------------
Python 没有强制中断线程的 API（CPython 的 `sys.settrace` 只能配合字节码事件，
对 `while True: pass` 这类纯计算循环也无效）。因此在本进程内跑沙箱代码时，一旦出现
死循环：

  1. 执行线程**永久泄漏** —— 每次泄漏一个，线程池会被逐个吃空；
  2. 外层超时（`run_with_timeout`）只能"放弃等待"，救不回线程。

进程可以 kill，所以把执行体挪到子进程后，超时即可**真正终止**（见
`tools/python_repl.py` 的 `_run_isolated`，用 `subprocess.run(timeout=)`）。

安全职责的划分
--------------
  - **AST 安全检查仍在主进程**（`python_repl` 里做）：快速失败，不必为被拒代码付
    一次进程启动的开销；
  - 本入口只负责"执行**已通过检查**的代码" + 把输出回传。

两条执行路径必须共用同一套沙箱 globals（这里复用 `_build_safe_globals`），
否则"进程内"与"子进程"的安全策略会漂移——那正是漏洞的温床。
"""
import io
import json
import os
import sys
import traceback

# 允许以 `python -m tools._sandbox_runner` 之外的方式被直接运行时也能找到包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.python_repl import _build_safe_globals  # noqa: E402


def main() -> int:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        _emit({"ok": False, "output": f"[沙箱子进程] 载荷解析失败: {exc}"})
        return 2

    code = payload.get("code", "")
    out_buffer = io.StringIO()
    # 注意：这里**不**重复做安全检查（主进程已做），但仍复用同一套白名单 globals，
    # 保证两条件路径的运行时环境一致（不暴露 __import__、print 只写局部 buffer）。
    safe_globals = _build_safe_globals(out_buffer)

    try:
        exec(code, safe_globals)
        _emit({"ok": True, "output": out_buffer.getvalue().strip()})
        return 0
    except Exception:
        partial = out_buffer.getvalue().strip()
        _emit({"ok": False, "output": partial, "traceback": traceback.format_exc()})
        return 1


def _emit(obj: dict) -> None:
    """把结果以 JSON 写到真实 stdout（子进程里 stdout 未被替换，可安全使用）。"""
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    sys.exit(main())
