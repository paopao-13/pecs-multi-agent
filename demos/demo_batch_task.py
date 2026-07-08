"""
Demo: 批量任务执行示例

演示如何使用 BatchRunner 批量执行任务，包括：
1. 方式一：传入自定义问题列表（无标准答案评估）
2. 方式二：从 GAIA Mock 数据集加载样本（含标准答案评估）
3. 导出批量执行报告到 results/batch_report.json

使用场景：
- 开发调试：快速验证多智能体框架在多个任务上的表现
- 回归测试：批量运行确认代码改动未引入退化
- 性能评估：统计成功率、Token消耗、执行时间等指标
- CI/CD：集成到持续集成流水线中自动评估

运行方式：
    cd pecs-multi-agent
    python demos/demo_batch_task.py

注意：此脚本会调用 DeepSeek API，请确保已配置 DEEPSEEK_API_KEY 环境变量。
"""
import os
import sys

# 将项目根目录加入 sys.path，确保 import 路径正确
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from src.batch_runner import BatchRunner
from datasets.gaia_mock_dataset import GAIAMockDataset
from datasets.webshop_mock_dataset import WebShopMockDataset


def demo_custom_queries():
    """
    演示一：批量执行自定义问题列表

    适用场景：快速测试框架对不同类型问题的处理能力，
    无需标准答案，只关注执行是否成功和 Token 消耗。
    """
    print("\n" + "=" * 60)
    print("演示一：批量执行自定义问题列表")
    print("=" * 60)

    # 定义 3-5 个示例任务
    queries = [
        "Fibonacci数列的第20项是多少？",
        "2的100次方的结果首位数字是什么？",
        "17的5次方是多少？",
        "中国有多少个省级行政区？这个数字乘以5等于多少？",
        "100的阶乘(100!)有多少位数字？",
    ]

    # 创建批量执行器，设置 Token 预算
    runner = BatchRunner(token_budget=50000, verbose=True)

    # 批量执行
    report = runner.run_tasks(queries)

    # 保存报告
    runner.save_report(report, filename="demo_custom_report.json")

    return report


def demo_gaia_mock_dataset():
    """
    演示二：从 GAIA Mock 数据集加载样本批量执行

    适用场景：评估框架在 GAIA 基准上的准确率，
    有标准答案，可以计算成功率（accuracy）。
    """
    print("\n" + "=" * 60)
    print("演示二：从 GAIA Mock 数据集批量执行（含答案评估）")
    print("=" * 60)

    # 加载 GAIA Mock 数据集
    dataset = GAIAMockDataset()
    info = dataset.get_dataset_info()
    print(f"数据集: {info['name']}")
    print(f"样本总数: {info['total_samples']}")
    print(f"数据来源: {info['source']}")
    print()

    # 创建批量执行器
    runner = BatchRunner(token_budget=50000, verbose=True)

    # 只执行前 5 个样本（演示用，完整评估可设为 None）
    report = runner.run_dataset(dataset, num_samples=5)

    # 保存报告
    runner.save_report(report, filename="demo_gaia_report.json")

    return report


def demo_webshop_mock_dataset():
    """
    演示三：从 WebShop Mock 数据集加载样本批量执行

    适用场景：评估框架在购物导航场景下的表现，
    评估标准是是否选对了目标商品（target_id）。
    """
    print("\n" + "=" * 60)
    print("演示三：从 WebShop Mock 数据集批量执行（商品匹配评估）")
    print("=" * 60)

    # 加载 WebShop Mock 数据集
    dataset = WebShopMockDataset()
    info = dataset.get_dataset_info()
    print(f"数据集: {info['name']}")
    print(f"样本总数: {info['total_samples']}")
    print(f"数据来源: {info['source']}")
    print()

    # 创建批量执行器
    runner = BatchRunner(token_budget=50000, verbose=True)

    # 执行前 3 个样本
    report = runner.run_dataset(dataset, num_samples=3)

    # 保存报告
    runner.save_report(report, filename="demo_webshop_report.json")

    return report


def main():
    """主入口：运行所有演示"""

    # --------------------------------------------------
    # 演示一：自定义问题列表（无标准答案）
    # 取消下方注释以运行（会调用 LLM API）
    # --------------------------------------------------
    # report1 = demo_custom_queries()

    # --------------------------------------------------
    # 演示二：GAIA Mock 数据集（有标准答案）
    # 取消下方注释以运行（会调用 LLM API）
    # --------------------------------------------------
    # report2 = demo_gaia_mock_dataset()

    # --------------------------------------------------
    # 演示三：WebShop Mock 数据集（商品匹配）
    # 取消下方注释以运行（会调用 LLM API）
    # --------------------------------------------------
    # report3 = demo_webshop_mock_dataset()

    # --------------------------------------------------
    # 默认演示：仅展示如何使用（不实际调用 API）
    # --------------------------------------------------
    print("=" * 60)
    print("PECS 多智能体批量任务执行 Demo")
    print("=" * 60)
    print()
    print("本 Demo 提供三种批量执行方式：")
    print()
    print("1. 自定义问题列表（demo_custom_queries）")
    print("   - 传入字符串列表，批量执行")
    print("   - 无标准答案，只统计执行成功率和 Token 消耗")
    print("   - 适用：快速测试、开发调试")
    print()
    print("2. GAIA Mock 数据集（demo_gaia_mock_dataset）")
    print("   - 从内置 GAIA Level 1 样本加载")
    print("   - 有标准答案，计算准确率")
    print("   - 适用：基准评估、回归测试")
    print()
    print("3. WebShop Mock 数据集（demo_webshop_mock_dataset）")
    print("   - 从内置 WebShop 样本加载")
    print("   - 评估标准：是否选对目标商品")
    print("   - 适用：购物导航场景测试")
    print()
    print("-" * 60)
    print("使用方法：")
    print("  1. 确保 .env 中已配置 DEEPSEEK_API_KEY")
    print("  2. 取消上方对应演示函数的注释")
    print("  3. 运行: python demos/demo_batch_task.py")
    print()
    print("或使用 CLI 直接批量执行 GAIA 样本：")
    print("  python -m src.batch_runner --num-samples 5")
    print()

    # 展示数据集信息（不调用 API）
    print("-" * 60)
    print("可用数据集信息：")
    print()

    gaia = GAIAMockDataset()
    gaia_info = gaia.get_dataset_info()
    print(f"  [{gaia_info['name']}]")
    print(f"    样本数: {gaia_info['total_samples']}")
    print(f"    来源: {gaia_info['source']}")
    print(f"    Mock: {gaia_info['is_mock']}")
    print()

    webshop = WebShopMockDataset()
    ws_info = webshop.get_dataset_info()
    print(f"  [{ws_info['name']}]")
    print(f"    样本数: {ws_info['total_samples']}")
    print(f"    来源: {ws_info['source']}")
    print(f"    Mock: {ws_info['is_mock']}")
    print()

    # 展示前 3 个 GAIA 样本
    print("GAIA Mock 前 3 个样本预览：")
    for sample in gaia.load_samples(3):
        print(f"  [{sample['task_id']}] {sample['question'][:60]}...")
        print(f"    答案: {sample['answer']} | 复杂度: {sample.get('complexity', '?')}")
    print()

    print("WebShop Mock 前 3 个样本预览：")
    for sample in webshop.load_samples(3):
        print(f"  [{sample['task_id']}] {sample['question'][:60]}...")
        print(f"    目标商品: {sample['answer']}")
    print()


if __name__ == "__main__":
    main()
