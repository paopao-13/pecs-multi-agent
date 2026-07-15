#!/bin/bash
# ====================================================================
# PECS 多智能体框架 —— 真实 API 评测一键脚本 (GLM-4.7-Flash)
#
# 运行完整评测：GAIA 多智能体 + ReAct 基线 + WebShop + 成本消融
# 最终汇总报告保存到 results/target_report.json
#
# 用法:
#   bash scripts/run_real_evaluation.sh
# ====================================================================

set -e

# ---------- 定位项目根目录 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "  PECS 真实 API 评测 (GLM-4.7-Flash)"
echo "  项目根目录: $PROJECT_ROOT"
echo "  开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 检查 .env ----------
if [ ! -f .env ]; then
    echo "错误：未找到 .env 文件，请先配置 LLM_API_KEY"
    echo "  cp .env.example .env"
    echo "  然后编辑 .env 填入你的 API Key"
    exit 1
fi

# ---------- 运行评测 ----------
echo ""
echo "--- [1/2] 运行完整评测（GAIA + WebShop + ReAct基线 + 成本消融） ---"
python -c "
from benchmarks.report import run_sample_report
import json

report = run_sample_report()
print()
print('=== 评测结果摘要 ===')
print(f'GAIA L1 准确率:      {report[\"metrics\"][\"gaia_l1_accuracy\"]:.2%}')
print(f'GAIA ReAct 准确率:   {report[\"metrics\"][\"gaia_react_accuracy\"]:.2%}')
print(f'GAIA 提升幅度:       +{report[\"metrics\"][\"gaia_improvement_pp\"]:.1f}pp')
print(f'WebShop 成功率:      {report[\"metrics\"][\"webshop_success_rate\"]:.2%}')
print(f'WebShop ReAct 成功率: {report[\"metrics\"][\"webshop_react_success_rate\"]:.2%}')
print(f'WebShop 提升幅度:    +{report[\"metrics\"][\"webshop_improvement_pp\"]:.1f}pp')
print(f'Token 节省比例:      {report[\"metrics\"][\"token_savings_pct\"]:.1%}')
print()
print(f'报告已保存到: results/target_report.json')
print(f'运行模式: {report[\"note\"]}')
"

echo ""
echo "--- [2/2] 运行消融实验（6组配置） ---"
bash scripts/run_all_ablation.sh 5

echo ""
echo "============================================================"
echo "  评测全部完成！"
echo "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  报告路径: $PROJECT_ROOT/results/target_report.json"
echo "  消融报告: $PROJECT_ROOT/results/ablation_report.json"
echo "============================================================"
