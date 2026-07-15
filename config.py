"""
项目配置文件
管理 LLM API 密钥、模型参数、Token 预算等全局配置

配置加载优先级（高→低）：
  1. 环境变量（.env / 平台注入）— 用于 API Key 等敏感信息
  2. experiments/config.yaml — 用于模型参数、预算阈值、执行限制等实验配置
  3. 代码级默认值（本文件硬编码）— 兜底，确保无 YAML 时也可运行

多 Provider 支持：
  通过 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 环境变量配置任意 OpenAI 兼容的 LLM。
  兼容旧的 DEEPSEEK_* 变量名（LLM_* 优先）。
  支持的 Provider：DeepSeek-V3、GLM-4.7-Flash（免费）、Qwen 等。

YAML 配置与代码参数映射关系：
  model.model_name              → LLM_MODEL
  model.max_tokens              → LLM_MAX_TOKENS
  model.temperatures.*           → llm_utils.ROLE_TEMPERATURES（由 llm_utils 单独读取）
  token_budget.total            → DEFAULT_TOKEN_BUDGET
  token_budget.allocation.*     → BUDGET_ALLOCATION
  token_budget.degrade_threshold_1 → DEGRADE_THRESHOLD_1
  token_budget.degrade_threshold_2 → DEGRADE_THRESHOLD_2
  token_budget.degrade_threshold_3 → DEGRADE_THRESHOLD_3
  execution.max_iterations      → MAX_ITERATIONS
  execution.max_retries         → MAX_RETRIES
  execution.use_heuristics      → USE_HEURISTICS（默认值，运行时可覆盖）
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量（本地开发用，部署时通过平台环境变量注入）
load_dotenv()

# ============ YAML 配置加载 ============

_yaml_config: dict = {}
_yaml_path = Path(__file__).parent / "experiments" / "config.yaml"
if _yaml_path.exists():
    try:
        import yaml
        with open(_yaml_path, "r", encoding="utf-8") as f:
            _yaml_config = yaml.safe_load(f) or {}
    except ImportError:
        # PyYAML 未安装时使用代码级默认值
        pass

# 辅助函数：从 YAML 嵌套字典中安全取值
def _yaml_get(*keys, default=None):
    """从 _yaml_config 中按路径取值，如 _yaml_get('token_budget', 'total')"""
    val = _yaml_config
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
    return val if val is not None else default


# ============ LLM API 配置（多 Provider 支持）============
# 优先读取通用变量名 LLM_*，兼容旧的 DEEPSEEK_*
# 支持的 Provider：
#   DeepSeek-V3:   LLM_BASE_URL=https://api.deepseek.com/v1       LLM_MODEL=deepseek-chat
#   GLM-4.7-Flash:  LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/  LLM_MODEL=glm-4.7-flash  (免费)
#   Qwen:           LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1  LLM_MODEL=qwen-plus

LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL") or _yaml_get("model", "model_name", default="deepseek-chat")

# 向后兼容：保留 DEEPSEEK_* 别名（现有代码引用这些变量名）
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = LLM_BASE_URL
DEEPSEEK_MODEL = LLM_MODEL

# ============ LLM 调用参数 ============
LLM_TEMPERATURE = _yaml_get("model", "temperatures", "default", default=0.1)  # 默认温度
LLM_MAX_TOKENS = _yaml_get("model", "max_tokens", default=2048)               # 对应 YAML: model.max_tokens

# ============ Token 预算配置 ============
# 每个任务的默认 Token 预算上限 — 对应 YAML: token_budget.total
DEFAULT_TOKEN_BUDGET = _yaml_get("token_budget", "total", default=50000)

# 各角色预算分配比例（总和=1.0）— 对应 YAML: token_budget.allocation
_yaml_allocation = _yaml_get("token_budget", "allocation", default={})
BUDGET_ALLOCATION = {
    "planner":     _yaml_allocation.get("planner", 0.15) if _yaml_allocation else 0.15,
    "executor":    _yaml_allocation.get("executor", 0.50) if _yaml_allocation else 0.50,
    "critic":      _yaml_allocation.get("critic", 0.20) if _yaml_allocation else 0.20,
    "synthesizer": _yaml_allocation.get("synthesizer", 0.15) if _yaml_allocation else 0.15,
}

# 降级阈值（占预算的比例）— 对应 YAML: token_budget.degrade_threshold_1/2/3
DEGRADE_THRESHOLD_1 = _yaml_get("token_budget", "degrade_threshold_1", default=0.70)  # 70%：Critic 跳过低风险验证
DEGRADE_THRESHOLD_2 = _yaml_get("token_budget", "degrade_threshold_2", default=0.85)  # 85%：Planner 合并剩余步骤
DEGRADE_THRESHOLD_3 = _yaml_get("token_budget", "degrade_threshold_3", default=0.95)  # 95%：Synthesizer 直接用已有结果输出

# ============ Agent 执行参数 ============
# 对应 YAML: execution.max_retries / max_iterations / use_heuristics
MAX_RETRIES = _yaml_get("execution", "max_retries", default=3)            # Executor 单步最大重试次数
MAX_ITERATIONS = _yaml_get("execution", "max_iterations", default=5)      # Plan-Execute-Reflect 最大循环次数
USE_HEURISTICS = _yaml_get("execution", "use_heuristics", default=True)   # 是否启用启发式兜底

# ============ Flask 配置 ============
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
