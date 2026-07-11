"""
配置加载单元测试

测试 config.py：
- 通过环境变量设置 API Key 后能正确读取
- 源码中不包含 "sk-" 开头的硬编码 key（防止密钥泄露）
"""
import os
import importlib


def test_api_key_from_env(monkeypatch):
    """通过环境变量设置 API Key 后能正确读取"""
    test_key = "test-key-from-env-12345"
    monkeypatch.setenv("DEEPSEEK_API_KEY", test_key)

    # 重新加载 config 模块，使其重新读取环境变量
    # （config.py 在 import 时通过 os.getenv 读取 DEEPSEEK_API_KEY）
    import config
    importlib.reload(config)

    assert config.DEEPSEEK_API_KEY == test_key


def test_api_key_not_hardcoded():
    """确认 config.py 源码中不包含 sk- 开头的硬编码 key"""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(project_root, "config.py")

    with open(config_path, "r", encoding="utf-8") as f:
        source = f.read()

    # 源码中不应出现 sk- 开头的硬编码密钥
    assert "sk-" not in source
