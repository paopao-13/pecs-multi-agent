"""
工具集注册表

每个工具是一个函数，接受参数字典，返回字符串结果。
Executor 根据计划中的 action 名称，从这里找到对应工具并调用。
"""
from tools.web_search import web_search
from tools.web_browser import web_browser
from tools.python_repl import python_repl
from tools.file_reader import file_reader
from tools.file_parser import file_parser
from tools.api_caller import api_caller
from tools.webshop import webshop_select

# 工具注册表：action名称 → 工具函数
TOOL_REGISTRY = {
    "search": web_search,
    "web_browse": web_browser,
    "python": python_repl,
    "file_read": file_reader,
    "file_parse": file_parser,
    "api_call": api_caller,
    "webshop": webshop_select,
}

# 工具描述（供 Planner 了解可用工具）
TOOL_DESCRIPTIONS = {
    "search": "Web搜索工具。输入查询关键词，返回搜索结果摘要。适用于需要查找实时信息、事实性问题。",
    "web_browse": "网页浏览工具。输入网页URL，返回页面正文内容。适用于需要从特定网页提取信息的任务。",
    "python": "Python代码执行工具。输入Python代码字符串，返回执行结果。适用于计算、数据处理、逻辑推理。",
    "file_read": "文件读取工具。输入文件路径，返回文件内容。适用于读取本地文档、配置文件。",
    "file_parse": "文件解析工具。输入文件路径，自动识别PDF/Excel/CSV/图片格式并提取内容。适用于GAIA文件处理任务。",
    "api_call": "通用API调用工具。输入URL和参数，返回响应内容。适用于调用外部API获取数据。",
    "webshop": "WebShop商品选择工具。输入购物需求和可选商品目录，返回最匹配商品。适用于购物导航任务。",
}


def execute_tool(action: str, args: dict) -> str:
    """
    执行工具调用

    参数:
        action: 工具名称（search / python / file_read / api_call）
        args: 工具参数字典

    返回:
        工具执行结果字符串
    """
    tool_fn = TOOL_REGISTRY.get(action)
    if tool_fn is None:
        return f"错误：未知工具 '{action}'，可用工具：{list(TOOL_REGISTRY.keys())}"
    try:
        return tool_fn(args)
    except Exception as e:
        return f"工具执行失败 [{action}]: {type(e).__name__}: {str(e)}"
