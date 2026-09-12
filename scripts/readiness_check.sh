#!/usr/bin/env bash
# PECS 生产就绪度一键自检
#
# 用法：
#   bash scripts/readiness_check.sh          # 全量自检
#   bash scripts/readiness_check.sh --quick  # 跳过评测集（只跑单测与配置检查）
#
# 输出三部分：① 环境与配置现状 ② 单测 + 覆盖率门禁 ③ 评测集 ci 档
# 退出码：0 = 全部通过；非 0 = 有项目未通过（可直接用于 CI 或面试现场演示）
#
# 注意：所有 pytest 调用都带 --basetemp=<独立目录>。项目宿主环境有批量删除
# 守卫，缺省临时目录会在 pytest 清理阶段触发保护，导致退出码异常（假失败）。

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

# 优先用项目 venv（装了全部依赖），回退到系统 python
PY=""
for cand in \
  "C:/Users/jx/.workbuddy/binaries/python/envs/default/Scripts/python.exe" \
  "python3" "python"
do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "❌ 找不到可用的 Python 解释器"
  exit 1
fi

TMP=".readiness_tmp"
FAILED=0
line() { printf '%s\n' "------------------------------------------------------------"; }

echo "============================================================"
echo "  PECS 生产就绪度自检"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Python: $PY"
echo "============================================================"

# ---------- ① 环境与配置现状 ----------
line
echo "① 环境与配置"
line
"$PY" - <<'PYEOF'
import os

# 显式传路径：这里从 stdin 执行，没有 __file__，dotenv 的 find_dotenv()
# 靠栈帧回溯找调用者文件，会以 AssertionError 崩掉。
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.getcwd(), ".env"))
except ImportError:
    pass

checks = [
    ("运行模式 RUN_MODE", os.getenv("RUN_MODE", "eval"),
     "eval=工具加固全关（评测模式）；business=全开（演示模式）"),
    ("LLM 凭据", "已配置" if os.getenv("LLM_API_KEY") else "未配置",
     "未配置时 /run_task 立即 503（fail-fast）"),
    ("鉴权 PECS_API_KEYS", "已启用" if os.getenv("PECS_API_KEYS") else "未启用",
     "未启用时鉴权自动关闭（本地开发/CI/评测不受影响）"),
    ("跨进程状态 PEC_SHARED_STATE_DB", os.getenv("PEC_SHARED_STATE_DB") or "未设置",
     "未设置时限流/熔断/幂等均为进程内（多 worker 下失效）"),
    ("GAIA 本地镜像", os.getenv("PEC_GAIA_LOCAL_DIR") or "未设置",
     "已设置时不触碰 HuggingFace 缓存"),
]

try:
    import config as c
    checks += [
        ("工具超时 TOOL_TIMEOUT_SEC", str(getattr(c, "TOOL_TIMEOUT_SEC", "?")), "秒"),
        ("任务超时 PEC_RUN_TASK_TIMEOUT", str(os.getenv("PEC_RUN_TASK_TIMEOUT", "300")), "秒"),
        ("Token 硬上限 DEFAULT_TOKEN_BUDGET", str(getattr(c, "DEFAULT_TOKEN_BUDGET", "?")), "tokens"),
        ("降级阈值", "/".join(str(getattr(c, f"DEGRADE_THRESHOLD_{i}", "?")) for i in (1, 2, 3)),
         "70% / 85% / 95% 三级"),
        ("最大重试 MAX_RETRIES", str(getattr(c, "MAX_RETRIES", "?")), "次（指数退避 + 抖动）"),
    ]
except Exception as exc:
    print(f"  ⚠️ 读取 config.py 失败：{exc}")

for name, value, note in checks:
    print(f"  {name:36} = {value:14} # {note}")
PYEOF

# ---------- ② 单测 + 覆盖率门禁 ----------
line
echo "② 单测与覆盖率门禁（不含依赖真实 LLM 的用例）"
line
rm -rf "$TMP"
"$PY" -m pytest -q -m "not slow and not requires_api and not requires_api_key" \
  --basetemp="$TMP" \
  --cov=tools --cov=metrics --cov=logger --cov-report=term --cov-fail-under=60 \
  2>&1 | tail -6
PYTEST_RC=${PIPESTATUS[0]}
if [ "$PYTEST_RC" -ne 0 ]; then
  echo "❌ 单测或覆盖率门禁未通过（退出码 $PYTEST_RC）"
  FAILED=1
else
  echo "✅ 单测与覆盖率门禁通过"
fi

# ---------- ③ 评测集 ci 档 ----------
if [ "$QUICK" -eq 0 ]; then
  line
  echo "③ 评测集 ci 档（零 LLM 消耗，对抗用例零容忍）"
  line
  "$PY" evals/run_eval.py --mode ci --out "$TMP/eval_report.json" 2>&1 | tail -8
  EVAL_RC=${PIPESTATUS[0]}
  if [ "$EVAL_RC" -ne 0 ]; then
    echo "❌ 评测集未达通过线（退出码 $EVAL_RC）"
    FAILED=1
  else
    echo "✅ 评测集 ci 档通过"
  fi
else
  echo "(--quick 模式：跳过评测集 ci 档)"
fi

rm -rf "$TMP"

# ---------- 汇总 ----------
echo
echo "============================================================"
if [ "$FAILED" -eq 0 ]; then
  echo "  ✅ 生产就绪度自检全部通过"
else
  echo "  ❌ 自检未全部通过，请查看上方输出定位"
fi
echo "============================================================"
exit "$FAILED"
