# 真实 WebShop 环境搭建指南（已校正版）

> 本文替代此前版本中**两处错误**的写法，步骤基于 2026-07 核实的官方事实。
> 目标：在能跑 WebShop 的机器（本机 / 云服务器）上部署真实环境，供 PECS 通过 HTTP 远程驱动，实现 WebShop 真实榜单（+18pp 或真实差值）。

## 0. 先纠正两个关键错误（之前文档踩的坑）

1. **gym 环境 ID 应是 `WebAgentTextEnv-v0`，不是 `WebShop-v1`/`WebShop-v0`。**
   官方文本环境注册名是 `WebAgentTextEnv-v0`（来自 `web_agent_site/envs`）。
   之前 `docker-compose.yml` / `webshop_env.py` 写的 `WebShop-v1/v0` 会让 `gym.make` 直接报"环境不存在"。

2. **真实 WebShop 文本环境是"进程内"运行的，不是简单 HTTP 服务。**
   它依赖本地 pyserini 搜索引擎（需 **Java** + 商品索引 + spaCy 模型），
   通过 `gym.make('WebAgentTextEnv-v0', observation_mode='text', num_products=...)` 在进程内启动。
   官方 `run_dev.sh` 起的是**给人看的 HTML 网站**（:3000），不是 Agent 用的 gym API。

   → 因此正确做法：**把 WebShop 跑在独立环境（conda py3.8 或 Docker），再起一个我们写的轻量 HTTP 桥（`tools/webshop_server.py`）暴露 reset/step；PECS 这边只做 HTTP 客户端（`tools/webshop_env.py`），无需装 webshop / Java。**

## 1. 仓库与事实（已核实 2026-07）

- 官方源码：`https://github.com/princeton-nlp/webshop`（含 118 万商品、12087 条指令）
- 带**维护中 Dockerfile** 的好 fork：`https://github.com/ai-nikolai/WebShop`（我们 Docker 路线用它）
- 文本环境 gym id：`WebAgentTextEnv-v0`
- 最少资源：**Java** + **≥8GB RAM**（small 数据集）/ 全量 ≥16GB；磁盘 ≥10GB
- 依赖：Flask、gym、pyserini、rank_bm25、spaCy（`en_core_web_lg`）、torch、transformers

## 2. 路线 A：conda 原生（★推荐 Windows 用户，省内存、最稳）

不需要 Docker，直接用 conda 把 WebShop 装在独立环境，再起 HTTP 桥。

```bash
# 1. 克隆
git clone https://github.com/princeton-nlp/webshop.git webshop
cd webshop

# 2. 建环境（官方指定 3.8.13）
conda create -n webshop python=3.8.13
conda activate webshop

# 3. 一键安装：依赖 + 数据 + spaCy 模型 + 构建索引
./setup.sh -d small        # small=1000 商品；-d all=全量（慢）

# 4. 装 Flask（给 HTTP 桥用），并把 PECS 的 HTTP 桥拷进来
pip install flask
cp /path/to/pecs/tools/webshop_server.py ./

# 5. 起 HTTP 桥（PECS 连这个）
python webshop_server.py --port 8000 --num-products 1000
#  另开终端可看 HTML 预览：./run_dev.sh  → http://localhost:3000/ABC
```

成功后 HTTP 桥在 `http://localhost:8000`，`curl http://localhost:8000/health` 返回 `{"status":"ok"}`。

## 3. 路线 B：Docker（部署 / 云服务器）

用带 Dockerfile 的 fork，一键构建并起 HTTP 桥。首次构建约 10-30 分钟。

```bash
# 一键脚本（Windows 用 scripts/run_real_webshop.bat，Linux 用 .sh）
git clone https://github.com/ai-nikolai/WebShop.git webshop-src
docker compose up -d --build
# 容器暴露：8000=HTTP 桥（PECS 用），3000=HTML 预览
curl http://localhost:8000/health
```

> Docker 版更吃内存（small 建议 ≥8GB，全量 ≥16GB）。若本机内存紧张，优先用路线 A。

## 4. PECS 对接（零代码改动）

只要设置环境变量，PECS 的 `webshop` 工具自动从本地 8 商品玩具切到真实环境：

```bash
# Windows
set WEBSHOP_SERVER_URL=http://localhost:8000
# Linux / macOS
export WEBSHOP_SERVER_URL=http://localhost:8000

python run_resumable.py webshop_001
```

`tools/webshop.py` 的 `use_real_env()` 据此判断；`webshop_interact` 多轮驱动
`search[...] → click[BUTTON_x] → buy`，最后用奖励分（`parse_webshop_reward`）计分。

**评测**（真实模式自动按奖励分判定，阈值见 `benchmarks/webshop_eval.py` 的 `REAL_REWARD_THRESHOLD`，默认 0.5，可调到 1.0 取严格口径）：

```bash
python -c "from benchmarks.webshop_eval import evaluate_webshop, evaluate_react_webshop; \
print('PECS', evaluate_webshop()); print('ReAct', evaluate_react_webshop())"
```

跑 N 道任务分别算 PECS vs ReAct 成功率，差值才是真实 +Xpp。

## 5. 常见坑

1. **Java 没装** → pyserini 初始化失败。务必先装 JDK 并加入 PATH。
2. **spaCy `en_core_web_lg` 下载慢** → `python -m spacy download en_core_web_lg`。
3. **gdown 下数据失败**（网络/cookie）→ 按文档手动从 Google Drive 下 `items_ins_v2.json` / `items_shuffle.json` 放 `data/`。
4. **默认只加载 1000 商品** → 想接近真实榜单改 `web_agent_site/utils.py` 指向全量文件。
5. **端口搞错** → PECS 连的是 HTTP 桥 **:8000**，不是官方 HTML 的 :3000。
6. **环境 ID 错** → 必须用 `WebAgentTextEnv-v0`（见第 0 节）。
7. **Docker 内存不足** → 给容器 ≥8GB（small）/ ≥16GB（全量）。

## 6. 与之前文档的差异（变更记录）

- `docker-compose.yml`：仓库改 `ai-nikolai/WebShop`；新增挂载并运行 `webshop_server.py`（:8000）。
- `tools/webshop_env.py`：从「本地 gym 包装」改为「纯 HTTP 客户端」，不再要求 PECS 侧装 webshop/gym/Java。
- 新增 `tools/webshop_server.py`：WebShop 环境侧的 gym HTTP 桥。
- `scripts/run_real_webshop.bat/.sh`：clone 仓库改正、端口改为探活 :8000、提示端口 8000。
