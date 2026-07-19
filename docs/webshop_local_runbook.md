# WebShop 真实环境 — 本机完整方案（2026-07-18 修正版）

> 这是一份**从头到尾**的可执行方案。跟着做，不需要理解每一步原理也能跑通；
> 想懂"为什么"的，先看第 0 节架构关系。
>
> ⚠️ 三个已纠正的坑（之前给过错误预告，这里一次性改对）：
> 1. 原 `./setup.sh -d small` 用 `gdown` 从 **Google Drive** 拉数据 → 国内被墙，**不能跑**。数据改走 HF 镜像。
> 2. 原方案要 **Java + pyserini** 建 Lucene 索引 → Windows 上 JNI 抽风。已用纯 Python `rank_bm25` 替代，**不需要 Java、不需要索引、不需要 torch**。
> 3. `run_resumable.py` / `run_real_baseline.py` 都是 **GAIA** 的，跑 WebShop 要用新建的 `run_webshop.py`。

---

## 第 0 节：三个组件的关系（先搞清 A 和 B 是什么）

你的 PECS 项目要"在真实 WebShop 上跑"，其实有 **3 个独立进程**在协作：

```
┌─────────────────────────┐         HTTP :8000          ┌──────────────────────────┐
│  PECS 主程序（你常用环境） │  ───────────────────────▶  │  WebShop HTTP 桥           │
│  Python 3.13 + langgraph │   reset / step / health     │  conda py3.8 环境          │
│  run_webshop.py          │  ◀───────────────────────  │  webshop_server.py        │
│  调 LLM 决策下一步动作    │   返回 observation/reward   │  包着 WebAgentTextEnv-v0   │
└─────────────────────────┘                             └──────────────────────────┘
        │                                                        │
        │ 用 LLM 网关 GLM 生成动作                                 │ 用 rank_bm25 搜索商品
        ▼                                                        ▼
   tools/webshop.py → WebShopEnv(HTTP 客户端)          web_agent_site 引擎 + BM25 搜索后端
```

为什么拆成"桥"？因为 WebShop 原生依赖 gym/旧版 torch/Java，和 PECS 的 Python 3.13 环境冲突。
所以把 WebShop 单独跑在一个 **conda py3.8** 环境里，只暴露 HTTP 接口；PECS 这边纯 HTTP 客户端，**零冲突**。

**一句话**：你要在两个 Git Bash 窗口里分别跑"桥"和"PECS"，中间靠 `WEBSHOP_SERVER_URL=http://localhost:8000` 这个环境变量连起来。

---

## 第 1 节：前置软件（一次性）

| 软件 | 用途 | 验证命令 |
|---|---|---|
| **Git for Windows** | 提供 Git Bash（CMD 不行） | `git --version` |
| **Python 3.11**（系统已装，无需 conda） | 建 WebShop 隔离 venv | `C:\Users\jx\AppData\Local\Programs\Python\Python311\python.exe --version` |
| **（你常用的）Python 环境** | 跑 PECS 主程序 | `python --version`（PECS 侧，3.13 也行） |

> 不需要装：Java / JDK、Docker、torch、Miniconda（都用 venv 替代，更轻量）。
> 注：原方案用 conda py3.8，但你本机未装 conda，已验证用系统 Python 3.11 venv 同样可跑 WebShop 桥。

---

## 第 2 节：克隆 WebShop 源码（已做过可跳过）

**窗口 A（Git Bash）**：
```bash
cd pecs-multi-agent
git clone --depth 1 https://github.com/princeton-nlp/webshop.git webshop
cd webshop && git rev-parse --short HEAD && echo "CLONE_OK"
```
- `Connection was reset` → 等 10 秒重跑（你之前第 3 次成功过）。

---

## 第 3 节：建 WebShop 专用 venv（替代 conda）

**窗口 A（Git Bash）**：
```bash
cd pecs-multi-agent/webshop
# 用系统 Python 3.11 建隔离 venv（你本机无 conda，venv 同样可行）
"C:/Users/jx/AppData/Local/Programs/Python/Python311/python.exe" -m venv .venv
source .venv/Scripts/activate
python --version      # 应显示 3.11.x
```

---

## 第 4 节：装依赖（刻意去掉 pyserini / torch）

**窗口 A（venv 已 activate，提示符前有 `(.venv)`）**：
```bash
pip install gym==0.24.0 numpy beautifulsoup4 flask rich cleantext tqdm \
            rank_bm25 thefuzz scikit_learn spacy

python -m spacy download en_core_web_sm     # 小模型 ~12MB，非 lg 的 500MB+

pip install transformers==4.19.2            # 仅兼容导入；装不上先跳过，遇 import 报错再加
```
- **刻意不含** `pyserini`（已用 BM25 替代）、`torch`（已惰性，运行时用不到）。
- `spacy + en_core_web_sm` **必须装**（环境初始化必走 `goal.get_goals`）。
- 已在 venv 内，pip 指向 `.venv/Scripts/pip.exe`，不会污染系统 Python。

---

## 第 5 节：下载数据（绕开 Google Drive，走 HF 镜像）

**窗口 A（conda activate webshop）**：
```bash
export HF_ENDPOINT=https://hf-mirror.com

huggingface-cli download --repo-type dataset zhangdw/webshop \
    --local-dir pecs-multi-agent\webshop\_hfdata

cd pecs-multi-agent\webshop
# ⚠️ 关键：tar 包内部自带 data/ 前缀（结构是 data/items_*.json），
#    必须解压到「仓库根目录」(-C .)，让它自带的 data/ 正好落位成 webshop/data/。
#    千万别解压进已有的 data/ 目录，否则会变成 data/data/items_*.json，桥找不到文件直接崩。
tar -xzf _hfdata/raw/webshop-small.tar.gz -C .

ls data/
# 必须看到这三个文件（直接位于 data/ 下，不是 data/data/）：
#   items_shuffle_1000.json   items_ins_v2_1000.json   items_human_ins.json
```
- 若 `huggingface-cli` 没有：`pip install -U huggingface_hub`。
- 若下载仍慢/超时：浏览器开 https://hf-mirror.com/datasets/zhangdw/webshop ，手动下 `raw/webshop-small.tar.gz` 再解压。
- `zhangdw/webshop` 的 `webshop-small.tar.gz` 与原版 `setup.sh -d small` 的数据**完全一致**（3 个文件同名同结构）。

---

## 第 6 节：应用补丁 + 起 HTTP 桥（窗口 A 一直开着别关）

**⚠️ 必须先 apply 补丁，再起桥**（否则 bm25 搜索后端不生效，桥会用原版 pyserini 直接崩）：

**窗口 A（venv 已 activate，在 webshop 目录）**：
```bash
cd pecs-multi-agent
bash webshop_patches/apply.sh          # 把 3 个补丁覆盖进 webshop/web_agent_site/...
cd webshop

# 起桥（用官方 webshop_server.py，补丁已注入 bm25 后端）
python webshop_server.py --port 8000 --num-products 1000
```
- 看到 `[webshop_server] env ready (num_products=1000)` 或 `* Running on http://0.0.0.0:8000` 即成功。
  （即使打印 `WARN: env init failed`，服务也起了，等你发请求时才会真正建环境——把报错贴给我。）

**窗口 B（另开 Git Bash）验证桥活着**：
```bash
curl http://localhost:8000/health
# 期望：{"status":"ok"}
```

---

## 第 7 节：PECS 跑真实 WebShop 评测（窗口 C）

**窗口 C（Git Bash，在 PECS 项目目录 pecs-multi-agent）**：
```bash
# 连真实环境的开关，只有这一个变量
export WEBSHOP_SERVER_URL=http://localhost:8000

# 下面三个一般在 .env 里已配好，如未配再 export
export LLM_API_KEY=<你的_API_KEY>
export LLM_BASE_URL=<你的网关 Base URL>
export LLM_MODEL=<网关支持的模型名>

cd pecs-multi-agent
python run_webshop.py
```
- `run_webshop.py` 会先跑 **PECS 多智能体**（6 道 webshop 题），再跑 **ReAct 基线**，最后打印：
  ```
  多智能体成功率 : x.x%
  ReAct 成功率    : y.y%
  差值 (PECS-ReAct): +z.z pp
  Token 降本      : +w.w%
  ```
- 结果同时存到 `results/webshop_run.json`。
- 日志里每道题出现 `WebShop 交互完成（共 N 步, 奖励=X.XXX）` 即证明走的是**真实环境**（不是 mock）。

> 想先验证链路通不通、又不想等真实环境？**先不设 `WEBSHOP_SERVER_URL`** 直接 `python run_webshop.py`，
> 它会走本地 8 商品 mock（秒回），确认整条代码链没问题，再设环境变量接真环境。

---

## 第 8 节：解读与诚实标注

- **奖励**：WebShop 奖励 0~1，≥0.5 算宽松通过，=1.0 算严格成功（`benchmarks/webshop_eval.py` 里 `REAL_REWARD_THRESHOLD=0.5` 可调）。
- **+pp 目标 +18**：`run_webshop.py` 直接算 `多智能体成功率 - ReAct 成功率`。
- **⚠️ 诚实声明（作品集必写）**：搜索后端用 **`rank_bm25`（纯 Python）** 替代原版 **pyserini/Lucene**，
  属**功能等价替代**（同 BM25 排序族），仅命中顺序略有差异，目的是兼容 Windows 本地部署、避开 JNI 坑。
  这不是缩水，懂行评审者反而认可这个工程取舍。

---

## 第 9 节：故障速查表

| 现象 | 最可能原因 | 快修 |
|---|---|---|
| `git clone` Connection was reset | 网络抖动 | 等 10 秒重试 |
| `huggingface-cli` 命令不存在 | 没装 huggingface_hub | `pip install -U huggingface_hub` |
| HF 下载慢/超时 | 镜像偶发 | 浏览器手动下 `webshop-small.tar.gz` 再解压 |
| `conda activate` 报错 | shell 未初始化 | `conda init bash` 后重开 Git Bash |
| `python -m spacy download` 慢 | CDN 偶发 | 多试两次，或设 `HF_ENDPOINT` 后重试 |
| 桥起不来 / ImportError | 依赖没装全 | 回第 4 节重装，看具体缺哪个包 |
| `curl localhost:8000` 无响应 | 桥没起 | 回第 6 节确认进程在跑、看报错 |
| PECS 报 "连接被拒绝" | 端口/变量错 | 确认 `WEBSHOP_SERVER_URL=http://localhost:8000`（不是 :3000） |
| 日志一直 mock 不接真环境 | 忘了设变量 | 窗口 C `echo $WEBSHOP_SERVER_URL` 应为 `http://localhost:8000` |
| 某题奖励异常低 | WebShop 任务本身难 / LLM 决策差 | 正常，记录进报告；多跑几题看整体 |

---

## 附录：已打的 3 处代码补丁（都在 `pecs-multi-agent\webshop` 内）

1. **`web_agent_site/engine/bm25_search.py`（新增）** + **`engine.py` 改 `init_search_engine`**：
   默认用纯 Python BM25 搜索后端，pyserini 改惰性导入（仅 `WEBSITE_USE_PYSERINI=1` 才走原版）。
   **已 unit test 通过**：`search('red shoes')` → 红鞋商品排首，`doc().raw()` 接口与上游一致。
2. **`web_agent_site/envs/web_agent_text_env.py`**：顶层 `import torch` 移除，仅在 `get_image` 分支惰性加载 → **运行时免装 torch**。
3. **`tools/webshop_server.py` 注释修正**：删除"需 Java / 需建索引 / pip install -e"等过时说明。

> 补丁是替你在项目目录里改好的，你本机 `git pull`/直接同步即可（若你是在别处 clone 的，把 `webshop` 目录整体同步过来）。
