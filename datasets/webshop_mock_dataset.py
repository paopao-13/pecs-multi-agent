"""
WebShop Mock 数据集

从 benchmarks/webshop_eval.py 导入内置的 WEBSHOP_SAMPLES，实现 BaseDataset 接口。

WebShop 样本的原始字段为 instruction / target_id，这里统一归一化为
question / answer 以兼容 BaseDataset 接口，并重写 evaluate_answer
使用「target_id 是否包含在预测答案中」作为评估标准。
"""
from typing import List, Dict, Any, Optional

from datasets.base_dataset import BaseDataset
from benchmarks.webshop_eval import WEBSHOP_SAMPLES


class WebShopMockDataset(BaseDataset):
    """WebShop 内置 Mock 数据集"""

    def __init__(self):
        # 归一化样本格式：instruction → question, target_id → answer
        self._samples: List[Dict[str, Any]] = []
        for s in WEBSHOP_SAMPLES:
            normalized = {
                "task_id": s["task_id"],
                "question": s["instruction"],
                "answer": s["target_id"],
                "instruction": s["instruction"],
                "target_id": s["target_id"],
            }
            self._samples.append(normalized)

        self._index: Dict[str, Dict[str, Any]] = {
            s["task_id"]: s for s in self._samples
        }

    def load_samples(self, num_samples: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        加载样本列表

        参数:
            num_samples: 加载前 N 条样本，None 表示全部加载

        返回:
            样本列表，每条样本包含 task_id, question, answer, instruction, target_id
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
            "name": "WebShop (Mock)",
            "total_samples": len(self._samples),
            "source": "mock",
            "is_mock": True,
            "description": "内置 WebShop 风格示例任务，用于评估多智能体在购物约束场景下的表现",
        }

    def evaluate_answer(self, predicted: str, ground_truth: str) -> bool:
        """
        WebShop 答案评估：检查 target_id 是否包含在预测答案中

        WebShop 的成功标准是选对目标商品，因此只要预测答案中包含
        正确的 target_id（如 "ws_tea_001"）即视为成功。
        """
        if not predicted:
            return False
        return ground_truth in predicted
