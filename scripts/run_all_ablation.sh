#!/bin/bash
# ====================================================================
# PECS 多智能体框架 —— 消融实验一键运行脚本
#
# 依次运行6组消融配置（full_pecs / no_critic / no_synthesizer / single_agent / critic_no_reflect / synthesizer_no_replan），
# 最后汇总结果到 results/ablation_report.json。
#
# 用法:
#   bash scripts/run_all_ablation.sh              # 全部样本
#   bash scripts/run_all_ablation.sh 10           # 每组10个样本
# ====================================================================

set -e

# ---------- 定位项目根目录 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "  PECS 消融实验 —— 批量执行"
echo "  项目根目录: $PROJECT_ROOT"
echo "  开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 可选参数：样本数量 ----------
NUM_SAMPLES=""
if [ -n "$1" ]; then
    NUM_SAMPLES="--num-samples $1"
    echo "  每组样本数: $1"
else
    echo "  每组样本数: 全部"
fi
echo ""

# ---------- 依次运行6组消融配置 ----------
CONFIGS=("full_pecs" "no_critic" "no_synthesizer" "single_agent" "critic_no_reflect" "synthesizer_no_replan")

for config in "${CONFIGS[@]}"; do
    echo ""
    echo "------------------------------------------------------------"
    echo "  正在运行: $config"
    echo "------------------------------------------------------------"
    python -m benchmarks.ablation_eval --config "$config" $NUM_SAMPLES
    echo ""
done

# ---------- 汇总结果 ----------
echo ""
echo "============================================================"
echo "  全部消融配置运行完成，正在生成汇总报告..."
echo "============================================================"
python -m benchmarks.ablation_eval $NUM_SAMPLES

echo ""
echo "============================================================"
echo "  消融实验全部完成！"
echo "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  报告路径: $PROJECT_ROOT/results/ablation_report.json"
echo "============================================================"
