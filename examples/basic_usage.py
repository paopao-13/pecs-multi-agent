"""
最简单的使用示例：让 PECS 多智能体框架回答一个事实性问题。
运行前请确保已配置 OPENAI_API_KEY 环境变量。
"""

import os
import sys

# 把项目根目录加到 Python 路径（如果已经 pip install -e . 了，这行可以删掉）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph.builder import build_graph, create_initial_state


def main():
    # 构建 PECS 执行图
    app = build_graph(token_budget=50000)

    # 输入一个简单任务
    task = "北京和上海之间的直线距离是多少公里？"

    print(f"📝 任务: {task}\n")
    print("=" * 50)

    # 执行任务
    initial_state = create_initial_state(task, token_budget=50000)
    result = app.invoke(initial_state)

    print("\n" + "=" * 50)
    print(f"✅ 最终答案: {result.get('final_answer', '无输出')}")
    print(f"📊 Token 消耗: {result.get('token_used', 0)}")


if __name__ == "__main__":
    main()
