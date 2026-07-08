"""
GAIA Official 数据集

从 HuggingFace 加载 GAIA 官方数据集（gaia-benchmark/GAIA），实现 BaseDataset 接口。

使用前提：
1. 安装 datasets 库：pip install datasets
2. 在 HuggingFace 上申请 GAIA 数据集许可：
   https://huggingface.co/datasets/gaia-benchmark/GAIA
3. 配置 HuggingFace 认证 Token（以下任一方式）：
   - 设置环境变量 HF_TOKEN 或 HUGGINGFACE_TOKEN
   - 运行 huggingface-cli login
   - 在构造函数中传入 hf_token 参数
"""
import os
from typing import List, Dict, Any, Optional

from datasets.base_dataset import BaseDataset


# GAIA 官方数据集在 HuggingFace 上的仓库标识
HF_DATASET_ID = "gaia-benchmark/GAIA"
HF_CONFIG_NAME = "2023_level1"  # Level 1 配置名
HF_SPLIT = "validation"          # GAIA 只有 validation 和 test 两个 split


class GAIAOfficialDataset(BaseDataset):
    """GAIA Level 1 官方数据集（从 HuggingFace 加载）"""

    def __init__(self, hf_token: Optional[str] = None, level: int = 1):
        """
        初始化 GAIA 官方数据集

        参数:
            hf_token: HuggingFace 认证 Token。如果为 None，则依次从环境变量
                      HF_TOKEN、HUGGINGFACE_TOKEN 读取。
            level: GAIA 难度等级（1/2/3），默认为 Level 1
        """
        self._level = level
        self._config_name = f"2023_level{level}"
        self._hf_token = hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
        self._samples: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """延迟加载：首次访问时从 HuggingFace 拉取数据"""
        if self._loaded:
            return

        # 检查 datasets 库是否安装
        try:
            from datasets import load_dataset
        except ImportError:
            raise ImportError(
                "未安装 datasets 库。请运行以下命令安装：\n"
                "  pip install datasets\n"
                "GAIA 官方数据集需要通过 HuggingFace datasets 库加载。"
            )

        # 检查认证 Token
        if not self._hf_token:
            raise PermissionError(
                "未配置 HuggingFace 认证 Token。GAIA 数据集需要访问许可。\n"
                "请通过以下任一方式配置 Token：\n"
                "  1. 设置环境变量：export HF_TOKEN=your_token_here\n"
                "  2. 运行 huggingface-cli login\n"
                "  3. 在构造函数中传入 hf_token 参数\n"
                "申请许可地址：https://huggingface.co/datasets/gaia-benchmark/GAIA"
            )

        # 从 HuggingFace 加载数据集
        try:
            dataset = load_dataset(
                HF_DATASET_ID,
                self._config_name,
                split=HF_SPLIT,
                token=self._hf_token,
                trust_remote_code=True,
            )
        except Exception as e:
            raise RuntimeError(
                f"从 HuggingFace 加载 GAIA 数据集失败: {e}\n"
                f"请检查：\n"
                f"  1. Token 是否有效且已获得 GAIA 数据集访问许可\n"
                f"  2. 网络连接是否正常\n"
                f"  3. 数据集配置名 '{self._config_name}' 是否正确"
            ) from e

        # 归一化样本格式，统一为 task_id, question, answer 字段
        for row in dataset:
            sample = {
                "task_id": str(row.get("task_id", "")),
                "question": row.get("Question", ""),
                "answer": str(row.get("Final answer", "")),
                "level": self._level,
                "file_name": row.get("file_name", ""),
                "anonymized_references": row.get("Annotator Metadata", {}),
            }
            self._samples.append(sample)

        self._index = {s["task_id"]: s for s in self._samples}
        self._loaded = True

    def load_samples(self, num_samples: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        加载样本列表

        参数:
            num_samples: 加载前 N 条样本，None 表示全部加载

        返回:
            样本列表，每条样本包含 task_id, question, answer, level
        """
        self._ensure_loaded()
        if num_samples is None:
            return list(self._samples)
        return list(self._samples[:num_samples])

    def get_sample_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """按 task_id 获取单条样本，不存在则返回 None"""
        self._ensure_loaded()
        return self._index.get(task_id)

    def get_dataset_info(self) -> Dict[str, Any]:
        """返回数据集元信息"""
        # 如果尚未加载，返回基本信息（不触发网络请求）
        if not self._loaded:
            return {
                "name": f"GAIA Level {self._level} (Official)",
                "total_samples": "未加载（需调用 load_samples 触发加载）",
                "source": "huggingface",
                "is_mock": False,
                "hf_dataset_id": HF_DATASET_ID,
                "hf_config": self._config_name,
                "hf_split": HF_SPLIT,
                "description": "GAIA 官方数据集，从 HuggingFace 加载，需要访问许可和认证 Token",
            }

        return {
            "name": f"GAIA Level {self._level} (Official)",
            "total_samples": len(self._samples),
            "source": "huggingface",
            "is_mock": False,
            "hf_dataset_id": HF_DATASET_ID,
            "hf_config": self._config_name,
            "hf_split": HF_SPLIT,
            "description": "GAIA 官方数据集，从 HuggingFace 加载，需要访问许可和认证 Token",
        }
