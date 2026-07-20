# WebShop 本地化补丁集（PECS 多智能体框架）

本目录收录了对 [princeton-nlp/WebShop](https://github.com/princeton-nlp/webshop)
所做的**三处工程修改**，目的是让 WebShop 真实环境能在 **Windows 本机**跑起来，
避开原版在 Windows 上的两个天坑：

| # | 文件 | 修改 | 解决的问题 |
|---|---|---|---|
| 1 | `web_agent_site/engine/bm25_search.py` | 🆕 新增，纯 Python BM25 搜索后端 | 替代原版 `pyserini` + Lucene + JNI（Windows 上极易崩），免 Java、免建索引 |
| 2 | `web_agent_site/engine/engine.py` | `pyserini` 改 try/except 惰性导入；`init_search_engine` 默认返回 `BM25SearchEngine` | 同上，默认走 BM25；仅 `WEBSITE_USE_PYSERINI=1` 才走原版 Lucene |
| 3 | `web_agent_site/envs/web_agent_text_env.py` | 顶层 `import torch` 改为仅在 `get_image` 分支惰性加载 | 运行时免装 torch（省数百 MB），PECS 通过 HTTP 提供动作不需要图像特征 |

> **诚实声明**：搜索后端用 `rank_bm25`（纯 Python）替代原版
> `pyserini/Lucene`，属**功能等价替代**（同 BM25 排序族），仅命中顺序略有差异，
> 目的是兼容 Windows 本地部署、避开 JNI 坑。这是工程取舍，不是缩水。

## 使用方式

1. 克隆原版 WebShop 到 `webshop/`：
   ```bash
   git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop
   cd webshop && git rev-parse --short HEAD
   ```
2. 把本目录的三个文件覆盖进 `webshop/` 对应路径：
   ```bash
   # 在仓库根目录执行
   cp webshop_patches/bm25_search.py            webshop/web_agent_site/engine/bm25_search.py
   cp webshop_patches/engine.py                 webshop/web_agent_site/engine/engine.py
   cp webshop_patches/web_agent_text_env.py     webshop/web_agent_site/envs/web_agent_text_env.py
   ```
   或直接运行一键脚本：`bash webshop_patches/apply.sh`
3. 按 `docs/webshop_local_runbook.md` 装依赖、下数据、起桥、跑评测。

## 与上游的差异定位

- `engine.py` 仅改动 `import` 区与 `init_search_engine()` 函数体，其余逻辑不变。
- `web_agent_text_env.py` 仅把第 6 行 `import torch` 移除，并在 `get_image()` 内惰性 `import torch`。
- `bm25_search.py` 为全新文件，实现与 `pyserini` 完全一致的 `search(k)` / `doc().raw()` 接口。
