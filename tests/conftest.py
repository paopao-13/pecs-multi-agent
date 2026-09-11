"""
pytest 全局配置

将项目根目录加入 sys.path，使测试文件能够通过
`from tools.xxx import ...` / `from graph.xxx import ...` 导入项目模块。

CI 环境下如果未配置 LLM_API_KEY，自动跳过需要 API Key 的用例，
不让整个 CI 报红。
"""
import os
import sys

import pytest

# 项目根目录 = conftest.py 所在目录的上一级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 测试环境隔离：预置空字符串占位，阻止本地 .env 的真实鉴权配置进入测试。
# 原理：config.py 的 load_dotenv() 默认 override=False，不覆盖已存在的
# 环境变量——因此这里先置空，.env 中的 PECS_API_KEYS 就不会被注入。
# （仅用 pop 不够：conftest 先执行，但后续导入 config 时 load_dotenv 会
# 把 .env 的值重新注入，scripts.auth 导入时 AUTH_ENABLED 又变 True，
# 所有不带 Key 的既有用例被 401 挡住。）
# 测试默认运行在"鉴权关闭"环境（与 CI 一致）；鉴权行为本身由
# tests/test_auth.py 通过 monkeypatch 构造 Key 表显式覆盖，不依赖真实凭据。
# 必须在任何项目模块（config / scripts.auth）被导入之前执行。
os.environ["PECS_API_KEYS"] = ""
os.environ["PEC_BENCHMARK_KEY"] = ""


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line(
        "markers", "requires_api_key: 标记需要 LLM_API_KEY 的测试用例"
    )


def pytest_collection_modifyitems(config, items):
    """收集测试后，自动跳过需要 API Key 但未配置的用例"""
    has_key = bool(os.environ.get("LLM_API_KEY"))
    skip_marker = pytest.mark.skip(
        reason="LLM_API_KEY 未配置，跳过需要 API Key 的用例"
    )
    for item in items:
        if "requires_api_key" in item.keywords and not has_key:
            item.add_marker(skip_marker)
