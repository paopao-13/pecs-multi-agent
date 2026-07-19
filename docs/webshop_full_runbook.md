# WebShop 真实环境 — 完整执行手册（从 clone 后到测出 +pp）

> 适用：你 Windows 本机，已把 `princeton-nlp/webshop` clone 到 `pecs-multi-agent/webshop`。
> 全程用 **Git Bash**（不要用 CMD，CMD 没有 grep/findstr/curl）。
> 结论前置：gym 环境 id 已确认 = `WebAgentTextEnv-v0`，`tools/webshop_server.py` 已匹配，**桥代码不用改**。

---

## Step 1 — 前置自检（Git Bash 里逐条）

```bash
java -version
conda --version
python --version        # 看你常用 Python 是哪个版本
```

- `java` 报错 → 去 https://adoptium.net/ 下 **JDK 8 或 11** 安装，装完重启 Git Bash
- `conda` 报错 → 去 https://docs.conda.io/en/latest/miniconda.html 下 Miniconda Windows 版安装
- 同时确认磁盘 `D:` 剩余 ≥ 20GB

## Step 2 — 建 conda 环境（Python 3.8）

```bash
cd pecs-multi-agent/webshop
conda create -n webshop python=3.8.13 -y
conda activate webshop
python --version        # 应是 3.8.13
```

## Step 3 — 装依赖 + 数据 + 索引（最耗时，约 10–20 分钟）

```bash
./setup.sh -d small
```

脚本会依次：pip 装 pyserini/spacy → 从 Google Drive 下商品数据 → 构建 BM25 索引（需 Java）。

**可能卡点：**

| 现象 | 处理 |
|---|---|
| `gdown` 下 Google Drive 数据失败 / 卡住 | 浏览器手动下 `items_ins_v2.json` + `items_shuffle.json`（从仓库 README 给的 Drive 链接），放到 `webshop/data/`，然后重跑 `./setup.sh -d small` |
| Java / pyserini 报错 | 确认 `JAVA_HOME` 设对：`export JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-8.x.x"`（按实际路径改）；Git Bash 里路径用 `/c/...` 不是 `C:\...` |
| spaCy `en_core_web_sm` 下载慢 | 自动拉 CDN；超时手动 `python -m spacy download en_core_web_sm` |

**验证数据准备好了：**
```bash
ls -la data/            # 应有 items_*.json 等
ls -la search_engine/   # 应有构建好的索引目录
```

## Step 4 — 起 HTTP 桥（PECS 连这个 :8000）

```bash
# 仍在 webshop 目录，conda 已激活
pip install flask
cp ../tools/webshop_server.py ./
python webshop_server.py --port 8000 --num-products 1000
```

看到 `* Running on http://0.0.0.0:8000` 即桥起成功。**这个窗口不要关。**

另开一个 Git Bash 验证：
```bash
curl http://localhost:8000/health
# 期望：{"status":"ok"} 或 {"status":"ok","env":"ready"}
```

> 若报 `env init failed ... WebAgentTextEnv-v0` → 把完整启动日志贴给我，我调 `webshop_server.py` 的 `_make_env()`（已内置 `WebShop-v0/v1` 兜底，一般不用动）。

## Step 5 — PECS 接真实环境跑评测

**另开 Git Bash 窗口**，在 PECS 项目目录：

```bash
export WEBSHOP_SERVER_URL=http://localhost:8000
export LLM_API_KEY=[REDACTED-LINGSHU-KEY]
export LLM_BASE_URL=https://www.lingshucode.com/v1
export LLM_MODEL=glm-5.2

cd pecs-multi-agent
python run_resumable.py webshop_001
```

- 日志出现 `WebShop 交互完成（共 N 步, 奖励=X.XXX）` = 走真实环境成功。
- 奖励 ≥ 0.5 算宽松通过；≥ 1.0 算严格成功（阈值在 `benchmarks/webshop_eval.py` 的 `REAL_REWARD_THRESHOLD`）。
- 跑多个任务：
  ```bash
  python run_resumable.py webshop_002
  python run_resumable.py webshop_003
  ```

## Step 6 — 跑 ReAct 基线算 +pp

同环境变量不变，跑基线对比：
```bash
python run_real_baseline.py webshop_001
```

对比同任务 PECS 与 ReAct 的奖励分差值 = pp。

---

## 退回本地 mock（"不行就退"）

```bash
unset WEBSHOP_SERVER_URL        # Git Bash
# 或 CMD: set WEBSHOP_SERVER_URL=
python run_resumable.py webshop_001   # 走本地 8 商品 mock，不依赖 WebShop 服务
```

停真实环境：关掉 Step 4 的 `webshop_server.py` 窗口即可，conda 环境留着复用。

---

## 完整代码清单（都已就绪，无需改写）

| 文件 | 作用 | 状态 |
|---|---|---|
| `tools/webshop_server.py` | 环境侧 HTTP 桥（包装 `WebAgentTextEnv-v0`） | ✅ 已匹配 gym id |
| `tools/webshop_env.py` | PECS 侧 HTTP 客户端（连 :8000） | ✅ 就绪 |
| `tools/webshop.py` | `webshop_interact` 多轮决策 + `use_real_env()` 自动路由 | ✅ 就绪 |
| `benchmarks/webshop_eval.py` | `mode="real"` 奖励分判定 | ✅ 就绪 |
| `docs/QUICKSTART_webshop_routeA.md` | 极简上手 | ✅ |
| `docs/webshop_local_checklist.md` | 6 步 checklist + 故障表 | ✅ |
| `scripts/webshop_preflight.py` | 前置体检 | ✅ |

---

## 故障速查

| 现象 | 原因 | 快修 |
|---|---|---|
| `setup.sh` 找不到 | 不在 webshop 目录 | `cd pecs-multi-agent/webshop` |
| Google Drive 数据下不动 | 国内墙 | 浏览器手动下 `items_*.json` 放 `data/` 重跑 |
| Java 报错 | 没 JAVA_HOME 或版本不对 | 装 JDK 8/11 + `export JAVA_HOME=...` |
| `env init failed` | gym 参数差异（极少） | 贴日志给我调 `_make_env` |
| curl localhost:8000 无响应 | 桥没起 | 确认 Step 4 进程在跑 |
| PECS "连接被拒绝" | 端口错 | 确认是 `localhost:8000` 不是 `:3000` |
