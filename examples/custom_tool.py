"""
展示如何给 Executor 添加自定义工具。
"""

from tools.base import BaseTool


class WeatherTool(BaseTool):
    """一个示例自定义工具：假装查天气（实际返回固定值，仅供演示）"""

    name = "weather"
    description = "查询指定城市的当前天气"

    def run(self, city: str) -> str:
        # 这里实际可以调 API，示例直接返回固定值
        return f"{city} 当前天气：晴，25°C，湿度 60%"


# 使用方式：在 graph/builder.py 里注册这个工具即可
# executor.register_tool(WeatherTool())
