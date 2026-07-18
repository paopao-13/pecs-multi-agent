# WebShop 真实环境 — 本机执行 Checklist（已修正版，2026-07-18）

> ⚠️ **重要修正**：原版 `./setup.sh -d small` 走不通——它用 `gdown` 从 **Google Drive** 拉数据，国内被墙。
> 且原版要求 pyserini + Java 构建 Lucene 索引，pyserini 0.17 在 Windows 上是 JNI 重灾区。
> 本版已通过代码补丁绕开这两点：
>   1. **数据**改从 HF 镜像 `zhangdw/webshop` 下载（国内可达）。
>   2. **搜索后端**改纯 Python `rank_bm25`（已写 `web_agent_site/engine/bm25_search.py` 并实测通过），**不再需要 Java / pyserini / Lucene 索引**。
>   3. **torch 改为惰性导入**，本机跑环境**不需要装 torch**。
> 全程在 Git Bash 中执行（CMD 跑不了 bash 脚本 & 没 grep/curl）。

---

## Step 0：前置检查（逐条打勾）

- [ ] **Git for Windows 已装** → `git --version`
- [ ] **conda 已装** → `conda --version`
  - ⚠️ 没装：https://docs.conda.io/en/latest/miniconda.html 下载 Miniconda Windows installer
- [ ] **磁盘空间 ≥ 8GB**（WebShop small 数据 ~几百 MB + conda 环境）
- [ ] **PECS 项目依赖已装**（在常用 Python 环境里）：
  ```bash
  pip install langgraph langchain-openai openai pydantic requests flask
  ```

> 注意：**不再需要 Java / JDK**（搜索已换 BM25）；**不再需要 torch**（已惰性）。

---

## Step 1：克隆 WebShop 源码（已做过可跳过）

```bash
cd D:\简历\pecs-multi-agent
git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop
cd webshop && git rev-parse --short HEAD && echo "CLONE_OK"
```
- 若 `Connection was reset`：等 10 秒重跑，一般第二次能通。

---

## Step 2：创建 conda 环境

```bash
cd D:\简历\pecs-multi-agent\webshop
conda create -n webshop python=3.8.13 -y
conda activate webshop
```
- Python 必须 3.8.x。验证：`python --version` → `3.8.13`。

---

## Step 3：安装 Python 依赖（**去掉 pyserini / torch**）

```bash
# 先装基础科学栈 + 环境必需
pip install gym==0.24.0 numpy beautifulsoup4 flask rich cleantext tqdm \
            rank_bm25 thefuzz scikit_learn spacy

# spaCy 小模型（~12MB，非 lg 的 500MB+）
python -m spacy download en_core_web_sm

# WebShop 其余依赖里挑要用的装（transformers 仅兼容导入，按需）
pip install transformers==4.19.2
```
- **刻意不装**：`pyserini`（已用 BM25 替代）、`torch`（已惰性，环境运行时用不到）。
- 若 `transformers==4.19.2` 装不上/有冲突，先跳过，遇到 import 报错再加。

---

## Step 4：下载数据（绕开 Google Drive，走 HF 镜像）

```bash
# 设国内镜像
export HF_ENDPOINT=https://hf-mirror.com

# 下载 zhangdw/webshop 数据集到任意临时目录
huggingface-cli download --repo-type dataset zhangdw/webshop \
    --local-dir D:\简历\pecs-multi-agent\webshop\_hfdata

# 取出 small 数据包并解压到 webshop/data/
cd D:\简历\pecs-multi-agent\webshop
mkdir -p data
tar -xzf _hfdata/raw/webshop-small.tar.gz -C data/
ls data/      # 应看到 items_shuffle_1000.json  items_ins_v2_1000.json  items_human_ins.json
```
- `zhangdw/webshop` 的 `raw/webshop-small.tar.gz` 即原版 `setup.sh -d small` 要的 3 个文件，一一对应。
- `huggingface-cli` 没了就先 `pip install -U huggingface_hub`。
- 若 `huggingface-cli download` 仍慢/超时：浏览器打开 https://hf-mirror.com/datasets/zhangdw/webshop ，手动下 `raw/webshop-small.tar.gz` 再解压。

---

## Step 5：起 HTTP 桥（PECS 连这个 :8000）

> 搜索后端补丁已在 `web_agent_site/engine/bm25_search.py` + `engine.py` 里，无需再改。

```bash
cd D:\简历\pecs-multi-agent\webshop
conda activate webshop
cp D:/简历/pecs-multi-agent/tools/webshop_server.py ./
python webshop_server.py --port 8000 --num-products 1000
```
- 看到 `* Running on http://0.0.0.0:8000` 即桥起。
- **验证**（另开 Git Bash）：
  ```bash
  curl http://localhost:8000/health
  # 期望：{"status":"ok","env":"ready"}
  ```
- 若报 `env init failed` / gym.make 参数不匹配：贴完整报错给我，我调 `webshop_server.py` 的 `_make_env()`。

---

## Step 6：PECS 接真实环境跑评测

另开 Git Bash，在 PECS 项目目录：

```bash
export WEBSHOP_SERVER_URL=http://localhost:8000
export LLM_API_KEY=[REDACTED-LINGSHU-KEY]
export LLM_BASE_URL=https://www.lingshucode.com/v1
export LLM_MODEL=glm-5.2

cd D:\简历\pecs-multi-agent
python run_resumable.py webshop_001
```
- 日志出现 `WebShop 交互完成（共 N 步, 奖励=X.XXX）` = 走真实环境成功。
- 多任务：依次 `python run_resumable.py webshop_002` / `003` ...

---

## Step 7：（可选）跑 ReAct 基线算 +pp

```bash
python run_real_baseline.py webshop_001   # 同环境变量不变
```
同任务奖励差值即 pp 数，目标 +18pp。

---

## 退回本地 mock（零代码改动）

```bash
unset WEBSHOP_SERVER_URL
python run_resumable.py webshop_001   # 走本地 8 商品 mock
```
停真实环境：关掉 Step 5 的 `webshop_server.py` 窗口。conda 环境留着复用。

---

## 故障速查

| 现象 | 最可能原因 | 快修 |
|---|---|---|
| `git clone` Connection was reset | 网络抖动 | 等 10 秒重试 |
| `huggingface-cli` 没这命令 | 没装 huggingface_hub | `pip install -U huggingface_hub` |
| HF 下载慢/超时 | 镜像偶发 | 浏览器手动下 `webshop-small.tar.gz` |
| `conda activate` 报错 | 没初始化 | `conda init bash` 后重开 Git Bash |
| `env init failed` | gym.make 参数差异 | 贴报错给我调 `webshop_server.py` |
| curl localhost:8000 无响应 | 桥没起 | 确认 Step 5 进程在跑、看它报错 |
| PECS "连接被拒绝" | 端口错 | 确认 `localhost:8000` 非 `:3000` |

## 求职诚实标注提醒
作品集须注明：**搜索后端用 `rank_bm25`（纯 Python）替代原版 pyserini/Lucene，功能等价（同属 BM25 排序族），仅命中顺序略有差异，以兼容 Windows 本地部署**。这是工程等价替代，不是缩水。
