# ====================================================================
# PECS 多智能体框架 —— 真实 API 评测一键脚本 (GLM-4.7-Flash)
#
# 运行完整评测：GAIA 多智能体 + ReAct 基线 + WebShop + 成本消融
# 最终汇总报告保存到 results/target_report.json
#
# 用法:
#   .\scripts\run_real_evaluation.ps1
# ====================================================================

$ErrorActionPreference = "Stop"

# ---------- 定位项目根目录 ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "============================================================"
Write-Host "  PECS 真实 API 评测 (GLM-4.7-Flash)"
Write-Host "  项目根目录: $ProjectRoot"
Write-Host "  开始时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "============================================================"

# ---------- 检查 .env ----------
if (-not (Test-Path ".env")) {
    Write-Host "错误：未找到 .env 文件，请先配置 LLM_API_KEY"
    Write-Host "  复制 .env.example 为 .env，然后填入你的 API Key"
    exit 1
}

# ---------- 运行评测 ----------
Write-Host ""
Write-Host "--- [1/2] 运行完整评测（GAIA + WebShop + ReAct基线 + 成本消融） ---"

$pythonCode = @"
from benchmarks.report import run_sample_report
import json

report = run_sample_report()
print()
print('=== 评测结果摘要 ===')
print(f'GAIA L1 准确率:      {report["metrics"]["gaia_l1_accuracy"]:.2%}')
print(f'GAIA ReAct 准确率:   {report["metrics"]["gaia_react_accuracy"]:.2%}')
print(f'GAIA 提升幅度:       +{report["metrics"]["gaia_improvement_pp"]:.1f}pp')
print(f'WebShop 成功率:      {report["metrics"]["webshop_success_rate"]:.2%}')
print(f'WebShop ReAct 成功率: {report["metrics"]["webshop_react_success_rate"]:.2%}')
print(f'WebShop 提升幅度:    +{report["metrics"]["webshop_improvement_pp"]:.1f}pp')
print(f'Token 节省比例:      {report["metrics"]["token_savings_pct"]:.1%}')
print()
print(f'报告已保存到: results/target_report.json')
print(f'运行模式: {report["note"]}')
"@

python -c $pythonCode

Write-Host ""
Write-Host "--- [2/2] 运行消融实验（6组配置） ---"
python -m benchmarks.ablation_eval --config full_pecs --num-samples 5
python -m benchmarks.ablation_eval --config no_critic --num-samples 5
python -m benchmarks.ablation_eval --config no_synthesizer --num-samples 5
python -m benchmarks.ablation_eval --config single_agent --num-samples 5
python -m benchmarks.ablation_eval --config critic_no_reflect --num-samples 5
python -m benchmarks.ablation_eval --config synthesizer_no_replan --num-samples 5
python -m benchmarks.ablation_eval --num-samples 5

Write-Host ""
Write-Host "============================================================"
Write-Host "  评测全部完成！"
Write-Host "  结束时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "  报告路径: $ProjectRoot\results\target_report.json"
Write-Host "  消融报告: $ProjectRoot\results\ablation_report.json"
Write-Host "============================================================"
