"""
pytest 全局配置

将项目根目录加入 sys.path，使测试文件能够通过
`from tools.xxx import ...` / `from graph.xxx import ...` 导入项目模块。

CI 环境下如果未配置 OPENAI_API_KEY，自动跳过需要 API Key 的用例，
不让整个 CI 报红。
"""
import os
import sys

import pytest

# 项目根目录 = conftest.py 所在目录的上一级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line(
        "markers", "requires_api_key: 标记需要 OPENAI_API_KEY 的测试用例"
    )


def pytest_collection_modifyitems(config, items):
    """收集测试后，自动跳过需要 API Key 但未配置的用例"""
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    skip_marker = pytest.mark.skip(
        reason="OPENAI_API_KEY 未配置，跳过需要 API Key 的用例"
    )
    for item in items:
        if "requires_api_key" in item.keywords and not has_key:
            item.add_marker(skip_marker)
