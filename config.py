"""
项目配置文件
管理 DeepSeek API 密钥、模型参数、Token 预算等全局配置
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ============ DeepSeek API 配置 ============
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = "deepseek-chat"

# ============ LLM 调用参数 ============
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 2048

# ============ Token 预算配置 ============
DEFAULT_TOKEN_BUDGET = 50000

BUDGET_ALLOCATION = {
    "planner": 0.15,
    "executor": 0.50,
    "critic": 0.20,
    "synthesizer": 0.15,
}

DEGRADE_THRESHOLD_1 = 0.70
DEGRADE_THRESHOLD_2 = 0.85
DEGRADE_THRESHOLD_3 = 0.95

# ============ Agent 执行参数 ============
MAX_RETRIES = 3
MAX_ITERATIONS = 5
USE_HEURISTICS = True

# ============ Flask 配置 ============
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
