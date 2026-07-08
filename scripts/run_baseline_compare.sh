#!/bin/bash
# ====================================================================
# 多框架基线对比实验一键运行脚本
#
# 执行顺序: ReAct → AutoGen → CrewAI → PECS(完整)
# 结果汇总输出到 results/baseline_compare.json
#
# 用法:
#   bash scripts/run_baseline_compare.sh              # 全部样本
#   bash scripts/run_baseline_compare.sh 10           # 每组10个样本
#
# 依赖:
#   - 核心依赖: langchain, langgraph, python-dotenv
#   - 对照框架: pip install pyautogen crewai
#   - 如果某个框架未安装，对应步骤会跳过并打印警告
# ====================================================================

# 不使用 set -e，因为我们需要在单步失败时继续执行后续步骤

# ---------- 定位项目根目录 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "============================================================"
echo "  多框架基线对比实验"
echo "  执行顺序: ReAct → AutoGen → CrewAI → PECS → 汇总报告"
echo "  项目根目录: $PROJECT_ROOT"
echo "  开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ---------- 可选参数：样本数量 ----------
NUM_SAMPLES="$1"
if [ -n "$NUM_SAMPLES" ]; then
    echo "  每组样本数: $NUM_SAMPLES"
    # 为 python -c 调用准备参数
    PY_NUM_SAMPLES="$NUM_SAMPLES"
    CLI_NUM_SAMPLES="--num-samples $NUM_SAMPLES"
else
    echo "  每组样本数: 全部"
    PY_NUM_SAMPLES="None"
    CLI_NUM_SAMPLES=""
fi
echo ""

# ---------- 结果文件路径 ----------
RESULTS_DIR="$PROJECT_ROOT/results"
mkdir -p "$RESULTS_DIR"

# 跟踪各步骤执行状态
declare -a STEP_NAMES=()
declare -a STEP_STATUS=()
declare -a STEP_FILES=()

# ====================================================================
# 步骤 1/5: ReAct 单 Agent 基线
# ====================================================================
echo "------------------------------------------------------------"
echo "  [1/5] 运行 ReAct 单 Agent 基线..."
echo "------------------------------------------------------------"

python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from benchmarks.react_baseline import evaluate_react_gaia
n = $PY_NUM_SAMPLES
result = evaluate_react_gaia(n)
print(f'ReAct 完成: 准确率={result[\"accuracy\"]:.2%}, 平均Token={result[\"avg_tokens_per_task\"]}')
"

if [ $? -eq 0 ]; then
    echo "  [OK] ReAct 基线运行成功"
    STEP_NAMES+=("ReAct")
    STEP_STATUS+=("SUCCESS")
    STEP_FILES+=("gaia_react_baseline.json")
else
    echo "  [WARNING] ReAct 基线运行失败，跳过此步骤"
    STEP_NAMES+=("ReAct")
    STEP_STATUS+=("FAILED")
    STEP_FILES+=("gaia_react_baseline.json")
fi
echo ""

# ====================================================================
# 步骤 2/5: AutoGen 框架评测
# ====================================================================
echo "------------------------------------------------------------"
echo "  [2/5] 运行 AutoGen 框架评测..."
echo "------------------------------------------------------------"

python -m benchmarks.eval_autogen $CLI_NUM_SAMPLES

if [ $? -eq 0 ]; then
    echo "  [OK] AutoGen 评测成功"
    STEP_NAMES+=("AutoGen")
    STEP_STATUS+=("SUCCESS")
    STEP_FILES+=("gaia_autogen.json")
else
    echo "  [WARNING] AutoGen 评测失败（可能未安装 pyautogen），跳过此步骤"
    echo "           安装命令: pip install pyautogen"
    STEP_NAMES+=("AutoGen")
    STEP_STATUS+=("FAILED")
    STEP_FILES+=("gaia_autogen.json")
fi
echo ""

# ====================================================================
# 步骤 3/5: CrewAI 框架评测
# ====================================================================
echo "------------------------------------------------------------"
echo "  [3/5] 运行 CrewAI 框架评测..."
echo "------------------------------------------------------------"

python -m benchmarks.eval_crewai $CLI_NUM_SAMPLES

if [ $? -eq 0 ]; then
    echo "  [OK] CrewAI 评测成功"
    STEP_NAMES+=("CrewAI")
    STEP_STATUS+=("SUCCESS")
    STEP_FILES+=("gaia_crewai.json")
else
    echo "  [WARNING] CrewAI 评测失败（可能未安装 crewai），跳过此步骤"
    echo "           安装命令: pip install crewai"
    STEP_NAMES+=("CrewAI")
    STEP_STATUS+=("FAILED")
    STEP_FILES+=("gaia_crewai.json")
fi
echo ""

# ====================================================================
# 步骤 4/5: PECS 多智能体框架（完整版）
# ====================================================================
echo "------------------------------------------------------------"
echo "  [4/5] 运行 PECS 多智能体框架评测..."
echo "------------------------------------------------------------"

python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from benchmarks.gaia_eval import evaluate_gaia
n = $PY_NUM_SAMPLES
result = evaluate_gaia(n)
print(f'PECS 完成: 准确率={result[\"accuracy\"]:.2%}, 平均Token={result[\"avg_tokens_per_task\"]}')
"

if [ $? -eq 0 ]; then
    echo "  [OK] PECS 框架评测成功"
    STEP_NAMES+=("PECS")
    STEP_STATUS+=("SUCCESS")
    STEP_FILES+=("gaia_multi_agent.json")
else
    echo "  [WARNING] PECS 框架评测失败，跳过此步骤"
    STEP_NAMES+=("PECS")
    STEP_STATUS+=("FAILED")
    STEP_FILES+=("gaia_multi_agent.json")
fi
echo ""

# ====================================================================
# 步骤 5/5: 聚合报告
# ====================================================================
echo "------------------------------------------------------------"
echo "  [5/5] 生成聚合报告..."
echo "------------------------------------------------------------"

# 调用 benchmarks.report 模块聚合结果
# report 模块会读取 GAIA_SAMPLES 环境变量控制样本数
if [ -n "$NUM_SAMPLES" ]; then
    GAIA_SAMPLES="$NUM_SAMPLES" python -m benchmarks.report
else
    python -m benchmarks.report
fi

if [ $? -eq 0 ]; then
    echo "  [OK] 聚合报告生成成功"
    STEP_NAMES+=("Report")
    STEP_STATUS+=("SUCCESS")
    STEP_FILES+=("target_report.json")
else
    echo "  [WARNING] 聚合报告生成失败"
    STEP_NAMES+=("Report")
    STEP_STATUS+=("FAILED")
    STEP_FILES+=("target_report.json")
fi
echo ""

# ====================================================================
# 汇总：生成 baseline_compare.json 并打印对比表
# ====================================================================
echo "============================================================"
echo "  生成多框架对比汇总..."
echo "============================================================"

python -c "
import json, os

results_dir = '$RESULTS_DIR'
output_file = os.path.join(results_dir, 'baseline_compare.json')

# 各框架的结果文件映射
framework_files = {
    'ReAct': 'gaia_react_baseline.json',
    'AutoGen': 'gaia_autogen.json',
    'CrewAI': 'gaia_crewai.json',
    'PECS': 'gaia_multi_agent.json',
}

comparison = {}
for name, filename in framework_files.items():
    filepath = os.path.join(results_dir, filename)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            comparison[name] = {
                'accuracy': data.get('accuracy', 0),
                'correct_count': data.get('correct_count', 0),
                'total_samples': data.get('total_samples', 0),
                'total_tokens': data.get('total_tokens', 0),
                'avg_tokens_per_task': data.get('avg_tokens_per_task', 0),
                'agent_type': data.get('agent_type', name.lower()),
            }
        except Exception as e:
            comparison[name] = {'error': str(e)}
    else:
        comparison[name] = {'error': 'result file not found'}

# 保存汇总结果
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(comparison, f, ensure_ascii=False, indent=2)
print(f'汇总结果已保存到: {output_file}')
"

# ====================================================================
# 打印最终汇总表
# ====================================================================
echo ""
echo "============================================================"
echo "  多框架基线对比 —— 最终汇总表"
echo "  结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo ""

python -c "
import json, os

results_dir = '$RESULTS_DIR'

framework_files = {
    'ReAct': 'gaia_react_baseline.json',
    'AutoGen': 'gaia_autogen.json',
    'CrewAI': 'gaia_crewai.json',
    'PECS': 'gaia_multi_agent.json',
}

# 打印表头
print('-' * 75)
print(f'{\"框架\":<12} {\"准确率\":<10} {\"正确数\":<8} {\"总样本\":<8} {\"平均Token\":<12} {\"状态\":<8}')
print('-' * 75)

for name, filename in framework_files.items():
    filepath = os.path.join(results_dir, filename)
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            acc = data.get('accuracy', 0)
            correct = data.get('correct_count', 0)
            total = data.get('total_samples', 0)
            avg_tokens = data.get('avg_tokens_per_task', 0)
            print(f'{name:<12} {acc:<10.2%} {correct:<8} {total:<8} {avg_tokens:<12} {\"OK\":<8}')
        except Exception as e:
            print(f'{name:<12} {\"N/A\":<10} {\"N/A\":<8} {\"N/A\":<8} {\"N/A\":<12} {\"ERROR\":<8}')
    else:
        print(f'{name:<12} {\"N/A\":<10} {\"N/A\":<8} {\"N/A\":<8} {\"N/A\":<12} {\"SKIP\":<8}')

print('-' * 75)
print()

# 打印各步骤执行状态
print('各步骤执行状态:')
print('-' * 40)
step_names = ['ReAct', 'AutoGen', 'CrewAI', 'PECS', 'Report']
step_files = ['gaia_react_baseline.json', 'gaia_autogen.json', 'gaia_crewai.json', 'gaia_multi_agent.json', 'target_report.json']
for name, filename in zip(step_names, step_files):
    filepath = os.path.join(results_dir, filename)
    status = 'OK' if os.path.exists(filepath) else 'SKIP/FAIL'
    print(f'  {name:<12} {status}')
print('-' * 40)
"

echo ""
echo "  汇总文件: $RESULTS_DIR/baseline_compare.json"
echo "  报告文件: $RESULTS_DIR/target_report.json"
echo "============================================================"
