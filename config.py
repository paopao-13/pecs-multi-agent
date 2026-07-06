"""
项目配置文件
管理 DeepSeek API 密钥、模型参数、Token 预算等全局配置
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量（本地开发用，部署时通过平台环境变量注入）
load_dotenv()

# ============ DeepSeek API 配置 ============
# 从环境变量读取，部署时通过 .env 或平台环境变量注入
# 请勿在代码中硬编码 API Key
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# DeepSeek API 基础地址（兼容 OpenAI 接口格式）
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 使用的模型名称
DEEPSEEK_MODEL = "deepseek-chat"

# ============ LLM 调用参数 ============
LLM_TEMPERATURE = 0.1      # 低温度=输出更稳定，适合需要精确结构的场景
LLM_MAX_TOKENS = 2048      # 单次调用最大输出 Token 数

# ============ Token 预算配置 ============
# 每个任务的默认 Token 预算上限（约 5 万 Token，DeepSeek 约 0.1 元）
DEFAULT_TOKEN_BUDGET = 50000

# 各角色预算分配比例（总和=1.0）
BUDGET_ALLOCATION = {
    "planner": 0.15,       # 规划者占 15%
    "executor": 0.50,      # 执行者占 50%（最耗 Token，因为要调用工具）
    "critic": 0.20,        # 评审者占 20%
    "synthesizer": 0.15,   # 综合者占 15%
}

# 降级阈值（占预算的比例）
DEGRADE_THRESHOLD_1 = 0.70  # 70%：Critic 跳过低风险验证
DEGRADE_THRESHOLD_2 = 0.85  # 85%：Planner 合并剩余步骤
DEGRADE_THRESHOLD_3 = 0.95  # 95%：Synthesizer 直接用已有结果输出

# ============ Agent 执行参数 ============
MAX_RETRIES = 3            # Executor 单步最大重试次数
MAX_ITERATIONS = 5         # Plan-Execute-Reflect 最大循环次数

# ============ Flask 配置 ============
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 5000
FLASK_DEBUG = True
