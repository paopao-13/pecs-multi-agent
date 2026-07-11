"""
pytest 全局配置

将项目根目录加入 sys.path，使测试文件能够通过
`from tools.xxx import ...` / `from graph.xxx import ...` 导入项目模块。
"""
import os
import sys

# 项目根目录 = conftest.py 所在目录的上一级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
