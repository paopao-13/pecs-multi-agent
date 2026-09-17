"""沙箱加固回归测试（2026-09-17）。

锁定三个**实测确认**过高危问题，防止回退：

  A. 库级文件 IO 逃逸 —— AST 黑名单只覆盖 builtins 面，预导入的 pandas/numpy
     自带文件读写，曾经可以读写任意文件（实测读出系统文件内容、写出文件落地）。
  B. 并发竞态 —— 输出捕获曾依赖替换进程级 sys.stdout，8 并发拉长窗口后 4 个串味。
  C. 死循环劫持 —— `while True: pass` 曾导致线程永久泄漏，且 sys.stdout 被永久
     指向一个 StringIO，整个进程失去输出能力。

每个用例都对应一次真实复现，不是理论防护。
"""
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tools.python_repl import python_repl

# 注意：`import tools.python_repl as X` 取到的是**函数**而非模块 ——
# tools/__init__.py 里的 `from tools.python_repl import python_repl` 会让
# `tools.python_repl` 这个属性被同名函数遮蔽。要访问模块级常量
# （如 SANDBOX_TIMEOUT_SEC），必须经 sys.modules 取真正的模块对象，
# 且**必须在 import 之后**取（否则模块尚未加载）。
repl_mod = sys.modules["tools.python_repl"]


def _run(code: str) -> str:
    return python_repl({"code": code})


def _blocked(out: str) -> bool:
    return "安全检查未通过" in out


# ============================================================
# A. 库级文件 IO 必须被拦（且不误伤内存计算）
# ============================================================

class TestLibraryIOBlocked:
    @pytest.mark.parametrize(
        "code,label",
        [
            ('print(pd.read_csv("C:/Windows/win.ini", nrows=1))', "pandas 读文件"),
            ('pd.DataFrame({"a":[1]}).to_csv("D:/简历/_do_not_write.csv")', "pandas 写文件"),
            ('print(pd.read_pickle("x.pkl"))', "pickle 反序列化"),
            ('pd.ExcelWriter("D:/简历/_do_not_write.xlsx")', "ExcelWriter"),
            ('print(np.loadtxt("C:/Windows/win.ini"))', "numpy 读文件"),
            ('np.save("D:/简历/_do_not_write.npy", np.array([1]))', "numpy 写文件"),
            ('import openpyxl', "openpyxl（纯 IO 库，已从白名单移除）"),
        ],
    )
    def test_io_paths_blocked(self, code, label):
        assert _blocked(_run(code)), f"{label} 未被拦截：{_run(code)[:120]}"

    def test_attribute_value_bypass_blocked(self):
        """`f = pd.read_csv` 这种先取属性再调用的绕过，也必须拦。"""
        assert _blocked(_run("f = pd.read_csv\nprint(f)"))

    @pytest.mark.parametrize(
        "code,expect",
        [
            ('print(pd.DataFrame({"a":[1,2]}).a.sum())', "3"),
            ('print(pd.DataFrame({"a":[1]}).to_dict())', "{'a': {0: 1}}"),
            ('print(pd.DataFrame({"a":[1]}).a.to_list())', "[1]"),
            ('print(np.array([1,2,3]).sum())', "6"),
            ('print(math.sqrt(144))', "12.0"),
        ],
    )
    def test_memory_ops_still_work(self, code, expect):
        """内存计算不能被误伤——to_dict/to_list 这类"to_"开头但非 IO 的方法必须可用。"""
        out = _run(code)
        assert not _blocked(out), f"正常计算被误拦：{out[:120]}"
        assert expect in out


# ============================================================
# B. 并发输出隔离
# ============================================================

class TestConcurrentIsolation:
    def test_concurrent_outputs_do_not_bleed(self):
        """8 个并发任务，每个的输出只能含自己的标记（修复前实测 4/8 串味）。"""
        def job(i):
            code = (
                f'print("TASK-{i}-START")\n'
                f'_t = sum(k * k for k in range(200000))\n'
                f'print("TASK-{i}-END")'
            )
            return i, _run(code)

        with ThreadPoolExecutor(max_workers=8) as ex:
            results = list(ex.map(job, range(8)))

        for i, out in results:
            nums = set(re.findall(r"TASK-(\d+)", out))
            assert nums == {str(i)}, f"任务 {i} 的输出串味：{sorted(nums)}"

    def test_stdout_not_replaced(self):
        """执行沙箱代码不得改变进程级 sys.stdout 的类型。"""
        before = type(sys.stdout).__name__
        _run('print("hello")')
        assert type(sys.stdout).__name__ == before


# ============================================================
# C. 死循环必须被强制终止，且不劫持 stdout
# ============================================================

class TestHangTermination:
    def test_infinite_loop_is_killed(self, monkeypatch):
        """死循环应被强制终止（而非永久占用线程）。"""
        monkeypatch.setattr(repl_mod, "SANDBOX_TIMEOUT_SEC", 3.0)
        threads_before = threading.active_count()

        t0 = time.time()
        out = _run("while True: pass")
        elapsed = time.time() - t0

        assert "超时" in out, f"死循环未被判定为超时：{out[:120]}"
        assert elapsed < 20, f"终止耗时异常（{elapsed:.1f}s），可能未真正中断"
        # 给子进程留一点回收时间，再确认没有把线程留在本进程
        time.sleep(0.5)
        assert threading.active_count() <= threads_before + 1, "存在线程泄漏"

    def test_memory_blowup_loop_is_killed(self, monkeypatch):
        """内存膨胀型死循环同样应被终止。"""
        monkeypatch.setattr(repl_mod, "SANDBOX_TIMEOUT_SEC", 3.0)
        out = _run('x = []\nwhile True: x.append("a" * 10000)')
        assert "超时" in out

    def test_stdout_survives_hang(self, monkeypatch):
        """死循环后进程 stdout 仍须可用（修复前会被永久劫持为 StringIO）。"""
        monkeypatch.setattr(repl_mod, "SANDBOX_TIMEOUT_SEC", 3.0)
        before = type(sys.stdout).__name__
        _run("while True: pass")
        assert type(sys.stdout).__name__ == before, "sys.stdout 被执行过程劫持了"

    def test_normal_then_hang_then_normal(self, monkeypatch):
        """死循环之后，沙箱仍能正常工作（不能一次挂起就把功能废掉）。"""
        monkeypatch.setattr(repl_mod, "SANDBOX_TIMEOUT_SEC", 3.0)
        assert "42" in _run("print(40 + 2)")
        assert "超时" in _run("while True: pass")
        assert "7" in _run("print(3 + 4)")
