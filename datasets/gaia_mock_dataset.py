"""
GAIA Mock 数据集

从 benchmarks/gaia_eval.py 导入内置的 GAIA_L1_SAMPLES，实现 BaseDataset 接口。
适用于开发调试和 CI 回归测试，无需 HuggingFace 认证。
"""
from typing import List, Dict, Any, Optional

from datasets.base_dataset import BaseDataset
from benchmarks.gaia_eval import GAIA_L1_SAMPLES


class GAIAMockDataset(BaseDataset):
    """GAIA Level 1 内置 Mock 数据集"""

    def __init__(self):
        self._samples: List[Dict[str, Any]] = list(GAIA_L1_SAMPLES)
        self._index: Dict[str, Dict[str, Any]] = {
            s["task_id"]: s for s in self._samples
        }

    def load_samples(self, num_samples: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        加载样本列表

        参数:
            num_samples: 加载前 N 条样本，None 表示全部加载

        返回:
            样本列表，每条样本包含 task_id, question, answer, level, hint, complexity
        """
        if num_samples is None:
            return list(self._samples)
        return list(self._samples[:num_samples])

    def get_sample_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """按 task_id 获取单条样本，不存在则返回 None"""
        return self._index.get(task_id)

    def get_dataset_info(self) -> Dict[str, Any]:
        """返回数据集元信息"""
        return {
            "name": "GAIA Level 1 (Mock)",
            "total_samples": len(self._samples),
            "source": "mock",
            "is_mock": True,
            "description": "内置 GAIA Level 1 示例任务，无需 HuggingFace 认证，适用于开发调试和 CI",
        }
