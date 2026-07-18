"""
GAIA Official 数据集

从 HuggingFace 加载 GAIA 官方数据集（gaia-benchmark/GAIA），实现 BaseDataset 接口。

使用前提：
1. 安装 datasets 库：pip install datasets huggingface_hub
2. 在 HuggingFace 上申请 GAIA 数据集许可：
   https://huggingface.co/datasets/gaia-benchmark/GAIA
3. 配置 HuggingFace 认证 Token（以下任一方式）：
   - 设置环境变量 HF_TOKEN 或 HUGGINGFACE_TOKEN
   - 运行 huggingface-cli login
   - 在构造函数中传入 hf_token 参数

字段映射（官方 → 内置）：
  task_id            → task_id
  Question           → question
  Final answer       → answer (validation split 有值, test split 为空)
  file_name          → file_name (附件名,可能为空)
  file_path          → file_path (附件相对路径,可能为空)
  Annotator Metadata → metadata (struct, 含 Steps/Tools 等)
  Level              → level
"""
import os
from typing import List, Dict, Any, Optional

# 注意:这里必须用相对导入,因为 datasets 是项目内的包
# (与 pip 安装的 huggingface datasets 库同名但不同包)
from datasets.base_dataset import BaseDataset


# GAIA 官方数据集在 HuggingFace 上的仓库标识
HF_DATASET_ID = "gaia-benchmark/GAIA"
HF_CONFIG_NAME = "2023_level1"  # Level 1 配置名
HF_SPLIT = "validation"          # GAIA validation split 有答案, test split 答案私有


class GAIAOfficialDataset(BaseDataset):
    """GAIA Level 1 官方数据集（从 HuggingFace 加载）

    官方数据集结构（2025年10月 Parquet 版）:
    - validation split: 53 题 Level 1 (有 Final answer, 可本地评测)
    - test split: 165 题 Level 1 (Final answer 为空, 需提交 leaderboard)
    - 字段: task_id, Question, Level, Final answer, file_name, file_path, Annotator Metadata
    - 附件: file_path 指向 PDF/xlsx/png/txt/docx/pptx/mp3/py 等, 相对仓库根目录
    """

    def __init__(self, hf_token: Optional[str] = None, level: int = 1, split: str = "validation"):
        """
        初始化 GAIA 官方数据集

        参数:
            hf_token: HuggingFace 认证 Token。如果为 None，则依次从环境变量
                      HF_TOKEN、HUGGINGFACE_TOKEN 读取。
            level: GAIA 难度等级（1/2/3），默认为 Level 1
            split: 数据集 split，"validation"（有答案，本地评测）或 "test"（答案私有，提交 leaderboard）
        """
        self._level = level
        self._split = split
        self._config_name = f"2023_level{level}"
        self._hf_token = hf_token or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
        self._data_dir: Optional[str] = None  # snapshot_download 返回的本地路径
        self._samples: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self):
        """延迟加载：首次访问时从 HuggingFace 拉取数据

        加载流程:
        1. snapshot_download 拉取整个仓库（含 parquet + 附件）到本地缓存
        2. load_dataset 从本地 parquet 加载为 Dataset 对象
        3. 归一化字段为内置格式

        注意: pip 安装的 huggingface datasets 库与项目内 datasets 包同名,
        必须临时调整 sys.path 并屏蔽项目内 datasets 包来避免冲突。
        """
        if self._loaded:
            return

        import sys

        # 找到 site-packages 目录
        import site
        site_packages = None
        for sp in site.getsitepackages():
            if os.path.exists(os.path.join(sp, "datasets", "__init__.py")):
                site_packages = sp
                break

        if not site_packages:
            raise ImportError(
                "未找到 pip 安装的 huggingface datasets 库。请运行：\n"
                "  pip install datasets huggingface_hub"
            )

        # 临时把 site-packages 放最前面,并屏蔽项目内 datasets 包
        original_path = sys.path[:]
        # 移除项目目录
        sys.path = [p for p in sys.path if "pecs-multi-agent" not in p and "简历" not in p]
        # 把 site-packages 放最前
        sys.path.insert(0, site_packages)
        # 临时移除已加载的项目内 datasets 包
        saved_datasets = sys.modules.pop("datasets", None)
        saved_hf_datasets = sys.modules.pop("huggingface_hub", None)

        try:
            # 现在能正确加载 huggingface 的 datasets 库
            from datasets import load_dataset
            from huggingface_hub import snapshot_download
        finally:
            # 恢复 sys.path
            sys.path = original_path
            # 恢复项目内 datasets 包（如果有）
            # 注意:不恢复 huggingface 的 datasets 到 sys.modules["datasets"],
            # 否则会破坏项目内 datasets 包的使用
            if saved_datasets is not None and "datasets" not in sys.modules:
                sys.modules["datasets"] = saved_datasets

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

        # 从 HuggingFace 下载整个仓库（含 parquet + 附件文件）
        # 返回本地缓存路径,附件文件也在这个目录下
        try:
            self._data_dir = snapshot_download(
                repo_id=HF_DATASET_ID,
                repo_type="dataset",
                token=self._hf_token,
            )
        except Exception as e:
            raise RuntimeError(
                f"从 HuggingFace 下载 GAIA 数据集失败: {e}\n"
                f"请检查：\n"
                f"  1. Token 是否有效且已获得 GAIA 数据集访问许可\n"
                f"  2. 网络连接是否正常（国内可设置 HF_ENDPOINT=https://hf-mirror.com）\n"
            ) from e

        # 从本地 parquet 加载数据集
        # 用本地路径加载,避免再次走网络
        # 注意: datasets 库的 load_dataset 在某些环境会触发 dill 序列化的
        # RecursionError（与 multiprocessing 的 spawn 模式有关）。
        # 解决: 在 load_dataset 前设置 multiprocessing 为 fork 模式（Linux）
        # 或禁用 multiprocessing（Windows）。
        try:
            import sys as _sys
            # Windows: 强制禁用 datasets 的 multiprocessing,避免 dill 递归
            if _sys.platform == "win32":
                import multiprocessing as _mp
                # 临时设为 spawn（默认）但限制递归深度
                _sys.setrecursionlimit(50000)
            dataset = load_dataset(
                self._data_dir,
                self._config_name,
                split=self._split,
            )
        except RecursionError as e:
            # dill 递归过深,改用 pandas 直接读 parquet
            import glob
            parquet_pattern = os.path.join(
                self._data_dir, "**", f"*{self._config_name}*{self._split}*.parquet"
            )
            parquet_files = glob.glob(parquet_pattern, recursive=True)
            if not parquet_files:
                # 退而求其次:找所有 parquet 文件
                parquet_files = glob.glob(
                    os.path.join(self._data_dir, "**", "*.parquet"),
                    recursive=True,
                )
            if not parquet_files:
                raise RuntimeError(
                    f"加载 GAIA parquet 失败（RecursionError + 无 parquet 文件）: {e}\n"
                    f"  data_dir={self._data_dir}"
                )
            # 用 pandas 读 parquet
            import pandas as pd
            dfs = [pd.read_parquet(f) for f in parquet_files]
            df = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
            # 转为 records（list of dict）
            dataset = df.to_dict("records")
        except Exception as e:
            raise RuntimeError(
                f"加载 GAIA parquet 失败: {e}\n"
                f"  data_dir={self._data_dir}\n"
                f"  config={self._config_name} split={self._split}"
            ) from e

        # 归一化样本格式，统一字段
        for row in dataset:
            sample = {
                "task_id": str(row.get("task_id", "")),
                "question": row.get("Question", ""),
                "answer": str(row.get("Final answer", "")),
                "level": self._level,
                "file_name": row.get("file_name", "") or "",
                "file_path": row.get("file_path", "") or "",
                "metadata": row.get("Annotator Metadata", {}) or {},
                "source": "official",
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
            样本列表，每条样本包含 task_id, question, answer, level, file_name, file_path, metadata
        """
        self._ensure_loaded()
        if num_samples is None:
            return list(self._samples)
        return list(self._samples[:num_samples])

    def get_sample_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """按 task_id 获取单条样本，不存在则返回 None"""
        self._ensure_loaded()
        return self._index.get(task_id)

    def resolve_attachment(self, sample: Dict[str, Any]) -> Optional[str]:
        """解析附件文件的本地绝对路径

        参数:
            sample: load_samples 返回的样本字典

        返回:
            附件的本地绝对路径，无附件或文件不存在则返回 None
        """
        self._ensure_loaded()
        file_path = sample.get("file_path", "")
        if not file_path or not self._data_dir:
            return None
        abs_path = os.path.join(self._data_dir, file_path)
        return abs_path if os.path.exists(abs_path) else None

    def get_dataset_info(self) -> Dict[str, Any]:
        """返回数据集元信息"""
        # 如果尚未加载，返回基本信息（不触发网络请求）
        if not self._loaded:
            return {
                "name": f"GAIA Level {self._level} (Official, {self._split})",
                "total_samples": "未加载（需调用 load_samples 触发加载）",
                "source": "huggingface",
                "is_mock": False,
                "hf_dataset_id": HF_DATASET_ID,
                "hf_config": self._config_name,
                "hf_split": self._split,
                "description": "GAIA 官方数据集，从 HuggingFace 加载，需要访问许可和认证 Token",
            }

        # 统计附件分布
        no_file = sum(1 for s in self._samples if not s.get("file_path"))
        has_file = len(self._samples) - no_file

        return {
            "name": f"GAIA Level {self._level} (Official, {self._split})",
            "total_samples": len(self._samples),
            "no_attachment_count": no_file,
            "with_attachment_count": has_file,
            "source": "huggingface",
            "is_mock": False,
            "hf_dataset_id": HF_DATASET_ID,
            "hf_config": self._config_name,
            "hf_split": self._split,
            "data_dir": self._data_dir,
            "description": "GAIA 官方数据集，从 HuggingFace 加载，需要访问许可和认证 Token",
        }
