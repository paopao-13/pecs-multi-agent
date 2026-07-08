"""
数据集抽象基类，解耦 Mock / 官方数据集切换逻辑。

所有具体数据集（GAIA Mock、GAIA Official、WebShop Mock 等）都继承此类，
实现统一的 load_samples / get_sample_by_id / get_dataset_info / evaluate_answer 接口，
使得 BatchRunner 和评估脚本无需关心数据来源。
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseDataset(ABC):
    """数据集抽象基类，解耦 Mock/官方数据集切换逻辑"""

    @abstractmethod
    def load_samples(self, num_samples: Optional[int] = None) -> List[Dict[str, Any]]:
        """加载样本列表，每条样本包含 task_id, question, answer"""
        pass

    @abstractmethod
    def get_sample_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """按ID获取单条样本"""
        pass

    @abstractmethod
    def get_dataset_info(self) -> Dict[str, Any]:
        """返回数据集元信息：名称、样本数、来源、是否mock"""
        pass

    def evaluate_answer(self, predicted: str, ground_truth: str) -> bool:
        """
        默认答案评估逻辑（可被子类重写）

        复用 benchmarks/gaia_eval.py 中的 evaluate_answer 函数，
        支持归一化匹配、包含匹配、数字提取匹配、语义等价判断。
        """
        from benchmarks.gaia_eval import evaluate_answer as _gaia_evaluate
        return _gaia_evaluate(predicted, ground_truth)
