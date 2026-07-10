"""
展示如何给 Executor 添加自定义工具。

自定义工具只需实现 run() 方法，然后在 tools/__init__.py 中注册即可。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class WeatherTool:
    """一个示例自定义工具：查询指定城市的当前天气（演示用，返回固定值）"""

    name = "weather"
    description = "查询指定城市的当前天气"

    def run(self, city: str) -> str:
        # 实际项目中这里可以调用天气 API
        # 示例直接返回固定值，仅供演示
        return f"{city} 当前天气：晴，25°C，湿度 60%"


if __name__ == "__main__":
    tool = WeatherTool()
    print(f"工具名称: {tool.name}")
    print(f"工具描述: {tool.description}")
    print(f"执行结果: {tool.run('北京')}")
    print()
    print("注册方式：在 tools/__init__.py 的 TOOL_REGISTRY 中添加：")
    print('  "weather": WeatherTool(),')
